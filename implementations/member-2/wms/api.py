"""REST 接口实现。

所有接口都返回 JSON，路径与响应结构见 `docs/api-contract.md`（冻结版）。
错误统一由 `errors.AppError` 转换成 `{error:{code,message,details}}`。
"""

from __future__ import annotations

import re

from . import config
from .analytics import Analytics
from .app import App
from .errors import NotFoundError, ValidationError
from .service import agv_to_dict, order_to_dict, task_to_dict
from .util import require_int, row_to_camel


def _camel_rows(rows) -> list[dict]:
    return [row_to_camel(row) for row in rows]


def _order_payload(order: dict) -> dict:
    """把订单结构统一成 camelCase，并整理明细与任务列表。"""
    payload = order_to_dict(order)
    lines = []
    for line in order.get("lines", []):
        item = row_to_camel(line)
        lines.append({
            "id": item.get("id"),
            "skuId": item.get("skuId"),
            "skuCode": item.get("skuCode", ""),
            "skuName": item.get("skuName", ""),
            "qty": item.get("qty"),
            "batch": item.get("batch", ""),
            "locationId": item.get("locationId"),
            "pickupLocationId": item.get("pickupLocationId"),
            "assignedAgvId": item.get("assignedAgvId"),
        })
    payload["lines"] = lines
    payload["taskIds"] = list(order.get("taskIds", []))
    return payload



def _int_param(query: dict, name: str, default=None):
    value = query.get(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"参数 {name} 必须是整数", {name: value}) from exc


def _page(query: dict) -> tuple[int, int]:
    page = max(0, _int_param(query, "page", 0) or 0)
    size = _int_param(query, "size", config.DEFAULT_PAGE_SIZE) or config.DEFAULT_PAGE_SIZE
    size = max(1, min(size, config.MAX_PAGE_SIZE))
    return page, size


def _rows_to_tasks(rows) -> list[dict]:
    tasks = [task_to_dict(row) for row in rows]
    return tasks


