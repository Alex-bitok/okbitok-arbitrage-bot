# advanced_trade_logger.py

import csv
from datetime import datetime, timezone, timedelta
from pathlib import Path
from config_manager import get_config_value
from logger import logger
from decimal import Decimal

LOG_FILE = Path("logs/trade_log.csv")

# CSV header (column names)
CSV_HEADER = [
    "ID",
    "Timestamp",
    "Symbol",
    "Final PnL ($)",
    "Total Duration (min)",
    "Delta Reason (TP/SL/Timeout)",
    "Delta Duration (min)",
    "Delta PnL ($)",
    "Failover Reason (TP/SL/Timeout)",
    "Failover Duration (min)",
    "Failover PnL ($)",
    "MIN_DELTA",
    "MIN_DELTA_LIFETIME",
    "POSITION_SIZE_USD",
    "LEVERAGE",
    "MIN_PROFIT",
    "TAKE_PROFIT_THRESHOLD",
    "STOP_LOSS_PCT",
    "FAILOVER_TRAILING_STOP_PCT",
    "FAILOVER_INITIAL_TAKE_PROFIT_PCT",
    "MAX_HOLD_TIME_MINUTES",
    "COOLDOWN_AFTER_TIMEOUT_MINUTES",
    "SL_IGNORE_MINUTES"
]

def _get_strategy_params():
    return {
        "MIN_DELTA": get_config_value("MIN_DELTA"),
        "MIN_DELTA_LIFETIME": get_config_value("MIN_DELTA_LIFETIME"),
        "POSITION_SIZE_USD": get_config_value("POSITION_SIZE_USD"),
        "LEVERAGE": get_config_value("LEVERAGE"),
        "MIN_PROFIT": get_config_value("MIN_PROFIT"),
        "TAKE_PROFIT_THRESHOLD": get_config_value("TAKE_PROFIT_THRESHOLD"),
        "STOP_LOSS_PCT": get_config_value("STOP_LOSS_PCT"),
        "FAILOVER_TRAILING_STOP_PCT": get_config_value("FAILOVER_TRAILING_STOP_PCT"),
        "FAILOVER_INITIAL_TAKE_PROFIT_PCT": get_config_value("FAILOVER_INITIAL_TAKE_PROFIT_PCT"),
        "MAX_HOLD_TIME_MINUTES": get_config_value("MAX_HOLD_TIME_MINUTES"),
        "COOLDOWN_AFTER_TIMEOUT_MINUTES": get_config_value("COOLDOWN_AFTER_TIMEOUT_MINUTES"),
        "SL_IGNORE_MINUTES": get_config_value("SL_IGNORE_MINUTES")
    }

def log_new_position(position: dict):
    now = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=5)))
    date_str = now.strftime("%Y-%m-%d %H:%M:%S")
    params = _get_strategy_params()

    # Trade ID counter
    if LOG_FILE.exists():
        with LOG_FILE.open("r", encoding="utf-8-sig", newline='') as f:
            lines = list(csv.reader(f))
            trade_number = len(lines)
    else:
        trade_number = 1
        with LOG_FILE.open("w", encoding="utf-8-sig", newline='') as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)

    row = [
        trade_number,
        date_str,
        position["symbol"],
        "", "", "", "", "", "", "", "",  
        params["MIN_DELTA"],
        params["MIN_DELTA_LIFETIME"],
        params["POSITION_SIZE_USD"],
        params["LEVERAGE"],
        params["MIN_PROFIT"],
        params["TAKE_PROFIT_THRESHOLD"],
        params["STOP_LOSS_PCT"],
        params["FAILOVER_TRAILING_STOP_PCT"],
        params["FAILOVER_INITIAL_TAKE_PROFIT_PCT"],
        params["MAX_HOLD_TIME_MINUTES"],
        params["COOLDOWN_AFTER_TIMEOUT_MINUTES"],
        params["SL_IGNORE_MINUTES"]
    ]

    with LOG_FILE.open("a", encoding="utf-8-sig", newline='') as f:
        writer = csv.writer(f)
        writer.writerow(row)

    # logger.info(f"[ADVANCED LOGGER] Trade #{trade_number} logged.")

def update_position_result(position: dict):
    import pandas as pd

    if not LOG_FILE.exists():
        logger.warning("[ADVANCED LOGGER] CSV not found, update aborted.")
        return

    df = pd.read_csv(LOG_FILE, encoding="utf-8-sig", dtype={"Delta Reason (TP/SL/Timeout)": "string", "Failover Reason (TP/SL/Timeout)": "string"})

    match = df["Symbol"] == position["symbol"]
    if not match.any():
        logger.warning(f"[ADVANCED LOGGER] No entry found for {position['symbol']} in CSV.")
        return

    idx = df[match].index[-1]  

    pnl = position.get("final_pnl_total", "")
    entry_time = position.get("entry_time")
    exit_time = position.get("exit_time")

    if entry_time and exit_time:
        duration = (exit_time - entry_time).total_seconds() / 60
    else:
        duration = ""

    # Fill base fields (total PnL and duration)
    df.at[idx, "Final PnL ($)"] = float(pnl) if pnl != "" else ""
    df.at[idx, "Total Duration (min)"] = round(duration, 2) if duration else ""

    from failover_manager import failover_positions
    pos_id = position["position_id"]
    failover = failover_positions.get(pos_id)

    # --- Delta stage ---
    delta_reason = position.get("start_reason", position.get("exit_reason", ""))
    df.at[idx, "Delta Reason (TP/SL/Timeout)"] = str(delta_reason)

    # PnL of the delta stage (first side)
    first_pnl = position.get("start_pnl", Decimal("0"))

    # Timestamps
    first_entry_time = position.get("entry_time")
    failover_entry_time = failover.get("entry_time") if failover else None

    if not failover:
        # No failover → delta covers the full position
        delta_duration = (exit_time - entry_time).total_seconds() / 60 if entry_time and exit_time else ""
        delta_pnl = pnl
    else:
        # With failover → delta ends at failover start
        delta_duration = (failover_entry_time - first_entry_time).total_seconds() / 60 if first_entry_time and failover_entry_time else ""
        delta_pnl = first_pnl

    df.at[idx, "Delta Duration (min)"] = round(delta_duration, 2) if delta_duration else ""
    df.at[idx, "Delta PnL ($)"] = float(delta_pnl) if delta_pnl != "" else ""

    # --- Failover stage ---
    if failover and failover.get("status") == "closed":
        failover_reason = failover.get("exit_reason", "")
        failover_pnl = failover.get("final_pnl_total", "")

        failover_exit_time = failover.get("exit_time")
        failover_start_time = failover.get("entry_time")

        if failover_start_time and failover_exit_time:
            failover_duration = (failover_exit_time - failover_start_time).total_seconds() / 60
        else:
            failover_duration = ""

        df.at[idx, "Failover Reason (TP/SL/Timeout)"] = str(failover_reason)
        df.at[idx, "Failover Duration (min)"] = round(failover_duration, 2) if failover_duration else ""
        df.at[idx, "Failover PnL ($)"] = float(failover_pnl) if failover_pnl != "" else ""

    df.to_csv(LOG_FILE, index=False, encoding="utf-8-sig")
    # logger.info(f"[ADVANCED LOGGER] Trade {position['symbol']} updated in CSV.")