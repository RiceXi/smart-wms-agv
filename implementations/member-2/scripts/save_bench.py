"""采集基准数据并落盘为验收证据。

用法：python scripts\\save_bench.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wms.app import App  # noqa: E402

TICKS = 600
SCENARIOS = ("single-agv", "peak-10", "peak-30")


def measure(scenario_id: str, ticks: int) -> dict:
    tmp = tempfile.mkdtemp(prefix="wms-bench-")
    try:
        app = App(data_dir=tmp)
        app.reset(scenario_id, 20261118)
        tracemalloc.start()
        started = time.perf_counter()
        for _ in range(ticks):
            app.sim.step_once()
        elapsed = time.perf_counter() - started
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        latency_rows = app.db.query(
            """SELECT c.tick AS created, a.tick AS assigned
               FROM events c JOIN events a ON a.aggregate_id = c.aggregate_id
               WHERE c.type = 'task.created' AND a.type = 'task.assigned'""")
        latencies = [int(r["assigned"]) - int(r["created"]) for r in latency_rows]
        snapshot = app.sim.snapshot()
        result = {
            "scenario": scenario_id,
            "seed": 20261118,
            "ticks": ticks,
            "agvs": len(snapshot["agvs"]),
            "tasks": len(snapshot["tasks"]),
            "wallClockSeconds": round(elapsed, 3),
            "msPerTick": round(elapsed / ticks * 1000, 3),
            "assignmentCount": app.db.query_one(
                "SELECT COUNT(*) AS c FROM events WHERE type = 'task.assigned'")["c"],
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
        app.close()
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    out_dir = ROOT / "docs" / "evidence"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for scenario_id in SCENARIOS:
        result = measure(scenario_id, TICKS)
        results.append(result)
        path = out_dir / f"bench_{scenario_id}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{scenario_id}: {result['msPerTick']} ms/tick, "
              f"指派延迟 avg={result['assignmentLatencyTicks']['avg']} tick, "
              f"堆峰值 {result['pythonHeapMb']} MB -> {path.name}")
    summary = out_dir / "bench-summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"汇总已写入 {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
