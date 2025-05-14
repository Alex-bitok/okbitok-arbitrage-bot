import asyncio
import aiohttp
from logger import logger
from decimal import Decimal, ROUND_DOWN

symbol_specs = {
    "Bybit": {},
    "KuCoin": {}
}

async def fetch_bybit_specs():
    url = "https://api.bybit.com/v5/market/instruments-info?category=linear"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get("result", {}).get("list", []):
                    symbol = item.get("symbol")
                    filters = item.get("lotSizeFilter", {})
                    price_filter = item.get("priceFilter", {})
                    contract_value = Decimal("1")  # Bybit USDT perpetual = $1 per contract
                    symbol_specs["Bybit"][symbol] = {
                        "min_qty": Decimal(filters.get("minOrderQty", "0")),
                        "step_qty": Decimal(filters.get("qtyStep", "1")),
                        "tick_size": Decimal(price_filter.get("tickSize", "0.0001")),
                        "contract_value": contract_value
                    }
        logger.info(f"[SYMBOL SPECS] Loaded {len(symbol_specs['Bybit'])} Bybit symbols")
    except Exception as e:
        logger.warning(f"[SYMBOL SPECS] Failed to fetch Bybit specs: {e}")

async def fetch_kucoin_specs():
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url) as resp:
                data = await resp.json()
                for item in data.get("data", []):
                    symbol = item.get("symbol")
                    symbol_specs["KuCoin"][symbol] = {
                        "min_qty": Decimal(item.get("baseMinSize", "0")),
                        "step_qty": Decimal(item.get("lotSize", "1")),
                        "tick_size": Decimal(item.get("tickSize", "0.0001")),
                        "contract_value": Decimal(str(item.get("multiplier", "1")))                
                    }
                    # print(f"[DEBUG CONTRACT] {symbol=} | multiplier={item.get('multiplier')} | parsed={symbol_specs['KuCoin'][symbol]['contract_value']}")
        logger.info(f"[SYMBOL SPECS] Loaded {len(symbol_specs['KuCoin'])} KuCoin symbols")
    except Exception as e:
        logger.warning(f"[SYMBOL SPECS] Failed to fetch KuCoin specs: {e}")

async def load_all_specs():
    await asyncio.gather(
        fetch_bybit_specs(),
        fetch_kucoin_specs()
    )

def get_specs(exchange: str, symbol: str) -> dict:
    if exchange not in symbol_specs:
        raise ValueError(f"Exchange {exchange} not supported")
    
    # print(f"[DEBUG SPECS] {exchange=} | {symbol=} → {symbol_specs[exchange].get(symbol)}")

    return symbol_specs[exchange].get(symbol, {})

# Initialization function to be called at project start
async def init_symbol_specs():
    await load_all_specs()
    logger.info("[SYMBOL SPECS] All symbol specs loaded.")

# Universal round-down for step size
def round_step(value: Decimal, step: Decimal) -> Decimal:
    return (value // step) * step
