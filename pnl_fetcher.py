import aiohttp
from decimal import Decimal
from order_manager import sign_bybit_request, sign_kucoin_request
from config_manager import get_config_value
import json
from logger import logger

async def fetch_pnl(exchange: str, symbol: str, side: str) -> Decimal:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            if exchange == "Bybit":
                url = f"https://api.bybit.com/v5/position/list?category=linear&symbol={symbol}"
                query_string = f"category=linear&symbol={symbol}"
                headers = sign_bybit_request(
                    get_config_value("BYBIT_KEY"),
                    get_config_value("BYBIT_SECRET"),
                    method="GET",
                    path_or_body=query_string
                )
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    positions = data.get("result", {}).get("list", [])
                    
                    if positions:
                                for pos in positions:
                                    if pos.get("side") == ("Sell" if side == "short" else "Buy"):
                                        return Decimal(str(pos.get("unrealisedPnl", "0")))

            elif exchange == "KuCoin":
                if not symbol.endswith("M"):
                    symbol += "M"
                url_path = f"/api/v1/position?symbol={symbol}"
                url = f"https://api-futures.kucoin.com{url_path}"
                headers = sign_kucoin_request(
                    get_config_value("KUCOIN_KEY"),
                    get_config_value("KUCOIN_SECRET"),
                    get_config_value("KUCOIN_PASSPHRASE"),
                    "GET",
                    url_path
                )
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    if data.get("data"):
                        return Decimal(str(data["data"].get("unrealisedPnl", "0")))

    except Exception as e:
        logger.warning(f"[PNL_FETCHER] Error requesting PnL for {exchange} {symbol}: {e}")

    return Decimal("0")
