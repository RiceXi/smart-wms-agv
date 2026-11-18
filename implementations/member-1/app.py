from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from wms_agv.api import create_server
from wms_agv.service import WmsService


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SmartWMS member-1 local server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--db", default=str(ROOT / "data" / "wms.sqlite"), help="SQLite file location"
    )
    parser.add_argument(
        "--agvs", type=int, default=10, help="Number of demo AGVs (up to map capacity)"
    )
    parser.add_argument(
        "--no-seed", action="store_true", help="Do not create demo data for an empty DB"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    service = WmsService(
        Path(args.db), seed_demo=not args.no_seed, agv_count=args.agvs
    )
    server = create_server(service, args.host, args.port, ROOT / "static")

    def shutdown(*_args) -> None:
        # HTTPServer.shutdown must run outside serve_forever's thread.
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    print(f"SmartWMS running at http://{args.host}:{args.port}")
    print(f"SQLite data: {Path(args.db).resolve()}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        service.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
