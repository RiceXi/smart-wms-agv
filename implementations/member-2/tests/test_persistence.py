"""AC16 持久化：关闭程序再启动，数据仍在，运行态可从快照恢复。"""

from __future__ import annotations

import shutil
import unittest
from pathlib import Path

from helpers import AppTestCase
from wms.app import App


class PersistenceTests(AppTestCase):
    def test_data_survives_restart(self):
        service = self.app.service
        sku = service.create_sku({"code": "PERSIST-SKU", "name": "持久化物料"})
        location = service.create_location({
            "code": "PERSIST-LOC", "zone": "A", "x": 2, "y": 22, "type": "floor", "capacity": 1,
        })
        service.add_inventory({"skuId": sku["id"], "locationId": location["id"], "qty": 42, "batch": "B1"})
        order = service.create_order({
            "code": "OUT-PERSIST-1", "priority": 4, "requester": "tester",
            "lines": [{"skuCode": "PERSIST-SKU", "qty": 2}],
        })
        service.release_order(order["id"])
        for _ in range(12):
            self.app.sim.step_once()
        tick_before = self.app.sim.tick
        agv_before = {a.id: (a.x, a.y, round(a.battery, 2)) for a in self.app.sim.agvs.values()}
        db_path = self.app.db_path
        data_dir = self.app.data_dir

        # 关闭程序
        self.app.close()

        # 重新启动
        reopened = App(data_dir=data_dir)
        try:
            self.assertEqual(reopened.db_path, db_path)
            sku2 = reopened.service.get_sku(sku["id"])
            self.assertEqual(sku2["code"], "PERSIST-SKU")
            self.assertEqual(reopened.service.get_location(location["id"])["code"], "PERSIST-LOC")
            inventory = reopened.service.list_inventory()["items"]
            self.assertTrue(any(item["qty"] == 42 for item in inventory))

            order2 = reopened.service.get_order(order["id"])
            self.assertEqual(order2["status"], "released")
            self.assertTrue(order2["taskIds"])

            # 运行态恢复
            self.assertEqual(reopened.sim.tick, tick_before)
            agv_after = {a.id: (a.x, a.y, round(a.battery, 2)) for a in reopened.sim.agvs.values()}
            self.assertEqual(agv_after, agv_before)
            self.assertEqual(reopened.sim.scenario_id, self.app.sim.scenario_id)

            # 恢复后还能继续推进
            reopened.sim.step_once()
            self.assertEqual(reopened.sim.tick, tick_before + 1)
        finally:
            reopened.close()

    def test_map_version_restored(self):
        self.app.sim.apply_roadblock(["12,7"], "持久化封路")
        version = self.app.maps.current.version
        data_dir = self.app.data_dir
        self.app.close()

        reopened = App(data_dir=data_dir)
        try:
            self.assertEqual(reopened.maps.current.version, version)
            self.assertEqual(len(reopened.maps.active_roadblocks()), 1)
        finally:
            reopened.close()

    def test_events_are_persisted(self):
        for _ in range(10):
            self.app.sim.step_once()
        count_before = self.app.db.query_one("SELECT COUNT(*) AS c FROM events")["c"]
        self.assertGreater(count_before, 0)
        data_dir = self.app.data_dir
        self.app.close()

        reopened = App(data_dir=data_dir)
        try:
            count_after = reopened.db.query_one("SELECT COUNT(*) AS c FROM events")["c"]
            self.assertEqual(count_after, count_before)
        finally:
            reopened.close()


class DataDirectoryTests(unittest.TestCase):
    def test_data_dir_created_on_demand(self):
        import tempfile

        base = Path(tempfile.mkdtemp(prefix="wms-datadir-"))
        try:
            target = base / "nested" / "data"
            app = App(data_dir=target)
            try:
                self.assertTrue((target / "wms.db").exists())
            finally:
                app.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)

    def test_reset_clears_previous_scenario(self):
        import tempfile

        base = Path(tempfile.mkdtemp(prefix="wms-reset-"))
        try:
            app = App(data_dir=base)
            try:
                app.reset("peak-10", 20261118)
                self.assertEqual(len(app.sim.agvs), 10)
                app.reset("single-agv", 20261118)
                self.assertEqual(len(app.sim.agvs), 1)
                self.assertEqual(app.sim.tick, 0)
                self.assertEqual(app.db.query_one("SELECT COUNT(*) AS c FROM orders")["c"], 1)
            finally:
                app.close()
        finally:
            shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
