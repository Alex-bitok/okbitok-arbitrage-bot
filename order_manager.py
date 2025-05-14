import asyncio
import aiohttp
from logger import logger
import uuid
import time
import hmac
import hashlib
import base64
from decimal import Decimal, getcontext
from config_manager import get_config_value
import json
from symbol_specs import get_specs, round_step
from hashlib import sha256
from profit_simulator import FEE_TAKER_BYBIT, FEE_TAKER_KUCOIN

getcontext().prec = 18

# Configuration — no defaults. Should raise if missing
POSITION_SIZE_USD = Decimal(get_config_value("POSITION_SIZE_USD"))
LEVERAGE = Decimal(get_config_value("LEVERAGE"))
ORDER_TIMEOUT_SEC = int(get_config_value("ORDER_TIMEOUT_SEC"))

API_KEYS = {
    "Bybit": {
        "key": get_config_value("BYBIT_KEY"),
        "secret": get_config_value("BYBIT_SECRET"),
    },
    "KuCoin": {
        "key": get_config_value("KUCOIN_KEY"),
        "secret": get_config_value("KUCOIN_SECRET"),
        "passphrase": get_config_value("KUCOIN_PASSPHRASE"),
    }
}

# TODO: add unit tests for quantity calculation logic
def calculate_quantity(price: Decimal, exchange: str, symbol: str) -> float:
    raw_qty = None

    symbol_for_specs = symbol + "M" if exchange == "KuCoin" and not symbol.endswith("M") else symbol
    specs = get_specs(exchange, symbol_for_specs)
    if not specs:
        raise ValueError(f"[QTY ERROR] No specs for {exchange} {symbol_for_specs}")

    step = specs.get("step_qty", Decimal("0.01"))
    contract_value = specs.get("contract_value", Decimal("1"))

    if exchange == "KuCoin":
        # contracts = usd * leverage / (price * contract value in FLM)
        raw_qty = (POSITION_SIZE_USD * LEVERAGE) / (price * contract_value)
    else:
        raw_qty = (POSITION_SIZE_USD * LEVERAGE) / price

    qty = round_step(Decimal(raw_qty), step)

    # DEBUG: quantity calculation details (enable if needed)
    # print(f"[DEBUG QTY] {symbol=} | {exchange=} | {price=} | contract_value={contract_value} → raw_qty={raw_qty} | step={step} | final_qty={qty} | final_cost={qty * price * contract_value}")
    return float(qty)

def sign_bybit_request(api_key: str, api_secret: str, method: str = "POST", path_or_body: str = "") -> dict:
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"

    if method.upper() == "GET":
        sign_str = timestamp + api_key + recv_window + path_or_body
    else:
        sign_str = timestamp + api_key + recv_window + path_or_body

    signature = hmac.new(api_secret.encode(), sign_str.encode(), hashlib.sha256).hexdigest()

    return {
        "X-BAPI-API-KEY": api_key,
        "X-BAPI-TIMESTAMP": timestamp,
        "X-BAPI-RECV-WINDOW": recv_window,
        "X-BAPI-SIGN": signature,
        "X-BAPI-SIGN-TYPE": "2",
        "Content-Type": "application/json"
    }

def sign_kucoin_request(api_key: str, api_secret: str, passphrase: str, method: str, endpoint: str, body: str = "") -> dict:
    if isinstance(body, dict):
        body_str = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
    else:
        body_str = body or ""

    now = str(int(time.time() * 1000))
    str_to_sign = now + method.upper() + endpoint + body_str
    signature = base64.b64encode(hmac.new(api_secret.encode(), str_to_sign.encode(), hashlib.sha256).digest()).decode()
    passphrase_signed = base64.b64encode(hmac.new(api_secret.encode(), passphrase.encode(), hashlib.sha256).digest()).decode()
    
    # DEBUG: KuCoin sign string
    # print(f"[KUCOIN SIGN] origin_string = {str_to_sign}")

    return {
        "KC-API-KEY": api_key,
        "KC-API-SIGN": signature,
        "KC-API-TIMESTAMP": now,
        "KC-API-PASSPHRASE": passphrase_signed,
        "KC-API-KEY-VERSION": "2",
        "Content-Type": "application/json"
    }

