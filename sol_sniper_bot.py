import os
import json
import asyncio
import logging
import aiohttp
import websockets
import hashlib
import hmac
import base64
import time
import urllib.parse
from dotenv import load_dotenv
from collections import deque
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from enum import Enum

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

class AlertPriority(Enum):
    """Alert priority levels for Twitter rate limiting"""
    PRICE_ALERT = 1     # Highest priority
    LIQUIDATION = 2     # Medium priority  
    LARGE_TRADE = 3     # Lowest priority

class CoinConfig:
    """Configuration for individual coins"""
    def __init__(self, symbol: str, **kwargs):
        self.symbol = symbol.upper()
        self.name = kwargs.get('name', symbol)
        
        # Telegram configuration (per coin)
        self.telegram_bot_token = kwargs.get('telegram_bot_token')
        self.telegram_channel_id = kwargs.get('telegram_channel_id')
        self.telegram_enabled = bool(self.telegram_bot_token and self.telegram_channel_id)
        
        # Twitter configuration (per coin)
        self.twitter_enabled = kwargs.get('twitter_enabled', False)
        
        # Telegram thresholds
        self.telegram_liquidation_threshold = kwargs.get('telegram_liquidation_threshold', 1000000)
        self.telegram_trade_threshold = kwargs.get('telegram_trade_threshold', 1000000)
        self.telegram_price_threshold = kwargs.get('telegram_price_threshold', 2.0)
        
        # Twitter thresholds (higher by default)
        self.twitter_liquidation_threshold = kwargs.get('twitter_liquidation_threshold', 5000000)
        self.twitter_trade_threshold = kwargs.get('twitter_trade_threshold', 3000000)
        self.twitter_price_threshold = kwargs.get('twitter_price_threshold', 3.0)
        
        # WebSocket streams
        self.liquidation_stream = kwargs.get('liquidation_stream', f"wss://fstream.binance.com/ws/{symbol.lower()}usdt@forceOrder")
        self.trade_stream = kwargs.get('trade_stream', f"wss://fstream.binance.com/ws/{symbol.lower()}usdt@aggTrade")
        
        # CoinGecko ID for price fetching
        self.coingecko_id = kwargs.get('coingecko_id', symbol.lower())

class TradingBotConfig:
    """Configuration class for better organization"""
    def __init__(self):
        # Twitter API credentials (shared across coins)
        self.TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
        self.TWITTER_API_SECRET = os.getenv("TWITTER_API_SECRET")
        self.TWITTER_ACCESS_TOKEN = os.getenv("TWITTER_ACCESS_TOKEN")
        self.TWITTER_ACCESS_TOKEN_SECRET = os.getenv("TWITTER_ACCESS_TOKEN_SECRET")
        self.TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")
        
        # Twelve Data API
        self.TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
        self.TWELVE_DATA_BASE_URL = "https://api.twelvedata.com"
        
        # Check if Twitter is configured globally
        self.TWITTER_AVAILABLE = all([
            self.TWITTER_API_KEY,
            self.TWITTER_API_SECRET,
            self.TWITTER_ACCESS_TOKEN,
            self.TWITTER_ACCESS_TOKEN_SECRET
        ])
        
        # Twitter rate limiting (shared)
        self.TWITTER_DAILY_LIMIT = int(os.getenv("TWITTER_DAILY_LIMIT", "15"))
        self.TWITTER_COOLDOWN_MINUTES = int(os.getenv("TWITTER_COOLDOWN_MINUTES", "90"))
        
        # Rate limiting and retry configuration
        self.MAX_RETRIES = 3
        self.RETRY_DELAY = 5
        self.REQUEST_TIMEOUT = 10
        self.HEARTBEAT_INTERVAL = 60
        
        # Multi-coin configuration
        self.coins = self._load_coin_configs()
        
        # Validate that at least one coin has valid config
        if not any(coin.telegram_enabled for coin in self.coins.values()):
            raise ValueError("At least one coin must have valid Telegram configuration")
    
    def _load_coin_configs(self) -> Dict[str, CoinConfig]:
        """Load coin configurations from environment"""
        coins = {}
        
        # SOL configuration
        coins['SOL'] = CoinConfig(
            'SOL',
            name='Solana',
            telegram_bot_token=os.getenv("SOL_TELEGRAM_BOT_TOKEN"),
            telegram_channel_id=os.getenv("SOL_TELEGRAM_CHANNEL_ID"),
            twitter_enabled=os.getenv("SOL_TWITTER_ENABLED", "true").lower() == "true",
            telegram_liquidation_threshold=float(os.getenv("SOL_TELEGRAM_LIQUIDATION_THRESHOLD", "500000")),
            telegram_trade_threshold=float(os.getenv("SOL_TELEGRAM_TRADE_THRESHOLD", "750000")),
            telegram_price_threshold=float(os.getenv("SOL_TELEGRAM_PRICE_THRESHOLD", "2.0")),
            twitter_liquidation_threshold=float(os.getenv("SOL_TWITTER_LIQUIDATION_THRESHOLD", "2000000")),
            twitter_trade_threshold=float(os.getenv("SOL_TWITTER_TRADE_THRESHOLD", "2000000")),
            twitter_price_threshold=float(os.getenv("SOL_TWITTER_PRICE_THRESHOLD", "3.0")),
            coingecko_id='solana'
        )
        
        # HBAR configuration
        coins['HBAR'] = CoinConfig(
            'HBAR',
            name='Hedera',
            telegram_bot_token=os.getenv("HBAR_TELEGRAM_BOT_TOKEN"),
            telegram_channel_id=os.getenv("HBAR_TELEGRAM_CHANNEL_ID"),
            twitter_enabled=os.getenv("HBAR_TWITTER_ENABLED", "false").lower() == "true",
            telegram_liquidation_threshold=float(os.getenv("HBAR_TELEGRAM_LIQUIDATION_THRESHOLD", "100000")),
            telegram_trade_threshold=float(os.getenv("HBAR_TELEGRAM_TRADE_THRESHOLD", "150000")),
            telegram_price_threshold=float(os.getenv("HBAR_TELEGRAM_PRICE_THRESHOLD", "1.5")),
            twitter_liquidation_threshold=float(os.getenv("HBAR_TWITTER_LIQUIDATION_THRESHOLD", "500000")),
            twitter_trade_threshold=float(os.getenv("HBAR_TWITTER_TRADE_THRESHOLD", "750000")),
            twitter_price_threshold=float(os.getenv("HBAR_TWITTER_PRICE_THRESHOLD", "3.0")),
            coingecko_id='hedera-hashgraph',
            liquidation_stream="wss://fstream.binance.com/ws/hbarusdt@forceOrder",
            trade_stream="wss://fstream.binance.com/ws/hbarusdt@aggTrade"
        )
        
        # Add more coins from environment variables if needed
        additional_coins = os.getenv("ADDITIONAL_COINS", "").split(",")
        for coin_symbol in additional_coins:
            coin_symbol = coin_symbol.strip().upper()
            if coin_symbol and coin_symbol not in coins:
                coins[coin_symbol] = CoinConfig(
                    coin_symbol,
                    telegram_bot_token=os.getenv(f"{coin_symbol}_TELEGRAM_BOT_TOKEN"),
                    telegram_channel_id=os.getenv(f"{coin_symbol}_TELEGRAM_CHANNEL_ID"),
                    twitter_enabled=os.getenv(f"{coin_symbol}_TWITTER_ENABLED", "false").lower() == "true"
                )
        
        # Only return coins that have valid Telegram configuration
        return {k: v for k, v in coins.items() if v.telegram_enabled}
        
    def get_twitter_enabled_coins(self) -> List[str]:
        """Get list of coins that have Twitter enabled"""
        return [symbol for symbol, config in self.coins.items() 
                if config.twitter_enabled and self.TWITTER_AVAILABLE]

