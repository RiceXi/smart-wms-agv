from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .pathfinding import GridMap, ReservationTable, find_cycles, find_leg
from .storage import Repository


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def require(data: dict[str, Any], field: str) -> Any:
    value = data.get(field)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ServiceError("VALIDATION_FAILED", f"字段 {field} 不能为空")
    return value


class WmsService:
    ALLOWED_TASK_TRANSITIONS = {
        "pending": {"assigned", "cancelled"},
        "assigned": {"running", "pending", "cancelled"},
        "running": {"completed", "failed"},
        "completed": set(),
        "cancelled": set(),
        "failed": set(),
    }

    def __init__(self, db_path: Path, seed_demo: bool = True) -> None:
        self.repo = Repository(db_path)
        self.lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._load()
        if seed_demo and not self.skus:
            self._seed_demo_locked()
        self._persist_runtime_locked()

    def _load(self) -> None:
        self.skus = self.repo.load_entities("sku")
        self.locations = self.repo.load_entities("location")
        self.inventory = self.repo.load_entities("inventory")
        self.orders = self.repo.load_entities("order")
        self.tasks = self.repo.load_entities("task")
        self.agvs = self.repo.load_entities("agv")
        events = self.repo.load_entities("event")
        self.events = sorted(events.values(), key=lambda item: item["sequence"])
        runtime = self.repo.get_state(
            "runtime",
            {
                "tick": 0,
                "paused": True,
                "speed": 1,
                "map_version": "map-1",
                "blocked_cells": [],
                "counters": {
                    "conflicts_prevented": 0,
                    "deadlocks_detected": 0,
                    "deadlocks_resolved": 0,
                },
                "traffic": {},
                "event_sequence": len(self.events),
            },
        )
        plans = self.repo.get_state("plans", {})
        self.runtime = runtime
        self.plans = plans

    @property
    def tick(self) -> int:
        return int(self.runtime["tick"])

    @property
    def paused(self) -> bool:
        return bool(self.runtime["paused"])

    def _touch(self, entity: dict[str, Any]) -> dict[str, Any]:
        entity["updated_at"] = utc_now()
        entity["version"] = int(entity.get("version", 0)) + 1
        return entity

    def _save_entity(self, kind: str, entity: dict[str, Any]) -> None:
        self.repo.upsert_entity(kind, entity)

    def _add_event(
        self,
        event_type: str,
        message: str,
        payload: dict[str, Any] | None = None,
        aggregate_id: str | None = None,
    ) -> dict[str, Any]:
        sequence = int(self.runtime.get("event_sequence", len(self.events))) + 1
        self.runtime["event_sequence"] = sequence
        event = {
            "id": new_id("event"),
            "type": event_type,
            "message": message,
            "payload": payload or {},
            "tick": self.tick,
            "aggregate_id": aggregate_id,
            "sequence": sequence,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "version": 1,
        }
        self.events.append(event)
        self.events = self.events[-500:]
        self._save_entity("event", event)
        return event

    def _persist_runtime_locked(self) -> None:
        self.repo.set_state("runtime", self.runtime)
        self.repo.set_state("plans", self.plans)

    def _grid_locked(self) -> GridMap:
        blocked = {tuple(cell) for cell in self.runtime.get("blocked_cells", [])}
        obstacles = {
            (int(loc["x"]), int(loc["y"]))
            for loc in self.locations.values()
            if loc["type"] in {"shelf", "blocked"}
        }
        return GridMap(12, 8, obstacles=obstacles, blocked=blocked)

    def _location_by_id(self, location_id: str) -> dict[str, Any]:
        location = self.locations.get(location_id)
        if not location:
            raise ServiceError("RESOURCE_NOT_FOUND", "库位不存在", 404)
        return location

    def _sku_by_id(self, sku_id: str) -> dict[str, Any]:
        sku = self.skus.get(sku_id)
        if not sku:
            raise ServiceError("RESOURCE_NOT_FOUND", "SKU 不存在", 404)
        return sku

    def _seed_demo_locked(self) -> None:
        location_rows = [
            ("LOC-SHELF-A", "A", 2, 2, "shelf", 100),
            ("LOC-SHELF-B", "A", 5, 2, "shelf", 100),
            ("LOC-SHELF-C", "A", 8, 2, "shelf", 100),
            ("LOC-PICK-1", "P", 1, 1, "pick", 10),
            ("LOC-PICK-2", "P", 4, 1, "pick", 10),
            ("LOC-PICK-3", "P", 7, 1, "pick", 10),
            ("LOC-DROP-1", "D", 10, 1, "drop", 10),
            ("LOC-DROP-2", "D", 4, 6, "drop", 10),
            ("LOC-CHARGE-1", "C", 0, 6, "charger", 1),
            ("LOC-CHARGE-2", "C", 11, 6, "charger", 1),
        ]
        now = utc_now()
        for code, zone, x, y, kind, capacity in location_rows:
            item = {
                "id": new_id("loc"),
                "code": code,
                "zone": zone,
                "x": x,
                "y": y,
                "type": kind,
                "capacity": capacity,
                "map_version": "map-1",
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.locations[item["id"]] = item
            self._save_entity("location", item)

        sku_rows = [
            ("SKU-100", "伺服电机", "M-100", "fifo"),
            ("SKU-200", "激光传感器", "S-200", "fefo"),
            ("SKU-300", "控制模块", "C-300", "none"),
        ]
        for code, name, spec, batch_rule in sku_rows:
            item = {
                "id": new_id("sku"),
                "code": code,
                "name": name,
                "spec": spec,
                "batch_rule": batch_rule,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.skus[item["id"]] = item
            self._save_entity("sku", item)

        shelf_ids = [
            row["id"] for row in self.locations.values() if row["type"] == "shelf"
        ]
        for index, sku in enumerate(
            sorted(self.skus.values(), key=lambda item: item["code"])
        ):
            inventory = {
                "id": new_id("inventory"),
                "sku_id": sku["id"],
                "location_id": shelf_ids[index],
                "qty": 40 + index * 10,
                "reserved_qty": 0,
                "batch": f"B-{index + 1:02d}",
                "expire_at": None,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.inventory[inventory["id"]] = inventory
            self._save_entity("inventory", inventory)

        starts = [
            (0, 0),
            (1, 0),
            (4, 0),
            (7, 0),
            (9, 0),
            (11, 0),
            (0, 3),
            (4, 3),
            (7, 3),
            (11, 3),
        ]
        for index, (x, y) in enumerate(starts, start=1):
            item = {
                "id": f"agv-{index:02d}",
                "map_id": "map-1",
                "status": "idle",
                "x": x,
                "y": y,
                "battery": 68.0 + index,
                "active_task_id": None,
                "payload_sku": None,
                "last_tick": 0,
                "fault_code": None,
                "waiting_on": None,
                "waiting_ticks": 0,
                "rollback_count": 0,
                "busy_ticks": 0,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.agvs[item["id"]] = item
            self._save_entity("agv", item)

        self.runtime.update(
            {
                "tick": 0,
                "paused": True,
                "speed": 1,
                "map_version": "map-1",
                "blocked_cells": [],
                "counters": {
                    "conflicts_prevented": 0,
                    "deadlocks_detected": 0,
                    "deadlocks_resolved": 0,
                },
                "traffic": {},
                "event_sequence": len(self.events),
            }
        )
        self.plans = {}
        self._add_event("system.seeded", "演示数据已初始化")
        self._persist_runtime_locked()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            map_locations = [
                {
                    "id": loc["id"],
                    "code": loc["code"],
                    "x": loc["x"],
                    "y": loc["y"],
                    "type": loc["type"],
                    "capacity": loc["capacity"],
                }
                for loc in self.locations.values()
            ]
            return {
                "runtime": copy.deepcopy(self.runtime),
                "map": {
                    "width": 12,
                    "height": 8,
                    "version": self.runtime["map_version"],
                    "locations": map_locations,
                    "blocked_cells": copy.deepcopy(
                        self.runtime.get("blocked_cells", [])
                    ),
                },
                "skus": sorted(
                    copy.deepcopy(list(self.skus.values())),
                    key=lambda item: item["code"],
                ),
                "locations": sorted(
                    copy.deepcopy(list(self.locations.values())),
                    key=lambda item: item["code"],
                ),
                "inventory": copy.deepcopy(list(self.inventory.values())),
                "orders": sorted(
                    copy.deepcopy(list(self.orders.values())),
                    key=lambda item: item["created_at"],
                    reverse=True,
                ),
                "tasks": sorted(
                    copy.deepcopy(list(self.tasks.values())),
                    key=lambda item: (-int(item["priority"]), item["created_at"]),
                ),
                "agvs": sorted(
                    copy.deepcopy(list(self.agvs.values())),
                    key=lambda item: item["id"],
                ),
                "plans": copy.deepcopy(list(self.plans.values())),
                "events": copy.deepcopy(self.events[-80:]),
                "analytics": self._analytics_locked(),
            }

    def list_events(
        self, tick_from: int | None = None, tick_to: int | None = None
    ) -> list[dict[str, Any]]:
        with self.lock:
            events = self.events
            if tick_from is not None:
                events = [e for e in events if int(e["tick"]) >= tick_from]
            if tick_to is not None:
                events = [e for e in events if int(e["tick"]) <= tick_to]
            return copy.deepcopy(events[-500:])

    def create_sku(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            code = str(require(data, "code")).strip()
            if any(sku["code"] == code for sku in self.skus.values()):
                raise ServiceError("DUPLICATE_CODE", "SKU 编码已存在", 409)
            now = utc_now()
            item = {
                "id": new_id("sku"),
                "code": code,
                "name": str(require(data, "name")).strip(),
                "spec": str(data.get("spec", "")).strip(),
                "batch_rule": data.get("batch_rule", "none"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            if item["batch_rule"] not in {"none", "fifo", "fefo"}:
                raise ServiceError("VALIDATION_FAILED", "批次规则不合法")
            self.skus[item["id"]] = item
            self._save_entity("sku", item)
            self._add_event("sku.created", f"创建 SKU {code}", aggregate_id=item["id"])
            self._persist_runtime_locked()
            return item

    def update_sku(self, sku_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            sku = self._sku_by_id(sku_id)
            if "code" in data:
                code = str(data["code"]).strip()
                if any(
                    item["code"] == code and item["id"] != sku_id
                    for item in self.skus.values()
                ):
                    raise ServiceError("DUPLICATE_CODE", "SKU 编码已存在", 409)
                sku["code"] = code
            if "name" in data:
                sku["name"] = str(data["name"]).strip()
            if "spec" in data:
                sku["spec"] = str(data["spec"]).strip()
            if "batch_rule" in data:
                if data["batch_rule"] not in {"none", "fifo", "fefo"}:
                    raise ServiceError("VALIDATION_FAILED", "批次规则不合法")
                sku["batch_rule"] = data["batch_rule"]
            self._touch(sku)
            self._save_entity("sku", sku)
            return sku

    def delete_sku(self, sku_id: str) -> None:
        with self.lock:
            self._sku_by_id(sku_id)
            if any(row["sku_id"] == sku_id for row in self.inventory.values()):
                raise ServiceError("CONFLICT", "SKU 仍有关联库存", 409)
            del self.skus[sku_id]
            self.repo.delete_entity("sku", sku_id)

    def create_location(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            code = str(require(data, "code")).strip()
            x = int(require(data, "x"))
            y = int(require(data, "y"))
            capacity = int(require(data, "capacity"))
            kind = data.get("type", "floor")
            if not (0 <= x < 12 and 0 <= y < 8):
                raise ServiceError("VALIDATION_FAILED", "库位坐标越界")
            if kind not in {"floor", "shelf", "pick", "drop", "charger", "blocked"}:
                raise ServiceError("VALIDATION_FAILED", "库位类型不合法")
            if capacity <= 0:
                raise ServiceError("VALIDATION_FAILED", "容量必须大于 0")
            if any(loc["code"] == code for loc in self.locations.values()):
                raise ServiceError("DUPLICATE_CODE", "库位编码已存在", 409)
            if any(
                int(loc["x"]) == x and int(loc["y"]) == y
                for loc in self.locations.values()
            ):
                raise ServiceError("VALIDATION_FAILED", "坐标已有库位", 409)
            now = utc_now()
            item = {
                "id": new_id("loc"),
                "code": code,
                "zone": str(data.get("zone", "A")),
                "x": x,
                "y": y,
                "type": kind,
                "capacity": capacity,
                "map_version": self.runtime["map_version"],
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.locations[item["id"]] = item
            self._save_entity("location", item)
            self._add_event("location.created", f"创建库位 {code}", aggregate_id=item["id"])
            return item

    def update_location(self, location_id: str, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            item = self._location_by_id(location_id)
            if "x" in data or "y" in data:
                x, y = int(data.get("x", item["x"])), int(data.get("y", item["y"]))
                if not (0 <= x < 12 and 0 <= y < 8):
                    raise ServiceError("VALIDATION_FAILED", "库位坐标越界")
                if any(
                    loc["id"] != location_id
                    and int(loc["x"]) == x
                    and int(loc["y"]) == y
                    for loc in self.locations.values()
                ):
                    raise ServiceError("VALIDATION_FAILED", "坐标已有库位", 409)
                item["x"], item["y"] = x, y
            if "type" in data:
                if data["type"] not in {
                    "floor",
                    "shelf",
                    "pick",
                    "drop",
                    "charger",
                    "blocked",
                }:
                    raise ServiceError("VALIDATION_FAILED", "库位类型不合法")
                item["type"] = data["type"]
            if "capacity" in data:
                capacity = int(data["capacity"])
                if capacity <= 0:
                    raise ServiceError("VALIDATION_FAILED", "容量必须大于 0")
                item["capacity"] = capacity
            self._touch(item)
            self._save_entity("location", item)
            return item

    def delete_location(self, location_id: str) -> None:
        with self.lock:
            self._location_by_id(location_id)
            if any(row["location_id"] == location_id for row in self.inventory.values()):
                raise ServiceError("CONFLICT", "库位仍有关联库存", 409)
            if any(
                (int(agv["x"]), int(agv["y"]))
                == (
                    int(self.locations[location_id]["x"]),
                    int(self.locations[location_id]["y"]),
                )
                for agv in self.agvs.values()
            ):
                raise ServiceError("CONFLICT", "库位上存在 AGV", 409)
            del self.locations[location_id]
            self.repo.delete_entity("location", location_id)

    def create_inventory(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            sku = self._sku_by_id(str(require(data, "sku_id")))
            location = self._location_by_id(str(require(data, "location_id")))
            qty = int(require(data, "qty"))
            if qty < 0:
                raise ServiceError("VALIDATION_FAILED", "库存数量不能小于 0")
            if location["type"] != "shelf":
                raise ServiceError("VALIDATION_FAILED", "库存必须放在货架库位")
            now = utc_now()
            batch = str(data.get("batch", "")).strip()
            existing = next(
                (
                    row
                    for row in self.inventory.values()
                    if row["sku_id"] == sku["id"]
                    and row["location_id"] == location["id"]
                    and row.get("batch", "") == batch
                ),
                None,
            )
            if existing:
                existing["qty"] += qty
                self._touch(existing)
                self._save_entity("inventory", existing)
                return existing
            item = {
                "id": new_id("inventory"),
                "sku_id": sku["id"],
                "location_id": location["id"],
                "qty": qty,
                "reserved_qty": 0,
                "batch": batch,
                "expire_at": data.get("expire_at"),
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.inventory[item["id"]] = item
            self._save_entity("inventory", item)
            return item

    def create_order(self, data: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            code = str(require(data, "code")).strip()
            if any(order["code"] == code for order in self.orders.values()):
                raise ServiceError("DUPLICATE_CODE", "订单编码已存在", 409)
            lines = data.get("lines")
            if not isinstance(lines, list) or not lines:
                raise ServiceError("VALIDATION_FAILED", "订单至少包含一条明细")
            clean_lines = []
            for line in lines:
                sku_id = str(require(line, "sku_id"))
                self._sku_by_id(sku_id)
                qty = int(require(line, "qty"))
                if qty <= 0:
                    raise ServiceError("VALIDATION_FAILED", "出库数量必须大于 0")
                clean_lines.append({"sku_id": sku_id, "qty": qty})
            now = utc_now()
            item = {
                "id": new_id("order"),
                "code": code,
                "type": "outbound",
                "status": "draft",
                "priority": max(1, min(10, int(data.get("priority", 5)))),
                "requester": str(data.get("requester", "demo")),
                "lines": clean_lines,
                "created_at": now,
                "updated_at": now,
                "version": 1,
            }
            self.orders[item["id"]] = item
            self._save_entity("order", item)
            self._add_event("order.created", f"创建订单 {code}", aggregate_id=item["id"])
            return item

    def release_order(self, order_id: str) -> dict[str, Any]:
        with self.lock:
            order = self.orders.get(order_id)
            if not order:
                raise ServiceError("RESOURCE_NOT_FOUND", "订单不存在", 404)
            if order["status"] in {"released", "completed"}:
                return order
            if order["status"] not in {"draft", "allocated"}:
                raise ServiceError("ORDER_INVALID_STATE", "订单当前状态不能释放", 409)

            allocations: list[dict[str, Any]] = []
            for line in order["lines"]:
                remaining = int(line["qty"])
                rows = sorted(
                    [
                        row
                        for row in self.inventory.values()
                        if row["sku_id"] == line["sku_id"]
                        and int(row["qty"]) - int(row["reserved_qty"]) > 0
                    ],
                    key=lambda row: (row.get("expire_at") or "9999", row["id"]),
                )
                for row in rows:
                    available = int(row["qty"]) - int(row["reserved_qty"])
                    take = min(available, remaining)
                    if take <= 0:
                        continue
                    allocations.append(
                        {
                            "inventory": row,
                            "sku_id": line["sku_id"],
                            "qty": take,
                        }
                    )
                    remaining -= take
                    if remaining == 0:
                        break
                if remaining:
                    raise ServiceError("INVENTORY_INSUFFICIENT", "库存不足", 422)

            now = utc_now()
            drop = next(loc for loc in self.locations.values() if loc["type"] == "drop")
            for allocation in allocations:
                inventory = allocation["inventory"]
                inventory["reserved_qty"] += allocation["qty"]
                self._touch(inventory)
                self._save_entity("inventory", inventory)
                task = {
                    "id": new_id("task"),
                    "order_id": order["id"],
                    "type": "pick_and_drop",
                    "priority": order["priority"],
                    "status": "pending",
                    "pickup_location_id": inventory["location_id"],
                    "drop_location_id": drop["id"],
                    "inventory_id": inventory["id"],
                    "sku_id": allocation["sku_id"],
                    "qty": allocation["qty"],
                    "assigned_agv_id": None,
                    "plan_version": 0,
                    "retry_count": 0,
                    "created_tick": self.tick,
                    "assigned_tick": None,
                    "completed_tick": None,
                    "created_at": now,
                    "updated_at": now,
                    "version": 1,
                }
                self.tasks[task["id"]] = task
                self._save_entity("task", task)
                self._add_event(
                    "task.created",
                    f"任务 {task['id']} 已创建",
                    {"order_id": order["id"]},
                    task["id"],
                )
            order["status"] = "released"
            self._touch(order)
            self._save_entity("order", order)
            self._add_event("order.released", f"订单 {order['code']} 已释放", aggregate_id=order["id"])
            self._dispatch_pending_locked()
            self._persist_runtime_locked()
            return order

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        with self.lock:
            order = self.orders.get(order_id)
            if not order:
                raise ServiceError("RESOURCE_NOT_FOUND", "订单不存在", 404)
            if order["status"] not in {"draft", "allocated"}:
                raise ServiceError("ORDER_INVALID_STATE", "只有草稿或已分配订单可直接取消", 409)
            order["status"] = "cancelled"
            self._touch(order)
            self._save_entity("order", order)
            self._add_event("order.cancelled", f"订单 {order['code']} 已取消", aggregate_id=order["id"])
            return order

    def set_task_priority(self, task_id: str, priority: int) -> dict[str, Any]:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise ServiceError("RESOURCE_NOT_FOUND", "任务不存在", 404)
            if task["status"] in {"completed", "cancelled", "failed"}:
                raise ServiceError("TASK_INVALID_STATE", "终态任务不能调整优先级", 409)
            task["priority"] = max(1, min(10, int(priority)))
            self._touch(task)
            self._save_entity("task", task)
            self._add_event("task.priority.changed", f"任务优先级调整为 {task['priority']}", aggregate_id=task_id)
            self._dispatch_pending_locked()
            return task

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise ServiceError("RESOURCE_NOT_FOUND", "任务不存在", 404)
            if task["status"] not in {"pending", "assigned"}:
                raise ServiceError("TASK_INVALID_STATE", "当前任务不能取消", 409)
            self._release_reserved_inventory_locked(task)
            if task.get("assigned_agv_id"):
                self._release_task_from_agv_locked(task)
            task["status"] = "cancelled"
            self._touch(task)
            self._save_entity("task", task)
            self._add_event("task.cancelled", f"任务 {task_id} 已取消", aggregate_id=task_id)
            self._persist_runtime_locked()
            return task

    def reassign_task(self, task_id: str) -> dict[str, Any]:
        with self.lock:
            task = self.tasks.get(task_id)
            if not task:
                raise ServiceError("RESOURCE_NOT_FOUND", "任务不存在", 404)
            if task["status"] not in {"assigned", "running"}:
                raise ServiceError("TASK_INVALID_STATE", "当前任务不能重派", 409)
            self._release_task_from_agv_locked(task)
            task["status"] = "pending"
            task["retry_count"] = int(task.get("retry_count", 0)) + 1
            self._touch(task)
            self._save_entity("task", task)
            self._dispatch_pending_locked()
            return task

    def report_agv_fault(self, agv_id: str, code: str = "MANUAL_FAULT") -> dict[str, Any]:
        with self.lock:
            agv = self.agvs.get(agv_id)
            if not agv:
                raise ServiceError("RESOURCE_NOT_FOUND", "AGV 不存在", 404)
            task = self.tasks.get(agv.get("active_task_id") or "")
            if task:
                self._release_task_from_agv_locked(task)
                task["status"] = "pending"
                task["retry_count"] = int(task.get("retry_count", 0)) + 1
                self._touch(task)
                self._save_entity("task", task)
            agv["status"] = "fault"
            agv["fault_code"] = code
            agv["active_task_id"] = None
            self._touch(agv)
            self._save_entity("agv", agv)
            self._add_event("agv.fault", f"{agv_id} 故障", {"code": code}, agv_id)
            self._dispatch_pending_locked()
            return agv

    def recover_agv(self, agv_id: str) -> dict[str, Any]:
        with self.lock:
            agv = self.agvs.get(agv_id)
            if not agv:
                raise ServiceError("RESOURCE_NOT_FOUND", "AGV 不存在", 404)
            agv["status"] = "idle"
            agv["fault_code"] = None
            self._touch(agv)
            self._save_entity("agv", agv)
            return agv

    def _release_reserved_inventory_locked(self, task: dict[str, Any]) -> None:
        inventory = self.inventory.get(task.get("inventory_id", ""))
        if not inventory:
            return
        inventory["reserved_qty"] = max(
            0, int(inventory["reserved_qty"]) - int(task.get("qty", 0))
        )
        self._touch(inventory)
        self._save_entity("inventory", inventory)

    def _release_task_from_agv_locked(self, task: dict[str, Any]) -> None:
        agv_id = task.get("assigned_agv_id")
        self.plans.pop(task["id"], None)
        if agv_id and agv_id in self.agvs:
            agv = self.agvs[agv_id]
            agv["status"] = "idle"
            agv["active_task_id"] = None
            agv["payload_sku"] = None
            self._touch(agv)
            self._save_entity("agv", agv)
        task["assigned_agv_id"] = None

    def _build_reservations_locked(self) -> ReservationTable:
        reservations = ReservationTable()
        future_limit = self.tick + 180
        for agv in self.agvs.values():
            if agv["status"] in {"idle", "fault", "offline"}:
                for tick in range(self.tick, future_limit + 1):
                    reservations.add_vertex(
                        (int(agv["x"]), int(agv["y"])), tick, agv["id"]
                    )
        for plan in self.plans.values():
            owner = plan["agv_id"]
            reservations.reserve_plan(plan["steps"], owner)
        return reservations

    def _target_cell_locked(
        self, location: dict[str, Any], origin: tuple[int, int]
    ) -> tuple[int, int]:
        point = (int(location["x"]), int(location["y"]))
        if location["type"] != "shelf":
            return point
        grid = self._grid_locked()
        neighbors = [
            (point[0] + dx, point[1] + dy)
            for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0))
            if grid.passable((point[0] + dx, point[1] + dy))
        ]
        if not neighbors:
            raise ServiceError("PATH_CONFLICT", "货架周围没有可通行格点", 409)
        return min(
            neighbors,
            key=lambda cell: (
                abs(cell[0] - origin[0]) + abs(cell[1] - origin[1]),
                cell[1],
                cell[0],
            ),
        )

    def _build_plan_locked(
        self, agv: dict[str, Any], task: dict[str, Any], reservations: ReservationTable
    ) -> dict[str, Any] | None:
        grid = self._grid_locked()
        pickup = self._location_by_id(task["pickup_location_id"])
        drop = self._location_by_id(task["drop_location_id"])
        owner = agv["id"]
        start = (int(agv["x"]), int(agv["y"]))
        pickup_cell = self._target_cell_locked(pickup, start)
        drop_cell = self._target_cell_locked(drop, pickup_cell)
        to_pickup = find_leg(
            grid,
            reservations,
            owner,
            start,
            pickup_cell,
            self.tick,
        )
        if to_pickup is None:
            return None
        pickup_tick = int(to_pickup[-1]["tick"])
        steps = [
            {**step, "phase": "to_pickup"}
            for step in to_pickup
        ]
        steps.append(
            {
                "tick": pickup_tick + 1,
                "x": pickup_cell[0],
                "y": pickup_cell[1],
                "action": "load",
                "phase": "loading",
            }
        )
        to_drop = find_leg(
            grid,
            reservations,
            owner,
            pickup_cell,
            drop_cell,
            pickup_tick + 2,
        )
        if to_drop is None:
            return None
        steps.extend({**step, "phase": "to_dropoff"} for step in to_drop[1:])
        drop_tick = int(steps[-1]["tick"])
        steps.append(
            {
                "tick": drop_tick + 1,
                "x": drop_cell[0],
                "y": drop_cell[1],
                "action": "unload",
                "phase": "unloading",
            }
        )
        return {
            "kind": "task",
            "task_id": task["id"],
            "agv_id": agv["id"],
            "plan_version": int(task["plan_version"]) + 1,
            "map_version": self.runtime["map_version"],
            "steps": steps,
            "estimated_ticks": drop_tick - self.tick,
            "wait_ticks": sum(1 for step in steps if step["action"] == "wait"),
        }

    def _dispatch_pending_locked(self) -> None:
        candidates_tasks = sorted(
            [task for task in self.tasks.values() if task["status"] == "pending"],
            key=lambda task: (-int(task["priority"]), task["created_at"], task["id"]),
        )
        for task in candidates_tasks:
            reservations = self._build_reservations_locked()
            candidates: list[tuple[int, str, dict[str, Any]]] = []
            for agv in sorted(self.agvs.values(), key=lambda item: item["id"]):
                if agv["status"] != "idle" or float(agv["battery"]) <= 15:
                    continue
                plan = self._build_plan_locked(agv, task, reservations)
                if plan:
                    candidates.append((int(plan["estimated_ticks"]), agv["id"], plan))
            if not candidates:
                if any(
                    agv["status"] in {"idle", "charging"}
                    for agv in self.agvs.values()
                ):
                    self.runtime["counters"]["conflicts_prevented"] = int(
                        self.runtime["counters"].get("conflicts_prevented", 0)
                    ) + 1
                continue
            _, agv_id, plan = min(candidates, key=lambda item: (item[0], item[1]))
            agv = self.agvs[agv_id]
            task["assigned_agv_id"] = agv_id
            task["plan_version"] = plan["plan_version"]
            task["assigned_tick"] = self.tick
            task["status"] = "assigned"
            self._touch(task)
            self._save_entity("task", task)
            agv["status"] = "to_pickup"
            agv["active_task_id"] = task["id"]
            self._touch(agv)
            self._save_entity("agv", agv)
            self.plans[task["id"]] = plan
            self._add_event(
                "task.assigned",
                f"任务 {task['id']} 已指派给 {agv_id}",
                {"estimated_ticks": plan["estimated_ticks"]},
                task["id"],
            )
        self._persist_runtime_locked()

    def start_simulation(self) -> dict[str, Any]:
        with self.lock:
            self.runtime["paused"] = False
            self._dispatch_pending_locked()
            self._ensure_worker_locked()
            self._persist_runtime_locked()
            return copy.deepcopy(self.runtime)

    def _ensure_worker_locked(self) -> None:
        if self._worker is None or not self._worker.is_alive():
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._simulation_loop, name="wms-sim", daemon=True
            )
            self._worker.start()

    def pause_simulation(self) -> dict[str, Any]:
        with self.lock:
            self.runtime["paused"] = True
            self._persist_runtime_locked()
            return copy.deepcopy(self.runtime)

    def set_speed(self, speed: int) -> dict[str, Any]:
        if speed not in {1, 4, 8}:
            raise ServiceError("VALIDATION_FAILED", "速度只能为 1、4、8")
        with self.lock:
            self.runtime["speed"] = speed
            self._persist_runtime_locked()
            return copy.deepcopy(self.runtime)

    def step_simulation(self, ticks: int = 1) -> dict[str, Any]:
        ticks = max(1, min(int(ticks), 1000))
        with self.lock:
            self.runtime["paused"] = True
            for _ in range(ticks):
                self._tick_once_locked()
            self._persist_runtime_locked()
            return self.snapshot()

    def _simulation_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.25)
            with self.lock:
                if not self.paused:
                    for _ in range(int(self.runtime.get("speed", 1))):
                        self._tick_once_locked()
                    self._persist_runtime_locked()

    def stop(self) -> None:
        self._stop_event.set()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=2)
        self.repo.close()

    def _tick_once_locked(self) -> None:
        self.runtime["tick"] = self.tick + 1
        for plan_key, plan in list(self.plans.items()):
            if plan["kind"] == "task":
                self._advance_task_plan_locked(plan_key, plan)
            else:
                self._advance_charge_plan_locked(plan_key, plan)
        for agv in self.agvs.values():
            if agv["status"] == "charging" and not any(
                plan["kind"] == "charge" and plan["agv_id"] == agv["id"]
                for plan in self.plans.values()
            ):
                agv["battery"] = min(100.0, float(agv["battery"]) + 1.5)
                if agv["battery"] >= 100:
                    agv["status"] = "idle"
                    agv.pop("charge_target", None)
                self._touch(agv)
                self._save_entity("agv", agv)
        self._check_deadlocks_locked()
        self._dispatch_pending_locked()

    def _advance_task_plan_locked(self, plan_key: str, plan: dict[str, Any]) -> None:
        task = self.tasks.get(plan["task_id"])
        agv = self.agvs.get(plan["agv_id"])
        if not task or not agv:
            self.plans.pop(plan_key, None)
            return
        step = next((s for s in plan["steps"] if int(s["tick"]) == self.tick), None)
        if not step:
            return
        action = step["action"]
        if action in {"start"}:
            return
        if action == "move":
            agv["x"] = int(step["x"])
            agv["y"] = int(step["y"])
            agv["status"] = (
                "to_dropoff" if step.get("phase") == "to_dropoff" else "to_pickup"
            )
            agv["battery"] = max(0.0, float(agv["battery"]) - 0.08)
            agv["busy_ticks"] = int(agv.get("busy_ticks", 0)) + 1
            cell = f"{step['x']},{step['y']}"
            traffic = self.runtime.setdefault("traffic", {})
            traffic[cell] = int(traffic.get(cell, 0)) + 1
            if task["status"] == "assigned":
                task["status"] = "running"
                self._touch(task)
                self._save_entity("task", task)
        elif action == "wait":
            agv["status"] = agv["status"]
            agv["waiting_ticks"] = int(agv.get("waiting_ticks", 0)) + 1
            agv["battery"] = max(0.0, float(agv["battery"]) - 0.02)
        elif action == "load":
            agv["status"] = "loading"
            agv["payload_sku"] = task["sku_id"]
        elif action == "unload":
            agv["status"] = "unloading"
            self._complete_task_locked(task, agv)
            self.plans.pop(plan_key, None)
            return
        agv["last_tick"] = self.tick
        self._touch(agv)
        self._save_entity("agv", agv)

    def _complete_task_locked(self, task: dict[str, Any], agv: dict[str, Any]) -> None:
        if task["status"] != "running":
            return
        inventory = self.inventory.get(task.get("inventory_id", ""))
        if inventory:
            inventory["qty"] = max(0, int(inventory["qty"]) - int(task["qty"]))
            inventory["reserved_qty"] = max(
                0, int(inventory["reserved_qty"]) - int(task["qty"])
            )
            self._touch(inventory)
            self._save_entity("inventory", inventory)
        task["status"] = "completed"
        task["completed_tick"] = self.tick
        task["duration_ticks"] = self.tick - int(
            task.get("assigned_tick", task.get("created_tick", 0))
        )
        self._touch(task)
        self._save_entity("task", task)
        agv["status"] = "idle"
        agv["active_task_id"] = None
        agv["payload_sku"] = None
        agv["waiting_on"] = None
        self._touch(agv)
        self._save_entity("agv", agv)
        self._add_event(
            "task.completed",
            f"任务 {task['id']} 已完成",
            {"duration_ticks": task["duration_ticks"]},
            task["id"],
        )
        self._complete_order_if_done_locked(task["order_id"])
        if float(agv["battery"]) <= 20:
            self._start_charging_locked(agv["id"])

    def _complete_order_if_done_locked(self, order_id: str) -> None:
        order = self.orders.get(order_id)
        if not order:
            return
        related = [task for task in self.tasks.values() if task["order_id"] == order_id]
        if related and all(task["status"] == "completed" for task in related):
            order["status"] = "completed"
            self._touch(order)
            self._save_entity("order", order)
            self._add_event("order.completed", f"订单 {order['code']} 已完成", aggregate_id=order_id)

    def _start_charging_locked(self, agv_id: str) -> dict[str, Any] | None:
        agv = self.agvs.get(agv_id)
        if not agv or agv["status"] == "fault":
            return None
        active = sum(1 for item in self.agvs.values() if item["status"] == "charging")
        chargers = [
            loc
            for loc in self.locations.values()
            if loc["type"] == "charger"
            and active < int(loc["capacity"])
        ]
        if not chargers:
            return None
        grid = self._grid_locked()
        reservations = self._build_reservations_locked()
        start = (int(agv["x"]), int(agv["y"]))
        charger = min(
            chargers,
            key=lambda loc: abs(int(loc["x"]) - start[0])
            + abs(int(loc["y"]) - start[1]),
        )
        owner = agv_id
        steps = find_leg(
            grid,
            reservations,
            owner,
            start,
            (int(charger["x"]), int(charger["y"])),
            self.tick,
        )
        if not steps:
            return None
        plan = {
            "kind": "charge",
            "task_id": None,
            "agv_id": agv_id,
            "plan_version": 1,
            "map_version": self.runtime["map_version"],
            "steps": steps,
            "estimated_ticks": int(steps[-1]["tick"]) - self.tick,
            "wait_ticks": sum(1 for step in steps if step["action"] == "wait"),
            "charge_target": charger["id"],
        }
        agv["status"] = "charging"
        agv["active_task_id"] = None
        agv["charge_target"] = charger["id"]
        self._touch(agv)
        self._save_entity("agv", agv)
        self.plans[f"charge:{agv_id}"] = plan
        return plan

    def _advance_charge_plan_locked(self, plan_key: str, plan: dict[str, Any]) -> None:
        agv = self.agvs.get(plan["agv_id"])
        if not agv:
            self.plans.pop(plan_key, None)
            return
        step = next((s for s in plan["steps"] if int(s["tick"]) == self.tick), None)
        if not step:
            return
        if step["action"] == "move":
            agv["x"] = int(step["x"])
            agv["y"] = int(step["y"])
            agv["battery"] = max(0.0, float(agv["battery"]) - 0.05)
            agv["last_tick"] = self.tick
            self._touch(agv)
            self._save_entity("agv", agv)
        if self.tick >= int(plan["steps"][-1]["tick"]):
            self.plans.pop(plan_key, None)

    def _check_deadlocks_locked(self) -> None:
        wait_graph = {
            agv["id"]: agv.get("waiting_on")
            for agv in self.agvs.values()
            if agv.get("waiting_on")
        }
        cycles = find_cycles(wait_graph)
        if cycles:
            self.runtime["counters"]["deadlocks_detected"] += len(cycles)
            for cycle in cycles:
                self._resolve_cycle_locked(cycle)

    def _resolve_cycle_locked(self, cycle: list[str]) -> dict[str, Any] | None:
        cycle_agvs = [self.agvs[item] for item in cycle if item in self.agvs]
        if not cycle_agvs:
            return None
        victim = min(
            cycle_agvs,
            key=lambda agv: (
                int(self.tasks.get(agv.get("active_task_id") or "", {}).get("priority", 0)),
                agv["id"],
            ),
        )
        task = self.tasks.get(victim.get("active_task_id") or "")
        if task:
            self._release_task_from_agv_locked(task)
            task["status"] = "pending"
            task["retry_count"] = int(task.get("retry_count", 0)) + 1
            self._touch(task)
            self._save_entity("task", task)
        victim["rollback_count"] = int(victim.get("rollback_count", 0)) + 1
        victim["waiting_on"] = None
        self._touch(victim)
        self._save_entity("agv", victim)
        self.runtime["counters"]["deadlocks_resolved"] += 1
        self._add_event(
            "deadlock.resolved",
            f"检测并解除等待环，回退 {victim['id']}",
            {"cycle": cycle, "victim": victim["id"]},
            victim["id"],
        )
        return {"cycle": cycle, "victim": victim["id"]}

    def inject_deadlock_demo(self) -> dict[str, Any]:
        with self.lock:
            selected = sorted(self.agvs)[:3]
            if len(selected) < 3:
                raise ServiceError("VALIDATION_FAILED", "至少需要 3 台 AGV")
            for index, agv_id in enumerate(selected):
                agv = self.agvs[agv_id]
                agv["waiting_on"] = selected[(index + 1) % len(selected)]
                agv["waiting_ticks"] = int(agv.get("waiting_ticks", 0)) + 1
                self._touch(agv)
                self._save_entity("agv", agv)
            before = int(self.runtime["counters"]["deadlocks_detected"])
            self._check_deadlocks_locked()
            self.runtime["counters"]["deadlocks_detected"] = before + 1
            self._persist_runtime_locked()
            return {
                "cycle": selected,
                "detected": True,
                "resolved": True,
                "message": "已检测到三车等待环并执行回退",
            }

    def inject_roadblock(self, x: int, y: int, blocked: bool = True) -> dict[str, Any]:
        with self.lock:
            point = [int(x), int(y)]
            if not (0 <= point[0] < 12 and 0 <= point[1] < 8):
                raise ServiceError("VALIDATION_FAILED", "封路坐标越界")
            cells = {tuple(cell) for cell in self.runtime.get("blocked_cells", [])}
            if blocked:
                cells.add(tuple(point))
            else:
                cells.discard(tuple(point))
            self.runtime["blocked_cells"] = [list(cell) for cell in sorted(cells)]
            self.runtime["map_version"] = (
                "map-1" if not cells else f"map-1+{len(cells)}"
            )
            affected = []
            for task_id, plan in list(self.plans.items()):
                if plan["kind"] != "task":
                    continue
                if any(
                    int(step["tick"]) >= self.tick
                    and (int(step["x"]), int(step["y"])) in cells
                    for step in plan["steps"]
                ):
                    affected.append(task_id)
                    task = self.tasks.get(task_id)
                    if task:
                        self._release_task_from_agv_locked(task)
                        task["status"] = "pending"
                        task["retry_count"] = int(task.get("retry_count", 0)) + 1
                        self._touch(task)
                        self._save_entity("task", task)
            self._add_event(
                "map.roadblock",
                f"节点 {point[0]},{point[1]} 已{'封闭' if blocked else '解除'}",
                {"affected_tasks": affected},
            )
            self._dispatch_pending_locked()
            self._persist_runtime_locked()
            return {"blocked_cells": self.runtime["blocked_cells"], "affected": affected}

    def set_agv_battery(self, agv_id: str, battery: float) -> dict[str, Any]:
        with self.lock:
            agv = self.agvs.get(agv_id)
            if not agv:
                raise ServiceError("RESOURCE_NOT_FOUND", "AGV 不存在", 404)
            agv["battery"] = max(0.0, min(100.0, float(battery)))
            self._touch(agv)
            self._save_entity("agv", agv)
            if agv["status"] == "idle" and agv["battery"] <= 20:
                self._start_charging_locked(agv_id)
            self._persist_runtime_locked()
            return agv

    def reset_demo(self) -> dict[str, Any]:
        with self.lock:
            for kind, entities in (
                ("event", self.events),
                ("task", list(self.tasks.values())),
                ("order", list(self.orders.values())),
                ("inventory", list(self.inventory.values())),
                ("sku", list(self.skus.values())),
                ("location", list(self.locations.values())),
                ("agv", list(self.agvs.values())),
            ):
                for entity in entities:
                    self.repo.delete_entity(kind, entity["id"])
            self.skus, self.locations, self.inventory = {}, {}, {}
            self.orders, self.tasks, self.agvs, self.events = {}, {}, {}, []
            self.plans = {}
            self._seed_demo_locked()
            return self.snapshot()

    def create_demo_order(self) -> dict[str, Any]:
        with self.lock:
            sku = sorted(self.skus.values(), key=lambda item: item["code"])[0]
            line = {"sku_id": sku["id"], "qty": min(3, self._available_qty(sku["id"]))}
            order = self.create_order(
                {
                    "code": f"OUT-{uuid.uuid4().hex[:8].upper()}",
                    "priority": 7,
                    "requester": "demo",
                    "lines": [line],
                }
            )
            return self.release_order(order["id"])

    def _available_qty(self, sku_id: str) -> int:
        return sum(
            int(row["qty"]) - int(row["reserved_qty"])
            for row in self.inventory.values()
            if row["sku_id"] == sku_id
        )

    def _analytics_locked(self) -> dict[str, Any]:
        task_rows = list(self.tasks.values())
        order_rows = list(self.orders.values())
        status_counts = Counter(task["status"] for task in task_rows)
        completed = [
            task for task in task_rows if task.get("status") == "completed"
        ]
        avg_duration = (
            round(sum(int(task.get("duration_ticks", 0)) for task in completed) / len(completed), 2)
            if completed
            else 0
        )
        utilization = []
        for agv in sorted(self.agvs.values(), key=lambda item: item["id"]):
            utilization.append(
                {
                    "agv_id": agv["id"],
                    "value": round(
                        float(agv.get("busy_ticks", 0)) / max(1, self.tick) * 100, 2
                    ),
                    "battery": round(float(agv["battery"]), 1),
                    "status": agv["status"],
                }
            )
        bottleneck = [
            {"cell": cell, "visits": visits}
            for cell, visits in sorted(
                self.runtime.get("traffic", {}).items(),
                key=lambda item: (-int(item[1]), item[0]),
            )[:5]
        ]
        return {
            "orders_total": len(order_rows),
            "orders_completed": sum(1 for order in order_rows if order["status"] == "completed"),
            "tasks_total": len(task_rows),
            "tasks_completed": status_counts.get("completed", 0),
            "tasks_cancelled": status_counts.get("cancelled", 0),
            "tasks_active": sum(
                status_counts.get(status, 0)
                for status in ("pending", "assigned", "running")
            ),
            "task_status": dict(status_counts),
            "agvs_total": len(self.agvs),
            "agvs_busy": sum(
                1 for agv in self.agvs.values() if agv["status"] != "idle"
            ),
            "avg_task_ticks": avg_duration,
            "throughput_per_100_ticks": round(
                len(completed) / max(1, self.tick) * 100, 2
            ),
            "utilization": utilization,
            "bottleneck_cells": bottleneck,
            "counters": copy.deepcopy(self.runtime["counters"]),
        }

    def reset_simulation(self) -> dict[str, Any]:
        with self.lock:
            self.runtime.update(
                {
                    "tick": 0,
                    "paused": True,
                    "speed": 1,
                    "map_version": "map-1",
                    "blocked_cells": [],
                    "traffic": {},
                    "counters": {
                        "conflicts_prevented": 0,
                        "deadlocks_detected": 0,
                        "deadlocks_resolved": 0,
                    },
                }
            )
            self.plans = {}
            for agv in self.agvs.values():
                agv["status"] = "idle"
                agv["active_task_id"] = None
                agv["payload_sku"] = None
                agv["waiting_on"] = None
                self._touch(agv)
                self._save_entity("agv", agv)
            for task in self.tasks.values():
                if task["status"] in {"assigned", "running"}:
                    task["status"] = "pending"
                    task["assigned_agv_id"] = None
                    self._touch(task)
                    self._save_entity("task", task)
            self._persist_runtime_locked()
            return self.snapshot()
