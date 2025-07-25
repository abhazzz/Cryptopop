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
        self.price_history = deque(maxlen=200)
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
                emoji = "🔴" if side == "SOLD" else "🟢"
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
        """Monitor 15-minute price changes"""
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

                # Improved price change calculation
                trigger_alert, alert_message = self._check_price_triggers(current_price, now)
                
                if trigger_alert:
                    logger.info(alert_message)
                    await self.send_alert_with_retry(alert_message)
                    self.last_alert_time = now
                    self.last_alert_price = current_price

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Price movement check error: {e}")
                await asyncio.sleep(60)

    def _check_price_triggers(self, current_price: float, now: datetime) -> Tuple[bool, str]:
        """Check if price change triggers should fire"""
        # Get price 15 minutes ago
        cutoff = now - timedelta(minutes=15)
        past_prices = [p for t, p in self.price_history if t <= cutoff]

        trigger_from_15min = False
        trigger_from_last_alert = False
        reference_price = current_price
        pct_change = 0.0

        if past_prices:
            price_15min_ago = past_prices[-1]
            pct_change_15min = ((current_price - price_15min_ago) / price_15min_ago) * 100
            trigger_from_15min = abs(pct_change_15min) >= self.config.PRICE_ALERT_THRESHOLD
            
            if trigger_from_15min:
                reference_price = price_15min_ago
                pct_change = pct_change_15min

        # Check against last alert if within 15 minutes
        if (self.last_alert_time and self.last_alert_price and 
            (now - self.last_alert_time < timedelta(minutes=15))):
            pct_change_since_alert = ((current_price - self.last_alert_price) / self.last_alert_price) * 100
            trigger_from_last_alert = abs(pct_change_since_alert) >= self.config.PRICE_ALERT_THRESHOLD
            
            if trigger_from_last_alert and not trigger_from_15min:
                reference_price = self.last_alert_price
                pct_change = pct_change_since_alert
        else:
            # Reset reference if no recent alert
            self.last_alert_price = past_prices[-1] if past_prices else current_price

        if trigger_from_15min or trigger_from_last_alert:
            arrow = "🚀" if current_price > reference_price else "📉"
            alert = f"{arrow} SOL Price Alert: {pct_change:.2f}% change\n${reference_price:.2f} → ${current_price:.2f}"
            return True, alert
            
        return False, ""

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
