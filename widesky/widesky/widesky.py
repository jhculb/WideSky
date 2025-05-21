"""WideSky Firehose Listener and Main Loop"""

import os
import asyncio
import signal
import logging
import queue

import websockets

from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener

from widesky_processor import WideSkyProcessor

FIREHOSE_URL = "wss://bsky.network/xrpc/com.atproto.sync.subscribeRepos"
FIREHOSE_RECONNECT_BASE_DELAY = 5
FIREHOSE_RECONNECT_MAX_DELAY = 60
FIREHOSE_RECONNECT_MULT_BASE = 2
FIREHOSE_RECONNECT_MAX_RECONNECTS = 5

LOG_DIR = "/app/logs"


async def firehose_listener(processor) -> None:
    reconnect_attempt = 0
    while True:
        try:
            logging.info("Connecting to Bluesky firehose...")
            async with websockets.connect(
                FIREHOSE_URL, ping_interval=5, ping_timeout=10, close_timeout=5
            ) as ws:
                logging.info("Connected to the Bluesky Firehose.")

                while True:
                    msg = await ws.recv()
                    processor.process_message(msg)

        except websockets.ConnectionClosed as e:
            logging.warning(f"WebSocket connection closed: {e}")
        except Exception as e:
            logging.exception(f"Unexpected error in firehose listener: {e}")

        reconnect_delay = min(
            FIREHOSE_RECONNECT_BASE_DELAY
            * (FIREHOSE_RECONNECT_MULT_BASE**reconnect_attempt),
            FIREHOSE_RECONNECT_MAX_DELAY,
        )
        reconnect_attempt += 1
        if reconnect_attempt < FIREHOSE_RECONNECT_MAX_RECONNECTS:
            logging.warning(
                f"Reconnect attempt {reconnect_attempt} Reconnecting in {reconnect_delay:.1f} seconds..."
            )
            await asyncio.sleep(reconnect_delay)
        else:
            logging.exception(
                f"Maximum reconnect attempts ({FIREHOSE_RECONNECT_MAX_RECONNECTS}) reached, exiting."
            )
            break


async def shutdown_handler() -> None:
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_event_loop().add_signal_handler(sig, shutdown_event.set)


async def main() -> None:
    await shutdown_handler()

    processor = WideSkyProcessor()
    await processor.create()

    listener_task = asyncio.create_task(firehose_listener(processor))

    await shutdown_event.wait()

    listener_task.cancel()

    try:
        await listener_task
    except asyncio.CancelledError:
        logging.info("Firehose listener cancelled.")

    await processor.close()
    logging.info("Shut down gracefully.")


def setup_logging() -> QueueListener:
    os.makedirs(LOG_DIR, exist_ok=True)
    log_file_path = os.path.join(LOG_DIR, "widesky.log")

    log_queue = queue.Queue()
    queue_handler = QueueHandler(log_queue)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(queue_handler)

    handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)

    listener = QueueListener(log_queue, handler)
    listener.start()

    return listener


if __name__ == "__main__":
    listener = setup_logging()
    shutdown_event = asyncio.Event()

    try:
        asyncio.run(main())
    finally:
        listener.stop()
