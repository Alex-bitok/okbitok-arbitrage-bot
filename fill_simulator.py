import aiohttp
from logger import logger
from decimal import Decimal, getcontext
import asyncio
from config_manager import get_config_value

# Decimal precision settings
getcontext().prec = 18

# Configuration
POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD", "100"))
LEVERAGE = Decimal(get_config_value("LEVERAGE", "3"))
MAX_PRICE_IMPACT = Decimal(get_config_value("MAX_PRICE_IMPACT", "0.5"))  # in %

# Market order simulation using orderbook
def simulate_market_fill(orderbook_side: list[tuple[str, str]], total_usd: Decimal) -> tuple[Decimal, Decimal] | None:
    filled_qty = Decimal("0")
    cost = Decimal("0")
    best_price = Decimal(orderbook_side[0][0]) if orderbook_side else Decimal("0")

    for price_str, qty_str in orderbook_side:
        price = Decimal(price_str)
        qty = Decimal(qty_str)
        level_value = price * qty

        if cost + level_value >= total_usd:
            needed_qty = (total_usd - cost) / price
            filled_qty += needed_qty
            cost += needed_qty * price
            break
        else:
            filled_qty += qty
            cost += level_value

    if filled_qty == 0:
        return None

    avg_price = cost / filled_qty
    impact = abs(avg_price - best_price) / best_price * 100
    return avg_price, impact

# Main function
async def simulate_fill(arb: dict) -> bool:
    symbol = arb["symbol"]
    usd_amount = POSITION_SIZE_USD * LEVERAGE

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            # BYBIT
            async def get_bybit_orderbook(symbol: str):
                url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={symbol}&limit=10"
                async with session.get(url) as resp:
                    data = await resp.json()

                    if data.get("retCode") != 0:
                        raise ValueError(f"Bybit error for {symbol}: {data}")

                    result = data.get("result")
                    if not result or "b" not in result or "a" not in result:
                        raise ValueError(f"Bybit returned invalid orderbook for {symbol}: {data}")

                    return result["b"], result["a"]


            # KUCOIN
            async def get_kucoin_orderbook(symbol: str):
                if not symbol.endswith("M"):
                    symbol += "M"
                url = f"https://api-futures.kucoin.com/api/v1/level2/snapshot?symbol={symbol}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return data["data"]["bids"], data["data"]["asks"]

            if arb["long_exchange"] == "Bybit":
                long_bids, long_asks = await get_bybit_orderbook(symbol)
            else:
                long_bids, long_asks = await get_kucoin_orderbook(symbol)

            if arb["short_exchange"] == "Bybit":
                short_bids, short_asks = await get_bybit_orderbook(symbol)
            else:
                short_bids, short_asks = await get_kucoin_orderbook(symbol)

            long_result = simulate_market_fill(long_asks, usd_amount)
            short_result = simulate_market_fill(short_bids, usd_amount)

            if not long_result or not short_result:
                logger.warning(f"[FILL SIMULATOR] Insufficient depth for {symbol}")
                return False

            long_price, long_impact = long_result
            short_price, short_impact = short_result

            max_impact = max(long_impact, short_impact)
            if max_impact > MAX_PRICE_IMPACT:
                logger.info(f"[FILL SIMULATOR] Price impact too high for {symbol}: {max_impact:.6f}%")
                return False

            arb["long_avg_price"] = long_price
            arb["short_avg_price"] = short_price
            arb["price_impact"] = max_impact

            # logger.info(f"[FILL SIMULATOR] OK: {symbol}, long={long_price:.8f}, short={short_price:.8f}, impact={max_impact:.4f}%")

            # print(f"[DEBUG FILL] {symbol=} | long_price={long_price} | short_price={short_price} | impact={max_impact}")

            return True

    except asyncio.TimeoutError:
        logger.warning(f"[FILL SIMULATOR] Timeout while fetching orderbook for {symbol}. Skipping arb.")
        return False
    except Exception as e:
        logger.warning(f"[FILL SIMULATOR] Error for {symbol}: {e}")
        return False
