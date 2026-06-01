from __future__ import annotations

import argparse
import asyncio
import logging

from aiohttp import web

from crawler.config import load_settings
from crawler.master import MasterNode


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = load_settings(args.config)
    node = MasterNode(settings)
    await node.start()

    runner = web.AppRunner(node.app)
    await runner.setup()
    site = web.TCPSite(runner, settings.master.host, settings.master.port)
    await site.start()
    logging.getLogger(__name__).info("Master started at http://%s:%s", settings.master.host, settings.master.port)

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
        await node.close()


if __name__ == "__main__":
    asyncio.run(main())
