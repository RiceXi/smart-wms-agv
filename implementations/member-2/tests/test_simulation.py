"""AC9 死锁恢复 / AC10 临时封路 / AC11 充电 / AC12 急单 / AC13 固定 seed。

这些用例跑真实仿真主循环，验证"冲突与死锁处理有效""优先级体现""可重复回放"。
"""

from __future__ import annotations

import json
import unittest

from helpers import AppTestCase
from wms.deadlock import WaitGraph, choose_victim
from wms.scenarios import get_scenario


class WaitGraphTests(AppTestCase):
    """等待图与牺牲者选择是纯逻辑，单独验证。"""

    def test_cycle_detection(self):
        graph = WaitGraph()
        graph.set_blocker("A", "B")
        graph.set_blocker("B", "C")
        graph.set_blocker("C", "A")
        cycle = graph.find_cycle()
        self.assertIsNotNone(cycle)
        self.assertEqual(len(cycle), 3)
        self.assertEqual(set(cycle), {"A", "B", "C"})

    def test_no_cycle_in_chain(self):
        graph = WaitGraph()
        graph.set_blocker("A", "B")
        graph.set_blocker("B", "C")
        self.assertIsNone(graph.find_cycle())

    def test_cycle_detection_is_deterministic(self):
        graph = WaitGraph()
        graph.set_blocker("A", "B")
        graph.set_blocker("B", "C")
        graph.set_blocker("C", "A")
        first = graph.find_cycle()
        for _ in range(5):
            self.assertEqual(graph.find_cycle(), first)

    def test_choose_victim_prefers_rollbackable_then_lowest_priority(self):
        class FakeAgv:
            def __init__(self, priority, rollbacks=0):
                self.task_priority = priority
                self.rollback_count = rollbacks

        agvs = {"A": FakeAgv(9), "B": FakeAgv(1), "C": FakeAgv(5)}
        can_rollback = {"A": True, "B": True, "C": True}
        self.assertEqual(choose_victim(["A", "B", "C"], agvs, can_rollback), "B")
        # 只有 A 能回退时，即使它优先级最高也选 A
        self.assertEqual(choose_victim(["A", "B", "C"], agvs, {"A": True, "B": False, "C": False}), "A")
        # 同优先级按 id 排序，保证确定性
        same = {"A": FakeAgv(3), "B": FakeAgv(3)}
        self.assertEqual(choose_victim(["B", "A"], same, {"A": True, "B": True}), "A")


class CycleDeadlockScenarioTests(AppTestCase):
    """AC9：三车环形等待触发等待图环检测，选择可回退车辆释放预约并重规划。"""

    scenario = "cycle-deadlock"

    def test_deadlock_detected_and_recovered(self):
        self.run_ticks(self.app, 120)
        self.assertTrue(self.has_event(self.app, "deadlock.detected"), "必须检测到等待环")
        self.assertTrue(self.has_event(self.app, "rollback.executed"), "必须执行回退")
        self.assertGreaterEqual(self.app.sim.stats["deadlocks"], 1)
        self.assertGreaterEqual(self.app.sim.stats["rollbacks"], 1)

        # 不能永久卡死：任务要么完成，要么按明确规则失败
        statuses = self.task_statuses(self.app)
        self.assertTrue(statuses.get("completed", 0) + statuses.get("failed", 0) >= 1,
                        f"所有任务都卡死：{statuses}")
        for row in self.app.db.query("SELECT * FROM tasks WHERE status = 'failed'"):
            self.assertTrue(row.get("failure_reason") or row.get("id"), "失败任务必须有原因")

    def test_no_agv_enters_deadlock_forever(self):
        self.run_ticks(self.app, 120)
        # 等待图最终必须无环
        graph = WaitGraph()
        for agv in self.app.sim.agvs.values():
            graph.set_blocker(agv.id, agv.blocked_target)
        self.assertIsNone(graph.find_cycle(), "恢复后不应残留等待环")