class Api:
    """把 HTTP 请求映射到业务调用。"""

    def __init__(self, app: App):
        self.app = app
        self.routes: list[tuple[str, re.Pattern, callable]] = []
        self._register()

    # ------------------------------------------------------------ 路由
    def _add(self, method: str, pattern: str, handler) -> None:
        self.routes.append((method, re.compile(f"^{pattern}$"), handler))

    def _register(self) -> None:
        self._add("GET", r"/api/v1/health", self.health)
        self._add("GET", r"/api/v1/meta", self.meta)
        self._add("GET", r"/api/v1/scenarios", self.scenarios)

        self._add("GET", r"/api/v1/skus", self.list_skus)
        self._add("POST", r"/api/v1/skus", self.create_sku)
        self._add("GET", r"/api/v1/skus/(?P<sku_id>[^/]+)", self.get_sku)
        self._add("PUT", r"/api/v1/skus/(?P<sku_id>[^/]+)", self.update_sku)
        self._add("DELETE", r"/api/v1/skus/(?P<sku_id>[^/]+)", self.delete_sku)

        self._add("GET", r"/api/v1/locations", self.list_locations)
        self._add("POST", r"/api/v1/locations", self.create_location)
        self._add("GET", r"/api/v1/locations/(?P<location_id>[^/]+)", self.get_location)
        self._add("PUT", r"/api/v1/locations/(?P<location_id>[^/]+)", self.update_location)
        self._add("DELETE", r"/api/v1/locations/(?P<location_id>[^/]+)", self.delete_location)

        self._add("GET", r"/api/v1/inventory", self.list_inventory)
        self._add("POST", r"/api/v1/inventory", self.add_inventory)
        self._add("POST", r"/api/v1/inventory/(?P<inventory_id>[^/]+)/adjust", self.adjust_inventory)
        self._add("GET", r"/api/v1/inventory/txns", self.list_inventory_txns)

        self._add("GET", r"/api/v1/orders", self.list_orders)
        self._add("POST", r"/api/v1/orders", self.create_order)
        self._add("GET", r"/api/v1/orders/(?P<order_id>[^/]+)", self.get_order)
        self._add("POST", r"/api/v1/orders/(?P<order_id>[^/]+)/release", self.release_order)
        self._add("POST", r"/api/v1/orders/(?P<order_id>[^/]+)/cancel", self.cancel_order)

        self._add("GET", r"/api/v1/tasks", self.list_tasks)
        self._add("GET", r"/api/v1/tasks/(?P<task_id>[^/]+)", self.get_task)
        self._add("POST", r"/api/v1/tasks/(?P<task_id>[^/]+)/priority", self.set_task_priority)
        self._add("POST", r"/api/v1/tasks/(?P<task_id>[^/]+)/cancel", self.cancel_task)
        self._add("POST", r"/api/v1/tasks/(?P<task_id>[^/]+)/reassign", self.reassign_task)

        self._add("GET", r"/api/v1/agvs", self.list_agvs)
        self._add("GET", r"/api/v1/agvs/(?P<agv_id>[^/]+)", self.get_agv)

        self._add("GET", r"/api/v1/map", self.get_map)
        self._add("POST", r"/api/v1/map/roadblocks", self.add_roadblock)
        self._add("DELETE", r"/api/v1/map/roadblocks/(?P<record_id>[^/]+)", self.remove_roadblock)

        self._add("GET", r"/api/v1/sim/state", self.sim_state)
        self._add("POST", r"/api/v1/sim/reset", self.sim_reset)
        self._add("POST", r"/api/v1/sim/start", self.sim_start)
        self._add("POST", r"/api/v1/sim/pause", self.sim_pause)
        self._add("POST", r"/api/v1/sim/step", self.sim_step)
        self._add("POST", r"/api/v1/sim/speed", self.sim_speed)
        self._add("POST", r"/api/v1/sim/inject", self.sim_inject)

        self._add("GET", r"/api/v1/events", self.list_events)
        self._add("GET", r"/api/v1/analytics/summary", self.analytics_summary)
        self._add("GET", r"/api/v1/analytics/throughput", self.analytics_throughput)
        self._add("GET", r"/api/v1/analytics/utilization", self.analytics_utilization)
        self._add("GET", r"/api/v1/analytics/bottlenecks", self.analytics_bottlenecks)
        self._add("GET", r"/api/v1/analytics/tasks/(?P<task_id>[^/]+)/timeline", self.analytics_timeline)

    # ------------------------------------------------------------ 分发
    def dispatch(self, method: str, path: str, query: dict, body: dict):
        allowed = set()
        for route_method, pattern, handler in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            if route_method != method:
                allowed.add(route_method)
                continue
            with self.app.lock:
                return handler(query=query, body=body, **match.groupdict())
        if allowed:
            raise ValidationError(f"接口 {path} 不支持 {method} 方法", {"allowed": sorted(allowed)})
        raise NotFoundError(f"接口不存在：{path}", {"path": path})

    # ------------------------------------------------------------ 元信息
    def health(self, query, body):
        return 200, {
            "status": "ok",
            "tick": self.app.sim.tick,
            "running": self.app.sim.running,
            "seed": self.app.sim.seed,
            "version": config.VERSION,
        }

    def meta(self, query, body):
        wm = self.app.maps.current
        return 200, {
            "tickSeconds": wm.tick_seconds,
            "mapId": wm.map_id,
            "mapVersion": wm.version,
            "width": wm.width,
            "height": wm.height,
            "seed": self.app.sim.seed,
            "scenarioId": self.app.sim.scenario_id,
            "scenarios": self.app.sim.scenario_id,
        }

    def scenarios(self, query, body):
        from .scenarios import list_scenarios

        return 200, {"items": list_scenarios()}

    # ------------------------------------------------------------ 主数据
    def list_skus(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_skus(q=query.get("q"), page=page, size=size)
        result["items"] = _camel_rows(result["items"])
        return 200, result

    def create_sku(self, query, body):
        return 201, row_to_camel(self.app.service.create_sku(body))

    def update_sku(self, query, body, sku_id):
        return 200, row_to_camel(self.app.service.update_sku(sku_id, body))

    def delete_sku(self, query, body, sku_id):
        self.app.service.delete_sku(sku_id)
        return 200, {"deleted": sku_id}

    def get_sku(self, query, body, sku_id):
        return 200, row_to_camel(self.app.service.get_sku(sku_id))

    def list_locations(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_locations(
            ltype=query.get("type"), zone=query.get("zone"), q=query.get("q"), page=page, size=size
        )
        result["items"] = _camel_rows(result["items"])
        return 200, result

    def create_location(self, query, body):
        return 201, row_to_camel(self.app.service.create_location(body))

    def update_location(self, query, body, location_id):
        return 200, row_to_camel(self.app.service.update_location(location_id, body))

    def delete_location(self, query, body, location_id):
        self.app.service.delete_location(location_id)
        return 200, {"deleted": location_id}

    def get_location(self, query, body, location_id):
        return 200, row_to_camel(self.app.service.get_location(location_id))

    def list_inventory(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_inventory(
            sku_id=query.get("skuId"), location_id=query.get("locationId"), page=page, size=size
        )
        result["items"] = _camel_rows(result["items"])
        return 200, result

    def add_inventory(self, query, body):
        return 201, row_to_camel(self.app.service.add_inventory(body))

    def adjust_inventory(self, query, body, inventory_id):
        return 200, row_to_camel(self.app.service.adjust_inventory(inventory_id, body))

    def list_inventory_txns(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_inventory_txns(
            sku_id=query.get("skuId"), location_id=query.get("locationId"), page=page, size=size
        )
        result["items"] = _camel_rows(result["items"])
        return 200, result

    # ------------------------------------------------------------ 订单
    def list_orders(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_orders(status=query.get("status"), code=query.get("code"), page=page, size=size)
        result["items"] = [order_to_dict(item) for item in result["items"]]
        return 200, result

    def create_order(self, query, body):
        return 201, _order_payload(self.app.service.create_order(body))

    def get_order(self, query, body, order_id):
        return 200, _order_payload(self.app.service.get_order(order_id))

    def release_order(self, query, body, order_id):
        return 200, _order_payload(self.app.service.release_order(order_id))

    def cancel_order(self, query, body, order_id):
        return 200, _order_payload(self.app.service.cancel_order(order_id))

    # ------------------------------------------------------------ 任务
    def list_tasks(self, query, body):
        page, size = _page(query)
        result = self.app.service.list_tasks(
            status=query.get("status"),
            agv_id=query.get("agvId"),
            order_id=query.get("orderId"),
            priority_min=_int_param(query, "priorityMin"),
            priority_max=_int_param(query, "priorityMax"),
            page=page,
            size=size,
        )
        result["items"] = _rows_to_tasks(result["items"])
        for task in result["items"]:
            pickup = self.app.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["pickupLocationId"],))
            drop = self.app.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["dropLocationId"],))
            task["pickupCell"] = f"{pickup['x']},{pickup['y']}" if pickup else ""
            task["dropCell"] = f"{drop['x']},{drop['y']}" if drop else ""
        return 200, result

    def get_task(self, query, body, task_id):
        task = task_to_dict(self.app.service.get_task(task_id))
        return 200, task

    def set_task_priority(self, query, body, task_id):
        return 200, task_to_dict(self.app.service.set_task_priority(task_id, body))

    def cancel_task(self, query, body, task_id):
        return 200, task_to_dict(self.app.service.cancel_task(task_id))

    def reassign_task(self, query, body, task_id):
        return 200, task_to_dict(self.app.service.reassign_task(task_id))

    # ------------------------------------------------------------ AGV 与地图
    def list_agvs(self, query, body):
        sim = self.app.sim
        items = [sim.agvs[agv_id].to_dict() for agv_id in sorted(sim.agvs)]
        status = query.get("status")
        if status:
            items = [item for item in items if item["status"] == status]
        return 200, {"items": items, "total": len(items)}

    def get_agv(self, query, body, agv_id):
        sim = self.app.sim
        if agv_id not in sim.agvs:
            raise NotFoundError(f"AGV 不存在：{agv_id}", {"agvId": agv_id})
        agv = sim.agvs[agv_id]
        payload = agv.to_dict()
        payload["plan"] = agv.plan.to_dict() if agv.plan else None
        return 200, payload

    def get_map(self, query, body):
        wm = self.app.maps.current
        payload = wm.to_dict()
        payload["roadblocks"] = [
            {
                "id": row["id"],
                "cellId": row["cell_id"],
                "reason": row["reason"],
                "active": bool(row["active"]),
                "createdAt": row["created_at"],
            }
            for row in self.app.maps.active_roadblocks()
        ]
        return 200, payload

    def add_roadblock(self, query, body):
        cells = body.get("cells")
        if not isinstance(cells, list) or not cells:
            raise ValidationError("封路需要 cells 数组", {"field": "cells"})
        reason = body.get("reason", "")
        self.app.sim.apply_roadblock([str(c) for c in cells], reason)
        return 201, {"mapVersion": self.app.maps.current.version, "cells": cells}

    def remove_roadblock(self, query, body, record_id):
        self.app.sim.remove_roadblock(record_id)
        return 200, {"mapVersion": self.app.maps.current.version}

    # ------------------------------------------------------------ 仿真
    def sim_state(self, query, body):
        return 200, self.app.sim.snapshot()

    def sim_reset(self, query, body):
        scenario_id = body.get("scenarioId", "single-agv")
        seed = body.get("seed")
        seed = int(seed) if seed is not None else None
        return 200, self.app.reset(scenario_id, seed)

    def sim_start(self, query, body):
        self.app.sim.start()
        return 200, {"running": True, "tick": self.app.sim.tick}

    def sim_pause(self, query, body):
        self.app.sim.pause()
        return 200, {"running": False, "tick": self.app.sim.tick}

    def sim_step(self, query, body):
        ticks = body.get("ticks", 1)
        ticks = int(ticks) if ticks is not None else 1
        self.app.sim.step(ticks)
        return 200, {"tick": self.app.sim.tick}

    def sim_speed(self, query, body):
        speed = int(body.get("speed", 1))
        self.app.sim.set_speed(speed)
        return 200, {"speed": self.app.sim.speed}

    def sim_inject(self, query, body):
        return 200, self.app.inject(body)

    # ------------------------------------------------------------ 事件与分析
    def list_events(self, query, body):
        page, size = _page(query)
        where, params = [], []
        if query.get("type"):
            where.append("type = ?")
            params.append(query["type"])
        if query.get("agvId"):
            where.append("aggregate_id = ?")
            params.append(query["agvId"])
        if query.get("taskId"):
            where.append("aggregate_id = ?")
            params.append(query["taskId"])
        if query.get("fromTick") is not None:
            where.append("tick >= ?")
            params.append(int(query["fromTick"]))
        if query.get("toTick") is not None:
            where.append("tick <= ?")
            params.append(int(query["toTick"]))
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.app.db.query_one(f"SELECT COUNT(*) AS c FROM events {clause}", tuple(params))["c"]
        rows = self.app.db.query(
            f"SELECT * FROM events {clause} ORDER BY seq DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        items = [
            {
                "id": row["id"], "type": row["type"], "tick": row["tick"],
                "aggregateId": row["aggregate_id"], "payload": _json(row["payload"]),
                "occurredAt": row["occurred_at"], "seq": row["seq"],
            }
            for row in rows
        ]
        return 200, {"items": items, "page": page, "size": size, "total": total}

    def analytics_summary(self, query, body):
        return 200, self.app.analytics.summary()

    def analytics_throughput(self, query, body):
        window = _int_param(query, "window", 600)
        bucket = _int_param(query, "bucket", 60)
        return 200, self.app.analytics.throughput(window=window, bucket=bucket)

    def analytics_utilization(self, query, body):
        return 200, self.app.analytics.utilization()

    def analytics_bottlenecks(self, query, body):
        top = _int_param(query, "top", 10)
        return 200, self.app.analytics.bottlenecks(top=top)

    def analytics_timeline(self, query, body, task_id):
        return 200, self.app.analytics.task_timeline(task_id)


def _json(text):
    from .util import loads

    return loads(text, {})
