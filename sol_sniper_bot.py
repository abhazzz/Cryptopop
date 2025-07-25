import os
import json
import asyncio
import requests
import websockets
from dotenv import load_dotenv
from collections import deque
from datetime import datetime, timedelta

# --- Load env vars from .env (works locally) ---
load_dotenv()

import time

async def heartbeat():
    while True:
        print("💓 Sniper bot is alive and running...")
        await asyncio.sleep(60)  # every 1 min

# --- CONFIG ---
BOT_TOKEN = "8068411290:AAGBIq8MF2V__uKIVh8bYcB6uOX6dJNwPGg"
CHANNEL_ID = "@Cryptopopprices"
LIQUIDATION_STREAM = "wss://fstream.binance.com/ws/solusdt@forceOrder"
TRADE_STREAM = "wss://fstream.binance.com/ws/solusdt@aggTrade"

LIQUIDATION_THRESHOLD = 1_000_000
TRADE_USD_THRESHOLD = 1_000_000
PRICE_ALERT_THRESHOLD = 2.0  # percent

# --- STATE ---
last_alert_time = None
last_alert_price = None

# --- SEND ALERT TO TELEGRAM ---
def send_alert(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload)
    except Exception as e:
        print(f"Error sending message: {e}")

# --- LISTEN TO LIQUIDATIONS ---
async def listen_liquidations():
    async with websockets.connect(LIQUIDATION_STREAM) as ws:
        print("🔴 Listening to SOLUSD liquidations...")
        while True:
            try:
                msg = json.loads(await ws.recv())
                order = msg.get('o', {})
                quantity = float(order.get('q', 0))
                price = float(order.get('p', 0))
                side = order.get('S', '')

                if quantity >= LIQUIDATION_THRESHOLD:
                    emoji = "🔴" if side == "SELL" else "🟢"
                    alert = f"{emoji} Liquidation: {quantity:,.0f} contracts {side.lower()} at ${price:,.2f}"
                    print(alert)
                    send_alert(alert)

            except Exception as e:
                print(f"Liquidation error: {e}")
                await asyncio.sleep(5)

# --- LISTEN TO BIG TRADES ---
async def listen_trades():
    async with websockets.connect(TRADE_STREAM) as ws:
        print("🟢 Listening to SOLUSD trades...")
        while True:
            try:
                msg = json.loads(await ws.recv())
                qty = float(msg['q'])
                price = float(msg['p'])
                usd_value = qty * price
                side = "BOUGHT" if msg['m'] is False else "SOLD"

                if usd_value >= TRADE_USD_THRESHOLD:
                    emoji = "🟢" if side == "BOUGHT" else "🔴"
                    alert = f"{emoji} {qty:,.2f} contracts {side.lower()} at ${price:,.2f}"
                    print(alert)
                    send_alert(alert)

            except Exception as e:
                print(f"Trade error: {e}")
                await asyncio.sleep(5)

# --- MONITOR 15-MIN PRICE CHANGE ---
async def monitor_price_movement():
    global last_alert_time, last_alert_price
    print("📊 Monitoring 15-minute price change...")
    history = deque(maxlen=200)

    while True:
        try:
            url = "https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd"
            response = requests.get(url)
            current_price = response.json().get("solana", {}).get("usd", 0)

            now = datetime.utcnow()
            history.append((now, current_price))

            # Get price 15 minutes ago
            cutoff = now - timedelta(minutes=15)
            past_prices = [p for t, p in history if t <= cutoff]

            if past_prices:
                price_15min_ago = past_prices[-1]
                pct_change_15min = ((current_price - price_15min_ago) / price_15min_ago) * 100
                trigger_from_15min = abs(pct_change_15min) >= PRICE_ALERT_THRESHOLD
            else:
                trigger_from_15min = False

            trigger_from_last_alert = False
            if last_alert_time and last_alert_price and (now - last_alert_time < timedelta(minutes=15)):
                pct_change_since_alert = ((current_price - last_alert_price) / last_alert_price) * 100
                trigger_from_last_alert = abs(pct_change_since_alert) >= PRICE_ALERT_THRESHOLD
            else:
                last_alert_price = price_15min_ago if past_prices else current_price

            if trigger_from_15min or trigger_from_last_alert:
                reference = last_alert_price if trigger_from_last_alert else price_15min_ago
                arrow = "🚀" if current_price > reference else "📉"
                pct_change = ((current_price - reference) / reference) * 100
                alert = f"{arrow} SOL price moved {pct_change:.2f}%\n${reference:.2f} → ${current_price:.2f}"
                print(alert)
                send_alert(alert)
                last_alert_time = now
                last_alert_price = current_price

            await asyncio.sleep(60)

        except Exception as e:
            print(f"Price movement check error: {e}")
            await asyncio.sleep(60)

# --- MAIN ENTRY ---
async def main():
    try:
        await asyncio.gather(
            listen_liquidations(),
            listen_trades(),
            monitor_price_movement(),
            heartbeat()
        )
    except asyncio.CancelledError:
        print("🔌 Shutdown signal received. Cleaning up tasks...")
        # Do any cleanup here if needed
    except Exception as e:
        print(f"🔥 Unhandled error in main(): {e}")

if __name__ == "__main__":
    try:
        print("💡 Starting sniper bot...")
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Sniper bot stopped manually.")


