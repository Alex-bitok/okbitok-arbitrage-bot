import asyncio
from logger import logger
from datetime import datetime, timedelta, UTC
from decimal import Decimal
from typing import Dict, Optional
import aiohttp
from order_manager import sign_bybit_request, sign_kucoin_request
from config_manager import get_config_value
from order_manager import place_market_order, get_position_size
from failover_manager import start_failover
from pnl_fetcher import fetch_pnl
from final_pnl_fetcher import fetch_final_pnl
from advanced_trade_logger import log_new_position, update_position_result

TAKE_PROFIT_THRESHOLD = Decimal(get_config_value("TAKE_PROFIT_THRESHOLD", "10"))
MAX_HOLD_TIME_MINUTES = int(get_config_value("MAX_HOLD_TIME_MINUTES", "120"))
SL_IGNORE_MINUTES = int(get_config_value("SL_IGNORE_MINUTES", "5"))
REST_POLL_INTERVAL = 5  # TODO: move to .env if adjustable in production
STOP_LOSS_PCT = Decimal(get_config_value("STOP_LOSS_PCT", "1.0"))
POSITION_CHECK_INTERVAL_SEC = int(get_config_value("POSITION_CHECK_INTERVAL_SEC", "60"))
POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD", "100"))
LEVERAGE = Decimal(get_config_value("LEVERAGE", "3"))

# Storage for all active positions
open_positions: Dict[str, dict] = {}

# Pairs currently being opened
pending_positions: set[tuple[str, str, str]] = set()

# Active symbols and their quotes
active_symbols: set[str] = set()
symbol_quotes: Dict[str, Dict[str, Decimal]] = {}

async def on_price_update(symbol: str, bid: float, ask: float, timestamp: str):
    symbol_quotes[symbol] = {"bid": Decimal(str(bid)), "ask": Decimal(str(ask))}

    for pos_id, pos in open_positions.items():
        if pos["symbol"] != symbol or pos["status"] != "open":
            continue

        long_ex = pos["long_exchange"]
        short_ex = pos["short_exchange"]

        quotes = symbol_quotes.get(symbol, {})
        if "bid" not in quotes or "ask" not in quotes:
            continue

        pos["last_price"] = {
            long_ex: Decimal(str(quotes["ask"])) if long_ex else Decimal("0"),
            short_ex: Decimal(str(quotes["bid"])) if short_ex else Decimal("0")
        }

        await check_position_exit(pos_id)

