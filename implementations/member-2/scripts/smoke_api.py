"""REST + SSE 冒烟联调脚本（开发期使用，不属于自动化测试套件）。

用法：python scripts\\smoke_api.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wms.app import App  # noqa: E402
from wms.server import serve_in_background  # noqa: E402


def request(url: str, method: str = "GET", body: dict | None = None, timeout: float = 10.0):
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="wms-smoke-")
    os.environ["WMS_DATA_DIR"] = tmp
    app = App(data_dir=tmp)
    httpd, thread, base = serve_in_background(app, host="127.0.0.1", port=0)
    print(f"服务地址 {base}")
    failures = []

    def check(name, cond, detail=""):
        status = "OK " if cond else "FAIL"
        print(f"[{status}] {name} {detail}")
        if not cond:
            failures.append(name)

    try:
        code, raw = request(f"{base}/api/v1/health")
        check("health 200", code == 200, raw[:120])
        code, raw = request(f"{base}/api/v1/meta")
        check("meta 200", code == 200, raw[:160])

        code, raw = request(f"{base}/api/v1/scenarios")
        data = json.loads(raw)
        check("scenarios >= 8", code == 200 and len(data["items"]) >= 8, f"items={len(data.get('items', []))}")

        code, raw = request(f"{base}/api/v1/map")
        data = json.loads(raw)
        check("map cells", code == 200 and len(data["cells"]) > 100, f"cells={len(data.get('cells', []))}")

        code, raw = request(f"{base}/api/v1/agvs")
        data = json.loads(raw)
        check("agvs list", code == 200 and "items" in data, raw[:120])

        code, raw = request(f"{base}/api/v1/orders")
        data = json.loads(raw)
        check("orders list", code == 200 and "total" in data, raw[:120])

        code, raw = request(f"{base}/api/v1/tasks")
        data = json.loads(raw)
        check("tasks list", code == 200 and "total" in data, raw[:120])

        code, raw = request(f"{base}/api/v1/inventory")
        check("inventory list", code == 200, raw[:100])

        code, raw = request(f"{base}/api/v1/events?size=5")
        data = json.loads(raw)
        check("events list", code == 200 and "items" in data, raw[:120])

        code, raw = request(f"{base}/api/v1/analytics/summary")
        check("analytics summary", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/analytics/throughput")
        check("analytics throughput", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/analytics/utilization")
        check("analytics utilization", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/analytics/bottlenecks")
        check("analytics bottlenecks", code == 200, raw[:120])

        # 写操作
        code, raw = request(f"{base}/api/v1/skus", "POST", {"code": "SMOKE-1", "name": "冒烟 SKU"})
        check("create sku 201", code == 201, raw[:140])
        sku = json.loads(raw)
        code, raw = request(f"{base}/api/v1/skus/{sku['id']}", "PUT", {"code": "SMOKE-1", "name": "冒烟 SKU 改名"})
        check("update sku 200", code == 200, raw[:140])
        code, raw = request(f"{base}/api/v1/skus/{sku['id']}", "DELETE")
        check("delete sku 200", code == 200, raw[:140])

        code, raw = request(f"{base}/api/v1/orders", "POST", {
            "code": "SMOKE-OUT-1", "priority": 7, "requester": "smoke",
            "dropLocationCode": "DROP-1", "lines": [{"skuCode": "SKU-001", "qty": 1}],
        })
        check("create order 201", code == 201, raw[:200])
        order = json.loads(raw)
        code, raw = request(f"{base}/api/v1/orders/{order['id']}/release", "POST", {})
        check("release order 200", code == 200, raw[:200])

        code, raw = request(f"{base}/api/v1/sim/step", "POST", {"ticks": 3})
        check("sim step 200", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/sim/start", "POST", {})
        check("sim start 200", code == 200, raw[:120])
        code, raw = request(f"{base}/api/v1/sim/pause", "POST", {})
        check("sim pause 200", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/sim/speed", "POST", {"speed": 4})
        check("sim speed 200", code == 200, raw[:120])

        code, raw = request(f"{base}/api/v1/sim/inject", "POST", {"type": "roadblock", "cells": ["12,7"], "reason": "smoke"})
        check("inject roadblock 200", code == 200, raw[:140])

        # 错误处理
        code, raw = request(f"{base}/api/v1/tasks/not-exist")
        check("404 structured error", code == 404 and "error" in json.loads(raw), raw[:160])
        code, raw = request(f"{base}/api/v1/skus", "POST", {"name": "缺 code"})
        check("400 structured error", code == 400 and json.loads(raw)["error"]["code"] == "VALIDATION_FAILED", raw[:160])

        # 静态文件
        code, raw = request(f"{base}/")
        check("static index 200", code == 200 and "<html" in raw.lower(), raw[:80])
        code, raw = request(f"{base}/app.js")
        check("static app.js 200", code == 200 and len(raw) > 1000, f"len={len(raw)}")

        # SSE
        req = urllib.request.Request(f"{base}/api/v1/stream")
        with urllib.request.urlopen(req, timeout=15) as resp:
            check("sse content-type", resp.headers.get("Content-Type", "").startswith("text/event-stream"),
                  resp.headers.get("Content-Type", ""))
            request(f"{base}/api/v1/sim/step", "POST", {"ticks": 2})
            chunk = resp.read(400)
            text = chunk.decode("utf-8", errors="replace")
            check("sse state event", "event: state" in text, text[:200].replace("\r\n", " | "))
    finally:
        httpd.shutdown()
        httpd.server_close()
        app.close()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{failures}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
