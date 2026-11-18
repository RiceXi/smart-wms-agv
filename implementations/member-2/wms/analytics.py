"""统计与分析。

指标口径必须明确（窗口、单位、分母），前端不能自行猜测。
所有指标都来自事件流与任务快照，保证可以与原始事件对账。
"""

from __future__ import annotations

WORKING_STATUSES = ("to_pickup", "loading", "to_dropoff", "unloading")


class Analytics:
    def __init__(self, db, sim):
        self.db = db
        self.sim = sim

    # ------------------------------------------------------------ 汇总
    def summary(self) -> dict:
        orders = self._count_by("orders", "status")
        tasks = self._count_by("tasks", "status")
        agvs = self._count_by("agvs", "status")
        durations = self._task_durations()
        avg = round(sum(durations) / len(durations), 2) if durations else 0.0
        return {
            "orders": {"total": sum(orders.values()), "byStatus": orders},
            "tasks": {"total": sum(tasks.values()), "byStatus": tasks},
            "agvs": {"total": sum(agvs.values()), "byStatus": agvs},
            "conflicts": self.sim.stats.get("conflicts", 0),
            "deadlocks": self.sim.stats.get("deadlocks", 0),
            "recoveries": self.sim.stats.get("recoveries", 0),
            "rollbacks": self.sim.stats.get("rollbacks", 0),
            "avgTaskTicks": avg,
            "completedTasks": tasks.get("completed", 0),
            "cancelledTasks": tasks.get("cancelled", 0),
            "failedTasks": tasks.get("failed", 0),
            "currentTick": self.sim.tick,
            "window": "cumulative",
            "unit": "tick",
        }

    def _count_by(self, table: str, column: str) -> dict:
        rows = self.db.query(f"SELECT {column} AS k, COUNT(*) AS c FROM {table} GROUP BY {column}")
        return {row["k"]: row["c"] for row in rows}

    def _task_durations(self) -> list[int]:
        """任务耗时：完成事件 tick - 创建事件 tick。"""
        rows = self.db.query(
            """SELECT t.id AS id,
                      (SELECT e.tick FROM task_events e
                        WHERE e.task_id = t.id AND e.type = 'task.completed' ORDER BY e.seq DESC LIMIT 1) AS end_tick,
                      (SELECT e.tick FROM task_events e
                        WHERE e.task_id = t.id AND e.type = 'task.created' ORDER BY e.seq ASC LIMIT 1) AS start_tick
               FROM tasks t WHERE t.status = 'completed'"""
        )
        durations = []
        for row in rows:
            if row["end_tick"] is not None and row["start_tick"] is not None:
                durations.append(int(row["end_tick"]) - int(row["start_tick"]))
        return durations

    # ------------------------------------------------------------ 吞吐
    def throughput(self, window: int = 600, bucket: int = 60) -> dict:
        """按时间桶统计完成的任务数。

        窗口与分母都显式返回，避免口径歧义。
        """
        window = max(bucket, int(window))
        current = self.sim.tick
        start = max(0, current - window)
        rows = self.db.query(
            """SELECT e.tick AS tick, COUNT(DISTINCT e.task_id) AS c
               FROM task_events e
               WHERE e.type = 'task.completed' AND e.tick >= ?
               GROUP BY e.tick""",
            (start,),
        )
        buckets = []
        for begin in range(start, current, bucket):
            end = min(begin + bucket, current)
            count = sum(r["c"] for r in rows if begin <= r["tick"] < end)
            buckets.append({"fromTick": begin, "toTick": end, "completed": count})
        total = sum(b["completed"] for b in buckets)
        elapsed = max(1, current - start)
        return {
            "buckets": buckets,
            "total": total,
            "windowTicks": window,
            "bucketTicks": bucket,
            "elapsedTicks": elapsed,
            "perMinute": round(total / (elapsed / 60.0), 3) if elapsed else 0.0,
            "unit": "task/tick",
        }

    # ------------------------------------------------------------ 利用率
    def utilization(self) -> dict:
        """每台 AGV 的利用率。

        利用率 = 作业状态（取货/装载/放货/卸载）累计 tick / 仿真已运行 tick。
        """
        current = max(1, self.sim.tick)
        rows = self.db.query(
            "SELECT * FROM events WHERE type = 'agv.state.changed' ORDER BY seq"
        )
        state_timeline: dict[str, list[tuple[int, str]]] = {}
        for row in rows:
            payload = row["payload"]
            try:
                import json

                data = json.loads(payload)
            except (TypeError, ValueError):
                continue
            agv_id = row["aggregate_id"]
            to_state = data.get("to")
            if not to_state:
                continue
            state_timeline.setdefault(agv_id, []).append((row["tick"], to_state))

        items = []
        for agv_id in sorted(self.sim.agvs):
            timeline = state_timeline.get(agv_id, [])
            busy = 0
            state = "idle"
            start_tick = 0
            for tick, new_state in timeline:
                if state in WORKING_STATUSES:
                    busy += max(0, tick - start_tick)
                state = new_state
                start_tick = tick
            if state in WORKING_STATUSES:
                busy += max(0, current - start_tick)
            items.append({
                "agvId": agv_id,
                "busyTicks": busy,
                "totalTicks": current,
                "utilization": round(busy / current * 100.0, 2),
                "status": self.sim.agvs[agv_id].status,
                "unit": "%",
            })
        total_busy = sum(i["busyTicks"] for i in items)
        return {
            "items": items,
            "averageUtilization": round(total_busy / (current * max(1, len(items))) * 100.0, 2),
            "elapsedTicks": current,
            "definition": "作业状态累计 tick / 仿真已运行 tick",
            "unit": "%",
        }

    # ------------------------------------------------------------ 瓶颈
    def bottlenecks(self, top: int = 10) -> dict:
        """瓶颈巷道 TopN。

        口径：某格点累计阻塞次数（等待 tick 数），并按行聚合出巷道。
        """
        contention = getattr(self.sim, "contention", {})
        cell_rows = sorted(((cell, count) for cell, count in contention.items()), key=lambda kv: (-kv[1], kv[0]))
        aisle_rows: dict[int, int] = {}
        for cell, count in contention.items():
            try:
                y = int(cell.split(",")[1])
            except (IndexError, ValueError):
                continue
            aisle_rows[y] = aisle_rows.get(y, 0) + count
        aisles = sorted(((y, count) for y, count in aisle_rows.items()), key=lambda kv: (-kv[1], kv[0]))
        return {
            "cells": [{"cell": cell, "blockedTicks": count, "x": int(cell.split(",")[0]), "y": int(cell.split(",")[1])}
                      for cell, count in cell_rows[:top]],
            "aisles": [{"aisle": f"row-{y}", "y": y, "blockedTicks": count} for y, count in aisles[:top]],
            "metric": "blockedTicks",
            "definition": "AGV 在该格点因冲突而等待的累计 tick 数",
            "unit": "tick",
        }

    # ------------------------------------------------------------ 任务时间线
    def task_timeline(self, task_id: str) -> dict:
        rows = self.db.query("SELECT * FROM task_events WHERE task_id = ? ORDER BY seq", (task_id,))
        events = [
            {
                "tick": row["tick"],
                "type": row["type"],
                "fromStatus": row["from_status"],
                "toStatus": row["to_status"],
                "agvId": row["agv_id"],
                "planVersion": row["plan_version"],
                "occurredAt": row["occurred_at"],
            }
            for row in rows
        ]
        return {"taskId": task_id, "events": events, "count": len(events)}
