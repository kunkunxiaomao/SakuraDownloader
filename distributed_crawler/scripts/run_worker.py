from __future__ import annotations

import argparse
import asyncio
import logging

from crawler.config import load_settings
from crawler.worker import WorkerNode


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--worker-id", required=False)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings(args.config)
    if args.worker_id:
        settings.worker.worker_id = args.worker_id

    node = WorkerNode(settings)
    await node.start()
    logging.getLogger(__name__).info("Worker started: %s", settings.worker.worker_id)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await node.close()


if __name__ == "__main__":
    asyncio.run(main())