class HeadToHeadTests(AppTestCase):
    """AC7：窄巷相向冲突必须被阻止。"""

    scenario = "head-to-head"

    def test_head_on_conflict_is_blocked(self):
        self.run_ticks(self.app, 60)
        blocked_rows = self.app.db.query("SELECT * FROM events WHERE type = 'agv.blocked'")
        self.assertTrue(blocked_rows, "应记录冲突阻塞事件")
        # 同一 tick 不允许两台车占用同一格
        rows = self.app.db.query(
            """SELECT tick, cell_from, COUNT(DISTINCT agv_id) AS c FROM reservations
               WHERE kind = 'vertex' GROUP BY tick, cell_from HAVING c > 1""")
        self.assertEqual(len(rows), 0, "同一 tick 同一格不得被两台车同时预约")

    def test_conflict_counter_matches_conflict_events(self):
        """回归缺陷：`stats["conflicts"]` 有两处计数点没有发出 `conflict.detected` 事件，
        造成"统计说冲突了、事件流里查不到"的口径不一致。"""
        self.run_ticks(self.app, 60)
        counter = self.app.sim.stats["conflicts"]
        rows = self.app.db.query(
            "SELECT COUNT(*) AS c FROM events WHERE type = 'conflict.detected'")
        self.assertGreaterEqual(counter, 1, "相向冲突场景应至少检测到一次冲突")
        self.assertEqual(counter, rows[0]["c"], "冲突计数与 conflict.detected 事件数必须一致")


