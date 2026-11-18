"""命令行入口。

```text
python -m wms serve --port 8080            # 启动 HTTP 服务（含前端页面）
python -m wms seed --scenario peak-10      # 导入固定场景
python -m wms demo --scenario single-agv --ticks 200
python -m wms export-fixtures              # 导出地图与场景 JSON
python -m wms bench --scenario peak-30     # 采集性能数据
```
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config
from .app import FIXTURES_DIR, App
from .mapgen import build_map
from .scenarios import SCENARIOS, list_scenarios


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    app = App(data_dir=args.data_dir)
    serve(app, host=args.host, port=args.port, open_browser=False)
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    app = App(data_dir=args.data_dir)
    snapshot = app.reset(args.scenario, args.seed)
    print(f"已导入场景 {args.scenario}（seed={args.seed}）：")
    print(f"  地图版本 {snapshot['mapVersion']}，AGV {len(snapshot['agvs'])} 台，"
          f"任务 {len(snapshot['tasks'])} 个，订单 {len(snapshot['orders'])} 个")
    print(f"  数据文件：{app.db_path}")
    app.close()
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """无界面跑一段仿真，打印关键事件与统计，用于回归与演示记录。"""
    app = App(data_dir=args.data_dir)
    app.reset(args.scenario, args.seed)
    started = time.perf_counter()
    for _ in range(args.ticks):
        app.sim.step_once()
    elapsed = time.perf_counter() - started
    snapshot = app.sim.snapshot()
    events = app.db.query("SELECT type, tick, aggregate_id FROM events ORDER BY seq")
    key_events = [
        {"type": e["type"], "tick": e["tick"], "aggregateId": e["aggregate_id"]}
        for e in events
        if e["type"] in (
            "order.released", "task.created", "task.assigned", "task.started", "task.completed",
            "task.failed", "task.cancelled", "conflict.detected", "deadlock.detected",
            "deadlock.resolved", "rollback.executed", "map.version.changed", "agv.fault",
        )
    ]
    print(f"场景 {args.scenario} 推进 {args.ticks} tick，耗时 {elapsed:.2f}s")
    print(f"统计：{json.dumps(snapshot['stats'], ensure_ascii=False)}")
    if args.verbose:
        for event in key_events:
            print(f"  tick={event['tick']:>5} {event['type']:<22} {event['aggregateId'] or ''}")
    app.close()
    return 0


def cmd_export_fixtures(args: argparse.Namespace) -> int:
    """把地图与场景导出为 JSON，保证 fixture 与代码同源。"""
    maps_dir = FIXTURES_DIR / "maps"
    scenarios_dir = FIXTURES_DIR / "scenarios"
    maps_dir.mkdir(parents=True, exist_ok=True)
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    for map_id in ("warehouse-24", "ring-3"):
        wm = build_map(map_id)
        path = maps_dir / f"{map_id}.json"
        path.write_text(json.dumps(wm.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已导出 {path}")

    index = []
    for scenario in SCENARIOS.values():
        path = scenarios_dir / f"{scenario.id}.json"
        path.write_text(json.dumps(scenario.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        index.append(scenario.to_dict())
        print(f"已导出 {path}")
    (scenarios_dir / "index.json").write_text(json.dumps(list_scenarios(), ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    """采集性能数据：指派延迟、每 tick 耗时、内存占用。"""
    import tracemalloc

    app = App(data_dir=args.data_dir)
    app.reset(args.scenario, args.seed)
    tracemalloc.start()
    started = time.perf_counter()
    for _ in range(args.ticks):
        app.sim.step_once()
    elapsed = time.perf_counter() - started
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assign_events = app.db.query("SELECT tick FROM events WHERE type = 'task.assigned' ORDER BY seq")
    assign_ticks = [row["tick"] for row in assign_events]
    # 指派延迟：任务创建 tick -> 任务被指派 tick（离散仿真以 tick 计）
    latency_rows = app.db.query(
        """SELECT c.tick AS created, a.tick AS assigned
           FROM events c JOIN events a ON a.aggregate_id = c.aggregate_id
           WHERE c.type = 'task.created' AND a.type = 'task.assigned'""")
    latencies = [int(row["assigned"]) - int(row["created"]) for row in latency_rows
                 if row["assigned"] is not None and row["created"] is not None]
    snapshot = app.sim.snapshot()
    result = {
        "scenario": args.scenario,
        "seed": args.seed,
        "ticks": args.ticks,
        "agvs": len(snapshot["agvs"]),
        "tasks": len(snapshot["tasks"]),
        "wallClockSeconds": round(elapsed, 3),
        "msPerTick": round(elapsed / max(1, args.ticks) * 1000, 3),
        "assignmentCount": len(assign_ticks),
        "assignmentLatencyTicks": {
            "count": len(latencies),
            "avg": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
            "max": max(latencies) if latencies else 0,
            "unit": "tick",
            "definition": "task.assigned 的 tick - task.created 的 tick",
        },
        "stats": snapshot["stats"],
        "pythonHeapMb": round(peak / 1024 / 1024, 2),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    app.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wms", description="SmartWMS 个人版命令行")
    parser.add_argument("--data-dir", default=None, help="数据目录，默认 implementations/member-2/data")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="启动 HTTP 服务")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=cmd_serve)

    seed = sub.add_parser("seed", help="导入固定场景")
    seed.add_argument("--scenario", default="single-agv")
    seed.add_argument("--seed", type=int, default=config.SEED_DEFAULT)
    seed.set_defaults(func=cmd_seed)

    demo = sub.add_parser("demo", help="无界面运行一段仿真")
    demo.add_argument("--scenario", default="single-agv")
    demo.add_argument("--seed", type=int, default=config.SEED_DEFAULT)
    demo.add_argument("--ticks", type=int, default=200)
    demo.add_argument("--verbose", action="store_true")
    demo.set_defaults(func=cmd_demo)

    export = sub.add_parser("export-fixtures", help="导出地图与场景 JSON")
    export.set_defaults(func=cmd_export_fixtures)

    bench = sub.add_parser("bench", help="采集性能数据")
    bench.add_argument("--scenario", default="peak-30")
    bench.add_argument("--seed", type=int, default=config.SEED_DEFAULT)
    bench.add_argument("--ticks", type=int, default=600)
    bench.set_defaults(func=cmd_bench)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
