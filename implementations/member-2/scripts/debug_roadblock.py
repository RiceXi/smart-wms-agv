"""专门排查：封路后 AGV 是否真的进入了封路格。

用法：python scripts\\debug_roadblock.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wms.app import App  # noqa: E402


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="wms-rb-")
    try:
        app = App(data_dir=tmp)
        app.reset("roadblock", 20261118)
        for _ in range(25):
            app.sim.step_once()

        agv = app.sim.agvs["AGV-01"]
        print("tick:", app.sim.tick, "AGV-01 cell:", agv.cell, "plan:", agv.plan is not None)
        remaining = [s for s in agv.plan.steps if s.tick > app.sim.tick]
        print("剩余路径:", [(s.tick, s.cell, s.action) for s in remaining[:8]])

        target = remaining[len(remaining) // 2].cell
        print("封路目标:", target)
        old_plan_version = agv.plan_version
        app.sim.apply_roadblock([target], "排查")
        print("封路后 AGV-01 cell:", agv.cell, "plan_version:", old_plan_version, "->", agv.plan_version)
        print("封路后 AGV-01 新计划:", [(s.tick, s.cell, s.action) for s in (agv.plan.steps[:8] if agv.plan else [])])
        print("目标可通行?", app.maps.current.is_passable(target))

        for _ in range(80):
            app.sim.step_once()

        x, y = (int(p) for p in target.split(","))
        rows = app.db.query(
            "SELECT * FROM agv_trail WHERE x = ? AND y = ? AND tick > 25 ORDER BY tick", (x, y))
        print("封路后出现在该格点的轨迹:")
        for row in rows:
            print("  ", dict(row))
        print("该格点的封路记录:", [dict(r) for r in app.db.query("SELECT * FROM roadblocks")])
        app.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
