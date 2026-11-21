from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wms_agv.service import WmsService


def run_scenario(
    scenario_index: int,
    agvs: int,
    orders: int,
    ticks: int,
    seed: int,
) -> dict:
    with tempfile.TemporaryDirectory() as temp:
        service = WmsService(
            Path(temp) / f"stress-{scenario_index}.sqlite", agv_count=agvs
        )
        try:
            rng = random.Random(seed + scenario_index)
            skus = list(service.skus.values())
            order_count = max(1, min(orders, agvs))
            for index in range(order_count):
                order = service.create_order(
                    {
                        "code": f"STRESS-{scenario_index}-{index}",
                        "priority": rng.randint(1, 10),
                        "requester": "stress",
                        "lines": [
                            {
                                "sku_id": rng.choice(skus)["id"],
                                "qty": 1,
                            }
                        ],
                    }
                )
                service.release_order(order["id"])

            vertex_conflicts = 0
            edge_conflicts = 0
            previous = {
                agv_id: (int(agv["x"]), int(agv["y"]))
                for agv_id, agv in service.agvs.items()
            }
            with service.lock:
                service._batching = True
                try:
                    for _ in range(ticks):
                        service._tick_once_locked()
                        current = {
                            agv_id: (int(agv["x"]), int(agv["y"]))
                            for agv_id, agv in service.agvs.items()
                        }
                        positions = list(current.values())
                        vertex_conflicts += len(positions) - len(set(positions))
                        for left in service.agvs:
                            for right in service.agvs:
                                if left >= right:
                                    continue
                                if (
                                    previous[left] == current[right]
                                    and previous[right] == current[left]
                                    and previous[left] != previous[right]
                                ):
                                    edge_conflicts += 1
                        previous = current
                finally:
                    service._flush_all_locked()

            task_status = [task["status"] for task in service.tasks.values()]
            stuck = sum(
                status in {"pending", "assigned", "running"} for status in task_status
            )
            return {
                "scenario": scenario_index,
                "agvs": len(service.agvs),
                "orders": len(service.orders),
                "tasks": len(service.tasks),
                "completed": task_status.count("completed"),
                "stuck": stuck,
                "vertex_conflicts": vertex_conflicts,
                "edge_conflicts": edge_conflicts,
                "tick": service.tick,
            }
        finally:
            service.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Member-1 dispatch stress runner")
    parser.add_argument("--agvs", type=int, default=30)
    parser.add_argument("--scenarios", type=int, default=10)
    parser.add_argument("--orders", type=int, default=3)
    parser.add_argument("--ticks", type=int, default=300)
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    started = time.perf_counter()
    results = [
        run_scenario(index, args.agvs, args.orders, args.ticks, args.seed)
        for index in range(args.scenarios)
    ]
    elapsed = time.perf_counter() - started
    summary = {
        "elapsed_seconds": round(elapsed, 3),
        "scenarios": len(results),
        "agvs": args.agvs,
        "orders": args.orders,
        "ticks": args.ticks,
        "successful": sum(
            1
            for item in results
            if item["vertex_conflicts"] == 0
            and item["edge_conflicts"] == 0
            and item["stuck"] == 0
        ),
        "vertex_conflicts": sum(item["vertex_conflicts"] for item in results),
        "edge_conflicts": sum(item["edge_conflicts"] for item in results),
        "stuck_tasks": sum(item["stuck"] for item in results),
        "results": results,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as output:
            output.write(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["successful"] == summary["scenarios"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
