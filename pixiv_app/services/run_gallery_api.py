from __future__ import annotations

import argparse
import logging

from pixiv_app.services.gallery_api import GalleryApiServer


def main() -> None:
    parser = argparse.ArgumentParser(description="Run local Sakura gallery API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="pixiv_app_library.db")
    parser.add_argument("--thumbnail-dir", default="runtime/thumbnails")
    parser.add_argument("--web-root", default="")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Local gallery API listening on http://%s:%s", args.host, args.port)
    GalleryApiServer(
        host=args.host,
        port=args.port,
        db_path=args.db,
        thumbnail_dir=args.thumbnail_dir,
        web_root=args.web_root or None,
    ).serve_forever()


if __name__ == "__main__":
    main()
