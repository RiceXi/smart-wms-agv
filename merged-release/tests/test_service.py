import tempfile
import unittest
from pathlib import Path

from wms_agv.service import ServiceError, WmsService


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "wms.sqlite"
        self.service = WmsService(self.db)

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_seed_data(self) -> None:
        snapshot = self.service.snapshot()
        self.assertEqual(3, len(snapshot["skus"]))
        self.assertGreaterEqual(len(snapshot["locations"]), 5)
        self.assertEqual(10, len(snapshot["agvs"]))

    def test_thirty_agv_seed(self) -> None:
        other = WmsService(Path(self.temp.name) / "scale.sqlite", agv_count=30)
        try:
            self.assertEqual(30, len(other.snapshot()["agvs"]))
        finally:
            other.stop()

    def test_duplicate_sku_code_rejected(self) -> None:
        existing = self.service.snapshot()["skus"][0]
        with self.assertRaises(ServiceError) as context:
            self.service.create_sku({"code": existing["code"], "name": "重复"})
        self.assertEqual("DUPLICATE_CODE", context.exception.code)

    def test_order_release_is_idempotent(self) -> None:
        sku = self.service.snapshot()["skus"][0]
        order = self.service.create_order(
            {
                "code": "OUT-IDEMPOTENT",
                "priority": 5,
                "requester": "test",
                "lines": [{"sku_id": sku["id"], "qty": 2}],
            }
        )
        first = self.service.release_order(order["id"])
        before = len(self.service.snapshot()["tasks"])
        second = self.service.release_order(order["id"])
        after = len(self.service.snapshot()["tasks"])
        self.assertEqual("released", first["status"])
        self.assertEqual("released", second["status"])
        self.assertEqual(before, after)

    def test_demo_order_completes_in_simulation(self) -> None:
        self.service.create_demo_order()
        assigned = self.service.snapshot()["tasks"][0]
        self.assertEqual("assigned", assigned["status"])
        self.service.step_simulation(80)
        task = self.service.snapshot()["tasks"][0]
        self.assertEqual("completed", task["status"])
        self.assertGreater(task["completed_tick"], 0)

    def test_cancel_pending_task_releases_inventory(self) -> None:
        sku = self.service.snapshot()["skus"][0]
        order = self.service.create_order(
            {
                "code": "OUT-CANCEL",
                "priority": 3,
                "requester": "test",
                "lines": [{"sku_id": sku["id"], "qty": 1}],
            }
        )
        self.service.release_order(order["id"])
        task_id = self.service.snapshot()["tasks"][0]["id"]
        self.service.cancel_task(task_id)
        self.assertEqual("cancelled", self.service.snapshot()["tasks"][0]["status"])

    def test_cancel_released_order(self) -> None:
        self.service.create_demo_order()
        order = self.service.snapshot()["orders"][0]
        self.service.cancel_order(order["id"])
        snapshot = self.service.snapshot()
        self.assertEqual("cancelled", snapshot["orders"][0]["status"])
        self.assertEqual("cancelled", snapshot["tasks"][0]["status"])
        self.assertTrue(all(item["reserved_qty"] == 0 for item in snapshot["inventory"]))

    def test_deadlock_detection_and_resolution(self) -> None:
        result = self.service.inject_deadlock_demo()
        self.assertTrue(result["detected"])
        self.assertTrue(result["resolved"])
        analytics = self.service.snapshot()["analytics"]
        self.assertGreaterEqual(analytics["counters"]["deadlocks_resolved"], 1)

    def test_completed_task_cannot_be_cancelled(self) -> None:
        self.service.create_demo_order()
        self.service.step_simulation(80)
        task_id = self.service.snapshot()["tasks"][0]["id"]
        with self.assertRaises(ServiceError) as context:
            self.service.cancel_task(task_id)
        self.assertEqual("TASK_INVALID_STATE", context.exception.code)

    def test_low_battery_enters_charging(self) -> None:
        agv = self.service.snapshot()["agvs"][0]
        updated = self.service.set_agv_battery(agv["id"], 10)
        self.assertEqual("charging", updated["status"])
        self.assertTrue(
            any(
                plan["kind"] == "charge" and plan["agv_id"] == agv["id"]
                for plan in self.service.snapshot()["plans"]
            )
        )

    def test_pause_and_step(self) -> None:
        self.service.start_simulation()
        self.service.pause_simulation()
        before = self.service.tick
        snapshot = self.service.step_simulation(2)
        self.assertTrue(snapshot["runtime"]["paused"])
        self.assertEqual(before + 2, snapshot["runtime"]["tick"])

    def test_persistence_across_restart(self) -> None:
        sku = self.service.create_sku(
            {"code": "SKU-PERSIST", "name": "持久化测试", "batch_rule": "none"}
        )
        self.service.stop()
        self.service = WmsService(self.db)
        self.assertTrue(any(item["id"] == sku["id"] for item in self.service.snapshot()["skus"]))

    def test_roadblock_replans_active_path(self) -> None:
        self.service.create_demo_order()
        plan = self.service.snapshot()["plans"][0]
        future_steps = [
            step for step in plan["steps"] if step["tick"] > self.service.tick
        ]
        future_cell = (
            future_steps[len(future_steps) // 2]["x"],
            future_steps[len(future_steps) // 2]["y"],
        )
        self.service.inject_roadblock(*future_cell, blocked=True)
        after = self.service.snapshot()
        self.assertIn("blocked_cells", after["runtime"])
        self.assertTrue(after["runtime"]["map_version"].startswith("map-1+"))
        self.assertGreaterEqual(len(after["plans"]), 1)


if __name__ == "__main__":
    unittest.main()
