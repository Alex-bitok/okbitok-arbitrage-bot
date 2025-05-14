import asyncio
import csv
from datetime import datetime, UTC
from decimal import Decimal
from logger import logger
from config_manager import get_config_value
from order_manager import place_market_order
from pnl_fetcher import fetch_pnl
from final_pnl_fetcher import fetch_final_pnl
from advanced_trade_logger import update_position_result
import position_manager

BOLD = "\033[1m"
WHITE = "\033[97m"
RESET = "\033[0m"

FAILOVER_TRAILING_STOP_PCT = Decimal(get_config_value("FAILOVER_TRAILING_STOP_PCT", "1.0"))
FAILOVER_INITIAL_TAKE_PROFIT_PCT = Decimal(get_config_value("FAILOVER_INITIAL_TAKE_PROFIT_PCT", "3.0"))
FAILOVER_CHECK_INTERVAL_SEC = int(get_config_value("FAILOVER_CHECK_INTERVAL_SEC", "30"))

failover_positions = {}

async def start_failover(position_id: str, exchange: str, direction: str, symbol: str,
                         entry_price: Decimal, qty: Decimal,
                         start_pnl: Decimal, entry_fee: Decimal, funding: Decimal,
                         position_notional: Decimal):
    
    from symbol_specs import get_specs
    specs = get_specs(exchange, symbol)
    contract_value = specs.get("contract_value", Decimal("1"))

    logger.debug(f"[FAILOVER DEBUG] Qty = {qty} | Entry Price = {entry_price} | Contract Value = {contract_value} | Notional = {position_notional}")

    failover_positions[position_id] = {
        "exchange": exchange,
        "symbol": symbol,
        "direction": direction,
        "entry_price": entry_price,
        "qty": qty,
        "start_pnl": start_pnl,
        "current_pnl": start_pnl,
        "max_pnl": start_pnl,
        "trailing_stop_pnl": start_pnl - (position_notional * (FAILOVER_TRAILING_STOP_PCT / 100)),
        "initial_take_profit_pnl": position_notional * (FAILOVER_INITIAL_TAKE_PROFIT_PCT / 100),
        "entry_fee": entry_fee,
        "funding": funding,
        "position_notional": position_notional,
        "entry_time": datetime.now(UTC)
    }

    logger.info(f"[FAILOVER] ✅ Activated for {position_id} | {symbol} | {exchange} | {direction} | entry_price={entry_price} | qty={qty}")
    from telegram_bot import send_message
    asyncio.create_task(send_message(
        f"✅ <b>Failover activated</b>\n"
        f"{symbol} | {exchange} ({direction})\n"
        f"Entry price: {entry_price}, Qty: {qty}"
    ))

async def _check_positions_loop():
    while True:
        await asyncio.sleep(FAILOVER_CHECK_INTERVAL_SEC)
        logger.info(f"[FAILOVER LOOP] Check loop alive. Position count: {len(failover_positions)}")
        await check_positions()

async def check_positions():
    for position_id, pos in list(failover_positions.items()):
        logger.info(f"[FAILOVER LOOP] Checking position {position_id} (total {len(failover_positions)} being monitored)")
        try:
            pnl = await fetch_pnl(pos["exchange"], pos["symbol"], side=pos["direction"])
            net_pnl = pnl  # Fees and funding already included by the exchange in unrealisedPnl

            logger.info(
                f"[FAILOVER CHECK✅] {position_id} | PnL = {BOLD}{WHITE}{net_pnl:.4f}{RESET} | "
                f"Trail stop = {pos['trailing_stop_pnl']:.4f} | Take profit = {pos['initial_take_profit_pnl']:.4f}"
            )

            if net_pnl == 0:
                logger.warning(f"[FAILOVER WARNING] PnL for position {position_id} = 0. Possible issue with fetch_pnl or WS. Skipping check.")
                return  # <-- add return to skip further check

            pos["current_pnl"] = net_pnl

            if net_pnl > pos["max_pnl"]:
                pos["max_pnl"] = net_pnl
                pos["trailing_stop_pnl"] = pos["max_pnl"] - (pos["position_notional"] * (FAILOVER_TRAILING_STOP_PCT / 100))
                

            if net_pnl <= pos["trailing_stop_pnl"]:
                await exit_position(position_id, "trailing_stop_exit")
            elif net_pnl >= pos["initial_take_profit_pnl"]:
                await exit_position(position_id, "take_profit_exit")

        except Exception as e:
            logger.error(f"[FAILOVER MANAGER] Error checking position {position_id}: {e}")

