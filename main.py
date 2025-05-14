import asyncio
from logger import logger
from symbol_specs import init_symbol_specs
from price_feed import main as price_feed_main
from pair_monitor import monitor_loop
from arb_worker import arb_worker
from position_manager import close_all_positions
from position_manager import _position_stop_loss_check_loop
from datetime import datetime, UTC
import failover_manager
from telegram_bot import telegram_bot_runner, send_message, get_stop_event
from balance_watchdog import balance_watchdog_loop

NUM_WORKERS = 3  # or more or less))
 
async def dev_main():
    await init_symbol_specs()

    telegram_task = asyncio.create_task(telegram_bot_runner())

    task1 = asyncio.create_task(price_feed_main())
    task2 = asyncio.create_task(monitor_loop())
    workers = [asyncio.create_task(arb_worker(i)) for i in range(NUM_WORKERS)]
    heartbeat_task = asyncio.create_task(heartbeat())
    stop_loss_task = asyncio.create_task(_position_stop_loss_check_loop())
    failover_task = asyncio.create_task(failover_manager._check_positions_loop())
    balance_watchdog_task = asyncio.create_task(balance_watchdog_loop())

    all_tasks = [task1, task2, *workers, heartbeat_task, stop_loss_task, failover_task, balance_watchdog_task, telegram_task]

    stop_event = get_stop_event()

    async def monitor_stop():
        await stop_event.wait()
        logger.info("🛑 Stop signal received from Telegram. Shutting down...")
        for task in all_tasks:
            task.cancel()

    asyncio.create_task(monitor_stop())

    try:
        await asyncio.gather(*all_tasks)
    except asyncio.CancelledError:
        logger.info("\n🧹 Ctrl+C caught. Starting graceful shutdown...")
        try:
            # Step 1. Try to close all positions
            await close_all_positions()
            logger.info("✅ All active positions successfully closed.")

            # Step 2. Give exchanges time to process
            await asyncio.sleep(2)

        except Exception as e:
            logger.warning(f"❌ Ошибка при закрытии позиций: {e}")

        finally:
            # Step 3. Cancel all tasks
            logger.info("⏳ Останавливаем все фоновые процессы...")
            from telegram_bot import send_message
            await send_message("🛑 Bot stopped. Closing all positions.")            
            for task in all_tasks:
                task.cancel()

            # Step 4. Wait for all tasks to complete
            results = await asyncio.gather(*all_tasks, return_exceptions=True)

            # Step 5. Suppress CancelledError to keep console clean
            for r in results:
                if isinstance(r, Exception) and not isinstance(r, asyncio.CancelledError):
                    logger.warning(f"⚠️ Error in task during shutdown: {r}")

            logger.info("🏁 Bot shut down cleanly. See you next time!")

async def heartbeat():
    while True:
        try:
            await asyncio.sleep(30)
            logger.info(f"[HEARTBEAT] Still alive at {datetime.now(UTC).isoformat()}")
        except Exception as e:
            logger.warning(f"[HEARTBEAT] Error in heartbeat: {e}")

if __name__ == "__main__":
    try:
        asyncio.run(dev_main())
    except KeyboardInterrupt:
        logger.info("\n❗ Forced termination of the program.")
        try:
            from telegram_bot import send_message
            asyncio.run(send_message("🛑 Bot manually stopped (Ctrl+C)."))
        except:
            pass
