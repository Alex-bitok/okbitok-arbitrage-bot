from logger import logger
from datetime import datetime, timedelta
from decimal import Decimal
import csv
from pathlib import Path
from config_manager import get_config_value

# Settings from .env
MIN_PROFIT = Decimal(get_config_value("MIN_PROFIT", "1.0"))
COOLDOWN_AFTER_TIMEOUT_MINUTES = int(get_config_value("COOLDOWN_AFTER_TIMEOUT_MINUTES", "15"))
SL_IGNORE_MINUTES = int(get_config_value("SL_IGNORE_MINUTES", "5"))
LIVE_MODE = get_config_value("LIVE_MODE", "false").lower() == "true"

# Internal state per pair
pair_state: dict[str, dict] = {}

# Timestamp parser
def parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")) if "Z" in ts else datetime.fromisoformat(ts)

# Main function
async def process_signal(arb: dict):
    symbol = arb["symbol"]
    now = datetime.utcnow()
    reason = None

    # Retrieve or initialize state
    state = pair_state.setdefault(symbol, {
        "blocked_until": None,
        "last_stopped_at": None,
        "fail_count": 0,
        "last_signal_ts": None
    })

    # Quarantine after timeout
    if state["blocked_until"] and now < state["blocked_until"]:
        reason = "quarantine"
    elif arb.get("exit_reason") == "sl":
        state["last_stopped_at"] = now
        reason = "signal_with_sl_ignored"
    elif state["last_stopped_at"] and now - state["last_stopped_at"] < timedelta(minutes=SL_IGNORE_MINUTES):
        reason = "recent_sl"
    elif arb.get("exit_reason") == "timeout":
        state["blocked_until"] = now + timedelta(minutes=COOLDOWN_AFTER_TIMEOUT_MINUTES)
        state["last_stopped_at"] = now
        reason = "signal_after_timeout_blocked"
    elif Decimal(str(arb.get("net_profit", "0"))) < MIN_PROFIT:
        reason = "low_net_profit"

    if reason:
        if not LIVE_MODE:
            print(f"[SIGNAL ENGINE] {symbol}: ❌ REJECT ({reason})")
        logger.info(f"[SIGNAL ENGINE] REJECTED: {symbol} - reason={reason}")
        return

    state["last_signal_ts"] = now

    if not LIVE_MODE:
        print(f"[SIGNAL ENGINE] {symbol}: ✅ PASS | Net Profit = ${arb['net_profit']:.2f} ({arb['profit_percent']:.2f}%)")

    try:
        from decision_engine import process_decision
        await process_decision(arb)
    except Exception as e:
        logger.exception(f"[SIGNAL ENGINE] Error passing to decision_engine: {e}")

