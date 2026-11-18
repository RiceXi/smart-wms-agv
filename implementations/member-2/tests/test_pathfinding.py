"""AC5 单 AGV 路径 / AC6 同格冲突 / AC7 相向穿边 / AC8 首尾相接。

这些用例只针对路径与预约两个纯算法模块，不依赖仿真主循环，
保证调度逻辑可以在无 UI、无数据库的条件下单独验证。
"""

from __future__ import annotations

import unittest

from helpers import AppTestCase
from wms.mapgen import build_map
from wms.pathfinding import (
    articulation_points,
    find_free_cell,
    find_safe_cell,
    manhattan,
    plan_path,
    static_distance,
)
from wms.reservations import ReservationEntry, ReservationStore, edge_key, vertex_key


class PathPlanTests(AppTestCase):
    """AC5：路径连续、不穿越货架或封路格。"""

    def setUp(self):
        super().setUp()
        self.wm = build_map("warehouse-24")

    def test_plan_is_continuous_and_passable(self):
        start = "12,18"
        goal = "4,6"
        reservations = ReservationStore(self.wm.map_id)
        plan = plan_path(self.wm, start, goal, 1, reservations, "AGV-01")
        self.assertIsNotNone(plan)
        self.assertGreater(len(plan.steps), 1)

        cells = [step.cell for step in plan.steps]
        # 连续性：相邻两步必须是地图上真实存在的有向边
        for index in range(1, len(cells)):
            self.assertIn(cells[index], self.wm.passable_neighbors(cells[index - 1]),
                          f"路径在第 {index} 步断裂：{cells[index - 1]} -> {cells[index]}")
        # 可通行性：不能穿越货架或封路格
        for cell in cells:
            self.assertTrue(self.wm.is_passable(cell), f"路径经过不可通行格：{cell}")
        self.assertEqual(cells[-1], goal)
        # tick 单调递增
        ticks = [step.tick for step in plan.steps]
        self.assertEqual(ticks, sorted(ticks))

    def test_plan_respects_blocked_cell(self):
        wm = build_map("warehouse-24")
        blocked = "12,12"
        wm.cells[blocked] = wm.cells[blocked].__class__(
            id=blocked, x=12, y=12, type="blocked", passable=False, capacity=0, zone="")
        wm.build_adjacency()
        plan = plan_path(wm, "12,18", "4,6", 1, ReservationStore(wm.map_id), "AGV-01")
        self.assertIsNotNone(plan)
        self.assertNotIn(blocked, [step.cell for step in plan.steps])

    def test_static_distance_and_manhattan(self):
        self.assertEqual(static_distance(self.wm, "12,18", "12,18"), 0)
        distance = static_distance(self.wm, "12,18", "12,6")
        self.assertIsNotNone(distance)
        self.assertGreater(distance, 0)
        self.assertEqual(manhattan(self.wm, "12,18", "12,18"), 0)

    def test_unreachable_goal_returns_none(self):
        plan = plan_path(self.wm, "12,18", "3,3", 1, ReservationStore(self.wm.map_id), "AGV-01")
        # 3,3 是货架格，不可通行
        self.assertIsNone(plan)

    def test_static_block_prevents_planning_through_parked_agv(self):
        reservations = ReservationStore(self.wm.map_id)
        reservations.set_static_block("12,12", "AGV-99")
        plan = plan_path(self.wm, "12,18", "12,6", 1, reservations, "AGV-01")
        self.assertIsNotNone(plan)
        self.assertNotIn("12,12", [step.cell for step in plan.steps])

    def test_safe_cell_avoids_articulation_points(self):
        cuts = articulation_points(self.wm)
        self.assertIsInstance(cuts, set)
        occupied = {"12,18"}
        path = find_safe_cell(self.wm, "12,18", occupied, goal="12,6")
        self.assertIsNotNone(path)
        self.assertNotIn(path[-1], occupied)
        self.assertNotIn(path[-1], cuts, "回退格不能是割点，否则会把通道堵死")

    def test_find_free_cell_returns_path_or_none(self):
        path = find_free_cell(self.wm, "12,18", {"12,18"})
        self.assertIsNotNone(path)
        self.assertNotIn(path[-1], {"12,18"})


