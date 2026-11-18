"""HTTP 服务：REST 接口、SSE 事件流与前端静态资源。

只依赖 Python 标准库：
- `http.server.ThreadingHTTPServer` 提供并发处理；
- `/api/v1/*` 转交 `Api.dispatch`，业务逻辑不写在 HTTP 层；
- `/api/v1/stream` 用 Server-Sent Events 推送每 tick 的事件增量；
- 其余路径回落到 `web/` 下的前端静态文件。

请求日志按行输出 JSON，便于离线环境用 `>` 重定向后用 `jq` 或文本工具检索。
"""

from __future__ import annotations

import json
import logging
import mimetypes
import queue
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from . import config
from .api import Api
from .app import App, WEB_DIR
from .errors import AppError
from .util import dumps

LOGGER = logging.getLogger("wms.server")

SSE_KEEPALIVE_SECONDS = 5.0
"""SSE 心跳间隔（秒），与冻结契约第 11 节一致。"""

SSE_POLL_SECONDS = 0.05
"""SSE 轮询间隔（秒），决定事件推送的最大延迟。"""

STATIC_ROOT = WEB_DIR.resolve()


def configure_logging(level: str = "INFO") -> None:
    """把根日志器配置成一行一条 JSON 的结构化日志。"""

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": record.getMessage(),
            }
            extra = getattr(record, "fields", None)
            if isinstance(extra, dict):
                payload.update(extra)
            if record.exc_info:
                payload["exception"] = self.formatException(record.exc_info)
            return json.dumps(payload, ensure_ascii=False, default=str)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)