async def place_market_order(exchange: str, symbol: str, side: str, qty: float, reduce_only: bool = False) -> dict:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:

            if exchange == "Bybit":
                url = "https://api.bybit.com/v5/order/create"
                data = {
                    "category": "linear",
                    "symbol": symbol,
                    "side": side,
                    "orderType": "Market",
                    "qty": str(qty),
                    "timeInForce": "FillOrKill",
                    "reduceOnly": reduce_only
                    
                }
                body_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)

                headers = sign_bybit_request(
                    API_KEYS["Bybit"]["key"],
                    API_KEYS["Bybit"]["secret"],
                    method="POST",
                    path_or_body=body_str
                )

                async with session.post(url, headers=headers, data=body_str) as resp:
                    result = await resp.json()
                    logger.info(f"[ORDER] ✅ Bybit {side} {symbol} result: {result}")
                    logger.info(f"[POSITION OPEN] {symbol} | {exchange} | Side = {side} | Qty = {qty}")
                    return {"success": True, "exchange": exchange, "side": side, "qty": qty, "symbol": symbol, "response": result}

            elif exchange == "KuCoin":
                if not symbol.endswith("M"):
                    symbol += "M"
                url_path = "/api/v1/orders"
                url = f"https://api-futures.kucoin.com{url_path}"
                data = {
                    "clientOid": str(uuid.uuid4()),
                    "symbol": symbol,
                    "side": side.lower(),
                    "type": "market", 
                    "size": str(int(qty)),
                    "leverage": str(int(LEVERAGE)),                    
                }

                if reduce_only:
                    data["closeOrder"] = True  # ← tells KuCoin this is a close order, not a new entry


                body_str = json.dumps(data, separators=(',', ':'), ensure_ascii=False)

                headers = sign_kucoin_request(
                    API_KEYS["KuCoin"]["key"],
                    API_KEYS["KuCoin"]["secret"],
                    API_KEYS["KuCoin"]["passphrase"],
                    "POST",
                    url_path,
                    body_str  # ← already serialized string passed here
                )


                async with session.post(url, headers=headers, data=body_str) as resp:
                    result = await resp.json()
                    logger.info(f"[ORDER] ✅ KuCoin {side} {symbol} result: {result}")
                    logger.info(f"[POSITION OPEN] {symbol} | {exchange} | Side = {side} | Qty = {qty}")
                    return {"success": True, "exchange": exchange, "side": side, "qty": qty, "symbol": symbol, "response": result}


    except Exception as e:
        logger.warning(f"[ORDER] {exchange} {side} order failed for {symbol}: {e}")
        return None

async def get_position_size(exchange: str, symbol: str) -> float:
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            if exchange == "Bybit":
                url = f"https://api.bybit.com/v5/position/list?category=linear&symbol={symbol}"
                query_string = f"category=linear&symbol={symbol}"
                headers = sign_bybit_request(
                    API_KEYS["Bybit"]["key"],
                    API_KEYS["Bybit"]["secret"],
                    method="GET",
                    path_or_body=query_string
                )
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    positions = data.get("result", {}).get("list", [])
                    if positions:
                        return abs(float(positions[0].get("size", 0)))
                    return 0.0

            elif exchange == "KuCoin":
                if not symbol.endswith("M"):
                    symbol += "M"
                url = f"https://api-futures.kucoin.com/api/v1/position?symbol={symbol}"
                headers = sign_kucoin_request(
                    API_KEYS["KuCoin"]["key"],
                    API_KEYS["KuCoin"]["secret"],
                    API_KEYS["KuCoin"]["passphrase"],
                    "GET",
                    f"/api/v1/position?symbol={symbol}"
                )
                async with session.get(url, headers=headers) as resp:
                    data = await resp.json()
                    position_data = data.get("data")
                    if position_data:
                        return abs(float(position_data.get("currentQty", 0)))
                    return 0.0

    except Exception as e:
        logger.error(f"[GET POSITION SIZE] Error fetching position size for {exchange} {symbol}: {e}")
        return 0.0

