# decision_engine.py
from logger import logger
from config_manager import get_config_value
from position_manager import get_open_positions, can_open_position, is_pending_open, set_pending_open, clear_pending
from order_manager import execute_order
from balance_watchdog import is_exchange_blocked
from failover_manager import failover_positions 

MAX_PARALLEL_POSITIONS = int(get_config_value("MAX_PARALLEL_POSITIONS", "1"))
LIVE_MODE = get_config_value("LIVE_MODE", "false").lower() == "true"

async def process_decision(arb: dict) -> bool:

    symbol = arb["symbol"]
    long_ex = arb["long_exchange"]
    short_ex = arb["short_exchange"]

    if not LIVE_MODE:
        logger.info(f"[DECISION ENGINE] LOG MODE: {symbol} ✅✅✅✅ passed with Net Profit = ${arb['net_profit']:.2f} ({arb['profit_percent']:.2f}%) — not executed.")
        return False

    if not can_open_position(symbol, long_ex, short_ex) or is_pending_open(symbol, long_ex, short_ex):
        reason = "duplicate_position"
        
        logger.info(f"[DECISION ENGINE] {symbol}: ❌ REJECT — {reason}")
        return False

    open_positions = get_open_positions()

    from failover_manager import failover_positions
    active_failovers = [f for f in failover_positions.values() if f.get("status") != "closed"]

    if len(open_positions) + len(active_failovers) >= MAX_PARALLEL_POSITIONS:
        reason = "too_many_open_positions"
        logger.info(f"[DECISION ENGINE] {symbol}: ❌ REJECT — {reason} (regular={len(open_positions)}, failover={len(active_failovers)})")
        return False
    
    if is_exchange_blocked(long_ex) or is_exchange_blocked(short_ex):
        reason = "balance_blocked"
        logger.info(f"[DECISION ENGINE] {symbol}: ❌ REJECT — {reason}")
        return False

    logger.info(f"[DECISION ENGINE] ✅✅✅✅ ACCEPTED: {symbol} | {long_ex} / {short_ex}")

    set_pending_open(symbol, long_ex, short_ex, True)

    try:
        success = await execute_order(arb)
    except Exception as e:
        logger.error(f"[DECISION ENGINE] Critical error during order execution: {e}")
        success = False
    finally:
        if not success:
            set_pending_open(symbol, long_ex, short_ex, False)

    if not success:
        reason = arb.get("exit_reason", "order_failed")
        logger.warning(f"[DECISION ENGINE] Order failed for {symbol} - reason: {reason}")
        return False

    return True