# Logic to check exit by TP/SL inside check_position_exit
async def check_position_exit(pos_id: str):
    pos = open_positions[pos_id]

    if pos["status"] != "open":
        return

    if datetime.now(UTC) >= pos["entry_time"] + timedelta(minutes=MAX_HOLD_TIME_MINUTES):
        await close_position(pos_id, reason="timeout")
        return

    long_ex = pos["long_exchange"]
    short_ex = pos["short_exchange"]
    symbol = pos["symbol"]

    pnl_long = await fetch_pnl(long_ex, symbol, side="long")
    pnl_short = await fetch_pnl(short_ex, symbol, side="short")

    #### ---- ZERO PnL SAFEGUARD ---- ####
    if pnl_long == 0 or pnl_short == 0:
        logger.warning(
            f"\n🛑🛑🛑 [POSITION CHECK❗] WARNING! Detected PnL = 0 for {symbol}:\n"
            f"  ➔ Long PnL = {pnl_long:.4f} USD\n"
            f"  ➔ Short PnL = {pnl_short:.4f} USD\n"
            f"  ➔ Skipping check until next cycle!"
        )
        return
    #### ---- END OF SAFEGUARD ---- ####

    entry_fee = pos.get("entry_fee", Decimal("0"))
    funding = Decimal("0")
    if isinstance(pos.get("funding"), dict):
        funding = Decimal(str(pos["funding"].get("long", {}).get("cost", 0))) + Decimal(str(pos["funding"].get("short", {}).get("cost", 0)))
    else:
        funding = pos.get("funding", Decimal("0"))

    INCLUDE_FUNDING = get_config_value("INCLUDE_FUNDING_IN_PROFIT", "true").lower() == "true"

    if INCLUDE_FUNDING:
        total_funding = funding
    else:
        total_funding = Decimal("0")

    net_profit = pnl_long + pnl_short - (entry_fee * 2) - total_funding

    pos["net_profit"] = net_profit
    
    # DEBUG: full profit breakdown for manual review
    # print(f"[POSITION CHECK🔥] {symbol}: Net Profit (from PnL) = {net_profit} USD (Take Profit Threshold = {TAKE_PROFIT_THRESHOLD} USD)")

    total_fees = entry_fee * 2  # entry + exit
    # ANSI Colors
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    WHITE = "\033[97m"

    logger.info(f"{YELLOW}[POSITION CHECK🔥] {symbol}:{RESET}")

    # Colorize Net Profit with emoji
    if net_profit >= 0:
        profit_color = GREEN
        profit_emoji = "💰"
    else:
        profit_color = RED
        profit_emoji = "💩"

    logger.info(
        f"{profit_color}  ➔ Net Profit (from PnL) = {BOLD}{WHITE}{net_profit:.4f} USD{RESET} "
        f"(Take Profit Threshold = {TAKE_PROFIT_THRESHOLD} USD) {profit_emoji}{RESET}"
    )
    logger.info(
        f"{CYAN}\n"
        f"  ➔ Fees =      {total_fees:.4f} USD\n"
        f"  ➔ Funding =   {funding:.4f} USD\n"
        f"  ➔ Long PnL =  {pnl_long:.4f} USD\n"
        f"  ➔ Short PnL = {pnl_short:.4f} USD{RESET}"
    )

    # Take Profit check
    if net_profit >= TAKE_PROFIT_THRESHOLD:
        await close_position(pos_id, reason="tp", net_profit=net_profit)
        return

    # Stop Loss check per leg (component-wise PnL)
    position_value = POSITION_SIZE_USD * LEVERAGE

    pnl_long_pct = (pnl_long / position_value) * 100
    pnl_short_pct = (pnl_short / position_value) * 100

    if pnl_long_pct <= -STOP_LOSS_PCT or pnl_short_pct <= -STOP_LOSS_PCT:
        await handle_stop_loss(pos_id, net_profit)
        return

async def handle_stop_loss(pos_id: str, net_profit: Decimal):
    pos = open_positions[pos_id]
    symbol = pos["symbol"]
    long_ex = pos["long_exchange"]
    short_ex = pos["short_exchange"]
    entry_fee = pos.get("entry_fee", Decimal("0"))
    funding = pos.get("funding", Decimal("0"))

    pnl_long = await fetch_pnl(long_ex, symbol, side="long")
    pnl_short = await fetch_pnl(short_ex, symbol, side="short")

    # Calculate net PnL per side
    net_pnl_long = pnl_long - (entry_fee) - (funding / 2)
    net_pnl_short = pnl_short - (entry_fee) - (funding / 2)

    position_value = POSITION_SIZE_USD * LEVERAGE

    pnl_long_pct = (net_pnl_long / position_value) * 100
    pnl_short_pct = (net_pnl_short / position_value) * 100

    logger.warning(f"[STOP LOSS TRIGGERED] {symbol}: Long PnL = {pnl_long_pct:.2f}%, Short PnL = {pnl_short_pct:.2f}%")

    if pnl_long_pct <= pnl_short_pct:
        # Stop triggered on long side
        await close_position_side(pos_id, side="long", reason="sl")
        survivor_exchange = short_ex
        survivor_direction = "short"
        survivor_entry_price = pos["entry_prices"][short_ex]
    else:
        # Stop triggered on short side
        await close_position_side(pos_id, side="short", reason="sl")
        survivor_exchange = long_ex
        survivor_direction = "long"
        survivor_entry_price = pos["entry_prices"][long_ex]

    # Transition to failover mode
    pos["status"] = "failover"

    logger.warning(f"[STOP LOSS CLOSED] 🟥Passing to failover qty = {pos['qty']} | symbol = {pos['symbol']} | position_id = {pos_id}")

    # --- Pass real PnL of closed side to failover ---
    if survivor_direction == "long":
        closed_side = "short"
    else:
        closed_side = "long"

    final_closed_pnl = pos.get(f"final_pnl_{closed_side}", Decimal("0"))

    await start_failover_from_position(
        position_id=pos_id,
        exchange=survivor_exchange,
        direction=survivor_direction,
        symbol=symbol,
        entry_price=survivor_entry_price,
        qty=pos["qty"],
        entry_fee=entry_fee,
        funding=funding,
        start_pnl=final_closed_pnl
    )

