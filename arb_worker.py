from logger import logger
from pair_monitor import arb_queue, arb_pipeline

async def arb_worker(worker_id: int):
    while True:
        arb = await arb_queue.get()
        try:
            # logger.info(f"[WORKER {worker_id}] Processing arb: {arb['symbol']}")   # debug print
            await arb_pipeline(arb)
        except Exception as e:
            logger.exception(f"[WORKER {worker_id}] Error: {e}")
