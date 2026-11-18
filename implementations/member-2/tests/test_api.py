"""REST 接口与 SSE 事件流。

覆盖 AC1（服务可访问）、AC17（查询与筛选）、AC18（统计）以及前端依赖的接口契约。
"""

from __future__ import annotations

import json
import unittest
import urllib.error
import urllib.request

from helpers import AppTestCase
from wms.server import serve_in_background


class ApiTestCase(AppTestCase):
    """在后台线程启动真实 HTTP 服务，用真实请求验证接口。"""

    def setUp(self):
        super().setUp()
        self.httpd, self.thread, self.base = serve_in_background(self.app, host="127.0.0.1", port=0)
        self.addCleanup(self._shutdown)

    def _shutdown(self):
        try:
            self.httpd.shutdown()
            self.httpd.server_close()
        except Exception:  # pragma: no cover
            pass

    # ------------------------------------------------------------ 请求辅助
    def call(self, path, method="GET", body=None):
        url = f"{self.base}{path}"
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read().decode("utf-8")
                status = response.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8")
            status = exc.code
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw


class HealthAndMetaTests(ApiTestCase):
    def test_health(self):
        status, payload = self.call("/api/v1/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        self.assertIn("tick", payload)
        self.assertIn("version", payload)

    def test_meta(self):
        status, payload = self.call("/api/v1/meta")
        self.assertEqual(status, 200)
        self.assertEqual(payload["tickSeconds"], 1)
        self.assertEqual(payload["width"], 24)
        self.assertEqual(payload["height"], 24)

    def test_scenarios_listed(self):
        status, payload = self.call("/api/v1/scenarios")
        self.assertEqual(status, 200)
        ids = {item["id"] for item in payload["items"]}
        for expected in ("single-agv", "peak-10", "peak-30", "head-to-head",
                         "cycle-deadlock", "roadblock", "low-battery", "rush-order"):
            self.assertIn(expected, ids)


class MasterDataApiTests(ApiTestCase):
    def test_sku_lifecycle(self):
        status, payload = self.call("/api/v1/skus", "POST",
                                    {"code": "API-SKU-1", "name": "接口测试", "batchRule": "fifo"})
        self.assertEqual(status, 201)
        sku_id = payload["id"]
        self.assertEqual(payload["batchRule"], "fifo")

        status, payload = self.call(f"/api/v1/skus/{sku_id}", "PUT",
                                    {"code": "API-SKU-1", "name": "改名"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["name"], "改名")

        status, payload = self.call(f"/api/v1/skus/{sku_id}", "DELETE")
        self.assertEqual(status, 200)

        status, payload = self.call(f"/api/v1/skus/{sku_id}")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_duplicate_sku_returns_409(self):
        self.call("/api/v1/skus", "POST", {"code": "DUP-API", "name": "一"})
        status, payload = self.call("/api/v1/skus", "POST", {"code": "DUP-API", "name": "二"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "DUPLICATE_CODE")

    def test_validation_error_shape(self):
        status, payload = self.call("/api/v1/skus", "POST", {"name": "缺编码"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        self.assertEqual(payload["error"]["code"], "VALIDATION_FAILED")
        self.assertIn("message", payload["error"])
        self.assertIn("details", payload["error"])

    def test_pagination_shape(self):
        status, payload = self.call("/api/v1/orders?page=0&size=2")
        self.assertEqual(status, 200)
        for key in ("items", "page", "size", "total"):
            self.assertIn(key, payload)
        self.assertLessEqual(len(payload["items"]), 2)


class TaskAndOrderApiTests(ApiTestCase):
    def test_order_release_and_task_query(self):
        status, payload = self.call("/api/v1/orders", "POST", {
            "code": "API-OUT-1", "priority": 6, "requester": "tester",
            "lines": [{"skuCode": "SKU-001", "qty": 2}],
        })
        self.assertEqual(status, 201)
        order_id = payload["id"]
        self.assertEqual(payload["status"], "draft")
        self.assertEqual(len(payload["lines"]), 1)
        self.assertEqual(payload["lines"][0]["skuCode"], "SKU-001")

        status, payload = self.call(f"/api/v1/orders/{order_id}/release", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "released")
        self.assertEqual(len(payload["taskIds"]), 1)

        status, payload = self.call("/api/v1/tasks")
        self.assertEqual(status, 200)
        self.assertTrue(payload["total"] >= 1)
        task = payload["items"][0]
        for key in ("id", "status", "priority", "pickupLocationId", "dropLocationId",
                    "assignedAgvId", "planVersion", "retryCount", "pickupCell", "dropCell"):
            self.assertIn(key, task)

    def test_task_filter_by_status_and_priority(self):
        status, payload = self.call("/api/v1/tasks?status=pending")
        self.assertEqual(status, 200)
        for task in payload["items"]:
            self.assertEqual(task["status"], "pending")

        status, payload = self.call("/api/v1/tasks?priorityMin=5&priorityMax=5")
        self.assertEqual(status, 200)
        for task in payload["items"]:
            self.assertGreaterEqual(task["priority"], 5)
            self.assertLessEqual(task["priority"], 5)

    def test_task_priority_update(self):
        status, payload = self.call("/api/v1/tasks")
        task_id = payload["items"][0]["id"]
        status, updated = self.call(f"/api/v1/tasks/{task_id}/priority", "POST", {"priority": 9})
        self.assertEqual(status, 200)
        self.assertEqual(updated["priority"], 9)

    def test_task_cancel_and_reassign(self):
        status, payload = self.call("/api/v1/tasks")
        task_id = payload["items"][0]["id"]
        status, cancelled = self.call(f"/api/v1/tasks/{task_id}/cancel", "POST", {})
        self.assertEqual(status, 200)
        self.assertEqual(cancelled["status"], "cancelled")

    def test_unknown_route_returns_404(self):
        status, payload = self.call("/api/v1/not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "NOT_FOUND")

    def test_wrong_method_returns_405_like_error(self):
        status, payload = self.call("/api/v1/health", "POST", {})
        self.assertGreaterEqual(status, 400)
        self.assertIn("error", payload)


class SimulationApiTests(ApiTestCase):
    def test_sim_state_and_controls(self):
        status, payload = self.call("/api/v1/sim/state")
        self.assertEqual(status, 200)
        for key in ("tick", "running", "speed", "seed", "scenarioId", "mapVersion",
                    "agvs", "tasks", "orders", "alerts", "stats", "lastEventSeq"):
            self.assertIn(key, payload)

        status, payload = self.call("/api/v1/sim/step", "POST", {"ticks": 3})
        self.assertEqual(status, 200)
        self.assertEqual(payload["tick"], 3)

        status, payload = self.call("/api/v1/sim/speed", "POST", {"speed": 4})
        self.assertEqual(status, 200)
        self.assertEqual(payload["speed"], 4)

        status, payload = self.call("/api/v1/sim/speed", "POST", {"speed": 99})
        self.assertEqual(status, 400)

        status, payload = self.call("/api/v1/sim/reset", "POST", {"scenarioId": "peak-10"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["scenarioId"], "peak-10")
        self.assertEqual(len(payload["agvs"]), 10)

    def test_inject_fault_and_recover(self):
        status, payload = self.call("/api/v1/sim/inject", "POST",
                                    {"type": "fault", "target": "AGV-01", "code": "E-SENSOR"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "fault")

        status, payload = self.call("/api/v1/sim/inject", "POST", {"type": "recover", "target": "AGV-01"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "idle")

    def test_inject_roadblock_updates_map(self):
        status, payload = self.call("/api/v1/map")
        self.assertEqual(status, 200)
        before = payload["version"]

        status, payload = self.call("/api/v1/map/roadblocks", "POST",
                                    {"cells": ["12,7"], "reason": "接口测试封路"})
        self.assertEqual(status, 201)
        self.assertNotEqual(payload["mapVersion"], before)

        status, payload = self.call("/api/v1/map")
        self.assertEqual(status, 200)
        self.assertTrue(payload["roadblocks"])
        self.assertEqual(payload["roadblocks"][0]["cellId"], "12,7")
        self.assertTrue(payload["roadblocks"][0]["active"])

        record_id = payload["roadblocks"][0]["id"]
        status, payload = self.call(f"/api/v1/map/roadblocks/{record_id}", "DELETE")
        self.assertEqual(status, 200)

    def test_agv_detail_contains_plan(self):
        status, payload = self.call("/api/v1/agvs/AGV-01")
        self.assertEqual(status, 200)
        for key in ("id", "status", "x", "y", "battery", "trail", "plan"):
            self.assertIn(key, payload)

    def test_events_query(self):
        self.call("/api/v1/sim/step", "POST", {"ticks": 5})
        status, payload = self.call("/api/v1/events?size=10")
        self.assertEqual(status, 200)
        self.assertIn("items", payload)
        self.assertIn("total", payload)
        for event in payload["items"]:
            for key in ("id", "type", "tick", "aggregateId", "payload", "occurredAt"):
                self.assertIn(key, event)


class AnalyticsApiTests(ApiTestCase):
    def test_summary_matches_records(self):
        self.call("/api/v1/sim/reset", "POST", {"scenarioId": "peak-10"})
        for _ in range(80):
            self.app.sim.step_once()

        status, summary = self.call("/api/v1/analytics/summary")
        self.assertEqual(status, 200)
        self.assertEqual(summary["orders"]["total"], 10)
        self.assertEqual(summary["tasks"]["total"], 10)
        self.assertEqual(summary["agvs"]["total"], 10)
        self.assertEqual(sum(summary["tasks"]["byStatus"].values()), 10)
        self.assertEqual(sum(summary["orders"]["byStatus"].values()), 10)
        self.assertEqual(summary["completedTasks"], summary["tasks"]["byStatus"].get("completed", 0))

    def test_utilization_and_throughput(self):
        self.call("/api/v1/sim/reset", "POST", {"scenarioId": "peak-10"})
        for _ in range(80):
            self.app.sim.step_once()

        status, payload = self.call("/api/v1/analytics/utilization")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["items"]), 10)
        for item in payload["items"]:
            self.assertGreaterEqual(item["utilization"], 0)
            self.assertLessEqual(item["utilization"], 100)

        status, payload = self.call("/api/v1/analytics/throughput?window=600&bucket=30")
        self.assertEqual(status, 200)
        self.assertIn("buckets", payload)
        self.assertIn("definition" if "definition" in payload else "unit", payload)

    def test_bottlenecks_shape(self):
        status, payload = self.call("/api/v1/analytics/bottlenecks?top=5")
        self.assertEqual(status, 200)
        self.assertIn("cells", payload)
        self.assertIn("aisles", payload)
        self.assertIn("metric", payload)


class StaticAndStreamTests(ApiTestCase):
    def test_index_and_assets_served(self):
        status, payload = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn("<html", str(payload).lower())

        status, payload = self.call("/app.js")
        self.assertEqual(status, 200)
        self.assertGreater(len(str(payload)), 1000)

        status, payload = self.call("/style.css")
        self.assertEqual(status, 200)

    def _read_sse_state(self, response, max_lines: int = 60) -> dict:
        """按行读取 SSE，直到拿到一条 data: 消息（SSE 是流式响应，不能按字节数读）。"""
        for _ in range(max_lines):
            line = response.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if text.startswith("data: "):
                return json.loads(text[6:])
        return {}

    def test_sse_stream_pushes_state_events(self):
        request = urllib.request.Request(f"{self.base}/api/v1/stream")
        with urllib.request.urlopen(request, timeout=20) as response:
            self.assertTrue(response.headers.get("Content-Type", "").startswith("text/event-stream"))
            self.call("/api/v1/sim/step", "POST", {"ticks": 3})
            payload = self._read_sse_state(response)
        self.assertTrue(payload, "SSE 必须推送 state 消息")
        self.assertIn("events", payload)
        self.assertIn("tick", payload)
        self.assertIn("seq", payload)
        self.assertTrue(payload["events"], "state 消息应包含本 tick 的事件")
        for event in payload["events"]:
            self.assertIn("type", event)
            self.assertIn("tick", event)

    def test_sse_data_is_valid_json(self):
        request = urllib.request.Request(f"{self.base}/api/v1/stream")
        with urllib.request.urlopen(request, timeout=20) as response:
            self.call("/api/v1/sim/step", "POST", {"ticks": 2})
            payload = self._read_sse_state(response)
        self.assertIsInstance(payload, dict)
        self.assertIn("seq", payload)
        self.assertIn("tick", payload)
        self.assertIn("events", payload)


if __name__ == "__main__":
    unittest.main()