async def start_failover_from_position(position_id: str, exchange: str, direction: str, symbol: str,
                                       entry_price: Decimal, qty: Decimal,
                                       entry_fee: Decimal, funding: Decimal,
                                       start_pnl: Decimal):
    logger.info(f"[FAILOVER INIT] Fetching initial PnL for {symbol} | {exchange} | {direction}")

    pos = open_positions[position_id]   

    initial_pnl = start_pnl

    if direction == "long":
        qty_for_failover = pos.get("qty_long", pos.get("qty", Decimal("0")))
    else:
        qty_for_failover = pos.get("qty_short", pos.get("qty", Decimal("0")))

    logger.info(f"[FAILOVER INIT POSITION MANAGER] Passing to failover qty = {qty_for_failover} | Entry Price = {entry_price} | Position ID = {position_id}")

    position_notional = pos.get("position_notional", qty * entry_price)

    await start_failover(
        position_id=position_id,
        exchange=exchange,
        direction=direction,
        symbol=symbol,
        entry_price=entry_price,
        qty=qty_for_failover,
        start_pnl=initial_pnl,
        entry_fee=entry_fee,
        funding=funding,
        position_notional=position_notional
    )

async def close_position_side(pos_id: str, side: str, reason: str):
    pos = open_positions[pos_id]
    exchange = pos["long_exchange"] if side == "long" else pos["short_exchange"]
    symbol = pos["symbol"]
    qty = pos["qty_long"] if side == "long" else pos["qty_short"]
    opposite_side = "Sell" if side == "long" else "Buy"
    
    try:
        await place_market_order(exchange, symbol, opposite_side, qty, reduce_only=True)
        await asyncio.sleep(1)  # give exchange time to update closed position history
        # --- Check and retry close if not fully closed ---
        remaining = await get_position_size(exchange, symbol)
        if remaining > 0:
            print(f"⚠️ Remaining {remaining} contracts on {exchange} after close. Retrying...")
            await place_market_order(exchange, symbol, opposite_side, remaining, reduce_only=True)
            await asyncio.sleep(1) # wait for exchange to update closed position history
            final_remaining = await get_position_size(exchange, symbol)
            if final_remaining > 0:
                logger.error(f"[STOP LOSS] ❌ Failed to fully close {side.upper()} on {exchange} ({final_remaining} contracts remaining)")

        # --- Fetch final PnL of the closed side ---
        pnl = await fetch_final_pnl(exchange, symbol, side)
        # If the other side is not closed yet — treat as delta-PnL
        other_side = "short" if side == "long" else "long"
        if pos.get(f"{other_side}_status") != "closed":
            pos["start_pnl"] = pnl
        if side == "long":
            pos["final_pnl_long"] = pnl
        else:
            pos["final_pnl_short"] = pnl
        pos[f"{side}_status"] = "closed"
        pos["exit_reason"] = reason
        pos["start_reason"] = reason
        logger.info(f"[STOP LOSS] Closed {side} position {pos_id} on {exchange} by stop-loss.")
        # --- Print final PnL to console ---
        print(
            f"[STOP LOSS] Final PnL {side.upper()} = {pnl:.4f} USD"
        )

        # --- Send to Telegram ---
        from telegram_bot import send_message

        pnl_text = f"Final PnL: ${pnl:.4f}" if pnl != 0 else "⚠ Exchange returned PnL = 0. Please verify manually."

        await send_message(
            f"❌ <b>Side closed {side.upper()} by stop-loss</b>\n"
            f"{symbol} | {exchange}\n"
            f"Reason: {reason}\n"
            f"{pnl_text}"
        )
    except Exception as e:
        logger.error(f"[STOP LOSS] Failed to close {side} position {pos_id}: {e}")

