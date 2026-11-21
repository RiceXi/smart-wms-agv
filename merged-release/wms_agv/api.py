from __future__ import annotations

import json
import mimetypes
import re
import sys
import time
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from .observability import (
    JsonLogger,
    MetricsRegistry,
    collect_service_metrics,
)
from .service import ServiceError, WmsService


_DYNAMIC_ROUTES = (
    (re.compile(r"^/api/skus/[^/]+$"), "/api/skus/{id}"),
    (re.compile(r"^/api/locations/[^/]+$"), "/api/locations/{id}"),
    (re.compile(r"^/api/orders/[^/]+/release$"), "/api/orders/{id}/release"),
    (re.compile(r"^/api/orders/[^/]+/cancel$"), "/api/orders/{id}/cancel"),
    (re.compile(r"^/api/tasks/[^/]+/priority$"), "/api/tasks/{id}/priority"),
    (re.compile(r"^/api/tasks/[^/]+/cancel$"), "/api/tasks/{id}/cancel"),
    (re.compile(r"^/api/tasks/[^/]+/reassign$"), "/api/tasks/{id}/reassign"),
    (re.compile(r"^/api/agvs/[^/]+/fault$"), "/api/agvs/{id}/fault"),
    (re.compile(r"^/api/agvs/[^/]+/recover$"), "/api/agvs/{id}/recover"),
    (re.compile(r"^/api/agvs/[^/]+/battery$"), "/api/agvs/{id}/battery"),
)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def normalize_route(path: str) -> str:
    for pattern, replacement in _DYNAMIC_ROUTES:
        if pattern.match(path):
            return replacement
    if path.startswith("/api/") or path in {
        "/health",
        "/ready",
        "/metrics",
        "/openapi.json",
        "/api/openapi.json",
    }:
        return path
    return "/static/{asset}" if path not in {"", "/"} else "/"


class ApiServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        service: WmsService,
        static_dir: Path,
        *,
        logger: JsonLogger | None = None,
        metrics: MetricsRegistry | None = None,
        version: str = "1.0.0",
    ):
        self.service = service
        self.static_dir = static_dir
        self.logger = logger or JsonLogger()
        self.metrics = metrics or MetricsRegistry()
        self.version = version
        super().__init__(address, ApiHandler)

    def handle_error(self, request, client_address) -> None:
        error = sys.exception()
        if isinstance(
            error, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
        ):
            return
        self.logger.error(
            "http.connection_error",
            client_address=str(client_address),
            error=str(error) if error else "unknown",
            traceback=traceback.format_exc(),
        )
        super().handle_error(request, client_address)


