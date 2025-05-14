from logger import logger
from decimal import Decimal, getcontext
import asyncio
import time
from config_manager import get_config_value

getcontext().prec = 18

arb_candidates = []
BATCH_TIME_SEC = 0.5
last_batch_time = time.time()

# Configuration
POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD", "100"))
LEVERAGE = Decimal(get_config_value("LEVERAGE", "3"))
FEE_TAKER_BYBIT = Decimal(get_config_value("FEE_TAKER_BYBIT", "0.0006"))
FEE_TAKER_KUCOIN = Decimal(get_config_value("FEE_TAKER_KUCOIN", "0.0006"))

async def simulate_profit(arb: dict) -> None:
    try:
        symbol = arb["symbol"]
        long_ex = arb["long_exchange"]
        short_ex = arb["short_exchange"]
        long_price = Decimal(str(arb["long_avg_price"]))
        short_price = Decimal(str(arb["short_avg_price"]))

        position_value = POSITION_SIZE_USD * LEVERAGE

        # Select fee by exchange
        fee_long = position_value * (FEE_TAKER_BYBIT if long_ex == "Bybit" else FEE_TAKER_KUCOIN)
        fee_short = position_value * (FEE_TAKER_BYBIT if short_ex == "Bybit" else FEE_TAKER_KUCOIN)
        total_fees = Decimal("2") * (fee_long + fee_short)  # entry + exit

        # Funding cost
        funding_long = Decimal(str(arb["funding"]["long"]["cost"]))
        funding_short = Decimal(str(arb["funding"]["short"]["cost"]))

        INCLUDE_FUNDING = get_config_value("INCLUDE_FUNDING_IN_PROFIT", "true").lower() == "true"

        if INCLUDE_FUNDING:
            total_funding = funding_long + funding_short
        else:
            total_funding = Decimal("0")

        # Gross profit (without fees/funding)
        gross_profit = (short_price - long_price) * (position_value / long_price)

        # Net profit
        net_profit = gross_profit - total_fees - total_funding
        profit_percent = (net_profit / position_value) * Decimal("100")

        # Write to arb
        arb["net_profit"] = round(net_profit, 4)
        arb["profit_percent"] = round(profit_percent, 2)
        arb["total_fees"] = round(total_fees, 4)
        arb["total_funding"] = round(total_funding, 4)

        logger.info(f"[PROFIT SIMULATOR] {symbol}: Net Profit = ${net_profit:.2f} ({profit_percent:.2f}%)")
        # --- ADD TO CANDIDATES ---
        global arb_candidates, last_batch_time
    
        net_profit = Decimal(str(arb.get("net_profit", "0")))
        if net_profit > 0:
            arb_candidates.append(arb)

        # --- If more than BATCH_TIME_SEC passed — pick the best ---
        if time.time() - last_batch_time >= BATCH_TIME_SEC:
            if arb_candidates:
                best_arb = max(arb_candidates, key=lambda x: Decimal(str(x.get("net_profit", "0"))))
                logger.info(
                    f"[PROFIT SIMULATOR] Best arbitrage in batch: {best_arb['symbol']} | "
                    f"Net Profit = ${best_arb['net_profit']:.4f} ({best_arb.get('profit_percent', 0):.2f}%)"
                )

                from signal_engine import process_signal
                asyncio.create_task(process_signal(best_arb))

            arb_candidates = []
            last_batch_time = time.time()

    except Exception as e:
        logger.warning(f"[PROFIT SIMULATOR] Error for {arb.get('symbol', '???')}: {e}")