async def _position_stop_loss_check_loop():
    while True:
        await asyncio.sleep(POSITION_CHECK_INTERVAL_SEC)
        for pos_id in list(open_positions.keys()):
            try:
                pos = open_positions[pos_id]
                if pos["status"] == "open":
                    await check_position_exit(pos_id)
            except Exception as e:
                logger.error(f"[POSITION CHECK LOOP] Error checking position {pos_id}: {e}")
        

# Closing position on both sides
async def close_position(pos_id: str, reason: str, net_profit: Optional[Decimal] = None):
    pos = open_positions[pos_id]
    if pos["status"] != "open":
        return

    symbol = pos["symbol"]
    long_exchange = pos["long_exchange"]
    short_exchange = pos["short_exchange"]

    # Enable duplicate protection during full close
    set_pending_open(symbol, long_exchange, short_exchange, True)

    pos["status"] = "closing"

    try:
        qty_long = pos["qty_long"]
        qty_short = pos["qty_short"]

        success = True

        # Close long side
        if qty_long > 0:
            res_long = await place_market_order(long_exchange, symbol, "Sell", qty_long, reduce_only=True)
            await asyncio.sleep(1) # ensure exchange state is updated before size recheck
            remaining_long = await get_position_size(long_exchange, symbol)
            if remaining_long > 0:
                print(f"⚠️ Remaining {remaining_long} contracts on {long_exchange} after close. Retrying...")
                await place_market_order(long_exchange, symbol, "Sell", remaining_long, reduce_only=True)
                await asyncio.sleep(1) # ensure exchange state is updated before size recheck
                final_long = await get_position_size(long_exchange, symbol)
                if final_long > 0:
                    logger.error(f"[POSITION MANAGER] ❌ Failed to fully close LONG on {long_exchange} ({final_long} contracts remaining)")
                    success = False

        # Close short side
        if qty_short > 0:
            res_short = await place_market_order(short_exchange, symbol, "Buy", qty_short, reduce_only=True)
            await asyncio.sleep(0.5) # wait before verifying final position size
            remaining_short = await get_position_size(short_exchange, symbol)
            if remaining_short > 0:
                print(f"⚠️ Remaining {remaining_short} contracts on {short_exchange} after close. Retrying...")
                await place_market_order(short_exchange, symbol, "Buy", remaining_short, reduce_only=True)
                await asyncio.sleep(0.5) # wait before verifying final position size
                final_short = await get_position_size(short_exchange, symbol)
                if final_short > 0:
                    logger.error(f"[POSITION MANAGER] ❌ Failed to fully close SHORT on {short_exchange} ({final_short} contracts remaining)")
                    success = False

        if success:
            pos["status"] = "closed"
            pos["exit_time"] = datetime.now(UTC)
            pos["exit_reason"] = reason
            pos["start_reason"] = reason

            # --- Fetch final PnL of both sides ---
            pnl_long = await fetch_final_pnl(long_exchange, symbol, "long")
            pnl_short = await fetch_final_pnl(short_exchange, symbol, "short")

            pos["final_pnl_long"] = pnl_long
            pos["final_pnl_short"] = pnl_short
            pos["final_pnl_total"] = pnl_long + pnl_short
            pos["start_pnl"] = pos["final_pnl_long"] + pos["final_pnl_short"]

            logger.info(f"[POSITION MANAGER] ✅ Final total PnL: LONG={pnl_long:.4f} + SHORT={pnl_short:.4f} = {pos['final_pnl_total']:.4f} USD")

            from telegram_bot import send_message

            final_pnl_long = pos.get("final_pnl_long", Decimal("0"))
            final_pnl_short = pos.get("final_pnl_short", Decimal("0"))
            final_pnl = final_pnl_long + final_pnl_short

            if final_pnl != 0:
                pnl_text = (
                    f"Final total PnL (both sides): ${final_pnl:.4f}\n"
                    f"PnL LONG: ${final_pnl_long:.4f}\n"
                    f"PnL SHORT: ${final_pnl_short:.4f}"
                )
            else:
                pnl_text = (
                    f"⚠ Failed to fetch actual PnL (exchange may not have returned data). "
                    f"Showing estimated: ${pos.get('net_profit', Decimal('0')):.4f}"
                )

            await send_message(
                f"❌ <b>Position closed</b>\n"
                f"{symbol} | {long_exchange}/{short_exchange}\n"
                f"Reason: {reason}\n"
                f"{pnl_text}"
            )

        else:
            pos["status"] = "error"
            logger.warning(f"[POSITION MANAGER] ⚠️ Position {symbol} closed with errors. Needs review.")

    except Exception as e:
        logger.error(f"[POSITION MANAGER] ❌❌❌ Critical error closing position {symbol}: {e}")
        pos["status"] = "error"
        pos["error"] = str(e)

    finally:
        # Clear pending regardless
        clear_pending(symbol)
    update_position_result(pos)

