"""AC2 主数据 / AC3 出库订单 / AC4 任务状态机。"""

from __future__ import annotations

import unittest

from helpers import AppTestCase
from wms.errors import (
    ConflictError,
    DuplicateCodeError,
    InsufficientInventoryError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)


class MasterDataTests(AppTestCase):
    """AC2：至少 3 个 SKU、5 个库位和初始库存；重复编码、非法坐标、负库存被拒绝。"""

    def test_create_sku_location_and_inventory(self):
        service = self.app.service
        for index in range(3):
            sku = service.create_sku({"code": f"TEST-SKU-{index}", "name": f"测试物料{index}",
                                      "spec": "10x10", "batchRule": "fifo"})
            self.assertTrue(sku["id"])
            self.assertEqual(sku["batch_rule"], "fifo")

        for index in range(5):
            location = service.create_location({
                "code": f"TEST-LOC-{index}", "zone": "A", "x": 1 + index, "y": 22,
                "type": "floor", "capacity": 2,
            })
            self.assertTrue(location["id"])

        sku = service.get_sku(service.list_skus()["items"][0]["id"])
        location = service.get_location(service.list_locations()["items"][0]["id"])
        inventory = service.add_inventory({"skuId": sku["id"], "locationId": location["id"],
                                            "qty": 100, "batch": "B1"})
        self.assertEqual(inventory["qty"], 100)
        self.assertEqual(inventory["reserved_qty"], 0)
        self.assertEqual(inventory["availableQty"], 100)

    def test_duplicate_sku_code_rejected(self):
        self.app.service.create_sku({"code": "DUP-1", "name": "第一"})
        with self.assertRaises(DuplicateCodeError):
            self.app.service.create_sku({"code": "DUP-1", "name": "第二"})

    def test_duplicate_location_code_rejected(self):
        self.app.service.create_location({"code": "DUP-LOC", "zone": "A", "x": 1, "y": 22,
                                          "type": "floor", "capacity": 1})
        with self.assertRaises(DuplicateCodeError):
            self.app.service.create_location({"code": "DUP-LOC", "zone": "A", "x": 2, "y": 22,
                                              "type": "floor", "capacity": 1})

    def test_invalid_coordinates_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.service.create_location({"code": "BAD-X", "zone": "A", "x": 999, "y": 0,
                                              "type": "floor", "capacity": 1})
        with self.assertRaises(ValidationError):
            self.app.service.create_location({"code": "BAD-Y", "zone": "A", "x": 0, "y": -3,
                                              "type": "floor", "capacity": 1})

    def test_negative_inventory_rejected(self):
        sku = self.app.service.list_skus()["items"][0]
        location = self.app.service.list_locations()["items"][0]
        with self.assertRaises(ValidationError):
            self.app.service.add_inventory({"skuId": sku["id"], "locationId": location["id"], "qty": -5})
        inventory = self.app.service.list_inventory()["items"][0]
        with self.assertRaises(ValidationError):
            self.app.service.adjust_inventory(inventory["id"], {"delta": -10_000, "reason": "盘点"})

    def test_invalid_enums_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.service.create_sku({"code": "BAD-RULE", "name": "非法批次规则", "batchRule": "random"})
        with self.assertRaises(ValidationError):
            self.app.service.create_location({"code": "BAD-TYPE", "zone": "A", "x": 1, "y": 22,
                                              "type": "warehouse", "capacity": 1})

    def test_delete_sku_with_inventory_conflicts(self):
        with self.assertRaises(ConflictError):
            self.app.service.delete_sku(self.app.service.list_skus()["items"][0]["id"])

    def test_missing_resource_raises_not_found(self):
        with self.assertRaises(NotFoundError):
            self.app.service.get_sku("sku-does-not-exist")