class ReservationTests(AppTestCase):
    """AC6 同格冲突 / AC7 相向穿边 / AC8 首尾相接。"""

    def setUp(self):
        super().setUp()
        self.store = ReservationStore("warehouse-24")

    def test_same_cell_same_tick_conflicts(self):
        entries = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=5)]
        ok, conflicts, _ = self.store.acquire("AGV-01", 1, 5, entries, 1)
        self.assertTrue(ok)

        ok2, conflicts2, _ = self.store.acquire("AGV-02", 1, 5, entries, 1)
        self.assertFalse(ok2)
        self.assertTrue(conflicts2)
        self.assertIn(vertex_key("warehouse-24", "12,18", 5), conflicts2)

    def test_opposite_edge_conflicts(self):
        forward = [ReservationEntry(kind="edge", cell_from="12,18", cell_to="12,17", tick=7)]
        ok, _, _ = self.store.acquire("AGV-01", 1, 5, forward, 1)
        self.assertTrue(ok)

        backward = [ReservationEntry(kind="edge", cell_from="12,17", cell_to="12,18", tick=7)]
        ok2, conflicts, _ = self.store.acquire("AGV-02", 1, 5, backward, 1)
        self.assertFalse(ok2, "相向穿边必须被阻止")
        self.assertIn(edge_key("warehouse-24", "12,18", "12,17", 7), conflicts)

    def test_handoff_next_tick_allowed(self):
        """AC8：A 在 tick t 离开格点，B 在 t+1 进入，允许预约。"""
        first = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=5)]
        second = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=6)]
        ok, _, _ = self.store.acquire("AGV-01", 1, 5, first, 1)
        self.assertTrue(ok)
        ok2, conflicts, _ = self.store.acquire("AGV-02", 1, 5, second, 1)
        self.assertTrue(ok2, "首尾相接必须允许")
        self.assertEqual(conflicts, [])

    def test_batch_acquire_is_atomic(self):
        ok, _, _ = self.store.acquire(
            "AGV-01", 1, 5, [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=5)], 1)
        self.assertTrue(ok)
        batch = [
            ReservationEntry(kind="vertex", cell_from="12,17", cell_to="", tick=5),
            ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=5),
        ]
        ok2, conflicts, _ = self.store.acquire("AGV-02", 1, 5, batch, 1)
        self.assertFalse(ok2, "批量预约必须整体失败")
        self.assertTrue(conflicts)
        # 失败的批次不能留下任何部分占用
        self.assertIsNone(self.store.owner_of(vertex_key("warehouse-24", "12,17", 5)))

    def test_high_priority_preempts_low_priority_future_reservation(self):
        low = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=9)]
        self.store.acquire("AGV-LOW", 1, 1, low, 1)
        high = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=9)]
        ok, conflicts, preempted = self.store.acquire("AGV-HIGH", 1, 9, high, 2)
        self.assertTrue(ok)
        self.assertEqual(preempted, ["AGV-LOW"])
        self.assertEqual(self.store.owner_of(vertex_key("warehouse-24", "12,18", 9)), "AGV-HIGH")

    def test_low_priority_cannot_preempt_high_priority(self):
        high = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=9)]
        self.store.acquire("AGV-HIGH", 1, 9, high, 1)
        low = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=9)]
        ok, conflicts, preempted = self.store.acquire("AGV-LOW", 1, 1, low, 2)
        self.assertFalse(ok)
        self.assertTrue(conflicts)
        self.assertEqual(preempted, [])

    def test_expired_reservation_released(self):
        entries = [ReservationEntry(kind="vertex", cell_from="12,18", cell_to="", tick=3)]
        self.store.acquire("AGV-01", 1, 5, entries, 1)
        self.assertIsNotNone(self.store.get(vertex_key("warehouse-24", "12,18", 3)))
        released = self.store.release_expired(3 + 500)
        self.assertGreaterEqual(released, 1)
        self.assertIsNone(self.store.get(vertex_key("warehouse-24", "12,18", 3)))


if __name__ == "__main__":
    unittest.main()