# Register new position
def register_position(position: dict):
    pos_id = position["position_id"]
    open_positions[pos_id] = {
        **position,
        "status": "open",
        "entry_time": datetime.now(UTC),
        "last_price": {},
    }
    active_symbols.add(position["symbol"])
    logger.info(f"[POSITION MANAGER] ▶️ REGISTERED: {position['symbol']} | ID = {pos_id}")

    from telegram_bot import send_message
    asyncio.create_task(send_message(
        f"✅ <b>Position opened</b>\n"
        f"{position['symbol']} | {position['long_exchange']}/{position['short_exchange']}\n"
        f"Size: {POSITION_SIZE_USD} x{LEVERAGE}\n"
        f"PnL: $0.00"
    ))
    # Log the trade
    log_new_position(position)

def get_active_symbols() -> set[str]:
    return active_symbols

# Get all active positions
def get_open_positions() -> list[dict]:
    return [p for p in open_positions.values() if p["status"] == "open"]

def can_open_position(symbol: str, long_ex: str, short_ex: str) -> bool:
    for p in open_positions.values():
        if (
            p["status"] == "open" and
            p["symbol"] == symbol and
            p["long_exchange"] == long_ex and
            p["short_exchange"] == short_ex
        ):
            return False
    return True

# Close all positions on shutdown
async def close_all_positions():
    logger.warning("[POSITION MANAGER] ⛔ SHUTDOWN: closing all positions")

    # Close regular positions
    for pos_id in list(open_positions.keys()):
        pos = open_positions[pos_id]
        if pos["status"] == "open":
            await close_position(pos_id, reason="manual_shutdown")

    # Close failover positions
    from failover_manager import failover_positions, exit_position
    for pos_id in list(failover_positions.keys()):
        pos = failover_positions[pos_id]
        if pos.get("status") != "closed":
            print(f"[SHUTDOWN] Closing failover position {pos_id} ({pos['symbol']})...")
            await exit_position(pos_id, reason="manual_shutdown")

# Return position info for UI charts
def get_position_data_for_ui() -> list[dict]:
    return [
        {
            "position_id": p["position_id"],
            "symbol": p["symbol"],
            "long_exchange": p["long_exchange"],
            "short_exchange": p["short_exchange"],
            "entry_prices": p.get("entry_prices", {}),
            "current_prices": p.get("last_price", {}),
            "TP": TAKE_PROFIT_THRESHOLD,
            "SL": "on_exchange",
            "PnL": p.get("net_profit", Decimal("0")),
            "status": p["status"],
            "opened_at": p["entry_time"].isoformat(),
        }
        for p in open_positions.values() if p["status"] == "open"
    ]

def is_pending_open(symbol: str, long_ex: str, short_ex: str) -> bool:
    return (symbol, long_ex, short_ex) in pending_positions

def set_pending_open(symbol: str, long_ex: str, short_ex: str, state: bool) -> None:
    key = (symbol, long_ex, short_ex)
    if state:
        pending_positions.add(key)
    else:
        pending_positions.discard(key)

def clear_pending(symbol: str) -> None:
    # Clear all pending states for symbol, just in case
    for item in list(pending_positions):
        if item[0] == symbol:
            pending_positions.discard(item)