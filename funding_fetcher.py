from logger import logger
import aiohttp
from decimal import Decimal, getcontext

from config_manager import get_config_value

getcontext().prec = 18

POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD", "100"))
LEVERAGE = Decimal(get_config_value("LEVERAGE", "3"))
MAX_HOLD_TIME_MINUTES = int(get_config_value("MAX_HOLD_TIME_MINUTES", "120"))
HOLD_HOURS = Decimal(MAX_HOLD_TIME_MINUTES) / Decimal(60)

async def fetch_funding(arb: dict) -> None:
    symbol = arb["symbol"]
    long_ex = arb["long_exchange"]
    short_ex = arb["short_exchange"]

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:

        async def get_bybit_rate(sym: str) -> Decimal:
            try:
                url = "https://api.bybit.com/v5/market/tickers?category=linear"
                async with session.get(url) as resp:
                    data = await resp.json()
                    for item in data.get("result", {}).get("list", []):
                        if item.get("symbol") == sym:
                            rate_str = item.get("fundingRate", "0")
                            # logger.info(f"[FUNDING] Bybit {sym} rate = {rate_str}")
                            return Decimal(rate_str)
            except Exception as e:
                logger.warning(f"[FUNDING] Bybit error for {sym}: {e}")
            return Decimal("0")

        async def get_kucoin_rate(sym: str) -> Decimal:
            try:
                if not sym.endswith("M"):
                    sym += "M"
                url = f"https://api-futures.kucoin.com/api/v1/funding-rate/{sym}/current"
                async with session.get(url) as resp:
                    data = await resp.json()
                    if data.get("code") != "200000" or not data.get("data"):
                        logger.warning(f"[FUNDING] KuCoin invalid response for {sym}: {data}")
                        return Decimal("0")

                    rate = data["data"].get("value")
                    if rate in [None, "", "null"]:
                        logger.warning(f"[FUNDING] KuCoin missing funding rate for {sym}")
                        return Decimal("0")

                    # logger.info(f"[FUNDING] KuCoin {sym} rate = {Decimal(rate):.8f}")
                    return Decimal(rate)
            except Exception as e:
                logger.warning(f"[FUNDING] KuCoin exception for {sym}: {e}")
                return Decimal("0")


        async def build(exchange: str) -> dict:
            try:
                if exchange == "Bybit":
                    rate = await get_bybit_rate(symbol)
                elif exchange == "KuCoin":
                    rate = await get_kucoin_rate(symbol)
                else:
                    rate = Decimal("0")

                cost = rate * POSITION_SIZE_USD * LEVERAGE * (HOLD_HOURS / Decimal(8))
                return {
                    "exchange": exchange,
                    "rate": round(rate, 6),
                    "hours": float(HOLD_HOURS),
                    "cost": round(cost, 4)
                }

            except Exception as e:
                logger.warning(f"[FUNDING] Failed to build for {exchange}/{symbol}: {e}")
                return {
                    "exchange": exchange,
                    "rate": Decimal("0"),
                    "hours": float(HOLD_HOURS),
                    "cost": Decimal("0"),
                    "fallback": True
                }

        arb["funding"] = {
            "long": await build(long_ex),
            "short": await build(short_ex)
        }
