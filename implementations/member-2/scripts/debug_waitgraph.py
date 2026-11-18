"""临时调试脚本：逐 tick 打印车辆状态与等待图。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wms.app import App

scenario = sys.argv[1] if len(sys.argv) > 1 else "head-to-head"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 20

app = App(data_dir=Path(__file__).resolve().parent.parent / "data")
app.reset(scenario, 20261118)
for tick in range(ticks):
    app.sim.step_once()
    snap = app.sim.snapshot()
    print(f"--- tick {snap['tick']} deadlocks={snap['stats']['deadlocks']} rollbacks={snap['stats']['rollbacks']}")
    for agv in snap["agvs"]:
        runtime = app.sim.agvs[agv["id"]]
        print(f"   {agv['id']} {agv['status']:<10} ({agv['x']},{agv['y']}) task={agv['activeTaskId'] or '-':<40} "
              f"block={runtime.block_ticks:<3} target={runtime.blocked_target} leg={runtime.leg}")
    graph = {k: sorted(v) for k, v in app.sim.wait_graph.edges.items() if v}
    print("   waitGraph:", graph)
app.close()
