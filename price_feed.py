import asyncio
import csv
import json
from logger import logger
import websockets
import aiohttp
from datetime import datetime, UTC
from pathlib import Path
from typing import Dict, List

# Queue for sending data to pair_monitor
price_queue: asyncio.Queue = asyncio.Queue()

# Path to CSV
CSV_PATH = Path("data/matched_pairs_enriched_filtered.csv")

# Interface for Bybit
class BybitWSClient:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.ws_url = "wss://stream.bybit.com/v5/public/linear"
        self.max_symbols_per_ws = 100
        self.connections = []  # WebSocket task list
        self.last_quotes = {}  # symbol -> {'bid': float, 'ask': float}

    async def connect(self):
        symbol_chunks = self.split_symbols(self.symbols, self.max_symbols_per_ws)
        for chunk in symbol_chunks:
            task = asyncio.create_task(self.connect_chunk(chunk))
            self.connections.append(task)
        await asyncio.gather(*self.connections)

    def split_symbols(self, symbols: List[str], max_per_chunk: int) -> List[List[str]]:
        return [symbols[i:i + max_per_chunk] for i in range(0, len(symbols), max_per_chunk)]

    async def connect_chunk(self, chunk: List[str]):
        while True:
            try:
                ws = await asyncio.wait_for(websockets.connect(self.ws_url), timeout=10)
                await self.subscribe(ws, chunk)
                logger.info(f"[BYBIT] Subscribed to {len(chunk)} symbols")
                await self.handle_messages(ws)
            except Exception as e:
                logger.warning(f"[BYBIT] WS connection failed for chunk {chunk[:3]}... ({len(chunk)} symbols). Retrying in 5s. Reason: {e}")
                await asyncio.sleep(5)

    async def subscribe(self, ws, chunk: List[str]):
        args = [f"tickers.{symbol}" for symbol in chunk]
        sub_msg = {
            "op": "subscribe",
            "args": args
        }
        await ws.send(json.dumps(sub_msg))

    async def handle_messages(self, ws):
        async for message in ws:
            try:
                data = json.loads(message)
                if "data" not in data:
                    continue
                await self.parse_message(data)
            except Exception as e:
                logger.exception(f"Error handling Bybit message: {e}")

    async def parse_message(self, msg: Dict):
        symbol = msg["data"]["symbol"]
        prev = self.last_quotes.get(symbol, {})

        bid = float(msg["data"].get("bid1Price") or prev.get("bid", 0))
        ask = float(msg["data"].get("ask1Price") or prev.get("ask", 0))

        self.last_quotes[symbol] = {'bid': bid, 'ask': ask}

        timestamp = datetime.now(UTC).isoformat()

        payload = {
            "pair_id": f"{symbol}-Bybit",
            "exchange": "Bybit",
            "bid": bid,
            "ask": ask,
            "timestamp": timestamp,
        }

        await price_queue.put(payload)
        # logger.info(f"[BYBIT] {symbol}: bid={bid:.8f}, ask={ask:.8f} @ {timestamp}")

# Interface for KuCoin
class KuCoinWSClient:
    def __init__(self, symbols: List[str]):
        self.symbols = symbols
        self.token = None
        self.endpoint = None
        self.max_symbols_per_ws = 100
        self.connections = []

    async def connect(self):
        symbol_chunks = self.split_symbols(self.symbols, self.max_symbols_per_ws)
        for chunk in symbol_chunks:
            task = asyncio.create_task(self.connect_chunk(chunk))
            self.connections.append(task)
        await asyncio.gather(*self.connections)

    def split_symbols(self, symbols: List[str], max_per_chunk: int) -> List[List[str]]:
        return [symbols[i:i + max_per_chunk] for i in range(0, len(symbols), max_per_chunk)]

    async def get_ws_token(self):
        url = "https://api-futures.kucoin.com/api/v1/bullet-public"
        async with aiohttp.ClientSession() as session:
            async with session.post(url) as resp:
                res = await resp.json()
                self.token = res["data"]["token"]
                self.endpoint = res["data"]["instanceServers"][0]["endpoint"]

    async def connect_chunk(self, chunk: List[str]):
        while True:
            try:
                await self.get_ws_token()
                ws_url = f"{self.endpoint}?token={self.token}"
                ws = await asyncio.wait_for(websockets.connect(ws_url, ping_interval=None), timeout=10)
                asyncio.create_task(self.ws_ping(ws))
                await self.subscribe(ws, chunk)
                logger.info(f"[KUCOIN] Subscribed to {len(chunk)} symbols")
                await self.handle_messages(ws)
            except Exception as e:
                logger.warning(f"[KUCOIN] WS connection failed for chunk {chunk[:3]}... Retrying. Reason: {e}")
                await asyncio.sleep(5)

    async def subscribe(self, ws, chunk: List[str]):
        for i, symbol in enumerate(chunk):
            sub_msg = {
                "id": f"sub-{symbol}",
                "type": "subscribe",
                "topic": f"/contractMarket/tickerV2:{symbol}",
                "privateChannel": False,
                "response": True
            }
            await ws.send(json.dumps(sub_msg))
            if i % 50 == 0 and i != 0:
                await asyncio.sleep(1)
            else:
                await asyncio.sleep(0.01)

    async def handle_messages(self, ws):
        async for message in ws:
            try:
                data = json.loads(message)
                if not isinstance(data, dict) or "data" not in data or not isinstance(data["data"], dict):
                    continue
                await self.parse_message(data)
            except Exception as e:
                logger.exception(f"Error handling KuCoin message: {e}")

    async def parse_message(self, msg: Dict):
        symbol = msg["data"]["symbol"]
        bid = float(msg["data"].get("bestBidPrice", 0))
        ask = float(msg["data"].get("bestAskPrice", 0))
        timestamp = datetime.now(UTC).isoformat()

        payload = {
            "pair_id": f"{symbol}-KuCoin",
            "exchange": "KuCoin",
            "bid": bid,
            "ask": ask,
            "timestamp": timestamp,
        }

        await price_queue.put(payload)
        # logger.info(f"[KUCOIN] {symbol}: bid={bid:.8f}, ask={ask:.8f} @ {timestamp}")

    async def ws_ping(self, ws):
        while True:
            try:
                await ws.ping()
                await asyncio.sleep(15)
            except Exception as e:
                logger.warning(f"[KUCOIN] WebSocket ping failed: {e}")
                break
            
# Load pairs from CSV
def load_bybit_symbols() -> List[str]:
    symbols = set()
    with open(CSV_PATH, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row.get("bybit_symbol"):
                symbols.add(row["bybit_symbol"].strip())
    return list(symbols)

def load_kucoin_symbols() -> List[str]:
    symbols = set()
    with open(CSV_PATH, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            if row.get("kucoin_symbol"):
                symbols.add(row["kucoin_symbol"].strip())
    return list(symbols)

# Entry point
async def main():
    bybit_symbols = load_bybit_symbols()
    kucoin_symbols = load_kucoin_symbols()
    logger.info(f"Loaded {len(bybit_symbols)} Bybit symbols and {len(kucoin_symbols)} KuCoin symbols from CSV.")

    bybit_client = BybitWSClient(bybit_symbols)
    kucoin_client = KuCoinWSClient(kucoin_symbols)

    await asyncio.gather(
        bybit_client.connect(),
        kucoin_client.connect(),
    )

if __name__ == "__main__":
    asyncio.run(main())