async def check_position(position_id: str):
    pos = failover_positions.get(position_id)
    if not pos:
        return

    try:
        pnl = await fetch_pnl(pos["exchange"], pos["symbol"], side=pos["direction"])
        net_pnl = pnl  # Fees and funding already included by the exchange in unrealisedPnl

        logger.info(
            f"[FAILOVER CHECK✅] {position_id} | PnL = {BOLD}{WHITE}{net_pnl:.4f}{RESET} | "
            f"Trail stop = {pos['trailing_stop_pnl']:.4f} | Take profit = {pos['initial_take_profit_pnl']:.4f}"
        )

        if net_pnl == 0:
            logger.warning(f"[FAILOVER WARNING] PnL for position {position_id} = 0. Possible issue with fetch_pnl or WS. Skipping check.")
            return  # <-- add return to skip further check

        pos["current_pnl"] = net_pnl

        if net_pnl > pos["max_pnl"]:
            pos["max_pnl"] = net_pnl
            pos["trailing_stop_pnl"] = pos["max_pnl"] - (pos["position_notional"] * (FAILOVER_TRAILING_STOP_PCT / 100))
            

        if net_pnl <= pos["trailing_stop_pnl"]:
            await exit_position(position_id, "trailing_stop_exit")
        elif net_pnl >= pos["initial_take_profit_pnl"]:
            await exit_position(position_id, "take_profit_exit")

    except Exception as e:
        logger.error(f"[FAILOVER MANAGER] Error checking position {position_id}: {e}")


async def exit_position(position_id: str, reason: str):
    pos = failover_positions.get(position_id)
    logger.warning(f"[FAILOVER] ❌ Closing position {position_id} | {pos['symbol']} | Reason: {reason}")
    if not pos:
        logger.warning(f"[FAILOVER] Tried to exit non-existent position {position_id}")
        return

    if pos.get("status") == "closed":
        logger.warning(f"[FAILOVER] Position {position_id} already closed, skipping duplicate exit.")
        return

    side = "Sell" if pos["direction"] == "long" else "Buy"
    qty = float(pos["qty"])
    symbol = pos["symbol"]

    try:
        await place_market_order(pos["exchange"], symbol, side, qty, reduce_only=True)
        await asyncio.sleep(1)  

        # --- Fetch final PnL of closed leg ---
        side = "long" if pos["direction"] == "long" else "short"
        pnl = await fetch_final_pnl(pos["exchange"], pos["symbol"], side)

        if pos["direction"] == "long":
            pos["final_pnl_long"] = pnl
        else:
            pos["final_pnl_short"] = pnl

        pos["final_pnl_total"] = pos.get("final_pnl_long", Decimal("0")) + pos.get("final_pnl_short", Decimal("0"))

    except Exception as e:
        logger.error(f"[FAILOVER MANAGER] ❌ Error while closing position {position_id}: {e}")

    # Marking position as closed
    pos["exit_time"] = datetime.now(UTC)
    pos["status"] = "closed"
    pos["exit_reason"] = reason

     # --- Sync with position_manager ---
    try:
        from position_manager import open_positions, clear_pending
        if position_id in position_manager.open_positions:
            open_positions[position_id]["status"] = "closed"
            open_positions[position_id]["exit_reason"] = reason
            symbol = pos["symbol"]
            clear_pending(symbol)
    except Exception as e:
        logger.error(f"[FAILOVER] Error syncing position status in position_manager: {e}")
    # --- End sync ---

    

    from telegram_bot import send_message

    final_pnl_failover = pos.get('final_pnl_total', Decimal("0"))
    final_pnl_pm = pos.get("start_pnl", Decimal("0"))
    final_pnl = final_pnl_failover + final_pnl_pm
    if final_pnl is not None and final_pnl != 0:
        pnl_text = (
            f"Final total PnL (both sides): ${final_pnl:.4f}\n"
            f"PnL of first leg (from position manager): ${final_pnl_pm:.4f}\n"
            f"PnL of second leg (failover): ${final_pnl_failover:.4f}"
        )
    else:
        pnl_text = (
            f"⚠ Failed to fetch actual PnL (exchange may not have returned data). "
            f"Showing estimated: ${pos.get('current_pnl', Decimal('0')):.4f}"
        )

    asyncio.create_task(send_message(
        f"❌ <b>Position closed in failover</b>\n"
        f"{pos['symbol']} | Reason: {reason}\n"
        f"{pnl_text}"
    ))


    # --- Sync with position_manager and CSV --- 
    if position_id in position_manager.open_positions:
        from advanced_trade_logger import update_position_result

        # First update the position's PnL
        pm_pos = position_manager.open_positions[position_id]
        pm_pos["final_pnl_total"] = pos.get("final_pnl_total", Decimal("0")) + pos.get("start_pnl", Decimal("0"))

        pm_pos["exit_time"] = datetime.now(UTC)
        pm_pos["exit_reason"] = reason

        update_position_result(pm_pos)
    # --- End sync ---

    # Remove position from memory
    del failover_positions[position_id]


