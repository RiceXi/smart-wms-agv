import json
import threading
import unittest
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory

from wms_agv.api import create_server
from wms_agv.service import WmsService


class AcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.service = WmsService(Path(self.temp.name) / "acceptance.sqlite")

    def tearDown(self) -> None:
        self.service.stop()
        self.temp.cleanup()

    def test_master_data_and_inventory_validation(self) -> None:
        sku = self.service.snapshot()["skus"][0]
        shelf = next(
            loc for loc in self.service.snapshot()["locations"] if loc["type"] == "shelf"
        )
        row = self.service.create_inventory(
            {"sku_id": sku["id"], "location_id": shelf["id"], "qty": 5, "batch": "T"}
        )
        self.assertEqual(5, row["qty"])

    def test_full_order_flow_and_stats(self) -> None:
        self.service.create_demo_order()
        self.service.step_simulation(100)
        snapshot = self.service.snapshot()
        self.assertEqual(1, snapshot["analytics"]["tasks_completed"])
        self.assertEqual(1, snapshot["analytics"]["orders_completed"])

    def test_fixed_scenario_preserves_path_geometry(self) -> None:
        def run_once(root: str):
            service = WmsService(Path(root) / "scenario.sqlite")
            service.create_demo_order()
            plan = service.snapshot()["plans"][0]
            geometry = [
                (step["x"], step["y"], step["action"])
                for step in plan["steps"]
            ]
            service.stop()
            return geometry

        first = run_once(self.temp.name)
        second = run_once(self.temp.name + "/second")
        self.assertEqual(first, second)

    def test_http_health_and_snapshot(self) -> None:
        server = create_server(
            self.service, "127.0.0.1", 0, Path(__file__).parents[1] / "static"
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health") as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.assertEqual("ok", payload["status"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state") as response:
                snapshot = json.loads(response.read().decode("utf-8"))
            self.assertIn("agvs", snapshot)
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/stream", timeout=2
            ) as response:
                line = response.readline().decode("utf-8")
            self.assertTrue(line.startswith("data: "))
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
