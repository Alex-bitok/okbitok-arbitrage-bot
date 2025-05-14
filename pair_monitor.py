import asyncio
from logger import logger
from datetime import datetime, UTC
from typing import Dict
from config_manager import get_config_value
from fill_simulator import simulate_fill
from funding_fetcher import fetch_funding
from profit_simulator import simulate_profit
from signal_engine import process_signal
from position_manager import get_active_symbols, on_price_update
from failover_manager import check_position, failover_positions

# Quote update queue
from price_feed import price_queue

arb_queue: asyncio.Queue = asyncio.Queue()

# Configuration
MIN_DELTA = float(get_config_value("MIN_DELTA"))
MAX_QUOTE_AGE_SEC = int(get_config_value("MAX_QUOTE_AGE_SEC"))
MIN_DELTA_LIFETIME = int(get_config_value("MIN_DELTA_LIFETIME", 2))
DELTA_CACHE_EXPIRATION_SEC = int(get_config_value("DELTA_CACHE_EXPIRATION_SEC", 10))

# Latest quotes by exchange and symbol
latest_quotes: Dict[str, Dict[str, Dict]] = {}
# Delta cache with timestamps
delta_cache: Dict[str, Dict] = {}

# TODO: unify timestamp parsing across modules (duplicate with signal_engine)
def parse_timestamp(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))

async def handle_price_update(payload: Dict):
    symbol_raw = payload["pair_id"].split("-")[0]
    # Strip 'M' suffix from KuCoin symbols
    symbol = symbol_raw[:-1] if payload["exchange"] == "KuCoin" and symbol_raw.endswith("M") else symbol_raw
    exchange = payload["exchange"]
    bid = payload["bid"]
    ask = payload["ask"]
    timestamp = payload["timestamp"]

    # Update cache
    if symbol not in latest_quotes:
        latest_quotes[symbol] = {}
    latest_quotes[symbol][exchange] = {
        "bid": bid,
        "ask": ask,
        "timestamp": timestamp
    }

    # If both sides exist — calculate deltas
    if len(latest_quotes[symbol]) < 2:
        return

    quotes = latest_quotes[symbol]
    exchanges = list(quotes.keys())
    ex1, ex2 = exchanges[0], exchanges[1]
    q1, q2 = quotes[ex1], quotes[ex2]

    now = datetime.now(UTC)
    age1 = (now - parse_timestamp(q1["timestamp"])).total_seconds()
    age2 = (now - parse_timestamp(q2["timestamp"])).total_seconds()

    if age1 > MAX_QUOTE_AGE_SEC or age2 > MAX_QUOTE_AGE_SEC:
        return

    # Delta calculation
    delta_1 = ((q2["bid"] - q1["ask"]) / q1["ask"]) * 100
    delta_2 = ((q1["bid"] - q2["ask"]) / q2["ask"]) * 100

    best_delta = max(delta_1, delta_2)

    if best_delta < MIN_DELTA:
        # Even if delta is small, update active positions      
        if symbol in get_active_symbols():
            try:
                await on_price_update(symbol, bid, ask, timestamp)
            except Exception as e:
                logger.warning(f"[PAIR_MONITOR] Failed to update position manager for {symbol}: {e}")
        # Update all failovers for this symbol
        for position_id, pos in failover_positions.items():
            # print(f"[PAIR_MONITOR] Failover check: {position_id} | {pos['symbol']} | status={pos.get('status')}")
            # print(symbol)
            if pos["symbol"] == symbol and pos.get("status") != "closed":
                logger.debug(f"[PAIR_MONITOR] Calling check_position for {position_id}")
                try:
                    await check_position(position_id)
                except Exception as e:
                    logger.warning(f"[PAIR_MONITOR] Failed to check failover position {position_id} for {symbol}: {e}")
        return
    
    # Check or initialize delta cache
    cache_entry = delta_cache.get(symbol)
    if cache_entry:
        age = (now - cache_entry["timestamp"]).total_seconds()

        if age >= MIN_DELTA_LIFETIME and age <= DELTA_CACHE_EXPIRATION_SEC:
            del delta_cache[symbol]  # sufficient time passed — trigger
        else:
            return  # either too early or expired
    else:
        delta_cache[symbol] = {"delta": best_delta, "timestamp": now}
        return

    # Determine best opportunity
    if delta_1 > delta_2:
        arb = {
            "symbol": symbol,
            "long_exchange": ex1,
            "short_exchange": ex2,
            "long_price": q1["ask"],
            "short_price": q2["bid"],
            "raw_delta": delta_1,
            "timestamp": now.isoformat()
        }
    else:
        arb = {
            "symbol": symbol,
            "long_exchange": ex2,
            "short_exchange": ex1,
            "long_price": q2["ask"],
            "short_price": q1["bid"],
            "raw_delta": delta_2,
            "timestamp": now.isoformat()
        }

    logger.info(f"[PAIR_MONITOR] {symbol}: Δ={arb['raw_delta']:.4f}%, long={arb['long_exchange']}, short={arb['short_exchange']}")

    # Send to position manager if symbol is active
    if symbol in get_active_symbols():
        try:
            await on_price_update(symbol, bid, ask, timestamp)
        except Exception as e:
            logger.warning(f"[PAIR_MONITOR] Failed to update position manager for {symbol}: {e}")

    # Also check failover regardless
    for position_id, pos in failover_positions.items():
        if pos["symbol"] == symbol and pos.get("status") != "closed":
            # logger.debug(f"[PAIR_MONITOR] Calling check_position for {position_id}")
            try:
                await check_position(position_id)
            except Exception as e:
                logger.warning(f"[PAIR_MONITOR] Failed to check failover position {position_id} for {symbol}: {e}")

    # Launch simulations
    await arb_queue.put(arb)

async def monitor_loop():
    while True:
        payload = await price_queue.get()
        try:
            await handle_price_update(payload)
        except Exception as e:
            logger.exception(f"[PAIR_MONITOR] Error handling payload: {e}")

async def arb_pipeline(arb: dict):
    from fill_simulator import simulate_fill
    from funding_fetcher import fetch_funding
    from profit_simulator import simulate_profit
    from signal_engine import process_signal

    # First: entry simulation
    fill_ok = await simulate_fill(arb)
    if not fill_ok:
        return

    # Then: funding
    await fetch_funding(arb)

    # Then: profit
    await simulate_profit(arb)

if __name__ == "__main__":
    asyncio.run(monitor_loop())

