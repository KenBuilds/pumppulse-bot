# main.py
import os
import asyncio
import json
from pathlib import Path
from datetime import datetime
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from fastapi import FastAPI
import uvicorn

# ---------- CONFIG ----------
BOT_TOKEN = os.getenv("BOT_TOKEN")  # set this in Render/Secrets, NOT in code
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "25"))
CHAIN = os.getenv("CHAIN", "solana")
DEXSCREENER_URL = f"https://api.dexscreener.com/latest/dex/pairs/{CHAIN}"
SEEN_FILE = Path("seen.json")
ADMIN_CHAT = os.getenv("ADMIN_CHAT")
# ----------------------------

if not bot_token:
    raise RuntimeError("BOT_TOKEN")

if not SEEN_FILE.exists():
    SEEN_FILE.write_text("{}")

def load_seen():
    try:
        return json.loads(SEEN_FILE.read_text())
    except Exception:
        return {}

def save_seen(data):
    SEEN_FILE.write_text(json.dumps(data, indent=2))

seen = load_seen()

def fetch_pairs(limit=50):
    try:
        resp = requests.get(DEXSCREENER_URL, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        pairs = data.get("pairs", [])
        return pairs[:limit]
    except Exception as e:
        print("Dexscreener fetch error:", e)
        return []

def format_pair_message(pair):
    base = pair.get("baseToken", {}) or {}
    name = base.get("name") or base.get("symbol") or "Unknown"
    symbol = base.get("symbol", "")
    address = base.get("address", "")
    pair_url = pair.get("url", "")
    price = pair.get("priceUsd", "N/A")
    fdv = pair.get("fdv", "N/A")
    liquidity = pair.get("liquidity", "N/A")
    created = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    msg = (
        f"🚨 *New Token Detected* 🚨\n"
        f"*{name}* ({symbol})\n"
        f"Address: `{address}`\n"
        f"Price USD: {price}\n"
        f"FDV: {fdv}\n"
        f"Liquidity: {liquidity}\n\n"
        f"[Chart & Pair Info]({pair_url})\n\n"
        f"_Detected: {created}_"
    )
    return msg

def get_subscribers():
    meta = seen.get("_meta", {})
    return set(meta.get("subscribers", []))

def add_subscriber(chat_id):
    meta = seen.setdefault("_meta", {})
    subs = set(meta.get("subscribers", []))
    subs.add(str(chat_id))
    meta["subscribers"] = list(subs)
    save_seen(seen)

def remove_subscriber(chat_id):
    meta = seen.setdefault("_meta", {})
    subs = set(meta.get("subscribers", []))
    subs.discard(str(chat_id))
    meta["subscribers"] = list(subs)
    save_seen(seen)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 PumpPulse (Basic) online!\n\n"
        "Commands:\n"
        "/newcoins - fetch latest new coins (manual)\n"
        "/status - show bot status\n"
        "/subscribe - subscribe this chat to live alerts\n"
        "/unsubscribe - stop live alerts"
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"PumpPulse status:\nSeen tokens: {len([k for k in seen.keys() if not k.startswith('_')])}\nPolling every {POLL_INTERVAL}s\nChain: {CHAIN}"
    )

async def newcoins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔎 Fetching latest pairs...")
    pairs = fetch_pairs(limit=10)
    if not pairs:
        await update.message.reply_text("No data right now.")
        return
    for p in pairs:
        msg = format_pair_message(p)
        try:
            await update.message.reply_markdown(msg, disable_web_page_preview=False)
        except Exception:
            await update.message.reply_text("Error sending formatted message. Try /status.")

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    add_subscriber(chat_id)
    await update.message.reply_text("✅ Subscribed to PumpPulse live alerts for this chat.")

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    remove_subscriber(chat_id)
    await update.message.reply_text("❌ Unsubscribed from PumpPulse alerts for this chat.")

async def poller(application):
    print("Poller started.")
    while True:
        try:
            pairs = fetch_pairs(limit=50)
            new_found = []
            for p in pairs:
                base = p.get("baseToken", {}) or {}
                addr = base.get("address")
                if not addr:
                    continue
                if addr not in seen:
                    seen[addr] = {"detected_at": datetime.utcnow().isoformat(), "pair": p}
                    new_found.append(p)
            if new_found:
                save_seen(seen)
                subs = get_subscribers()
                if not subs:
                    if ADMIN_CHAT:
                        try:
                            await application.bot.send_message(int(ADMIN_CHAT), f"Found {len(new_found)} new tokens but no subscribers.")
                        except Exception as e:
                            print("Admin notify failed:", e)
                else:
                    for p in new_found:
                        msg = format_pair_message(p)
                        for chat_id in list(subs):
                            try:
                                await application.bot.send_message(int(chat_id), msg, parse_mode="Markdown", disable_web_page_preview=False)
                            except Exception as e:
                                print("Error sending to", chat_id, e)
        except Exception as polling_error:
            print("Poller error:", polling_error)
        await asyncio.sleep(POLL_INTERVAL)

app_fastapi = FastAPI()

@app_fastapi.get("/")
def root():
    return {"status": "PumpPulse Basic Running", "seen": len([k for k in seen.keys() if not k.startswith('_')])}

def run_bot():
    application = ApplicationBuilder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("newcoins", newcoins_cmd))
    application.add_handler(CommandHandler("subscribe", subscribe))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe))

    async def on_startup(app):
        app.create_task(poller(app))

    application.post_init.append(on_startup)

    print("Starting PumpPulse bot...")
    application.run_polling()

if __name__ == "__main__":
    import threading
    def start_uvicorn():
        uvicorn.run(app_fastapi, host="0.0.0.0", port=8000, log_level="info")
    web_thread = threading.Thread(target=start_uvicorn, daemon=True)
    web_thread.start()
    run_bot()
