from __future__ import annotations

import io
import json
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from wms_agv.api import create_server
from wms_agv.observability import JsonLogger, MetricsRegistry
from wms_agv.service import WmsService


class SystemFoundationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.service = WmsService(Path(self.temp.name) / "wms.sqlite", agv_count=10)
        self.log_stream = io.StringIO()
        self.logger = JsonLogger(stream=self.log_stream)
        self.metrics = MetricsRegistry()
        self.server = create_server(
            self.service,
            "127.0.0.1",
            0,
            Path(__file__).resolve().parents[1] / "static",
            logger=self.logger,
            metrics=self.metrics,
            version="test-1.0",
        )
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="test-api", daemon=True
        )
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.service.stop()
        self.temp.cleanup()

    def request(self, path: str, headers: dict[str, str] | None = None):
        request = urllib.request.Request(
            f"{self.base_url}{path}", headers=headers or {}
        )
        return urllib.request.urlopen(request, timeout=3)

    def test_health_readiness_and_security_headers(self) -> None:
        request_id = "member-one-system-test"
        with self.request("/health", {"X-Request-ID": request_id}) as response:
            payload = json.loads(response.read())
            self.assertEqual(200, response.status)
            self.assertEqual("ok", payload["status"])
            self.assertEqual("test-1.0", payload["version"])
            self.assertEqual(request_id, response.headers["X-Request-ID"])
            self.assertEqual("nosniff", response.headers["X-Content-Type-Options"])
            self.assertEqual("DENY", response.headers["X-Frame-Options"])

        with self.request("/ready") as response:
            payload = json.loads(response.read())
            self.assertEqual("ready", payload["status"])
            self.assertEqual("ok", payload["checks"]["database"])

    def test_metrics_expose_http_and_business_state(self) -> None:
        with self.request("/health"):
            pass
        with self.request("/metrics") as response:
            body = response.read().decode("utf-8")
            self.assertEqual(200, response.status)
            self.assertIn("wms_tick", body)
            self.assertIn("wms_agvs 10", body)
            self.assertIn('route="/health"', body)
            self.assertIn('status="200"', body)

    def test_openapi_contract_is_served_and_valid_json(self) -> None:
        with self.request("/api/openapi.json") as response:
            document = json.loads(response.read())
            self.assertEqual("SmartWMS API", document["info"]["title"])
            self.assertIn("/api/orders/{id}/release", document["paths"])

    def test_structured_request_log_contains_response_request_id(self) -> None:
        with self.request("/health") as response:
            request_id = response.headers["X-Request-ID"]
            response.read()
        deadline = time.monotonic() + 1
        request_record = None
        while time.monotonic() < deadline and request_record is None:
            records = [
                json.loads(line)
                for line in self.log_stream.getvalue().splitlines()
                if line.strip()
            ]
            request_record = next(
                (item for item in records if item["message"] == "http.request"),
                None,
            )
            if request_record is None:
                time.sleep(0.01)
        self.assertIsNotNone(request_record)
        self.assertEqual(request_id, request_record["request_id"])
        self.assertEqual("GET", request_record["method"])
        self.assertEqual(200, request_record["status"])


if __name__ == "__main__":
    unittest.main()