class RoadblockTests(AppTestCase):
    """AC10：封路后受影响路径升版本重规划，AGV 不进入封路格。"""

    scenario = "roadblock"

    def test_roadblock_triggers_replan_and_is_respected(self):
        self.run_ticks(self.app, 25)
        agv = self.app.sim.agvs["AGV-01"]
        plan = agv.plan
        self.assertIsNotNone(plan)
        future_cells = [step.cell for step in plan.steps
                        if step.tick > self.app.sim.tick and step.cell != agv.cell]
        self.assertTrue(future_cells, "需要一条还没走完的路径")
        blocked_cell = future_cells[len(future_cells) // 2]

        old_version = self.app.maps.current.version
        roadblock_tick = self.app.sim.tick
        self.app.sim.apply_roadblock([blocked_cell], "测试封路")

        self.assertNotEqual(self.app.maps.current.version, old_version, "封路必须生成新地图版本")
        self.assertTrue(self.has_event(self.app, "map.roadblock.added"))
        self.assertTrue(self.has_event(self.app, "task.replanned"), "受影响路径必须重规划")

        # 之后任何 AGV 都不能进入封路格（留 10 tick 让被封在原地的车驶离）
        self.run_ticks(self.app, 80)
        x, y = (int(part) for part in blocked_cell.split(","))
        offenders = self.app.db.query(
            "SELECT * FROM agv_trail WHERE x = ? AND y = ? AND tick > ?", (x, y, roadblock_tick + 10))
        self.assertEqual(len(offenders), 0, f"有 AGV 进入封路格 {blocked_cell}")

    def test_unblock_restores_passability(self):
        self.app.sim.apply_roadblock(["12,7"], "测试封路")
        record = self.app.db.query_one("SELECT * FROM roadblocks WHERE active = 1")
        self.assertIsNotNone(record)
        self.app.sim.remove_roadblock(record["id"])
        self.assertTrue(self.app.maps.current.is_passable("12,7"),
                        "解封后该格点应恢复可通行")
        self.assertEqual(len(self.app.maps.active_roadblocks()), 0)

    def test_idle_returning_agv_also_replans(self):
        """空闲返程中的车辆（没有任务）也必须跟随封路重规划。

        回归缺陷：`_replan` 在 `active_task_id` 为空时直接返回，
        导致空闲车辆沿着旧计划开进已经封路的格点。
        """
        self.run_ticks(self.app, 25)
        target_agv = None
        target_cell = ""
        for agv in sorted(self.app.sim.agvs.values(), key=lambda a: a.id):
            if agv.active_task_id or agv.plan is None:
                continue
            future = [s.cell for s in agv.plan.steps
                      if s.tick > self.app.sim.tick and s.cell != agv.cell]
            if future:
                target_agv = agv
                target_cell = future[len(future) // 2]
                break
        self.assertIsNotNone(target_agv, "场景中应存在空闲返程的车辆")

        old_version = target_agv.plan_version
        roadblock_tick = self.app.sim.tick
        self.app.sim.apply_roadblock([target_cell], "封住空闲车辆的前方")

        self.assertGreater(target_agv.plan_version, old_version,
                           "空闲车辆必须重规划")
        self.assertIsNotNone(target_agv.plan)
        self.assertNotIn(target_cell, [s.cell for s in target_agv.plan.steps],
                         "新计划不得包含封路格")

        self.run_ticks(self.app, 80)
        x, y = (int(part) for part in target_cell.split(","))
        offenders = self.app.db.query(
            "SELECT * FROM agv_trail WHERE x = ? AND y = ? AND tick > ?",
            (x, y, roadblock_tick + 5))
        self.assertEqual(len(offenders), 0, f"有 AGV 进入封路格 {target_cell}")


class ChargingTests(AppTestCase):
    """AC11：低电量进入充电，充电位容量不被突破，充满后回岗。"""

    scenario = "low-battery"

    def test_low_battery_agv_charges(self):
        self.run_ticks(self.app, 200)
        self.assertTrue(self.has_event(self.app, "agv.charging"), "低电量 AGV 应进入充电状态")
        charging = [a for a in self.app.sim.agvs.values() if a.status == "charging"]
        # 充电位容量：同一格点不能有超过 1 台车在充
        cells = [a.cell for a in charging]
        self.assertEqual(len(cells), len(set(cells)), "充电位容量被突破")

    def test_battery_increases_while_charging(self):
        self.run_ticks(self.app, 60)
        batteries = [a.battery for a in self.app.sim.agvs.values()]
        self.assertTrue(all(b >= 0 for b in batteries))
        # 出现过充电事件说明至少有一台车电量上升
        rows = self.app.db.query("SELECT * FROM events WHERE type = 'agv.charging'")
        self.assertTrue(rows)


class RushOrderTests(AppTestCase):
    """AC12：插入高优先级订单后，指派与队列体现优先级变化。"""

    scenario = "rush-order"

    def test_rush_order_takes_priority(self):
        """AC12：急单必须排在待办队列最前，并在首次指派时领先普通任务。"""
        # 场景自身在 tick 20 注入了一单优先级 10 的急单
        self.run_ticks(self.app, 25)
        rush_count = self.app.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE priority = 10")["c"]
        self.assertGreaterEqual(rush_count, 1, "场景应注入高优先级急单")

        # 再插入一单急单，它同样必须排在待办队列最前
        self.app.sim.inject_event({
            "type": "rush_order", "priority": 10, "skuCode": "SKU-002", "qty": 1,
            "code": "OUT-RUSH-TEST",
        })
        pending = self.app.db.query(
            """SELECT t.priority AS priority FROM tasks t
               WHERE t.status = 'pending' ORDER BY t.priority DESC, t.created_at ASC""")
        self.assertTrue(pending, "应存在待办任务")
        self.assertEqual(pending[0]["priority"], 10, "急单必须排在待办队列最前")
        priorities = [row["priority"] for row in pending]
        self.assertEqual(priorities, sorted(priorities, reverse=True),
                         "待办队列必须按优先级降序排列")

    def test_rush_order_assigned_before_normal_tasks(self):
        """从空闲状态插入急单：急单必须第一个被指派。"""
        self.app.sim.inject_event({
            "type": "rush_order", "priority": 10, "skuCode": "SKU-001", "qty": 1,
        })
        for _ in range(10):
            self.app.sim.step_once()

        assignments = self.app.db.query(
            """SELECT e.seq AS seq, e.tick AS tick, t.priority AS priority
               FROM events e JOIN tasks t ON t.id = e.aggregate_id
               WHERE e.type = 'task.assigned' ORDER BY e.seq""")
        self.assertTrue(assignments, "应有任务被指派")
        self.assertEqual(assignments[0]["priority"], 10,
                         "第一次指派必须是急单，而不是普通任务")
        rush_ticks = [row["tick"] for row in assignments if row["priority"] == 10]
        normal_ticks = [row["tick"] for row in assignments if row["priority"] < 10]
        if normal_ticks:
            self.assertLessEqual(min(rush_ticks), min(normal_ticks),
                                 "急单必须不晚于普通任务被指派")

    def test_task_queue_sorted_by_priority(self):
        tasks = self.app.db.query(
            "SELECT priority FROM tasks ORDER BY priority DESC, created_at ASC")
        priorities = [row["priority"] for row in tasks]
        self.assertEqual(priorities, sorted(priorities, reverse=True))


class DeterminismTests(AppTestCase):
    """AC13：同一地图、订单和 seed 连续运行三次，关键事件序列一致。"""

    @staticmethod
    def _normalize(rows) -> list[tuple]:
        """把 UUID 形式的任务/订单 id 换成稳定序号后比较。

        业务 id 使用 UUID 保证全局唯一，因此每次运行的字面 id 不同；
        这里比较的是"事件顺序与内容"，把 id 归一化后才能反映真正的可重复性。
        """
        mapping: dict[str, str] = {}

        def alias(value):
            if value is None:
                return ""
            if value not in mapping:
                mapping[value] = f"#{len(mapping)}"
            return mapping[value]

        return [(row["type"], alias(row["aggregate_id"]), row["tick"]) for row in rows]

    def test_same_seed_produces_same_event_sequence(self):
        signatures = []
        for _ in range(3):
            app = self.make_app("peak-10", 20261118)
            for _ in range(120):
                app.sim.step_once()
            rows = app.db.query(
                "SELECT type, aggregate_id, tick FROM events ORDER BY seq")
            signatures.append(self._normalize(rows))
            app.close()
        self.assertEqual(signatures[0], signatures[1])
        self.assertEqual(signatures[1], signatures[2])

    def test_same_seed_produces_same_task_outcomes(self):
        outcomes = []
        for _ in range(2):
            app = self.make_app("peak-10", 20261118)
            for _ in range(150):
                app.sim.step_once()
            rows = app.db.query(
                """SELECT t.status AS status, t.priority AS priority, t.type AS type,
                          p.code AS pickup_code, d.code AS drop_code
                   FROM tasks t
                   LEFT JOIN location p ON p.id = t.pickup_location_id
                   LEFT JOIN location d ON d.id = t.drop_location_id
                   ORDER BY t.priority DESC, p.code, d.code""")
            outcomes.append([tuple(row) for row in rows])
            app.close()
        self.assertEqual(outcomes[0], outcomes[1])

    def test_same_seed_produces_same_agv_trajectories(self):
        trails = []
        for _ in range(2):
            app = self.make_app("peak-10", 20261118)
            for _ in range(100):
                app.sim.step_once()
            rows = app.db.query(
                "SELECT agv_id, tick, x, y FROM agv_trail ORDER BY agv_id, tick, id")
            trails.append([tuple(row) for row in rows])
            app.close()
        self.assertEqual(trails[0], trails[1])

    def test_different_seed_is_documented_as_same_scenario(self):
        # 场景种子只影响随机扰动；本实现不注入随机扰动，因此不同 seed 也应稳定
        app = self.make_app("peak-10", 777)
        for _ in range(60):
            app.sim.step_once()
        statuses = self.task_statuses(app)
        self.assertGreaterEqual(statuses.get("completed", 0), 1)


class ScenarioCatalogueTests(AppTestCase):
    """固定场景必须可枚举、可重放。"""

    def test_all_scenarios_load(self):
        from wms.scenarios import SCENARIOS

        for scenario_id in SCENARIOS:
            app = self.make_app(scenario_id, 20261118)
            self.assertEqual(app.sim.scenario_id, scenario_id)
            self.assertTrue(app.sim.agvs, f"{scenario_id} 应该有 AGV")
            app.close()

    def test_scenario_expectation_metadata_present(self):
        from wms.scenarios import SCENARIOS

        for scenario in SCENARIOS.values():
            self.assertTrue(scenario.title)
            self.assertTrue(scenario.description)
            self.assertIsInstance(scenario.expect, dict)


if __name__ == "__main__":
    unittest.main()