class WmsHandler(BaseHTTPRequestHandler):
    """把 HTTP 请求映射到 `Api`、SSE 或静态文件。"""

    server_version = f"SmartWMS/{config.VERSION}"
    protocol_version = "HTTP/1.1"

    # ------------------------------------------------------------ 基础工具
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003 - 基类签名
        LOGGER.debug(fmt % args)

    @property
    def app(self) -> App:
        return self.server.app  # type: ignore[attr-defined]

    @property
    def api(self) -> Api:
        return self.server.api  # type: ignore[attr-defined]

    def _send_json(self, status: int, payload) -> None:
        body = dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_bytes(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = self.headers.get("Content-Length")
        if not length:
            return {}
        try:
            size = int(length)
        except (TypeError, ValueError):
            raise AppError("Content-Length 不是整数", {"contentLength": length}) from None
        if size <= 0:
            return {}
        raw = self.rfile.read(size)
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError("请求体不是合法 JSON", {"reason": str(exc)}) from exc
        if payload is None:
            return {}
        if not isinstance(payload, dict):
            raise AppError("请求体必须是 JSON 对象", {"type": type(payload).__name__})
        return payload

    # ------------------------------------------------------------ 入口
    def do_GET(self) -> None:  # noqa: N802 - 基类命名
        self._route("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._route("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._route("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._route("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._route("DELETE")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _route(self, method: str) -> None:
        started = time.perf_counter()
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        status = 500
        try:
            if path == "/api/v1/stream" and method == "GET":
                self._serve_sse()
                status = 200
                return
            if path.startswith("/api/"):
                body = self._read_json_body()
                status, payload = self.api.dispatch(method, path, query, body)
                self._send_json(status, payload)
                return
            if method in ("GET", "HEAD"):
                status = self._serve_static(path)
                return
            status = 405
            self._send_json(status, {"error": {"code": "VALIDATION_FAILED",
                                              "message": f"路径 {path} 不支持 {method} 方法",
                                              "details": {"path": path}}})
        except AppError as exc:
            status = exc.status
            self._send_json(status, exc.to_dict())
        except (BrokenPipeError, ConnectionResetError):
            LOGGER.info("客户端中断连接", extra={"fields": {"path": path}})
            return
        except Exception as exc:  # pragma: no cover - 兜底
            LOGGER.exception("未捕获的服务端异常", extra={"fields": {"path": path}})
            status = 500
            self._send_json(status, {"error": {
                "code": "INTERNAL_ERROR",
                "message": f"服务端内部错误：{exc}",
                "details": {"path": path},
            }})
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            LOGGER.info("http_request", extra={"fields": {
                "method": method,
                "path": path,
                "status": status,
                "durationMs": round(elapsed, 2),
            }})

    # ------------------------------------------------------------ 静态文件
    def _serve_static(self, path: str) -> int:
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        target = (STATIC_ROOT / rel).resolve()
        if not str(target).startswith(str(STATIC_ROOT)) or not target.is_file():
            if path == "/favicon.ico":
                self._send_bytes(204, b"", "image/x-icon")
                return 204
            self._send_json(404, {"error": {"code": "NOT_FOUND",
                                            "message": f"静态资源不存在：{path}",
                                            "details": {"path": path}}})
            return 404
        content_type, _ = mimetypes.guess_type(target.name)
        if content_type is None:
            content_type = "application/octet-stream"
        if content_type.startswith(("text/", "application/javascript", "application/json")):
            content_type += "; charset=utf-8"
        self._send_bytes(200, target.read_bytes(), content_type)
        return 200

    # ------------------------------------------------------------ SSE
    def _write_chunk(self, text: str) -> None:
        data = text.encode("utf-8")
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def _serve_sse(self) -> None:
        app = self.app
        bus = app.bus
        subscription = bus.subscribe()
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._write_chunk(": connected\n\n")

            pending: list[dict] = []
            last_beat = time.monotonic()
            while True:
                try:
                    pending.append(subscription.get(timeout=SSE_POLL_SECONDS))
                    while True:
                        pending.append(subscription.get_nowait())
                except queue.Empty:
                    pass
                if pending:
                    payload = {"seq": bus.seq, "tick": app.sim.tick, "events": pending}
                    self._write_chunk(f"event: state\ndata: {dumps(payload)}\n\n")
                    pending = []
                    last_beat = time.monotonic()
                elif time.monotonic() - last_beat >= SSE_KEEPALIVE_SECONDS:
                    self._write_chunk(": keepalive\n\n")
                    last_beat = time.monotonic()
        except (BrokenPipeError, ConnectionResetError, OSError):
            LOGGER.info("sse_client_disconnected", extra={"fields": {"subscribers": bus.subscriber_count()}})
        finally:
            bus.unsubscribe(subscription)
            try:
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass


class WmsServer(ThreadingHTTPServer):
    """带 `app` 与 `api` 的 HTTP 服务。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler_class, app: App):
        super().__init__(address, handler_class)
        self.app = app
        self.api = Api(app)


def serve(app: App, host: str = "127.0.0.1", port: int = 8080, open_browser: bool = False) -> None:
    """启动 HTTP 服务并阻塞运行，直到 Ctrl+C。"""

    httpd = WmsServer((host, port), WmsHandler, app)
    app.sim.start_thread()
    LOGGER.info("server_started", extra={"fields": {
        "host": host,
        "port": port,
        "version": config.VERSION,
        "dataDir": str(app.data_dir),
        "scenarioId": app.sim.scenario_id,
        "seed": app.sim.seed,
        "tick": app.sim.tick,
    }})
    print(f"SmartWMS 个人版已启动：http://{host}:{port}/  (Ctrl+C 停止)", flush=True)
    try:
        httpd.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\n正在停止服务……", flush=True)
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()
        LOGGER.info("server_stopped", extra={"fields": {"host": host, "port": port}})


def serve_in_background(app: App, host: str = "127.0.0.1", port: int = 0):
    """在后台线程启动服务，返回 `(httpd, thread, base_url)`，供自动化测试使用。"""

    httpd = WmsServer((host, port), WmsHandler, app)
    app.sim.start_thread()
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05},
                              name="http-server", daemon=True)
    thread.start()
    base_url = f"http://{httpd.server_address[0]}:{httpd.server_address[1]}"
    return httpd, thread, base_url
