from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from pathlib import Path

from wms_agv.api import create_server
from wms_agv.observability import JsonLogger
from wms_agv.service import WmsService


ROOT = Path(__file__).resolve().parent
VERSION = os.getenv("SMARTWMS_VERSION", "1.0.0")


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return default if value is None else int(value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartWMS system foundation server")
    parser.add_argument("--version", action="version", version=VERSION)
    parser.add_argument("--host", default=os.getenv("SMARTWMS_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=env_int("SMARTWMS_PORT", 8000)
    )
    parser.add_argument(
        "--db",
        default=os.getenv("SMARTWMS_DB", str(ROOT / "data" / "wms.sqlite")),
        help="SQLite file location",
    )
    parser.add_argument(
        "--agvs",
        type=int,
        default=env_int("SMARTWMS_AGVS", 10),
        help="Number of demo AGVs (up to map capacity)",
    )
    parser.add_argument(
        "--map-width", type=int, default=env_int("SMARTWMS_MAP_WIDTH", 0) or None
    )
    parser.add_argument(
        "--map-height", type=int, default=env_int("SMARTWMS_MAP_HEIGHT", 0) or None
    )
    parser.add_argument(
        "--log-level",
        choices=("debug", "info", "warning", "error"),
        default=os.getenv("SMARTWMS_LOG_LEVEL", "info").lower(),
    )
    parser.add_argument(
        "--no-seed", action="store_true", help="Do not create demo data for an empty DB"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = JsonLogger(args.log_level)
    service = WmsService(
        Path(args.db),
        seed_demo=not args.no_seed,
        agv_count=args.agvs,
        map_width=args.map_width,
        map_height=args.map_height,
    )
    server = create_server(
        service,
        args.host,
        args.port,
        ROOT / "static",
        logger=logger,
        version=VERSION,
    )

    def shutdown(*_args) -> None:
        # HTTPServer.shutdown must run outside serve_forever's thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    logger.info(
        "app.started",
        version=VERSION,
        address=f"http://{args.host}:{args.port}",
        database=str(Path(args.db).resolve()),
        agvs=args.agvs,
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.stop()
        logger.info("app.stopped", version=VERSION)
    return 0


if __name__ == "__main__":
    sys.exit(main())
