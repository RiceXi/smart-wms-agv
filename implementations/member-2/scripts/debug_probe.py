"""开发期排查脚本：观察轨迹写入时机与任务指派时机。

用法：python scripts\\debug_probe.py

用途：
1. 打印指定场景每个 tick 的任务指派/开始/完成时序，定位"任务没被指派"类问题；
2. 打印某台 AGV 的轨迹写入时机，定位"车辆走进封路格"类问题
   （封路正好落在车辆脚下时，轨迹里会在封路后仍出现该格点，
   这是车辆尚未驶离造成的，不是它重新进入了封路格）。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wms.app import App  # noqa: E402


def probe_assignment(scenario_id: str, ticks: int) -> None:
    tmp = tempfile.mkdtemp(prefix="wms-probe-")
    try:
        app = App(data_dir=tmp)
        app.reset(scenario_id, 20261118)
        for i in range(ticks):
            app.sim.step_once()
            if i % 5 == 0:
                assigned = app.db.query_one(
                    "SELECT COUNT(*) AS c FROM events WHERE type = 'task.assigned'")["c"]
                rows = app.db.query("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status")
                print(f"tick={app.sim.tick} assigned_events={assigned} "
                      f"tasks={[(r['status'], r['c']) for r in rows]}")
        print("任务类事件序列：")
        for row in app.db.query(
                "SELECT type, aggregate_id, tick FROM events WHERE type LIKE 'task.%' ORDER BY seq LIMIT 15"):
            print("  ", row["tick"], row["type"], row["aggregate_id"])
        app.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def probe_trail(scenario_id: str, ticks: int, target_cell: str) -> None:
    tmp = tempfile.mkdtemp(prefix="wms-probe-")
    try:
        app = App(data_dir=tmp)
        app.reset(scenario_id, 20261118)
        for _ in range(ticks):
            app.sim.step_once()
        agv = app.sim.agvs["AGV-01"]
        plan = agv.plan
        if plan is None:
            print("AGV-01 没有计划，场景不足以支撑该排查")
            app.close()
            return
        future = [s.cell for s in plan.steps if s.tick > app.sim.tick and s.cell != agv.cell]
        blocked = future[len(future) // 2] if future else target_cell
        print("封路格点:", blocked, "当前 tick:", app.sim.tick)
        app.sim.apply_roadblock([blocked], "probe")
        for _ in range(80):
            app.sim.step_once()
        x, y = (int(p) for p in blocked.split(","))
        print("封路后仍出现在该格点的轨迹：")
        for row in app.db.query(
                "SELECT * FROM agv_trail WHERE x = ? AND y = ? AND tick > ?", (x, y, ticks)):
            print("  ", dict(row))
        app.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print("=== 急单场景：指派时序 ===")
    probe_assignment("rush-order", 30)
    print()
    print("=== 封路场景：轨迹写入时机 ===")
    probe_trail("roadblock", 25, "12,6")
    return 0


if __name__ == "__main__":
    sys.exit(main())
