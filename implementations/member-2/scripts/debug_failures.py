"""临时调试脚本：查看失败任务与关键事件原因。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wms.app import App

scenario = sys.argv[1] if len(sys.argv) > 1 else "peak-10"
ticks = int(sys.argv[2]) if len(sys.argv) > 2 else 400

app = App(data_dir=Path(__file__).resolve().parent.parent / "data")
app.reset(scenario, 20261118)
for _ in range(ticks):
    app.sim.step_once()

print("stats:", app.sim.stats)
for row in app.db.query("SELECT task_id, payload FROM task_events WHERE type = 'task.failed'"):
    print("FAILED", row["task_id"], row["payload"])
for row in app.db.query("SELECT type, COUNT(*) AS c FROM events GROUP BY type ORDER BY c DESC"):
    print(f"  {row['type']:<28} {row['c']}")
statuses = {}
for row in app.db.query("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status"):
    statuses[row["status"]] = row["c"]
print("tasks:", statuses)
app.close()