async def execute_order(arb: dict) -> bool:
    symbol = arb["symbol"]
    long_ex = arb["long_exchange"]
    short_ex = arb["short_exchange"]
    long_price = Decimal(str(arb["long_avg_price"]))
    short_price = Decimal(str(arb["short_avg_price"]))

    qty_long = calculate_quantity(long_price, long_ex, symbol)
    qty_short = calculate_quantity(short_price, short_ex, symbol)

    async def order_long():
        return await place_market_order(long_ex, symbol, "Buy", qty_long)

    async def order_short():
        return await place_market_order(short_ex, symbol, "Sell", qty_short)

    try:
        task_long = asyncio.create_task(order_long())
        task_short = asyncio.create_task(order_short())

        done, pending = await asyncio.wait(
            [task_long, task_short],
            timeout=ORDER_TIMEOUT_SEC
        )

        if len(done) < 2:
            for task in pending:
                task.cancel()

            results = [t.result() for t in done if t.done() and t.result() is not None]
            if results:
                filled = results[0]
                opposite_side = "Sell" if filled["side"] == "Buy" else "Buy"
                logger.warning(f"[ORDER_MANAGER] TIMEOUT: Canceling filled side {filled['exchange']} with opposite order")
                reversed_price = long_price if filled["side"] == "Sell" else short_price
                reversed_qty = calculate_quantity(reversed_price, filled["exchange"], filled["symbol"])
                await place_market_order(filled["exchange"], filled["symbol"], opposite_side, reversed_qty)

            arb["exit_reason"] = "order_timeout"
            return False

        results = [t.result() for t in done]

        valid_results = []
        for r in results:
            if r and r.get("success"):
                if "retCode" in r["response"] and r["response"].get("retCode", 0) == 0:
                    valid_results.append(r)
                elif "code" in r["response"] and str(r["response"].get("code")) == "200000":
                    valid_results.append(r)

        # If both sides succeeded — all good
        if len(valid_results) == 2:
            arb["entry_time"] = uuid.uuid4().hex
            arb["position_id"] = uuid.uuid4().hex
            logger.info(f"[ORDER_MANAGER] ✅ EXECUTED: {symbol}, position_id={arb['position_id']}")

            from position_manager import register_position  # import should be at the top

            # Gather data to register position
            entry_prices = {
                long_ex: long_price,
                short_ex: short_price
            }
            qty_long = calculate_quantity(long_price, long_ex, symbol)
            qty_short = calculate_quantity(short_price, short_ex, symbol)

            symbol_long = symbol if long_ex == "Bybit" else symbol + "M"
            symbol_short = symbol if short_ex == "Bybit" else symbol + "M"

            contract_value_long = get_specs(long_ex, symbol_long)["contract_value"]
            contract_value_short = get_specs(short_ex, symbol_short)["contract_value"]

            real_notional_long = Decimal(str(qty_long)) * long_price * contract_value_long
            real_notional_short = Decimal(str(qty_short)) * short_price * contract_value_short
            avg_position_notional = (real_notional_long + real_notional_short) / 2

            qty = min(qty_long, qty_short)
            entry_fee_long = (FEE_TAKER_BYBIT if long_ex == "Bybit" else FEE_TAKER_KUCOIN) * POSITION_SIZE_USD * LEVERAGE
            entry_fee_short = (FEE_TAKER_BYBIT if short_ex == "Bybit" else FEE_TAKER_KUCOIN) * POSITION_SIZE_USD * LEVERAGE
            entry_fee = entry_fee_long + entry_fee_short

            position_data = {
                "position_id": arb["position_id"],
                "symbol": symbol,
                "long_exchange": long_ex,
                "short_exchange": short_ex,
                "entry_prices": entry_prices,
                "qty": Decimal(str(qty)),  # keep qty for compatibility
                "qty_long": Decimal(str(qty_long)),
                "qty_short": Decimal(str(qty_short)),
                "entry_fee": entry_fee,
                "position_notional": avg_position_notional,
                "funding": Decimal(str(arb.get("funding", {}).get("long", {}).get("cost", 0))) +
                           Decimal(str(arb.get("funding", {}).get("short", {}).get("cost", 0)))
            }
            register_position(position_data)

            return True

        # If only one side succeeded — apply failsafe
        if len(valid_results) == 1:
            filled = valid_results[0]
            opposite_side = "Sell" if filled["side"] == "Buy" else "Buy"
            logger.warning(f"[ORDER_MANAGER] FAILSAFE: reversing {filled['exchange']} {opposite_side} {filled['symbol']}")

            reverse_price = long_price if filled["side"] == "Sell" else short_price
            response_data = filled.get("response", {}).get("result", {})

            executed_qty = (
                response_data.get("qty") or
                response_data.get("origQty") or
                response_data.get("size") or
                filled.get("qty")
            )

            if executed_qty is None:
                logger.warning(f"[ORDER_MANAGER] Could not determine executed qty for {filled['exchange']} {filled['symbol']}. Using fallback 0.")
                executed_qty = 0

            await place_market_order(
                filled["exchange"],
                filled["symbol"],
                opposite_side,
                executed_qty,
                reduce_only=True
            )


        arb["exit_reason"] = "order_error"
        return False
  
    except Exception as e:
        logger.exception(f"[ORDER_MANAGER] Critical error on order execution: {e}")
        arb["exit_reason"] = "order_exception"
        return False
