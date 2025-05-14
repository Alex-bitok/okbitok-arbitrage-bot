import aiohttp
import asyncio
from decimal import Decimal
from order_manager import sign_bybit_request, sign_kucoin_request
from config_manager import get_config_value
from logger import logger

BYBIT_KEY = get_config_value("BYBIT_KEY")
BYBIT_SECRET = get_config_value("BYBIT_SECRET")
KUCOIN_KEY = get_config_value("KUCOIN_KEY")
KUCOIN_SECRET = get_config_value("KUCOIN_SECRET")
KUCOIN_PASSPHRASE = get_config_value("KUCOIN_PASSPHRASE")

async def fetch_final_pnl_bybit(symbol: str, side: str) -> Decimal:
    try:
        await asyncio.sleep(3) # give time for exchange to register closed position
        url = f"https://api.bybit.com/v5/position/closed-pnl?category=linear&symbol={symbol}&limit=5"
        headers = sign_bybit_request(BYBIT_KEY, BYBIT_SECRET, method="GET", path_or_body="category=linear&symbol=" + symbol + "&limit=5")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                # print(f"[BYBIT PNL DEBUG] Symbol={symbol}, Side={side}")
                # print(f"[BYBIT PNL DEBUG] Full API response:\n{data}")
                rows = data.get("result", {}).get("list", [])
                if not rows:
                    logger.debug("[BYBIT PNL DEBUG] No closed positions in response.")
                # --- Proper side mapping ---
                side_map = {"long": "Sell", "short": "Buy"}  # long is closed by selling, short by buying
                target_side = side_map.get(side.lower(), side)    
                latest_row = None
                latest_time = 0

                for row in rows:
                    if row.get("side", "").lower() == target_side.lower():
                        update_time = int(row.get("updatedTime", 0))
                        if update_time > latest_time:
                            latest_time = update_time
                            latest_row = row

                if latest_row:
                    return Decimal(str(latest_row.get("closedPnl", "0")))
    except Exception as e:
        logger.warning(f"[FINAL_PNL_FETCHER] Bybit error for {symbol}: {e}")

    return Decimal("0")

async def fetch_final_pnl_kucoin(symbol: str, side: str) -> Decimal:
    try:
        await asyncio.sleep(3) # give time for exchange to register closed position
        if not symbol.endswith("M"):
            symbol += "M"

        url_path = f"/api/v1/history-positions?symbol={symbol}&limit=10"
        url = f"https://api-futures.kucoin.com{url_path}"

        headers = sign_kucoin_request(
            KUCOIN_KEY, KUCOIN_SECRET, KUCOIN_PASSPHRASE, "GET", url_path
        )

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async with session.get(url, headers=headers) as resp:
                data = await resp.json()
                # print(f"[KUCOIN PNL DEBUG] Symbol={symbol}, Side={side} (searching in history-positions)")
                # print(f"[KUCOIN PNL DEBUG] Full API response:\n{data}")
                rows = data.get("data", {}).get("items", [])
                if not rows:
                    logger.debug("[KUCOIN PNL DEBUG] No closed position history in response.")

                # --- Find the most recent position by closeTime ---
                latest_row = None
                latest_time = 0

                for row in rows:
                    # logger.debug(f"[KUCOIN PNL DEBUG] Parsing position: {row}")
                    close_time = int(row.get("closeTime", 0))
                    if close_time > latest_time:
                        latest_time = close_time
                        latest_row = row

                if latest_row:
                    pnl = Decimal(str(latest_row.get("pnl", "0")))
                    logger.debug(f"[KUCOIN PNL DEBUG] Found most recent PnL: {pnl}")
                    return pnl
                else:
                    logger.debug("[KUCOIN PNL DEBUG] No matching position found.")

    except Exception as e:
        logger.warning(f"[FINAL_PNL_FETCHER] KuCoin error for {symbol}: {e}")

    return Decimal("0")

async def fetch_final_pnl(exchange: str, symbol: str, side: str) -> Decimal:
    attempts = 3
    delay = 2  # seconds between attempts

    for attempt in range(attempts):
        if exchange == "Bybit":
            pnl = await fetch_final_pnl_bybit(symbol, side)
        elif exchange == "KuCoin":
            pnl = await fetch_final_pnl_kucoin(symbol, side)
        else:
            pnl = Decimal("0")

        if pnl != 0:
            return pnl
        else:
            logger.info(f"[FINAL_PNL_FETCHER] Attempt {attempt+1}/{attempts}: PnL is still 0, retrying in {delay} seconds...")
            await asyncio.sleep(delay)

    logger.warning(f"[FINAL_PNL_FETCHER] ❗ All PnL fetch attempts returned 0. Returning 0.")
    return Decimal("0")
