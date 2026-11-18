"""SmartWMS 个人版实现（成员二）。

模块划分：

- `db` / `service`：业务数据与状态机（SQLite 持久化）。
- `mapdata` / `mapgen`：地图模型、版本与固定地图生成。
- `reservations` / `pathfinding` / `scheduler` / `deadlock`：调度内核。
- `simulator`：tick 仿真引擎。
- `api` / `server`：REST 与 SSE 接口。
- `analytics`：统计指标。
- `scenarios` / `app`：固定场景与应用装配。
"""

__version__ = "0.2.0-member-2"