class TwitterRateLimiter:
    """Manages Twitter rate limiting with priority queues"""
    def __init__(self, daily_limit: int, cooldown_minutes: int):
        self.daily_limit = daily_limit
        self.cooldown_minutes = cooldown_minutes
        self.posts_today = 0
        self.daily_reset_time: Optional[datetime] = None
        self.last_post_time: Optional[datetime] = None
        self.pending_alerts: List[Tuple[AlertPriority, str, datetime]] = []
    
    def reset_daily_counter_if_needed(self):
        """Reset daily counter at midnight"""
        now = datetime.utcnow()
        if self.daily_reset_time is None or now > self.daily_reset_time:
            self.posts_today = 0
            # Set reset time to next midnight
            tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            self.daily_reset_time = tomorrow
            logger.info(f"🐦 Daily Twitter counter reset. Posts today: {self.posts_today}/{self.daily_limit}")
    
    def can_post_now(self) -> bool:
        """Check if we can post to Twitter right now"""
        self.reset_daily_counter_if_needed()
        
        # Check daily limit
        if self.posts_today >= self.daily_limit:
            return False
        
        # Check cooldown
        if self.last_post_time:
            time_since_last = (datetime.utcnow() - self.last_post_time).total_seconds() / 60
            if time_since_last < self.cooldown_minutes:
                return False
        
        return True
    
    def should_queue_alert(self, priority: AlertPriority, text: str) -> bool:
        """Decide whether to queue an alert or post immediately"""
        if self.can_post_now():
            return False  # Post immediately
        
        # Queue high priority alerts
        if priority == AlertPriority.PRICE_ALERT:
            self.add_to_queue(priority, text)
            return True
        
        # Queue medium priority if we have capacity
        if priority == AlertPriority.LIQUIDATION and len(self.pending_alerts) < 3:
            self.add_to_queue(priority, text)
            return True
        
        # Drop low priority alerts when rate limited
        logger.info(f"🐦 Dropping low priority alert due to rate limits: {text[:50]}...")
        return True
    
    def add_to_queue(self, priority: AlertPriority, text: str):
        """Add alert to priority queue"""
        self.pending_alerts.append((priority, text, datetime.utcnow()))
        # Sort by priority (lower number = higher priority)
        self.pending_alerts.sort(key=lambda x: (x[0].value, x[2]))
        logger.info(f"🐦 Queued {priority.name} alert. Queue size: {len(self.pending_alerts)}")
    
    def get_next_queued_alert(self) -> Optional[Tuple[AlertPriority, str, datetime]]:
        """Get next alert from queue if we can post"""
        if not self.can_post_now() or not self.pending_alerts:
            return None
        
        # Remove expired alerts (older than 30 minutes)
        now = datetime.utcnow()
        self.pending_alerts = [
            (priority, text, timestamp) for priority, text, timestamp in self.pending_alerts
            if (now - timestamp).total_seconds() < 1800
        ]
        
        if self.pending_alerts:
            return self.pending_alerts.pop(0)
        return None
    
    def record_successful_post(self):
        """Record that a post was successfully made"""
        self.posts_today += 1
        self.last_post_time = datetime.utcnow()
        logger.info(f"🐦 Twitter posts today: {self.posts_today}/{self.daily_limit}")
        
        # Calculate next available post time
        next_available = self.last_post_time + timedelta(minutes=self.cooldown_minutes)
        logger.info(f"🐦 Next Twitter post available at: {next_available.strftime('%H:%M:%S')}")

