import asyncio
from logger import logger
import os
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from position_manager import get_open_positions, close_all_positions
from decimal import Decimal
from failover_manager import failover_positions

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

from aiogram.client.default import DefaultBotProperties

bot = Bot(
    token=TELEGRAM_BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

# Event for graceful shutdown
stop_event = asyncio.Event()

# Notifications 
async def send_message(text: str):
    try:
        await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
    except Exception as e:
        logger.error(f"[TELEGRAM] Failed to send message: {e}")

# Commands
@dp.message(lambda message: message.text and message.text.startswith("/stop"))
async def cmd_stop(message: types.Message):
    if message.chat.id != TELEGRAM_CHAT_ID:
        return  # ignore non-authorized chats
    await message.reply("⛔ /stop command received. Closing all positions and shutting down...")
    await send_message("🛑 /stop command activated. Initiating bot shutdown.")
    stop_event.set()  # trigger shutdown

@dp.message(lambda message: message.text and message.text.startswith("/status"))
async def cmd_status(message: types.Message):
    if message.chat.id != TELEGRAM_CHAT_ID:
        return
    await message.reply("✅ Bot is running.")

@dp.message(lambda message: message.text and message.text.startswith("/positions"))
async def cmd_positions(message: types.Message):
    if message.chat.id != TELEGRAM_CHAT_ID:
        return

    positions = get_open_positions()
    failovers = list(failover_positions.values())

    if not positions and not failovers:
        await message.reply("No open positions.")
    else:
        text = "📊 <b>Open Positions:</b>\n\n"

        # Emojis for regular positions
        for p in positions:
            net_profit = p.get('net_profit', Decimal("0"))
            emoji = "💰" if net_profit >= 0 else "💩"
            text += (
                f"<b>{p['symbol']}</b> | {p['long_exchange']}/{p['short_exchange']}\n"
                f"PnL: <code>{net_profit:.4f} USD</code> {emoji}\n\n"
            )

        # Failover positions
        for f in failovers:
            if f.get("status") != "closed":
                pnl = f.get("current_pnl", Decimal("0"))
                emoji = "🟢" if pnl >= 0 else "🔻"
                direction = "🟩 Long" if f["direction"] == "long" else "🟥 Short"

                text += (
                    f"<b>{f['symbol']}</b> | {f['exchange']} ({direction}, <b>FAILOVER</b>)\n"
                    f"PnL: <code>{pnl:.4f} USD</code> {emoji}\n\n"
                )

        await message.reply(text)

async def telegram_bot_runner():
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"[TELEGRAM] Polling error: {e}")

# Expose stop_event so main.py can await it
get_stop_event = lambda: stop_event
