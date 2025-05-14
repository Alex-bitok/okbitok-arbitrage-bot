import asyncio
from logger import logger
from decimal import Decimal
from config_manager import get_config_value
from order_manager import sign_bybit_request, sign_kucoin_request
from telegram_bot import send_message
import aiohttp

BALANCE_MARGIN_PCT = Decimal(get_config_value("BALANCE_MARGIN_PCT", "20"))
BALANCE_CHECK_INTERVAL_SEC = int(get_config_value("BALANCE_CHECK_INTERVAL_SEC", "30"))
POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD", "100"))

# Trading block status by exchange
_trading_blocked = {
    "Bybit": False,
    "KuCoin": False
}

# Cache of last successful balances
_last_balance = {
    "Bybit": Decimal("0"),
    "KuCoin": Decimal("0")
}

async def fetch_bybit_balance() -> Decimal:
    url = "https://api.bybit.com/v5/account/wallet-balance?accountType=UNIFIED"
    query = "accountType=UNIFIED"
    headers = sign_bybit_request(
        get_config_value("BYBIT_KEY"),
        get_config_value("BYBIT_SECRET"),
        method="GET",
        path_or_body=query
    )
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            usdt = Decimal("0")
            # print(f"[WATCHDOG DEBUG] Bybit balance raw response: {data}")  # temporary debug print
            for coin in data.get("result", {}).get("list", [{}])[0].get("coin", []):
                if coin["coin"] == "USDT":
                    usdt = Decimal(coin.get("walletBalance", "0"))
            return usdt

async def fetch_kucoin_balance() -> Decimal:
    url_path = "/api/v1/account-overview?currency=USDT"
    url = f"https://api-futures.kucoin.com{url_path}"
    headers = sign_kucoin_request(
        get_config_value("KUCOIN_KEY"),
        get_config_value("KUCOIN_SECRET"),
        get_config_value("KUCOIN_PASSPHRASE"),
        "GET",
        url_path
    )
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return Decimal(data["data"]["availableBalance"])

async def get_balance(exchange: str) -> Decimal:
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            if exchange == "Bybit":
                return await fetch_bybit_balance()
            elif exchange == "KuCoin":
                return await fetch_kucoin_balance()
        except Exception as e:
            logger.warning(f"[WATCHDOG] Failed to fetch balance from {exchange} (attempt {attempt}/3): {e}")
            await asyncio.sleep(3)
    # If all attempts fail
    prev = _last_balance.get(exchange, Decimal("0"))
    logger.warning(f"[WATCHDOG] {exchange}: failed to fetch balance after 3 attempts. Keeping previous value: {prev:.2f} USD")
    return prev

def is_exchange_blocked(exchange: str) -> bool:
    return _trading_blocked.get(exchange, False)

async def balance_watchdog_loop():
    logger.info("[WATCHDOG] Balance watchdog started 🛡")
    required = POSITION_SIZE_USD * (Decimal("1") + BALANCE_MARGIN_PCT / Decimal("100"))

    notified = {
        "Bybit": None,
        "KuCoin": None
    }

    while True:
        for exchange in ["Bybit", "KuCoin"]:
            balance = await get_balance(exchange)
            _last_balance[exchange] = balance  # Update balance cache

            if balance >= required:
                logger.info(f"[WATCHDOG] {exchange}: free_balance={balance:.2f} USD | required={required:.2f} USD → OK")
                if _trading_blocked[exchange]:
                    _trading_blocked[exchange] = False
                    if notified[exchange] != "ok":
                        msg = f"✅ Balance on {exchange} restored. Trading resumed."
                        logger.info(f"[WATCHDOG] {msg}")
                        await send_message(msg)
                        notified[exchange] = "ok"
            else:
                logger.warning(f"[WATCHDOG] {exchange}: free_balance={balance:.2f} USD | required={required:.2f} USD → BLOCKED")
                if not _trading_blocked[exchange]:
                    _trading_blocked[exchange] = True
                    if notified[exchange] != "blocked":
                        msg = f"❌ Insufficient balance on {exchange} to open positions. Trading paused."
                        logger.info(f"[WATCHDOG] {msg}")
                        await send_message(msg)
                        notified[exchange] = "blocked"

        await asyncio.sleep(BALANCE_CHECK_INTERVAL_SEC)