class TradingBot:
    def __init__(self, config: TradingBotConfig):
        self.config = config
        # Multi-coin price tracking
        self.coin_data = {}
        for symbol in config.coins:
            self.coin_data[symbol] = {
                'last_alert_time': None,
                'last_alert_price': None,
                'price_history': deque(maxlen=1000),  # ~16 hours of data at 60s intervals
                'buy_volume_24h': 0.0,
                'sell_volume_24h': 0.0,
                'volume_start_time': datetime.utcnow()
            }
        
        # Daily indicators storage
        self.daily_indicators = {}
        for symbol in config.coins:
            self.daily_indicators[symbol] = {
                'rsi': None,
                'rsi_yesterday': None,
                'ema_20': None,
                'ema_50': None,
                'ema_200': None,
                'last_updated': None
            }
        
        # Use aiohttp session for better performance
        self.session: Optional[aiohttp.ClientSession] = None
        
        # Twitter rate limiter (shared across all coins)
        self.twitter_limiter = TwitterRateLimiter(
            config.TWITTER_DAILY_LIMIT,
            config.TWITTER_COOLDOWN_MINUTES
        ) if config.TWITTER_AVAILABLE else None
        
    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.config.REQUEST_TIMEOUT)
        )
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    async def send_alert_with_retry(self, text: str, coin_symbol: str, priority: AlertPriority) -> bool:
        """Send alert with retry logic and proper error handling"""
        coin_config = self.config.coins[coin_symbol]
        
        # Send to Telegram (each coin has its own channel)
        telegram_success = await self._send_telegram_alert(text, coin_config)
        twitter_success = True  # Default to success if Twitter is disabled
        
        # Send to Twitter only if enabled for this specific coin
        if coin_config.twitter_enabled and self.config.TWITTER_AVAILABLE and self.twitter_limiter:
            should_queue = self.twitter_limiter.should_queue_alert(priority, text)
            if not should_queue:
                # Post immediately
                twitter_success = await self._send_twitter_alert(text, coin_symbol)
            # If queued, we'll consider it successful for now
        
        # Consider successful if at least one platform succeeds
        return telegram_success or twitter_success

    async def _send_telegram_alert(self, text: str, coin_config: CoinConfig) -> bool:
        """Send alert to specific coin's Telegram channel"""
        if not coin_config.telegram_enabled:
            return True  # Skip if not configured
            
        url = f"https://api.telegram.org/bot{coin_config.telegram_bot_token}/sendMessage"
        payload = {
            "chat_id": coin_config.telegram_channel_id,
            "text": text,
            "parse_mode": "Markdown"
        }
        
        for attempt in range(self.config.MAX_RETRIES):
            try:
                async with self.session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info(f"📱 {coin_config.symbol} Telegram alert sent: {text[:50]}...")
                        return True
                    else:
                        logger.warning(f"{coin_config.symbol} Telegram API returned status {response.status}")
                        
            except Exception as e:
                logger.error(f"{coin_config.symbol} Telegram attempt {attempt + 1} failed: {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    await asyncio.sleep(self.config.RETRY_DELAY * (attempt + 1))
        
        logger.error(f"Failed to send {coin_config.symbol} Telegram alert after {self.config.MAX_RETRIES} attempts")
        return False

    async def _send_twitter_alert(self, text: str, coin_symbol: str) -> bool:
        """Send alert to Twitter using API v2 with rate limiting"""
        if not self.twitter_limiter.can_post_now():
            logger.info("🐦 Twitter rate limited, cannot post now")
            return False
        
        # Convert Markdown to plain text for Twitter
        twitter_text = self._convert_to_twitter_format(text, coin_symbol)
        logger.info(f"🐦 Attempting to post to Twitter: {twitter_text}")
        
        url = "https://api.twitter.com/2/tweets"
        payload = {"text": twitter_text}
        
        for attempt in range(self.config.MAX_RETRIES):
            try:
                headers = await self._get_twitter_headers("POST", url, json.dumps(payload))
                logger.info(f"🐦 Twitter API attempt {attempt + 1}/3")
                
                async with self.session.post(url, json=payload, headers=headers) as response:
                    response_text = await response.text()
                    logger.info(f"🐦 Twitter API response {response.status}: {response_text}")
                    
                    if response.status in [200, 201]:
                        try:
                            response_data = json.loads(response_text)
                            tweet_id = response_data.get('data', {}).get('id', 'unknown')
                            logger.info(f"🐦 ✅ Twitter post successful! Tweet ID: {tweet_id}")
                        except:
                            logger.info(f"🐦 ✅ Twitter post successful!")
                        
                        self.twitter_limiter.record_successful_post()
                        return True
                        
                    elif response.status == 429:
                        logger.warning(f"🐦 Twitter rate limited (429): {response_text}")
                        return False
                        
                    elif response.status == 403:
                        logger.error(f"🐦 Twitter API Forbidden (403): {response_text}")
                        return False
                        
                    elif response.status == 401:
                        logger.error(f"🐦 Twitter API Unauthorized (401): {response_text}")
                        return False
                        
                    else:
                        logger.warning(f"🐦 Twitter API error {response.status}: {response_text}")
                        
            except Exception as e:
                logger.error(f"🐦 Twitter attempt {attempt + 1} failed with exception: {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    await asyncio.sleep(self.config.RETRY_DELAY * (attempt + 1))
        
        logger.error(f"🐦 ❌ Failed to send Twitter alert after {self.config.MAX_RETRIES} attempts")
        return False

    def _convert_to_twitter_format(self, markdown_text: str, coin_symbol: str) -> str:
        """Convert Telegram markdown to Twitter-friendly format"""
        # Remove markdown formatting
        text = markdown_text.replace("*", "").replace("`", "").replace("_", "")
        
        # Replace emojis with hashtags for better engagement
        text = text.replace("🚀", f"🚀 #{coin_symbol}")
        text = text.replace("📉", f"📉 #{coin_symbol}")
        text = text.replace("🟢", "🟢")
        text = text.replace("🔴", "🔴")
        
        # Add relevant hashtags based on coin
        coin_config = self.config.coins.get(coin_symbol)
        coin_name = coin_config.name if coin_config else coin_symbol
        
        if "Price Alert" in text:
            text += f" #{coin_name} #Crypto #PriceAlert"
        elif "Liquidation" in text:
            text += f" #{coin_name} #Liquidation #Crypto"
        elif "Large Trade" in text:
            text += f" #{coin_name} #WhaleAlert #Crypto"
        
        # Ensure tweet length is under 280 characters
        if len(text) > 280:
            text = text[:276] + "..."
        
        return text

    async def _get_twitter_headers(self, method: str, url: str, body: str = "") -> Dict[str, str]:
        """Generate OAuth 1.0a headers for Twitter API"""
        oauth_params = {
            'oauth_consumer_key': self.config.TWITTER_API_KEY,
            'oauth_token': self.config.TWITTER_ACCESS_TOKEN,
            'oauth_signature_method': 'HMAC-SHA1',
            'oauth_timestamp': str(int(time.time())),
            'oauth_nonce': base64.b64encode(os.urandom(32)).decode('utf-8').rstrip('='),
            'oauth_version': '1.0'
        }
        
        # Create signature base string
        params_string = '&'.join([f"{k}={urllib.parse.quote(str(v), safe='')}" 
                                 for k, v in sorted(oauth_params.items())])
        
        base_string = f"{method}&{urllib.parse.quote(url, safe='')}&{urllib.parse.quote(params_string, safe='')}"
        
        # Create signing key
        signing_key = f"{urllib.parse.quote(self.config.TWITTER_API_SECRET, safe='')}&{urllib.parse.quote(self.config.TWITTER_ACCESS_TOKEN_SECRET, safe='')}"
        
        # Generate signature
        signature = base64.b64encode(
            hmac.new(signing_key.encode(), base_string.encode(), hashlib.sha1).digest()
        ).decode()
        
        oauth_params['oauth_signature'] = signature
        
        # Create authorization header
        auth_header = 'OAuth ' + ', '.join([f'{k}="{urllib.parse.quote(str(v), safe="")}"' 
                                           for k, v in sorted(oauth_params.items())])
        
        return {
            'Authorization': auth_header,
            'Content-Type': 'application/json'
        }

    async def process_twitter_queue(self):
        """Process queued Twitter alerts when rate limits allow"""
        if not self.config.TWITTER_AVAILABLE or not self.twitter_limiter:
            return
        
        while True:
            try:
                queued_alert = self.twitter_limiter.get_next_queued_alert()
                if queued_alert:
                    priority, text, timestamp = queued_alert
                    logger.info(f"🐦 Processing queued {priority.name} alert")
                    
                    # Determine coin symbol from text (simple heuristic)
                    coin_symbol = 'SOL'  # Default
                    for symbol in self.config.coins:
                        if symbol in text:
                            coin_symbol = symbol
                            break
                    
                    success = await self._send_twitter_alert(text, coin_symbol)
                    if not success:
                        # Re-queue if failed (unless it's too old)
                        if (datetime.utcnow() - timestamp).total_seconds() < 1800:
                            self.twitter_limiter.add_to_queue(priority, text)
                
                await asyncio.sleep(30)  # Check queue every 30 seconds
                
            except Exception as e:
                logger.error(f"Twitter queue processing error: {e}")
                await asyncio.sleep(60)

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

    async def handle_liquidation(self, msg: Dict[str, Any], coin_symbol: str):
        """Handle liquidation messages for specific coin"""
        try:
            order = msg.get('o', {})
            quantity = float(order.get('q', 0))
            price = float(order.get('p', 0))
            side = order.get('S', '')
            
            coin_config = self.config.coins[coin_symbol]

            # Check Telegram threshold
            if quantity >= coin_config.telegram_liquidation_threshold:
                emoji = "🔴" if side == "SELL" else "🟢"
                
                # Fix 1: Use past tense "sold/bought" instead of "sell/buy"
                side_text = "sold" if side == "SELL" else "bought"
                
                # Fix 2: Use 4 decimal places for HBAR, 2 for others
                if coin_symbol == "HBAR":
                    price_format = f"${price:.4f}"
                else:
                    price_format = f"${price:.2f}"
                
                alert = f"{emoji} {coin_symbol} Liquidation: {quantity:,.0f} contracts {side_text} at {price_format}"
                logger.info(alert)
                
                # Determine if it meets Twitter threshold
                priority = AlertPriority.LIQUIDATION
                await self.send_alert_with_retry(alert, coin_symbol, priority)
                
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error processing {coin_symbol} liquidation data: {e}")

    async def handle_trade(self, msg: Dict[str, Any], coin_symbol: str):
        """Handle trade messages for specific coin"""
        try:
            qty = float(msg['q'])
            price = float(msg['p'])
            usd_value = qty * price
            side = "BOUGHT" if msg['m'] is False else "SOLD"
            
            coin_config = self.config.coins[coin_symbol]
            coin_data = self.coin_data[coin_symbol]
            
            # Track buy/sell volumes for 24h
            now = datetime.utcnow()
            hours_since_start = (now - coin_data['volume_start_time']).total_seconds() / 3600
            
            # Reset volumes if more than 24 hours have passed
            if hours_since_start >= 24:
                coin_data['buy_volume_24h'] = 0.0
                coin_data['sell_volume_24h'] = 0.0
                coin_data['volume_start_time'] = now
            
            # Add to appropriate volume counter
            if side == "BOUGHT":
                coin_data['buy_volume_24h'] += usd_value
            else:
                coin_data['sell_volume_24h'] += usd_value

            # Check Telegram threshold
            if usd_value >= coin_config.telegram_trade_threshold:
                emoji = "🟢" if side == "BOUGHT" else "🔴"
                
                # Use 4 decimal places for HBAR, 2 for others
                if coin_symbol == "HBAR":
                    price_format = f"${price:.4f}"
                else:
                    price_format = f"${price:.2f}"
                
                alert = f"{emoji} {coin_symbol} Large Trade: {qty:,.2f} contracts {side.lower()} at {price_format} (${usd_value:,.0f})"
                logger.info(alert)
                
                # Determine priority (lowest for trades)
                priority = AlertPriority.LARGE_TRADE
                await self.send_alert_with_retry(alert, coin_symbol, priority)
                
        except (KeyError, ValueError, TypeError) as e:
            logger.error(f"Error processing {coin_symbol} trade data: {e}")

    async def listen_liquidations(self, coin_symbol: str):
        """Listen to liquidation stream for specific coin"""
        coin_config = self.config.coins[coin_symbol]
        await self.connect_websocket_with_retry(
            coin_config.liquidation_stream, 
            lambda msg: self.handle_liquidation(msg, coin_symbol),
            f"{coin_symbol} liquidations"
        )

    async def listen_trades(self, coin_symbol: str):
        """Listen to trade stream for specific coin"""
        coin_config = self.config.coins[coin_symbol]
        await self.connect_websocket_with_retry(
            coin_config.trade_stream, 
            lambda msg: self.handle_trade(msg, coin_symbol),
            f"{coin_symbol} trades"
        )

    async def get_current_prices_with_retry(self) -> Dict[str, Optional[float]]:
        """Get current prices for all coins with retry logic"""
        # Build CoinGecko API URL for all coins
        coin_ids = [config.coingecko_id for config in self.config.coins.values()]
        ids_param = ','.join(coin_ids)
        url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids_param}&vs_currencies=usd"
        
        for attempt in range(self.config.MAX_RETRIES):
            try:
                async with self.session.get(url) as response:
                    if response.status == 200:
                        data = await response.json()
                        prices = {}
                        for symbol, config in self.config.coins.items():
                            price_data = data.get(config.coingecko_id, {})
                            prices[symbol] = price_data.get('usd')
                        return prices
                    else:
                        logger.warning(f"CoinGecko API returned status {response.status}")
                        
            except Exception as e:
                logger.error(f"Price fetch attempt {attempt + 1} failed: {e}")
                if attempt < self.config.MAX_RETRIES - 1:
                    await asyncio.sleep(self.config.RETRY_DELAY)
        
        return {symbol: None for symbol in self.config.coins}

    async def monitor_price_movement(self):
        """Monitor 15-minute price changes for all coins"""
        logger.info("📊 Starting price movement monitoring for all coins...")
        
        while True:
            try:
                prices = await self.get_current_prices_with_retry()
                now = datetime.utcnow()
                
                for coin_symbol, current_price in prices.items():
                    if current_price is None:
                        logger.error(f"Failed to get {coin_symbol} price, skipping")
                        continue
                    
                    coin_data = self.coin_data[coin_symbol]
                    coin_data['price_history'].append((now, current_price))
                    
                    # Fixed: Use separate variables for price formatting to avoid f-string issues
                    if coin_symbol == "HBAR":
                        price_str = f"${current_price:.4f}"
                    else:
                        price_str = f"${current_price:.2f}"
                    
                    logger.info(f"💰 Current {coin_symbol} price: {price_str}")
                    
                    # Check for price alerts
                    trigger_alert, alert_message = self._check_price_triggers_fixed(
                        current_price, now, coin_symbol
                    )
                    
                    if trigger_alert:
                        logger.info(f"🚨 {coin_symbol} PRICE ALERT TRIGGERED: {alert_message}")
                        priority = AlertPriority.PRICE_ALERT
                        await self.send_alert_with_retry(alert_message, coin_symbol, priority)
                        coin_data['last_alert_time'] = now
                        coin_data['last_alert_price'] = current_price

                await asyncio.sleep(60)

            except Exception as e:
                logger.error(f"Price movement check error: {e}")
                await asyncio.sleep(60)

    def _check_price_triggers_fixed(self, current_price: float, now: datetime, coin_symbol: str) -> Tuple[bool, str]:
        """FIXED: Rolling 15-minute window trigger logic for specific coin"""
        coin_data = self.coin_data[coin_symbol]
        coin_config = self.config.coins[coin_symbol]
        
        # Need at least 15 minutes of data for initial comparison
        if len(coin_data['price_history']) < 15:
            logger.info(f"📈 Building {coin_symbol} price history... ({len(coin_data['price_history'])}/15 minutes)")
            return False, ""
        
        # Determine reference price and time based on recent alerts
        if coin_data['last_alert_time'] and coin_data['last_alert_price']:
            time_since_last_alert = (now - coin_data['last_alert_time']).total_seconds() / 60
            
            if time_since_last_alert < 15:
                # Within 15 minutes of LAST alert - compare against that alert price
                reference_price = coin_data['last_alert_price']
                reference_time = coin_data['last_alert_time']
                reference_type = "last alert"
                logger.info(f"📊 Using {coin_symbol} last alert price as reference ({time_since_last_alert:.1f}m ago)")
            else:
                # More than 15 minutes since LAST alert - reset and use historical price
                logger.info(f"📊 15+ minutes since {coin_symbol} last alert ({time_since_last_alert:.1f}m) - resetting to historical comparison")
                reference_price, reference_time, reference_type = self._get_historical_reference_price(now, coin_symbol)
                # Reset last alert since we're outside the 15-minute window from the LAST alert
                coin_data['last_alert_price'] = None
                coin_data['last_alert_time'] = None
        else:
            # No recent alert - use historical price
            reference_price, reference_time, reference_type = self._get_historical_reference_price(now, coin_symbol)
        
        if reference_price is None:
            return False, ""
        
        # Calculate percentage change
        pct_change = ((current_price - reference_price) / reference_price) * 100
        minutes_ago = (now - reference_time).total_seconds() / 60
        
        # Fixed: Use separate variables for price formatting in logs
        if coin_symbol == "HBAR":
            ref_price_str = f"${reference_price:.4f}"
            curr_price_str = f"${current_price:.4f}"
        else:
            ref_price_str = f"${reference_price:.2f}"
            curr_price_str = f"${current_price:.2f}"
        
        # Log the calculation for debugging
        logger.info(f"📊 {coin_symbol} price check: {ref_price_str} ({reference_type}, {minutes_ago:.1f}m ago) → {curr_price_str} = {pct_change:+.2f}%")
        
        # Check against Telegram threshold first
        if abs(pct_change) >= coin_config.telegram_price_threshold:
            direction = "🚀" if pct_change > 0 else "📉"
            alert_type = "from alert" if reference_type == "last alert" else "15min"
            alert = f"{direction} *{coin_symbol} Price Alert* ({alert_type})\n"
            alert += f"`{pct_change:+.2f}%` change in {minutes_ago:.0f} minutes\n"
            alert += f"{ref_price_str} → {curr_price_str}"
            return True, alert
            
        return False, ""
        
    def _get_historical_reference_price(self, now: datetime, coin_symbol: str) -> Tuple[Optional[float], Optional[datetime], str]:
        """Get reference price from 15 minutes ago in price history for specific coin"""
        coin_data = self.coin_data[coin_symbol]
        
        # Find price approximately 15 minutes ago (within 2-minute window)
        cutoff_time = now - timedelta(minutes=15)
        tolerance = timedelta(minutes=2)
        
        # Get prices within the tolerance window around 15 minutes ago
        reference_prices = [
            (timestamp, price) for timestamp, price in coin_data['price_history']
            if abs((timestamp - cutoff_time).total_seconds()) <= tolerance.total_seconds()
        ]
        
        if reference_prices:
            # Use the closest price to 15 minutes ago
            reference_time, reference_price = min(reference_prices, key=lambda x: abs((x[0] - cutoff_time).total_seconds()))
            return reference_price, reference_time, "15min history"
        else:
            # Fallback: get the oldest price we have that's at least 10 minutes old
            min_age = now - timedelta(minutes=10)
            old_prices = [(t, p) for t, p in coin_data['price_history'] if t <= min_age]
            if old_prices:
                reference_time, reference_price = old_prices[0]  # oldest price
                return reference_price, reference_time, "oldest available"
            else:
                return None, None, "none"

    async def fetch_twelve_data_indicators(self, coin_symbol: str):
        """Fetch all indicators for a coin from Twelve Data"""
        
        if not self.config.TWELVE_DATA_API_KEY:
            logger.error("TWELVE_DATA_API_KEY not set")
            return False
        
        try:
            # Map crypto symbols (Twelve Data uses BTC/USD format)
            symbol_map = {
                'SOL': 'SOL/USD',
                'HBAR': 'HBAR/USD',
                'BTC': 'BTC/USD',
                'ETH': 'ETH/USD'
            }
            
            twelve_symbol = symbol_map.get(coin_symbol, f'{coin_symbol}/USD')
            
            # Store all results
            indicators = {}
            
            # 1. Fetch RSI (current and yesterday)
            rsi_url = f"{self.config.TWELVE_DATA_BASE_URL}/rsi"
            rsi_params = {
                'symbol': twelve_symbol,
                'interval': '1day',
                'time_period': 14,
                'outputsize': 2,  # Get today and yesterday
                'apikey': self.config.TWELVE_DATA_API_KEY
            }
            
            async with self.session.get(rsi_url, params=rsi_params) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'values' in data and len(data['values']) >= 2:
                        indicators['rsi'] = float(data['values'][0]['rsi'])
                        indicators['rsi_yesterday'] = float(data['values'][1]['rsi'])
                        logger.info(f"✅ {coin_symbol} RSI: {indicators['rsi']:.2f}")
                    else:
                        logger.error(f"Invalid RSI data for {coin_symbol}")
                        
                else:
                    logger.error(f"RSI fetch failed: {response.status}")
            
            # Wait to respect rate limit (12 requests/minute = 5 seconds between)
            await asyncio.sleep(5)
            
            # 2. Fetch EMAs (20, 50, 200)
            for period in [20, 50, 200]:
                ema_url = f"{self.config.TWELVE_DATA_BASE_URL}/ema"
                ema_params = {
                    'symbol': twelve_symbol,
                    'interval': '1day',
                    'time_period': period,
                    'outputsize': 1,
                    'apikey': self.config.TWELVE_DATA_API_KEY
                }
                
                async with self.session.get(ema_url, params=ema_params) as response:
                    if response.status == 200:
                        data = await response.json()
                        if 'values' in data and len(data['values']) > 0:
                            indicators[f'ema_{period}'] = float(data['values'][0]['ema'])
                            logger.info(f"✅ {coin_symbol} EMA{period}: ${indicators[f'ema_{period}']:.2f}")
                    else:
                        logger.error(f"EMA{period} fetch failed: {response.status}")
                
                await asyncio.sleep(5)  # Rate limit
            
            # 3. Update stored indicators
            self.daily_indicators[coin_symbol].update({
                'rsi': indicators.get('rsi'),
                'rsi_yesterday': indicators.get('rsi_yesterday'),
                'ema_20': indicators.get('ema_20'),
                'ema_50': indicators.get('ema_50'),
                'ema_200': indicators.get('ema_200'),
                'last_updated': datetime.utcnow()
            })
            
            return True
            
        except Exception as e:
            logger.error(f"Error fetching indicators for {coin_symbol}: {e}")
            return False

    async def fetch_all_daily_indicators(self):
        """Fetch indicators for all coins once daily"""
        logger.info("📊 Fetching daily indicators for all coins...")
        
        for coin_symbol in self.config.coins:
            success = await self.fetch_twelve_data_indicators(coin_symbol)
            if success:
                logger.info(f"✅ {coin_symbol} indicators updated")
            else:
                logger.error(f"❌ {coin_symbol} indicators failed")
            
            # Wait between coins to avoid rate limits
            if coin_symbol != list(self.config.coins.keys())[-1]:  # Not last coin
                await asyncio.sleep(5)
        
        logger.info("📊 Daily indicators fetch complete")

    def calculate_buy_sell_ratio(self, coin_symbol: str) -> Optional[float]:
        """Calculate buy/sell ratio if we have 24 hours of data"""
        coin_data = self.coin_data[coin_symbol]
        
        # Check if we have exactly 24 hours of data
        hours_since_start = (datetime.utcnow() - coin_data['volume_start_time']).total_seconds() / 3600
        
        if hours_since_start < 24:
            return None  # Not enough data - need full 24 hours
        
        buy_vol = coin_data['buy_volume_24h']
        sell_vol = coin_data['sell_volume_24h']
        
        # Need minimum volume to calculate meaningful ratio
        total_vol = buy_vol + sell_vol
        if total_vol < 100000:  # Less than $100k total volume
            return None
        
        if sell_vol == 0:
            return float('inf')  # All buys
        
        return buy_vol / sell_vol

    async def generate_daily_summary(self, coin_symbol: str) -> str:
        """Generate daily summary using stored indicators"""
        
        # Get current price and daily OHLCV from Binance
        ticker_url = f"https://api.binance.com/api/v3/ticker/24hr"
        params = {'symbol': f'{coin_symbol}USDT'}
        
        try:
            async with self.session.get(ticker_url, params=params) as response:
                if response.status == 200:
                    ticker = await response.json()
                    
                    current_price = float(ticker['lastPrice'])
                    high_price = float(ticker['highPrice'])
                    low_price = float(ticker['lowPrice'])
                    volume = float(ticker['quoteVolume']) / 1e6  # Convert to millions
                    
            # Get funding rate from Binance Futures
            funding_url = f"https://fapi.binance.com/fapi/v1/fundingRate"
            params = {'symbol': f'{coin_symbol}USDT', 'limit': 1}
            
            funding_rate = 0.0
            async with self.session.get(funding_url, params=params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data:
                        funding_rate = float(data[0]['fundingRate']) * 100  # Convert to percentage
            
            # Get stored indicators
            indicators = self.daily_indicators[coin_symbol]
            
            # Format RSI with direction
            rsi_text = "N/A"
            if indicators['rsi'] and indicators['rsi_yesterday']:
                rsi_current = indicators['rsi']
                rsi_yesterday = indicators['rsi_yesterday']
                direction = "↑" if rsi_current > rsi_yesterday else "↓" if rsi_current < rsi_yesterday else "→"
                rsi_text = f"{rsi_current:.1f} ({direction}{rsi_yesterday:.1f} yday)"
            
            # Format prices based on coin
            if coin_symbol == "HBAR":
                price_fmt = f"${current_price:.4f} | H: ${high_price:.4f} | L: ${low_price:.4f}"
                ema_fmt = f"20: ${indicators['ema_20']:.4f} | 50: ${indicators['ema_50']:.4f} | 200: ${indicators['ema_200']:.4f}"
            else:
                price_fmt = f"${current_price:.2f} | H: ${high_price:.2f} | L: ${low_price:.2f}"
                ema_fmt = f"20: ${indicators['ema_20']:.2f} | 50: ${indicators['ema_50']:.2f} | 200: ${indicators['ema_200']:.2f}"
            
            # Build summary
            summary = f"""📊 {coin_symbol} Daily Summary | {datetime.utcnow().strftime('%b %d')}
Price: {price_fmt}
Volume: ${volume:.1f}M"""
            
            # Add buy/sell ratio only if available
            buy_sell_ratio = self.calculate_buy_sell_ratio(coin_symbol)
            if buy_sell_ratio is not None:
                summary += f" | Buy/Sell: {buy_sell_ratio:.1f}"
            
            summary += f"""
RSI: {rsi_text} | Funding: {funding_rate:.3f}%
EMA: {ema_fmt}"""
            
            return summary
            
        except Exception as e:
            logger.error(f"Error generating summary for {coin_symbol}: {e}")
            return None

    async def daily_summary_scheduler(self):
        """Fetch indicators and send summary once daily at 8:30 AM UTC"""
        while True:
            try:
                now = datetime.utcnow()
                
                # Fetch indicators at 08:15 UTC (15 minutes before summary)
                indicators_time = now.replace(hour=8, minute=15, second=0, microsecond=0)
                if now > indicators_time:
                    indicators_time += timedelta(days=1)
                
                seconds_until_indicators = (indicators_time - now).total_seconds()
                
                if seconds_until_indicators > 0:
                    logger.info(f"⏰ Next indicators fetch in {seconds_until_indicators/3600:.1f} hours")
                    await asyncio.sleep(seconds_until_indicators)
                    
                    # Fetch all indicators
                    await self.fetch_all_daily_indicators()
                
                # Wait until 08:30 UTC for summary
                await asyncio.sleep(900)  # 15 minutes
                
                # Send summaries
                for coin_symbol in self.config.coins:
                    summary = await self.generate_daily_summary(coin_symbol)
                    if summary:
                        coin_config = self.config.coins[coin_symbol]
                        await self._send_telegram_alert(summary, coin_config)
                        logger.info(f"✅ Sent {coin_symbol} daily summary")
                
                # Wait before next cycle
                await asyncio.sleep(3600)  # 1 hour
                
            except Exception as e:
                logger.error(f"Daily scheduler error: {e}")
                await asyncio.sleep(300)

    async def heartbeat(self):
        """Heartbeat to show bot is alive"""
        while True:
            logger.info("💓 Trading bot is alive and running...")
            
            # Show Twitter rate limiter status
            if self.twitter_limiter:
                self.twitter_limiter.reset_daily_counter_if_needed()
                remaining = self.twitter_limiter.daily_limit - self.twitter_limiter.posts_today
                queue_size = len(self.twitter_limiter.pending_alerts)
                logger.info(f"🐦 Twitter: {remaining}/{self.twitter_limiter.daily_limit} posts remaining, {queue_size} queued")
                
                if self.twitter_limiter.last_post_time:
                    time_since_last = (datetime.utcnow() - self.twitter_limiter.last_post_time).total_seconds() / 60
                    cooldown_remaining = max(0, self.twitter_limiter.cooldown_minutes - time_since_last)
                    if cooldown_remaining > 0:
                        logger.info(f"🐦 Cooldown: {cooldown_remaining:.1f} minutes remaining")
            
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
        logger.info("💡 Starting multi-coin trading bot...")
        
        # Log Twitter configuration
        logger.info(f"Twitter available: {config.TWITTER_AVAILABLE}")
        if config.TWITTER_AVAILABLE:
            logger.info(f"Twitter API Key: {config.TWITTER_API_KEY[:10]}...")
            logger.info(f"Twitter daily limit: {config.TWITTER_DAILY_LIMIT} posts")
            logger.info(f"Twitter cooldown: {config.TWITTER_COOLDOWN_MINUTES} minutes")
            twitter_coins = config.get_twitter_enabled_coins()
            logger.info(f"Twitter enabled for: {', '.join(twitter_coins) if twitter_coins else 'None'}")
        
        # Log Twelve Data configuration
        if config.TWELVE_DATA_API_KEY:
            logger.info(f"Twelve Data API Key: {config.TWELVE_DATA_API_KEY[:10]}...")
        else:
            logger.warning("Twelve Data API Key not set - daily summaries will have limited data")
        
        # Log coin configurations
        logger.info(f"Monitoring {len(config.coins)} coins: {', '.join(config.coins.keys())}")
        for symbol, coin_config in config.coins.items():
            logger.info(f"📊 {symbol} ({coin_config.name}):")
            logger.info(f"  📱 Telegram: {'✅' if coin_config.telegram_enabled else '❌'} "
                       f"Bot: ...{coin_config.telegram_bot_token[-10:] if coin_config.telegram_bot_token else 'None'}")
            logger.info(f"  🐦 Twitter: {'✅' if coin_config.twitter_enabled else '❌'}")
            logger.info(f"  📱 Telegram thresholds: Price {coin_config.telegram_price_threshold}%, "
                       f"Liquidation ${coin_config.telegram_liquidation_threshold:,.0f}, "
                       f"Trade ${coin_config.telegram_trade_threshold:,.0f}")
            if coin_config.twitter_enabled:
                logger.info(f"  🐦 Twitter thresholds: Price {coin_config.twitter_price_threshold}%, "
                           f"Liquidation ${coin_config.twitter_liquidation_threshold:,.0f}, "
                           f"Trade ${coin_config.twitter_trade_threshold:,.0f}")
        
        async with TradingBot(config) as bot:
            # Create all tasks for all coins
            tasks = []
            
            # Add websocket listeners for each coin
            for coin_symbol in config.coins:
                tasks.append(safe_task_runner(bot.listen_liquidations(coin_symbol), f"{coin_symbol} liquidations"))
                tasks.append(safe_task_runner(bot.listen_trades(coin_symbol), f"{coin_symbol} trades"))
            
            # Add shared tasks
            tasks.append(safe_task_runner(bot.monitor_price_movement(), "price monitor"))
            tasks.append(safe_task_runner(bot.heartbeat(), "heartbeat"))
            tasks.append(safe_task_runner(bot.daily_summary_scheduler(), "daily summary"))
            
            # Add Twitter queue processor if enabled
            if config.TWITTER_AVAILABLE:
                tasks.append(safe_task_runner(bot.process_twitter_queue(), "twitter queue"))
            
            logger.info(f"🚀 Starting {len(tasks)} tasks...")
            
            # Run all tasks concurrently
            await asyncio.gather(*tasks)
            
    except KeyboardInterrupt:
        logger.info("🛑 Trading bot stopped manually.")
    except Exception as e:
        logger.error(f"🧨 Top-level crash: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())
