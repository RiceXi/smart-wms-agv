"""场景回归：逐个跑固定场景，输出可复现的结果表。

用法：python scripts\\run_scenarios.py [ticks]
输出同时用于个人报告里的验收证据。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wms.app import App  # noqa: E402
from wms.scenarios import SCENARIOS  # noqa: E402

TICKS = {
    "single-agv": 200,
    "peak-10": 400,
    "peak-30": 600,
    "head-to-head": 200,
    "cycle-deadlock": 200,
    "roadblock": 300,
    "low-battery": 300,
    "rush-order": 300,
}


def run(scenario_id: str, ticks: int) -> dict:
    tmp = tempfile.mkdtemp(prefix="wms-scenario-")
    try:
        app = App(data_dir=tmp)
        app.reset(scenario_id, 20261118)
        started = time.perf_counter()
        for _ in range(ticks):
            app.sim.step_once()
        elapsed = time.perf_counter() - started
        snapshot = app.sim.snapshot()
        events = app.db.query("SELECT type, COUNT(*) AS c FROM events GROUP BY type")
        event_counts = {row["type"]: row["c"] for row in events}
        result = {
            "scenario": scenario_id,
            "ticks": ticks,
            "agvs": len(snapshot["agvs"]),
            "tasks": len(snapshot["tasks"]),
            "taskStatus": _counter(snapshot["tasks"], "status"),
            "orderStatus": _counter(snapshot["orders"], "status"),
            "stats": snapshot["stats"],
            "wallClockSeconds": round(elapsed, 3),
            "msPerTick": round(elapsed / ticks * 1000, 3),
            "eventCounts": event_counts,
            "alerts": len(snapshot["alerts"]),
        }
        app.close()
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _counter(items: list[dict], key: str) -> dict:
    counts: dict[str, int] = {}
    for item in items:
        counts[item.get(key, "")] = counts.get(item.get(key, ""), 0) + 1
    return counts


def main() -> int:
    override = int(sys.argv[1]) if len(sys.argv) > 1 else None
    results = []
    lines: list[str] = []
    for scenario_id in SCENARIOS:
        ticks = override or TICKS.get(scenario_id, 200)
        result = run(scenario_id, ticks)
        results.append(result)
        lines.append(
            f"[{scenario_id}] tick={result['ticks']} 任务={result['taskStatus']} "
            f"订单={result['orderStatus']} 统计={result['stats']} "
            f"耗时={result['wallClockSeconds']}s ({result['msPerTick']} ms/tick)")
        interesting = {k: v for k, v in result["eventCounts"].items()
                       if k.startswith(("conflict", "deadlock", "rollback", "map.", "agv.charging", "task.failed"))}
        lines.append(f"    关键事件：{interesting}")
    out = ROOT / "docs" / "scenario-results.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    lines.append(f"\n结果已写入 {out}")
    evidence = ROOT / "docs" / "evidence" / "scenario-run.txt"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"文本输出已写入 {evidence}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
