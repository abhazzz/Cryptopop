# Multi-Coin Trading Bot v2.0

A sophisticated cryptocurrency monitoring bot that tracks liquidations, large trades, and price movements across multiple coins. Supports dual-platform posting to Telegram and Twitter with intelligent rate limiting.

## 🚀 Features

### Multi-Coin Support
- **Scalable architecture** for monitoring multiple cryptocurrencies
- **Per-coin Telegram channels** with dedicated bot tokens
- **Individual WebSocket streams** for real-time data
- **Separate threshold configurations** per coin
- Built-in support for **SOL (Solana)** and **HBAR (Hedera)**

### Dual-Platform Integration
- **Telegram**: Instant alerts to dedicated channels per coin
- **Twitter**: Smart posting with rate limiting and priority queues
- **Platform-specific formatting** optimized for each social media platform
- **Intelligent hashtag generation** for maximum engagement

### Advanced Alert System
- **Price movement alerts** with rolling 15-minute windows
- **Large liquidation tracking** with customizable thresholds
- **Whale trade monitoring** for significant market movements
- **Priority-based queuing** for Twitter rate limiting

### Smart Rate Limiting
- **15 tweets per day** with 90-minute cooldowns (configurable)
- **Priority queuing**: Price Alerts → Liquidations → Large Trades
- **Automatic queue management** with stale alert cleanup
- **Graceful degradation** when limits are reached

## 🛠️ Setup

### Prerequisites
- Python 3.8+
- Telegram Bot API tokens (one per coin)
- Twitter API v2 credentials (optional)
- Fly.io account (for deployment)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/trading-bot.git
   cd trading-bot
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your configuration
   ```

### Configuration

#### Required Secrets (Fly.io)
```bash
# SOL Configuration
fly secrets set SOL_TELEGRAM_BOT_TOKEN="your_sol_bot_token"
fly secrets set SOL_TELEGRAM_CHANNEL_ID="@YourSOLChannel"

# HBAR Configuration  
fly secrets set HBAR_TELEGRAM_BOT_TOKEN="your_hbar_bot_token"
fly secrets set HBAR_TELEGRAM_CHANNEL_ID="@YourHBARChannel"

# Twitter API (if using Twitter)
fly secrets set TWITTER_API_KEY="your_api_key"
fly secrets set TWITTER_API_SECRET="your_api_secret"
fly secrets set TWITTER_ACCESS_TOKEN="your_access_token"
fly secrets set TWITTER_ACCESS_TOKEN_SECRET="your_access_token_secret"
```

#### Environment Variables (fly.toml)
```toml
[env]
  SOL_TWITTER_ENABLED = "true"
  HBAR_TWITTER_ENABLED = "false"
  TWITTER_DAILY_LIMIT = "15"
  TWITTER_COOLDOWN_MINUTES = "90"
```

## 📊 Default Thresholds

### SOL (Solana)
| Alert Type | Telegram | Twitter |
|------------|----------|---------|
| Price Change | 2.0% | 3.0% |
| Liquidations | $1M | $5M |
| Large Trades | $1M | $3M |

### HBAR (Hedera)
| Alert Type | Telegram | Twitter |
|------------|----------|---------|
| Price Change | 2.0% | 3.0% |
| Liquidations | $500K | $2M |
| Large Trades | $500K | $1.5M |

*All thresholds are customizable via environment variables*

## 🚀 Deployment

### Local Development
```bash
python main.py
```

### Fly.io Deployment
```bash
# Set your secrets first (one-time setup)
fly secrets set SOL_TELEGRAM_BOT_TOKEN="your_sol_bot_token"
fly secrets set SOL_TELEGRAM_CHANNEL_ID="@YourSOLChannel"
fly secrets set HBAR_TELEGRAM_BOT_TOKEN="your_hbar_bot_token"
fly secrets set HBAR_TELEGRAM_CHANNEL_ID="@YourHBARChannel"
fly secrets set TWITTER_API_KEY="your_api_key"
fly secrets set TWITTER_API_SECRET="your_api_secret"
fly secrets set TWITTER_ACCESS_TOKEN="your_access_token"
fly secrets set TWITTER_ACCESS_TOKEN_SECRET="your_access_token_secret"

# Deploy the bot
git add .
git commit -m "force redeploy with Dockerfile"
git push
fly deploy
```

### Local Development
```bash
docker build -t trading-bot .
docker run --env-file .env trading-bot
```

## 📱 Alert Examples

### Telegram (Markdown formatting)
```
🚀 *SOL Price Alert* (15min)
`+3.25%` change in 15 minutes
$180.00 → $185.85
```

### Twitter (Plain text with hashtags)
```
🚀 #SOL SOL Price Alert (15min)
+3.25% change in 15 minutes
$180.00 → $185.85 #Solana #Crypto #PriceAlert
```

## 🔧 Adding New Coins

1. **Add environment variables:**
   ```env
   NEWCOIN_TELEGRAM_BOT_TOKEN=your_token
   NEWCOIN_TELEGRAM_CHANNEL_ID=@channel
   NEWCOIN_TWITTER_ENABLED=false
   ```

2. **Set as secrets in Fly.io:**
   ```bash
   fly secrets set NEWCOIN_TELEGRAM_BOT_TOKEN="your_token"
   fly secrets set NEWCOIN_TELEGRAM_CHANNEL_ID="@channel"
   ```

3. **Add to ADDITIONAL_COINS list:**
   ```env
   ADDITIONAL_COINS=NEWCOIN
   ```

## 🐛 Troubleshooting

### Common Issues

**No alerts being sent:**
- Check Telegram bot tokens and channel permissions
- Verify WebSocket connections in logs
- Ensure thresholds are appropriate for current market conditions

**Twitter posts not appearing:**
- Check Twitter API rate limits (Free plan: 17 tweets/day)
- Verify Twitter app permissions (Read + Write)
- Review Twitter API response codes in logs

**Price alerts not triggering:**
- Wait for 15+ minutes of price history to build
- Check price threshold settings
- Review price calculation logs

### Debug Logging
The bot provides extensive logging for troubleshooting:
```
💰 Current SOL price: $180.55
📊 SOL price check: $175.20 (15min history, 15.0m ago) → $180.55 = +3.05%
🚨 SOL PRICE ALERT TRIGGERED: 🚀 *SOL Price Alert* (15min)
📱 SOL Telegram alert sent: 🚀 *SOL Price Alert* (15min)...
🐦 Twitter posts today: 5/15
```

## 📜 License

[Add your license here]

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## ⚠️ Disclaimer

This bot is for informational purposes only. Cryptocurrency trading involves significant risk. Always do your own research before making investment decisions.

## 📞 Support

- Create an issue for bugs or feature requests
- Join our community: [Your community link]
- Documentation: [Your docs link]

---

**Built with ❤️ for the crypto community**
