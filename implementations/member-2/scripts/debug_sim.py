"""临时调试脚本：观察多车场景的推进过程。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wms.app import App

scenario = sys.argv[1] if len(sys.argv) > 1 else "peak-10"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 60
step = int(sys.argv[3]) if len(sys.argv) > 3 else 10

app = App(data_dir=Path(__file__).resolve().parent.parent / "data")
app.reset(scenario, 20261118)
for tick in range(ticks):
    app.sim.step_once()
    if (tick + 1) % step == 0:
        snap = app.sim.snapshot()
        print(f"--- tick {snap['tick']} stats={snap['stats']}")
        for agv in snap["agvs"][:12]:
            print(f"   {agv['id']} {agv['status']:<10} ({agv['x']},{agv['y']}) bat={agv['battery']:<6} "
                  f"task={agv['activeTaskId'] or '-'} plan={agv['planVersion']}")
        for task in snap["tasks"][:6]:
            print(f"   task {task['status']:<10} p={task['priority']} agv={task['assignedAgvId'] or '-'} "
                  f"pick={task['pickupCell']} drop={task['dropCell']} plan={task['planVersion']}")
app.close()
