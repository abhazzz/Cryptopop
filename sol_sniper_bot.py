import os
import json
import asyncio
import logging
import aiohttp
import websockets
from dotenv import load_dotenv
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any

# --- Load env vars from .env (works locally) ---
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TradingBotConfig:
    """Configuration class for better organization"""
    def __init__(self):
        # Use environment variables for sensitive data
        self.BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
        self.CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@Cryptopopprices")
        
        if not self.BOT_TOKEN:
            raise ValueError("TELEGRAM_BOT_TOKEN environment variable is required")
        
        # WebSocket streams
        self.LIQUIDATION_STREAM = "wss://fstream.binance.com/ws/solusdt@forceOrder"
        self.TRADE_STREAM = "wss://fstream.binance.com/ws/solusdt@aggTrade"
        
        # Thresholds
        self.LIQUIDATION_THRESHOLD = float(os.getenv("LIQUIDATION_THRESHOLD", "1000000"))
        self.TRADE_USD_THRESHOLD = float(os.getenv("TRADE_USD_THRESHOLD", "1000000"))
        self.PRICE_ALERT_THRESHOLD = float(os.getenv("PRICE_ALERT_THRESHOLD", "2.0"))
        
        # Rate limiting and retry configuration
        self.MAX_RETRIES = 3
        self.RETRY_DELAY = 5
        self.REQUEST_TIMEOUT = 10
        self.HEARTBEAT_INTERVAL = 60