class ApiHandler(BaseHTTPRequestHandler):
    server: ApiServer

    def log_message(self, fmt: str, *args) -> None:
        return

    def parse_request(self) -> bool:
        parsed = super().parse_request()
        if parsed:
            incoming = self.headers.get("X-Request-ID", "")
            if _REQUEST_ID_PATTERN.match(incoming):
                self._request_id = incoming
        return parsed

    def handle_one_request(self) -> None:
        self._request_id = uuid4().hex[:16]
        self._request_started = time.perf_counter()
        self._response_status = None
        try:
            super().handle_one_request()
        finally:
            if getattr(self, "command", None):
                elapsed = time.perf_counter() - self._request_started
                route = normalize_route(urlparse(self.path).path)
                status = self._response_status or 499
                labels = {
                    "method": self.command,
                    "route": route,
                    "status": str(status),
                }
                self.server.metrics.inc("http_requests_total", labels)
                duration_labels = {"method": self.command, "route": route}
                self.server.metrics.inc(
                    "http_request_duration_seconds_sum", duration_labels, elapsed
                )
                self.server.metrics.inc(
                    "http_request_duration_seconds_count", duration_labels
                )
                self.server.logger.info(
                    "http.request",
                    request_id=self._request_id,
                    method=self.command,
                    route=route,
                    status=status,
                    duration_ms=round(elapsed * 1000, 3),
                    client=self.client_address[0] if self.client_address else None,
                )

    def send_response(self, code: int, message: str | None = None) -> None:
        self._response_status = int(code)
        super().send_response(code, message)

    def end_headers(self) -> None:
        self.send_header("X-Request-ID", self._request_id)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send_json(self, payload: object, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, body: str, content_type: str, status: int = 200) -> None:
        encoded = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, error: ServiceError) -> None:
        self._send_json(
            {"error": {"code": error.code, "message": error.message}}, error.status
        )

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ServiceError("INVALID_JSON", "请求体必须是合法 JSON") from exc
        if not isinstance(payload, dict):
            raise ServiceError("INVALID_JSON", "请求体必须是 JSON 对象")
        return payload

    def _serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (self.server.static_dir / relative).resolve()
        root = self.server.static_dir.resolve()
        if not candidate.is_relative_to(root) or not candidate.is_file():
            raise ServiceError("RESOURCE_NOT_FOUND", "静态文件不存在", 404)
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_event_stream(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(
                    self.server.service.snapshot(),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(0.5)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_GET(self) -> None:
        try:
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query)
            service = self.server.service
            if path == "/health":
                self._send_json(
                    {
                        "status": "ok",
                        "version": self.server.version,
                        "tick": service.tick,
                    }
                )
                return
            if path == "/ready":
                try:
                    database_ready = service.repo.ping()
                    if not database_ready:
                        raise RuntimeError("database ping failed")
                except Exception as error:
                    self.server.logger.error(
                        "readiness.failed",
                        request_id=self._request_id,
                        error=str(error),
                    )
                    self._send_json(
                        {
                            "status": "not_ready",
                            "version": self.server.version,
                            "checks": {"database": "failed"},
                        },
                        503,
                    )
                    return
                self._send_json(
                    {
                        "status": "ready",
                        "version": self.server.version,
                        "tick": service.tick,
                        "checks": {"database": "ok"},
                    }
                )
                return
            if path == "/metrics":
                collect_service_metrics(service, self.server.metrics)
                self._send_text(
                    self.server.metrics.render(),
                    "text/plain; version=0.0.4; charset=utf-8",
                )
                return
            if path in {"/openapi.json", "/api/openapi.json"}:
                self._serve_static("/openapi.json")
                return
            if path in {"/api/state", "/api/snapshot"}:
                self._send_json(service.snapshot())
                return
            if path == "/api/skus":
                self._send_json(service.snapshot()["skus"])
                return
            if path == "/api/locations":
                self._send_json(service.snapshot()["locations"])
                return
            if path == "/api/inventory":
                self._send_json(service.snapshot()["inventory"])
                return
            if path == "/api/orders":
                orders = service.snapshot()["orders"]
                status = query.get("status", [None])[0]
                if status:
                    orders = [item for item in orders if item["status"] == status]
                self._send_json(orders)
                return
            if path == "/api/tasks":
                tasks = service.snapshot()["tasks"]
                for field in ("status", "agv_id"):
                    value = query.get(field, [None])[0]
                    if value:
                        tasks = [
                            item
                            for item in tasks
                            if str(item.get(field) if field != "agv_id" else item.get("assigned_agv_id")) == value
                        ]
                self._send_json(tasks)
                return
            if path == "/api/agvs":
                self._send_json(service.snapshot()["agvs"])
                return
            if path == "/api/events":
                tick_from = query.get("tick_from", [None])[0]
                tick_to = query.get("tick_to", [None])[0]
                self._send_json(
                    service.list_events(
                        int(tick_from) if tick_from is not None else None,
                        int(tick_to) if tick_to is not None else None,
                    )
                )
                return
            if path == "/api/analytics":
                self._send_json(service.snapshot()["analytics"])
                return
            if path == "/api/stream":
                self._serve_event_stream()
                return
            self._serve_static(path)
        except ServiceError as error:
            self._error(error)
        except Exception as error:  # pragma: no cover - final HTTP safety net
            self.server.logger.error(
                "http.unhandled_error",
                request_id=self._request_id,
                method="GET",
                path=self.path,
                error=str(error),
                traceback=traceback.format_exc(),
            )
            self._send_json(
                {"error": {"code": "INTERNAL_ERROR", "message": str(error)}}, 500
            )

    def do_POST(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            service = self.server.service
            if path == "/api/skus":
                self._send_json(service.create_sku(body), 201)
                return
            if path == "/api/locations":
                self._send_json(service.create_location(body), 201)
                return
            if path == "/api/inventory":
                self._send_json(service.create_inventory(body), 201)
                return
            if path == "/api/orders":
                self._send_json(service.create_order(body), 201)
                return
            if path.startswith("/api/orders/") and path.endswith("/release"):
                order_id = path.split("/")[3]
                self._send_json(service.release_order(order_id))
                return
            if path.startswith("/api/orders/") and path.endswith("/cancel"):
                order_id = path.split("/")[3]
                self._send_json(service.cancel_order(order_id))
                return
            if path.startswith("/api/tasks/") and path.endswith("/priority"):
                task_id = path.split("/")[3]
                self._send_json(
                    service.set_task_priority(task_id, int(body.get("priority", 5)))
                )
                return
            if path.startswith("/api/tasks/") and path.endswith("/cancel"):
                task_id = path.split("/")[3]
                self._send_json(service.cancel_task(task_id))
                return
            if path.startswith("/api/tasks/") and path.endswith("/reassign"):
                task_id = path.split("/")[3]
                self._send_json(service.reassign_task(task_id))
                return
            if path.startswith("/api/agvs/") and path.endswith("/fault"):
                agv_id = path.split("/")[3]
                self._send_json(service.report_agv_fault(agv_id))
                return
            if path.startswith("/api/agvs/") and path.endswith("/recover"):
                agv_id = path.split("/")[3]
                self._send_json(service.recover_agv(agv_id))
                return
            if path.startswith("/api/agvs/") and path.endswith("/battery"):
                agv_id = path.split("/")[3]
                self._send_json(
                    service.set_agv_battery(agv_id, float(body.get("battery", 15)))
                )
                return
            if path == "/api/sim/start":
                self._send_json(service.start_simulation())
                return
            if path == "/api/sim/pause":
                self._send_json(service.pause_simulation())
                return
            if path == "/api/sim/step":
                self._send_json(service.step_simulation(int(body.get("ticks", 1))))
                return
            if path == "/api/sim/speed":
                self._send_json(service.set_speed(int(body.get("speed", 1))))
                return
            if path == "/api/sim/roadblock":
                self._send_json(
                    service.inject_roadblock(
                        int(body["x"]), int(body["y"]), bool(body.get("blocked", True))
                    )
                )
                return
            if path == "/api/sim/deadlock":
                self._send_json(service.inject_deadlock_demo())
                return
            if path == "/api/sim/reset":
                self._send_json(service.reset_simulation())
                return
            if path == "/api/demo/reset":
                self._send_json(service.reset_demo())
                return
            if path == "/api/demo/order":
                self._send_json(service.create_demo_order(), 201)
                return
            raise ServiceError("RESOURCE_NOT_FOUND", "接口不存在", 404)
        except ServiceError as error:
            self._error(error)
        except (KeyError, ValueError) as error:
            self._error(ServiceError("VALIDATION_FAILED", str(error)))
        except Exception as error:  # pragma: no cover
            self.server.logger.error(
                "http.unhandled_error",
                request_id=self._request_id,
                method="POST",
                path=self.path,
                error=str(error),
                traceback=traceback.format_exc(),
            )
            self._send_json(
                {"error": {"code": "INTERNAL_ERROR", "message": str(error)}}, 500
            )

    def do_PUT(self) -> None:
        try:
            path = urlparse(self.path).path
            body = self._read_json()
            service = self.server.service
            parts = [item for item in path.split("/") if item]
            if len(parts) == 3 and parts[:2] == ["api", "skus"]:
                self._send_json(service.update_sku(parts[2], body))
                return
            if len(parts) == 3 and parts[:2] == ["api", "locations"]:
                self._send_json(service.update_location(parts[2], body))
                return
            raise ServiceError("RESOURCE_NOT_FOUND", "接口不存在", 404)
        except ServiceError as error:
            self._error(error)
        except Exception as error:  # pragma: no cover
            self.server.logger.error(
                "http.unhandled_error",
                request_id=self._request_id,
                method="PUT",
                path=self.path,
                error=str(error),
                traceback=traceback.format_exc(),
            )
            self._send_json(
                {"error": {"code": "INTERNAL_ERROR", "message": str(error)}}, 500
            )

    def do_DELETE(self) -> None:
        try:
            path = urlparse(self.path).path
            service = self.server.service
            parts = [item for item in path.split("/") if item]
            if len(parts) == 3 and parts[:2] == ["api", "skus"]:
                service.delete_sku(parts[2])
                self._send_json({"deleted": parts[2]})
                return
            if len(parts) == 3 and parts[:2] == ["api", "locations"]:
                service.delete_location(parts[2])
                self._send_json({"deleted": parts[2]})
                return
            raise ServiceError("RESOURCE_NOT_FOUND", "接口不存在", 404)
        except ServiceError as error:
            self._error(error)
        except Exception as error:  # pragma: no cover
            self.server.logger.error(
                "http.unhandled_error",
                request_id=self._request_id,
                method="DELETE",
                path=self.path,
                error=str(error),
                traceback=traceback.format_exc(),
            )
            self._send_json(
                {"error": {"code": "INTERNAL_ERROR", "message": str(error)}}, 500
            )


def create_server(
    service: WmsService,
    host: str,
    port: int,
    static_dir: Path,
    *,
    logger: JsonLogger | None = None,
    metrics: MetricsRegistry | None = None,
    version: str = "1.0.0",
) -> ApiServer:
    return ApiServer(
        (host, port),
        service,
        static_dir,
        logger=logger,
        metrics=metrics,
        version=version,
    )