class OrderTests(AppTestCase):
    """AC3：多明细订单释放后生成任务；重复释放不重复扣减。"""

    def test_create_and_release_order_creates_tasks(self):
        service = self.app.service
        before = self.task_statuses(self.app)
        order = service.create_order({
            "code": "OUT-TEST-001", "priority": 6, "requester": "tester",
            "dropLocationCode": "DROP-1",
            "lines": [{"skuCode": "SKU-001", "qty": 2}, {"skuCode": "SKU-002", "qty": 3}],
        })
        self.assertEqual(order["status"], "draft")

        released = service.release_order(order["id"])
        self.assertEqual(released["status"], "released")
        self.assertEqual(len(released["taskIds"]), 2)
        after = self.task_statuses(self.app)
        self.assertEqual(after.get("pending", 0), before.get("pending", 0) + 2)

    def test_release_is_idempotent(self):
        service = self.app.service
        reserved_before = self.reserved_qty(self.app)
        order = service.create_order({
            "code": "OUT-TEST-IDEM", "priority": 5, "requester": "tester",
            "lines": [{"skuCode": "SKU-001", "qty": 4}],
        })
        service.release_order(order["id"])
        reserved_after_first = self.reserved_qty(self.app)
        self.assertEqual(reserved_after_first, reserved_before + 4)

        again = service.release_order(order["id"])
        self.assertEqual(again["status"], "released")
        self.assertEqual(self.reserved_qty(self.app), reserved_after_first)
        self.assertEqual(len(again["taskIds"]), 1)

    def test_release_without_stock_raises_and_keeps_state(self):
        service = self.app.service
        reserved_before = self.reserved_qty(self.app)
        order = service.create_order({
            "code": "OUT-TEST-NOSTOCK", "priority": 5, "requester": "tester",
            "lines": [{"skuCode": "SKU-001", "qty": 99_999}],
        })
        with self.assertRaises(InsufficientInventoryError):
            service.release_order(order["id"])
        self.assertEqual(service.get_order(order["id"])["status"], "draft")
        # 分配失败不得留下任何部分预占
        self.assertEqual(self.reserved_qty(self.app), reserved_before)

    def test_cancel_order_rolls_back_reservation(self):
        service = self.app.service
        reserved_before = self.reserved_qty(self.app)
        order = service.create_order({
            "code": "OUT-TEST-CANCEL", "priority": 5, "requester": "tester",
            "lines": [{"skuCode": "SKU-001", "qty": 3}],
        })
        service.release_order(order["id"])
        self.assertEqual(self.reserved_qty(self.app), reserved_before + 3)
        cancelled = service.cancel_order(order["id"])
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(self.reserved_qty(self.app), reserved_before)

    def test_unknown_sku_in_order_rejected(self):
        with self.assertRaises(NotFoundError):
            self.app.service.create_order({
                "code": "OUT-TEST-BADSKU", "priority": 5, "requester": "tester",
                "lines": [{"skuCode": "NO-SUCH-SKU", "qty": 1}],
            })


class TaskStateMachineTests(AppTestCase):
    """AC4：pending -> assigned -> running -> completed；非法流转被拒绝。"""

    def test_legal_transitions(self):
        service = self.app.service
        task_id = service.list_tasks()["items"][0]["id"]
        service.assign_task(task_id, "AGV-01", 1)
        self.assertEqual(service.get_task(task_id)["status"], "assigned")
        service.start_task(task_id, "AGV-01", 1)
        self.assertEqual(service.get_task(task_id)["status"], "running")
        service.complete_task(task_id, {"skuId": "sku-1"})
        self.assertEqual(service.get_task(task_id)["status"], "completed")

    def test_illegal_transition_rejected(self):
        service = self.app.service
        task_id = service.list_tasks()["items"][0]["id"]
        # pending 不能直接 completed
        with self.assertRaises(InvalidStateError):
            service._transition_task(task_id, "completed", event_type="task.completed")
        # completed 是终态
        service.assign_task(task_id, "AGV-01", 1)
        service.start_task(task_id, "AGV-01", 1)
        service.complete_task(task_id, {})
        with self.assertRaises(InvalidStateError):
            service._transition_task(task_id, "running", event_type="task.started")

    def test_reassign_returns_task_to_pool(self):
        service = self.app.service
        task_id = service.list_tasks()["items"][0]["id"]
        service.assign_task(task_id, "AGV-01", 1)
        task = service.reassign_task(task_id)
        self.assertEqual(task["status"], "pending")
        self.assertIsNone(task["assigned_agv_id"])

    def test_priority_change_recorded(self):
        service = self.app.service
        task_id = service.list_tasks()["items"][0]["id"]
        task = service.set_task_priority(task_id, {"priority": 9})
        self.assertEqual(task["priority"], 9)

    def test_cancel_task_from_pending(self):
        service = self.app.service
        task_id = service.list_tasks()["items"][0]["id"]
        task = service.cancel_task(task_id, reason="测试取消")
        self.assertEqual(task["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