class TradingBot:
    def __init__(self, config: TradingBotConfig):
        self.config = config
        self.last_alert_time: Optional[datetime] = None
        self.last_alert_price: Optional[float] = None
        # Increased history size and store more granular data
        self.price_history = deque(maxlen=1000)  # ~16 hours of data at 60s intervals
        # Use aiohttp session for better performance
        self.session: Optional[aiohttp.ClientSession] = None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.REQUEST_TIMEOUT)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def send_alert_with_retry(self, text: str) -> bool:
        """Send alert with retry logic and proper error handling"""
        url = f"https://api.telegram.org/bot{self.config.BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": self.config.CHANNEL_ID,
            "text": text,
            "parse_mode": "Markdown"
        }
        
        for attempt in range(self.config.MAX_RETRIES):
            try:
                async with self.session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"Alert sent successfully: {text[:50]}...")
                        return True
                    else:
                        logger.warning(f"Telegram API returned status {response.status}")
                        
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed to send alert: {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    await asyncio.sleep(self.config.RETRY_DELAY * (attempt + 1))
        
        logger.error(f"Failed to send alert after {self.config.MAX_RETRIES} attempts")
        return False

    async def connect_websocket_with_retry(self, uri: str, handler_func, label: str):
        """WebSocket connection with exponential backoff retry"""
        retry_delay = 1
        max_retry_delay = 60
        
        while True:
            try:
                logger.info(f"Connecting to {label}...")
                async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                    logger.info(f"✅ Connected to {label}")
                    retry_delay = 1  # Reset delay on successful connection
                    
                    while True:
                        try:
                            # Add timeout to prevent hanging
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            await handler_func(json.loads(msg))
                        except asyncio.TimeoutError:
                            logger.warning(f"{label} - No message received in 30s, sending ping")
                            await ws.ping()
                        except websockets.exceptions.ConnectionClosed:
                            logger.warning(f"{label} connection closed")
                            break
                            
            except Exception as e:
                logger.error(f"{label} error: {e}")
                logger.info(f"Retrying {label} in {retry_delay}s...")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

    async def handle_liquidation(self, msg: Dict[str, Any]):
        """Handle liquidation messages"""
        try:
            order = msg.get('o', {})
            quantity = float(order.get('q', 0))
            price = float(order.get('p', 0))
            side = order.get('S', '')

            if quantity >= self.config.LIQUIDATION_THRESHOLD:
                emoji = "🔴" if side == "SELL" else "🟢"
                alert = f"{emoji} Liquidation: {quantity:,.0f} contracts {side.lower()} at ${price:,.2f}"
                logger.info(alert)
                await self.send_alert_with_retry(alert)
                
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error processing liquidation data: {e}")

    async def handle_trade(self, msg: Dict[str, Any]):
        """Handle trade messages"""
        try:
            qty = float(msg['q'])
            price = float(msg['p'])
            usd_value = qty * price
            side = "BOUGHT" if msg['m'] is False else "SOLD"

            if usd_value >= self.config.TRADE_USD_THRESHOLD:
                emoji = "🟢" if side == "BOUGHT" else "🔴"
                alert = f"{emoji} Large Trade: {qty:,.2f} contracts {side.lower()} at ${price:,.2f} (${usd_value:,.0f})"
                logger.info(alert)
                await self.send_alert_with_retry(alert)
                
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error processing trade data: {e}")

    async def listen_liquidations(self):
        """Listen to liquidation stream"""
        await self.connect_websocket_with_retry(
            self.config.LIQUIDATION_STREAM, 
            self.handle_liquidation, 
            "SOLUSD liquidations"
        )

    async def listen_trades(self):
        """Listen to trade stream"""
        await self.connect_websocket_with_retry(
            self.config.TRADE_STREAM, 
            self.handle_trade, 
            "SOLUSD trades"
        )

    async def get_current_price_with_retry(self) -> Optional[float]:
        """Get current price with retry logic"""
        url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
        
        for attempt in range(self.config.MAX_RETRIES):
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("solana", {}).get("usd")
                    else:
                        logger.warning(f"CoinGecko API returned status {response.status}")
                        
            except Exception as e:
                logger.error(f"Attempt {attempt + 1} failed to get price: {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    await asyncio.sleep(self.config.RETRY_DELAY)
        
        return None

    async def monitor_price_movement(self):
        """Monitor 15-minute price changes - FIXED VERSION"""
        logger.info("📊 Starting price movement monitoring...")
        
        while True:
            try:
                current_price = await self.get_current_price_with_retry()
                
                if current_price is None:
                    logger.error("Failed to get current price, skipping this cycle")
                    await asyncio.sleep(60)
                    continue

                now = datetime.utcnow()
                self.price_history.append((now, current_price))
                
                # Log current price for debugging
                logger.info(f"💰 Current SOL price: ${current_price:.2f}")

                # Check for price alerts (simplified logic)
                trigger_alert, alert_message = self._check_price_triggers_fixed(current_price, now)
                
                if trigger_alert:
                    logger.info(f"🚨 PRICE ALERT TRIGGERED: {alert_message}")
                    await self.send_alert_with_retry(alert_message)
                    self.last_alert_time = now
                    self.last_alert_price = current_price

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Price movement check error: {e}")
                await asyncio.sleep(60)

    def _check_price_triggers_fixed(self, current_price: float, now: datetime) -> Tuple[bool, str]:
        """FIXED: Rolling 15-minute window trigger logic"""
        
        # Need at least 15 minutes of data for initial comparison
        if len(self.price_history) < 15:
            logger.info(f"📈 Building price history... ({len(self.price_history)}/15 minutes)")
            return False, ""
        
        # Determine reference price and time based on recent alerts
        if self.last_alert_time and self.last_alert_price:
            time_since_last_alert = (now - self.last_alert_time).total_seconds() / 60
            
            if time_since_last_alert < 15:
                # Within 15 minutes of LAST alert - compare against that alert price
                reference_price = self.last_alert_price
                reference_time = self.last_alert_time
                reference_type = "last alert"
                logger.info(f"📊 Using last alert price as reference ({time_since_last_alert:.1f}m ago)")
            else:
                # More than 15 minutes since LAST alert - reset and use historical price
                logger.info(f"📊 15+ minutes since last alert ({time_since_last_alert:.1f}m) - resetting to historical comparison")
                reference_price, reference_time, reference_type = self._get_historical_reference_price(now)
                # Reset last alert since we're outside the 15-minute window from the LAST alert
                self.last_alert_price = None
                self.last_alert_time = None
        else:
            # No recent alert - use historical price
            reference_price, reference_time, reference_type = self._get_historical_reference_price(now)
        
        if reference_price is None:
            return False, ""
        
        # Calculate percentage change
        pct_change = ((current_price - reference_price) / reference_price) * 100
        minutes_ago = (now - reference_time).total_seconds() / 60
        
        # Log the calculation for debugging
        logger.info(f"📊 Price check: ${reference_price:.2f} ({reference_type}, {minutes_ago:.1f}m ago) → ${current_price:.2f} = {pct_change:+.2f}%")
        
        # Trigger alert if change exceeds threshold
        if abs(pct_change) >= self.config.PRICE_ALERT_THRESHOLD:
            direction = "🚀" if pct_change > 0 else "📉"
            alert_type = "from alert" if reference_type == "last alert" else "15min"
            alert = f"{direction} *SOL Price Alert* ({alert_type})\n"
            alert += f"`{pct_change:+.2f}%` change in {minutes_ago:.0f} minutes\n"
            alert += f"${reference_price:.2f} → ${current_price:.2f}"
            return True, alert
            
        return False, ""
    
    def _get_historical_reference_price(self, now: datetime) -> Tuple[Optional[float], Optional[datetime], str]:
        """Get reference price from 15 minutes ago in price history"""
        # Find price approximately 15 minutes ago (within 2-minute window)
        cutoff_time = now - timedelta(minutes=15)
        tolerance = timedelta(minutes=2)
        
        # Get prices within the tolerance window around 15 minutes ago
        reference_prices = [
            (timestamp, price) for timestamp, price in self.price_history 
            if abs((timestamp - cutoff_time).total_seconds()) <= tolerance.total_seconds()
        ]
        
        if reference_prices:
            # Use the closest price to 15 minutes ago
            reference_time, reference_price = min(reference_prices, key=lambda x: abs((x[0] - cutoff_time).total_seconds()))
            return reference_price, reference_time, "15min history"
        else:
            # Fallback: get the oldest price we have that's at least 10 minutes old
            min_age = now - timedelta(minutes=10)
            old_prices = [(t, p) for t, p in self.price_history if t <= min_age]
            if old_prices:
                reference_time, reference_price = old_prices[0]  # oldest price
                return reference_price, reference_time, "oldest available"
            else:
                return None, None, "none"

    async def heartbeat(self):
        """Heartbeat to show bot is alive"""
        while True:
            logger.info("💓 Trading bot is alive and running...")
            await asyncio.sleep(self.config.HEARTBEAT_INTERVAL)

async def safe_task_runner(coro, label: str):
    """Safely run a coroutine with error handling and restart logic"""
    while True:
        try:
            logger.info(f"Starting {label}...")
            await coro
        except Exception as e:
            logger.error(f"🔥 {label} crashed: {e}", exc_info=True)
            logger.info(f"Restarting {label} in 10 seconds...")
            await asyncio.sleep(10)

async def main():
    """Main entry point"""
    try:
        config = TradingBotConfig()
        logger.info("💡 Starting trading bot...")
        logger.info(f"Bot token: {config.BOT_TOKEN[:10]}...") # Debug line
        logger.info(f"Channel ID: {config.CHANNEL_ID}")      # Debug line
        logger.info(f"Price alert threshold: {config.PRICE_ALERT_THRESHOLD}%")
        
        async with TradingBot(config) as bot:
            # Create all tasks
            tasks = [
                safe_task_runner(bot.listen_liquidations(), "liquidations"),
                safe_task_runner(bot.listen_trades(), "trades"), 
                safe_task_runner(bot.monitor_price_movement(), "price monitor"),
                safe_task_runner(bot.heartbeat(), "heartbeat")
            ]
            
            # Run all tasks concurrently
            await asyncio.gather(*tasks)
            
    except KeyboardInterrupt:
        logger.info("🛑 Trading bot stopped manually.")
    except Exception as e:
        logger.error(f"🧨 Top-level crash: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
