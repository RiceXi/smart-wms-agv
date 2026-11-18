"""业务服务层：主数据、库存、订单与任务。

本模块只处理"业务事实"，不涉及 AGV 运动；AGV 相关逻辑在 `simulator.py`。
所有写操作都会：

1. 校验输入（编码、数量、坐标、状态）。
2. 在事务里落库。
3. 产生事件（订单释放、任务创建/指派/完成等）。
"""

from __future__ import annotations

from . import config
from .errors import (
    ConflictError,
    DuplicateCodeError,
    InsufficientInventoryError,
    InvalidStateError,
    NotFoundError,
    ValidationError,
)
from .util import dumps, new_id, now_iso, require_enum, require_int, require_str

SKU_BATCH_RULES = ("none", "fifo", "fefo")
LOCATION_TYPES = ("floor", "shelf", "pick", "drop", "charger", "blocked")

ORDER_STATUSES = ("draft", "allocated", "released", "completed", "cancelled", "failed")
TASK_STATUSES = ("pending", "assigned", "running", "completed", "cancelled", "failed")

TASK_TRANSITIONS = {
    "pending": {"assigned", "cancelled"},
    "assigned": {"running", "pending", "cancelled", "failed"},
    "running": {"completed", "failed"},
    "completed": set(),
    "cancelled": set(),
    "failed": set(),
}


def task_to_dict(row) -> dict:
    """把任务记录转换成对外统一的 camelCase 结构。"""
    task = dict(row)
    task["orderId"] = task.pop("order_id")
    task["pickupLocationId"] = task.pop("pickup_location_id")
    task["dropLocationId"] = task.pop("drop_location_id")
    task["assignedAgvId"] = task.pop("assigned_agv_id")
    task["pinnedAgvId"] = task.pop("pinned_agv_id")
    task["planVersion"] = task.pop("plan_version")
    task["retryCount"] = task.pop("retry_count")
    task["createdAt"] = task.pop("created_at")
    task["updatedAt"] = task.pop("updated_at")
    task["orderLineId"] = task.pop("order_line_id", None)
    task["type"] = task.get("type", "pick_and_drop")
    return task


def order_to_dict(row) -> dict:
    """把订单记录转换成对外统一的 camelCase 结构。"""
    order = dict(row)
    order["dropLocationId"] = order.pop("drop_location_id")
    order["createdAt"] = order.pop("created_at")
    order["updatedAt"] = order.pop("updated_at")
    return order


def agv_to_dict(row) -> dict:
    """把 AGV 记录转换成对外统一的 camelCase 结构。"""
    agv = dict(row)
    agv["mapId"] = agv.pop("map_id")
    agv["activeTaskId"] = agv.pop("active_task_id")
    agv["payloadSku"] = agv.pop("payload_sku")
    agv["lastTick"] = agv.pop("last_tick")
    agv["faultCode"] = agv.pop("fault_code")
    agv["createdAt"] = agv.pop("created_at")
    agv["updatedAt"] = agv.pop("updated_at")
    return agv


class WmsService:
    """业务事实的唯一写入入口。"""

    def __init__(self, db, bus, tick_provider=None):
        self.db = db
        self.bus = bus
        self._tick_provider = tick_provider or (lambda: 0)
        self.sim = None  # 由 Simulator 注入，用于释放 AGV 与重派

    # ------------------------------------------------------------ 工具
    def tick(self) -> int:
        return int(self._tick_provider())

    def _emit(self, event_type: str, aggregate_id: str | None, payload: dict) -> None:
        event = self.bus.publish(event_type, aggregate_id, payload, self.tick())
        self.db.execute(
            """INSERT INTO events (id, type, tick, aggregate_id, payload, occurred_at, seq)
               VALUES (?,?,?,?,?,?,?)""",
            (event["id"], event["type"], event["tick"], event["aggregateId"], dumps(event["payload"]),
             event["occurredAt"], event["seq"]),
        )
        self.db.commit()

    # ============================================================ SKU
    def create_sku(self, body: dict) -> dict:
        code = require_str(body, "code", max_len=64)
        name = require_str(body, "name", max_len=128)
        spec = require_str(body, "spec", max_len=128, required=False, default="")
        batch_rule = require_enum(body, "batchRule", SKU_BATCH_RULES, required=False, default="none")
        if self.db.query_one("SELECT id FROM sku WHERE code = ?", (code,)):
            raise DuplicateCodeError(f"SKU 编码已存在：{code}", {"code": code})
        sku_id = new_id("sku")
        ts = now_iso()
        self.db.execute(
            """INSERT INTO sku (id, code, name, spec, batch_rule, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?)""",
            (sku_id, code, name, spec, batch_rule, ts, ts),
        )
        self.db.commit()
        return self.get_sku(sku_id)

    def update_sku(self, sku_id: str, body: dict) -> dict:
        sku = self.get_sku(sku_id)
        code = require_str(body, "code", max_len=64)
        name = require_str(body, "name", max_len=128)
        spec = require_str(body, "spec", max_len=128, required=False, default="")
        batch_rule = require_enum(body, "batchRule", SKU_BATCH_RULES, required=False, default="none")
        if code != sku["code"] and self.db.query_one("SELECT id FROM sku WHERE code = ?", (code,)):
            raise DuplicateCodeError(f"SKU 编码已存在：{code}", {"code": code})
        self.db.execute(
            "UPDATE sku SET code=?, name=?, spec=?, batch_rule=?, updated_at=? WHERE id=?",
            (code, name, spec, batch_rule, now_iso(), sku_id),
        )
        self.db.commit()
        return self.get_sku(sku_id)

    def delete_sku(self, sku_id: str) -> None:
        self.get_sku(sku_id)
        used = self.db.query_one("SELECT COUNT(*) AS c FROM inventory WHERE sku_id = ?", (sku_id,))
        if used and used["c"]:
            raise ConflictError("SKU 仍有库存记录，不能删除", {"skuId": sku_id})
        self.db.execute("DELETE FROM sku WHERE id = ?", (sku_id,))
        self.db.commit()

    def get_sku(self, sku_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM sku WHERE id = ?", (sku_id,))
        if not row:
            raise NotFoundError(f"SKU 不存在：{sku_id}", {"skuId": sku_id})
        return dict(row)

    def list_skus(self, q: str | None = None, page: int = 0, size: int = config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if q:
            where.append("(code LIKE ? OR name LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM sku {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM sku {clause} ORDER BY code LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "page": page, "size": size, "total": total}

    # ============================================================ 库位
    def create_location(self, body: dict) -> dict:
        code = require_str(body, "code", max_len=64)
        zone = require_str(body, "zone", max_len=32)
        x = require_int(body, "x", minimum=0, maximum=config.MAP_WIDTH - 1)
        y = require_int(body, "y", minimum=0, maximum=config.MAP_HEIGHT - 1)
        ltype = require_enum(body, "type", LOCATION_TYPES)
        capacity = require_int(body, "capacity", minimum=1, maximum=999)
        if self.db.query_one("SELECT id FROM location WHERE code = ?", (code,)):
            raise DuplicateCodeError(f"库位编码已存在：{code}", {"code": code})
        location_id = new_id("loc")
        ts = now_iso()
        self.db.execute(
            """INSERT INTO location (id, code, zone, x, y, type, capacity, map_version, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (location_id, code, zone, x, y, ltype, capacity, self._map_version(), ts, ts),
        )
        self.db.commit()
        return self.get_location(location_id)

    def update_location(self, location_id: str, body: dict) -> dict:
        location = self.get_location(location_id)
        code = require_str(body, "code", max_len=64)
        zone = require_str(body, "zone", max_len=32)
        x = require_int(body, "x", minimum=0, maximum=config.MAP_WIDTH - 1)
        y = require_int(body, "y", minimum=0, maximum=config.MAP_HEIGHT - 1)
        ltype = require_enum(body, "type", LOCATION_TYPES)
        capacity = require_int(body, "capacity", minimum=1, maximum=999)
        if code != location["code"] and self.db.query_one("SELECT id FROM location WHERE code = ?", (code,)):
            raise DuplicateCodeError(f"库位编码已存在：{code}", {"code": code})
        self.db.execute(
            """UPDATE location SET code=?, zone=?, x=?, y=?, type=?, capacity=?, updated_at=? WHERE id=?""",
            (code, zone, x, y, ltype, capacity, now_iso(), location_id),
        )
        self.db.commit()
        return self.get_location(location_id)

    def delete_location(self, location_id: str) -> None:
        self.get_location(location_id)
        used = self.db.query_one("SELECT COUNT(*) AS c FROM inventory WHERE location_id = ?", (location_id,))
        if used and used["c"]:
            raise ConflictError("库位仍有库存记录，不能删除", {"locationId": location_id})
        tasks = self.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE pickup_location_id = ? OR drop_location_id = ?",
            (location_id, location_id),
        )
        if tasks and tasks["c"]:
            raise ConflictError("库位仍被任务引用，不能删除", {"locationId": location_id})
        self.db.execute("DELETE FROM location WHERE id = ?", (location_id,))
        self.db.commit()

    def get_location(self, location_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM location WHERE id = ?", (location_id,))
        if not row:
            raise NotFoundError(f"库位不存在：{location_id}", {"locationId": location_id})
        return dict(row)

    def list_locations(self, ltype: str | None = None, zone: str | None = None, q: str | None = None,
                       page: int = 0, size: int = config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if ltype:
            where.append("type = ?")
            params.append(ltype)
        if zone:
            where.append("zone = ?")
            params.append(zone)
        if q:
            where.append("code LIKE ?")
            params.append(f"%{q}%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM location {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM location {clause} ORDER BY code LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "page": page, "size": size, "total": total}

    def _map_version(self) -> str:
        if self.sim is not None:
            return self.sim.maps.current.version
        return "map-1"

    # ============================================================ 库存
    def add_inventory(self, body: dict) -> dict:
        sku_id = require_str(body, "skuId", max_len=64)
        location_id = require_str(body, "locationId", max_len=64)
        qty = require_int(body, "qty", minimum=0, maximum=10_000_000)
        batch = require_str(body, "batch", max_len=64, required=False, default="")
        expire_at = require_str(body, "expireAt", max_len=40, required=False, default=None)
        self.get_sku(sku_id)
        self.get_location(location_id)
        row = self.db.query_one(
            "SELECT * FROM inventory WHERE sku_id = ? AND location_id = ? AND batch = ?",
            (sku_id, location_id, batch),
        )
        ts = now_iso()
        if row:
            new_qty = row["qty"] + qty
            self.db.execute(
                "UPDATE inventory SET qty = ?, updated_at = ? WHERE id = ?", (new_qty, ts, row["id"])
            )
            inventory_id = row["id"]
            delta = qty
            qty_after = new_qty
        else:
            inventory_id = new_id("inv")
            self.db.execute(
                """INSERT INTO inventory (id, sku_id, location_id, qty, reserved_qty, batch, expire_at, version, created_at, updated_at)
                   VALUES (?,?,?,?,0,?,?,1,?,?)""",
                (inventory_id, sku_id, location_id, qty, batch, expire_at, ts, ts),
            )
            delta = qty
            qty_after = qty
        self._txn(sku_id, location_id, delta, 0, qty_after, "inbound", None)
        self.db.commit()
        return self.get_inventory(inventory_id)

    def adjust_inventory(self, inventory_id: str, body: dict) -> dict:
        item = self.get_inventory(inventory_id)
        delta = require_int(body, "delta", minimum=-10_000_000, maximum=10_000_000)
        reason = require_str(body, "reason", max_len=128, required=False, default="手工调整")
        new_qty = item["qty"] + delta
        if new_qty < 0:
            raise ValidationError("库存数量不能为负", {"qty": new_qty})
        if new_qty < item["reserved_qty"]:
            raise ValidationError("调整后的数量小于已预占数量", {"qty": new_qty, "reserved": item["reserved_qty"]})
        self.db.execute(
            "UPDATE inventory SET qty = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (new_qty, now_iso(), inventory_id),
        )
        self._txn(item["sku_id"], item["location_id"], delta, 0, new_qty, "adjust", reason)
        self.db.commit()
        return self.get_inventory(inventory_id)

    def _txn(self, sku_id, location_id, delta, reserved_delta, qty_after, txn_type, ref_id) -> None:
        self.db.execute(
            """INSERT INTO inventory_txn (id, sku_id, location_id, delta, reserved_delta, qty_after, txn_type, ref_id, tick, occurred_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (new_id("txn"), sku_id, location_id, delta, reserved_delta, qty_after, txn_type, ref_id, self.tick(), now_iso()),
        )

    def get_inventory(self, inventory_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM inventory WHERE id = ?", (inventory_id,))
        if not row:
            raise NotFoundError(f"库存记录不存在：{inventory_id}", {"inventoryId": inventory_id})
        item = dict(row)
        item["availableQty"] = item["qty"] - item["reserved_qty"]
        return item

    def list_inventory(self, sku_id: str | None = None, location_id: str | None = None,
                       page: int = 0, size: int = config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if sku_id:
            where.append("sku_id = ?")
            params.append(sku_id)
        if location_id:
            where.append("location_id = ?")
            params.append(location_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM inventory {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM inventory {clause} ORDER BY created_at LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        items = []
        for row in rows:
            item = dict(row)
            item["availableQty"] = item["qty"] - item["reserved_qty"]
            items.append(item)
        return {"items": items, "page": page, "size": size, "total": total}

    def list_inventory_txns(self, sku_id=None, location_id=None, page=0, size=config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if sku_id:
            where.append("sku_id = ?")
            params.append(sku_id)
        if location_id:
            where.append("location_id = ?")
            params.append(location_id)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM inventory_txn {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM inventory_txn {clause} ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "page": page, "size": size, "total": total}

    # ============================================================ 订单
    def create_order(self, body: dict) -> dict:
        code = require_str(body, "code", max_len=64)
        priority = require_int(body, "priority", minimum=1, maximum=10)
        requester = require_str(body, "requester", max_len=64)
        lines = body.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValidationError("订单至少需要一条明细", {"field": "lines"})
        drop_code = require_str(body, "dropLocationCode", max_len=64, required=False, default="")
        if self.db.query_one("SELECT id FROM orders WHERE code = ?", (code,)):
            raise DuplicateCodeError(f"订单号已存在：{code}", {"code": code})

        drop_location_id = None
        if drop_code:
            drop = self.db.query_one("SELECT * FROM location WHERE code = ? AND type = 'drop'", (drop_code,))
            if not drop:
                raise NotFoundError(f"放货点不存在：{drop_code}", {"dropLocationCode": drop_code})
            drop_location_id = drop["id"]
        else:
            drop = self.db.query_one("SELECT * FROM location WHERE type = 'drop' ORDER BY code LIMIT 1")
            if drop:
                drop_location_id = drop["id"]

        prepared = []
        for raw in lines:
            sku_code = require_str(raw, "skuCode", max_len=64)
            qty = require_int(raw, "qty", minimum=1, maximum=100_000)
            sku = self.db.query_one("SELECT * FROM sku WHERE code = ?", (sku_code,))
            if not sku:
                raise NotFoundError(f"SKU 不存在：{sku_code}", {"skuCode": sku_code})
            # 可选：指定拣选点与指定车辆（人工派工 / 固定演示场景）
            pickup_location_id = None
            pickup_code = require_str(raw, "pickupLocationCode", max_len=64, required=False, default="")
            if pickup_code:
                pickup = self.db.query_one("SELECT * FROM location WHERE code = ?", (pickup_code,))
                if not pickup:
                    raise NotFoundError(f"拣选点不存在：{pickup_code}", {"pickupLocationCode": pickup_code})
                pickup_location_id = pickup["id"]
            pinned_agv_id = require_str(raw, "agvId", max_len=64, required=False, default="") or None
            prepared.append((sku["id"], sku_code, qty, pickup_location_id, pinned_agv_id))

        order_id = new_id("ord")
        ts = now_iso()
        self.db.execute(
            """INSERT INTO orders (id, code, type, status, priority, requester, drop_location_id, created_at, updated_at, version)
               VALUES (?,?,?,?,?,?,?,?,?,1)""",
            (order_id, code, "outbound", "draft", priority, requester, drop_location_id, ts, ts),
        )
        for sku_id, _sku_code, qty, pickup_location_id, pinned_agv_id in prepared:
            self.db.execute(
                """INSERT INTO order_lines (id, order_id, sku_id, qty, batch, location_id, pickup_location_id, assigned_agv_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (new_id("oln"), order_id, sku_id, qty, "", None, pickup_location_id, pinned_agv_id),
            )
        self.db.commit()
        return self.get_order(order_id)

    def get_order(self, order_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
        if not row:
            raise NotFoundError(f"订单不存在：{order_id}", {"orderId": order_id})
        order = dict(row)
        order["lines"] = self._order_lines([order_id])[order_id]
        order["taskIds"] = self._order_task_ids([order_id]).get(order_id, [])
        return order

    def _order_lines(self, order_ids: list[str]) -> dict[str, list[dict]]:
        """批量取订单明细（含 SKU 编码与名称），避免逐单查询。"""
        if not order_ids:
            return {}
        placeholders = ",".join("?" for _ in order_ids)
        rows = self.db.query(
            f"""SELECT ol.*, s.code AS sku_code, s.name AS sku_name
                FROM order_lines ol LEFT JOIN sku s ON s.id = ol.sku_id
                WHERE ol.order_id IN ({placeholders}) ORDER BY ol.id""",
            tuple(order_ids),
        )
        grouped: dict[str, list[dict]] = {}
        for row in rows:
            line = {
                "id": row["id"],
                "orderId": row["order_id"],
                "skuId": row["sku_id"],
                "skuCode": row["sku_code"] or "",
                "skuName": row["sku_name"] or "",
                "qty": row["qty"],
                "batch": row["batch"] or "",
                "locationId": row["location_id"],
                "pickupLocationId": row["pickup_location_id"],
                "assignedAgvId": row["assigned_agv_id"],
            }
            grouped.setdefault(row["order_id"], []).append(line)
        return grouped

    def _order_task_ids(self, order_ids: list[str]) -> dict[str, list[str]]:
        if not order_ids:
            return {}
        placeholders = ",".join("?" for _ in order_ids)
        rows = self.db.query(
            f"SELECT id, order_id FROM tasks WHERE order_id IN ({placeholders}) ORDER BY id",
            tuple(order_ids),
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(row["order_id"], []).append(row["id"])
        return grouped

    def attach_order_details(self, orders: list[dict]) -> list[dict]:
        """给订单列表补上 `lines` 与 `taskIds`（前端表格依赖这两个字段）。"""
        if not orders:
            return orders
        ids = [order["id"] for order in orders]
        lines = self._order_lines(ids)
        tasks = self._order_task_ids(ids)
        for order in orders:
            order["lines"] = lines.get(order["id"], [])
            order["taskIds"] = tasks.get(order["id"], [])
        return orders

    def all_orders(self) -> list[dict]:
        """全部订单（含明细与任务列表），按创建时间倒序。"""
        rows = self.db.query("SELECT * FROM orders ORDER BY created_at DESC, id DESC")
        orders = [order_to_dict(row) for row in rows]
        return self.attach_order_details(orders)

    def list_orders(self, status: str | None = None, code: str | None = None,
                    page: int = 0, size: int = config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if code:
            where.append("code LIKE ?")
            params.append(f"%{code}%")
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM orders {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM orders {clause} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        items = self.attach_order_details([dict(r) for r in rows])
        return {"items": items, "page": page, "size": size, "total": total}

    def release_order(self, order_id: str) -> dict:
        """释放订单：预占库存并生成拣选任务。重复调用是幂等的。"""
        order = self.get_order(order_id)
        if order["status"] in ("released", "completed"):
            return order  # 幂等：已释放的订单不重复扣减
        if order["status"] in ("cancelled", "failed"):
            raise InvalidStateError(f"订单状态 {order['status']} 不允许释放", {"status": order["status"]})
        if order["status"] != "draft":
            raise InvalidStateError(f"订单状态 {order['status']} 不允许释放", {"status": order["status"]})

        lines = self.db.query("SELECT * FROM order_lines WHERE order_id = ? ORDER BY id", (order_id,))
        per_line: list[list[tuple[str, str, int, str, str]]] = []
        try:
            for line in lines:
                per_line.append(self._allocate(line["sku_id"], line["qty"]))
        except InsufficientInventoryError:
            # 回滚已预占部分，保证"失败不留下部分扣减"
            for group in per_line:
                for inventory_id, _sku_id, qty, _batch, _location_id in group:
                    self._unreserve(inventory_id, qty)
            self.db.commit()
            raise

        self.db.execute(
            "UPDATE orders SET status = 'allocated', updated_at = ?, version = version + 1 WHERE id = ?",
            (now_iso(), order_id),
        )
        # 写回明细的批次、库位与分配记录
        for line, group in zip(lines, per_line):
            if not group:
                continue
            primary = group[0]
            self.db.execute(
                "UPDATE order_lines SET batch = ?, location_id = ? WHERE id = ?",
                (primary[3], primary[4], line["id"]),
            )
            for allocation in group:
                self.db.execute(
                    "INSERT INTO order_allocations (id, order_line_id, inventory_id, qty, created_at) VALUES (?,?,?,?,?)",
                    (new_id("alloc"), line["id"], allocation[0], allocation[2], now_iso()),
                )

        # 重新读取明细（拿到最新的库位）
        lines = self.db.query("SELECT * FROM order_lines WHERE order_id = ? ORDER BY id", (order_id,))

        # 生成任务：每个明细一个任务
        task_ids = []
        for line in lines:
            pickup = line["pickup_location_id"] or self._pick_station_for(line["location_id"])
            drop_id = order["drop_location_id"] or self._default_drop_location()
            if pickup is None or drop_id is None:
                raise InvalidStateError("缺少拣选点或放货点，无法生成任务", {"orderId": order_id})
            task = self.create_task(
                order_id=order_id,
                priority=order["priority"],
                pickup_location_id=pickup,
                drop_location_id=drop_id,
                sku_id=line["sku_id"],
                pinned_agv_id=line["assigned_agv_id"],
                order_line_id=line["id"],
            )
            task_ids.append(task["id"])

        self.db.execute(
            "UPDATE orders SET status = 'released', updated_at = ?, version = version + 1 WHERE id = ?",
            (now_iso(), order_id),
        )
        self.db.commit()
        self._emit("order.released", order_id, {"taskIds": task_ids, "priority": order["priority"]})
        return self.get_order(order_id)

    def cancel_order(self, order_id: str) -> dict:
        order = self.get_order(order_id)
        if order["status"] in ("cancelled", "completed", "failed"):
            raise InvalidStateError(f"订单状态 {order['status']} 不允许取消", {"status": order["status"]})
        tasks = self.db.query("SELECT * FROM tasks WHERE order_id = ?", (order_id,))
        for task in tasks:
            if task["status"] in ("running", "completed"):
                raise InvalidStateError("存在进行中或已完成的任务，订单不能取消", {"taskId": task["id"]})
            if task["status"] in ("pending", "assigned"):
                self.cancel_task(task["id"], reason="订单取消")

        # 回滚预占库存
        allocations = self.db.query(
            """SELECT a.* FROM order_allocations a
               JOIN order_lines l ON l.id = a.order_line_id WHERE l.order_id = ?""",
            (order_id,),
        )
        for allocation in allocations:
            self._unreserve(allocation["inventory_id"], allocation["qty"])
        self.db.execute("UPDATE orders SET status = 'cancelled', updated_at = ?, version = version + 1 WHERE id = ?",
                        (now_iso(), order_id))
        self.db.commit()
        self._emit("order.cancelled", order_id, {"reason": "用户取消"})
        return self.get_order(order_id)

    def _default_drop_location(self) -> str | None:
        row = self.db.query_one("SELECT id FROM location WHERE type = 'drop' ORDER BY code LIMIT 1")
        return row["id"] if row else None

    def _pick_station_for(self, location_id: str | None) -> str | None:
        """按区域找到拣选点。货架格不可进入，AGV 在同区域的拣选点完成取货。

        同一区域可能有多个拣选点，这里选当前在办任务最少的一个，避免所有任务
        挤到同一个工位排队。
        """
        if not location_id:
            return None
        location = self.db.query_one("SELECT * FROM location WHERE id = ?", (location_id,))
        if not location:
            return None
        if location["type"] == "pick":
            return location["id"]
        stations = self.db.query(
            "SELECT id FROM location WHERE type = 'pick' AND zone = ? ORDER BY code", (location["zone"],)
        )
        if not stations:
            return None
        best = None
        best_load = None
        for station in stations:
            load = self.db.query_one(
                """SELECT COUNT(*) AS c FROM tasks
                   WHERE pickup_location_id = ? AND status IN ('pending','assigned','running')""",
                (station["id"],),
            )["c"]
            if best_load is None or load < best_load:
                best = station["id"]
                best_load = load
        return best

    def _allocate(self, sku_id: str, qty: int) -> list[tuple[str, str, int, str, str]]:
        """按批次规则分配库存并预占，返回 (inventory_id, sku_id, qty, batch, location_id)。"""
        sku = self.get_sku(sku_id)
        rule = sku["batch_rule"]
        order_clause = "expire_at IS NULL, expire_at ASC, created_at ASC" if rule == "fefo" else "created_at ASC, id ASC"
        rows = self.db.query(
            f"""SELECT * FROM inventory WHERE sku_id = ? AND (qty - reserved_qty) > 0
                ORDER BY {order_clause}""",
            (sku_id,),
        )
        remaining = qty
        picked: list[tuple[str, str, int, str]] = []
        for row in rows:
            if remaining <= 0:
                break
            available = row["qty"] - row["reserved_qty"]
            take = min(available, remaining)
            self.db.execute(
                "UPDATE inventory SET reserved_qty = reserved_qty + ?, version = version + 1, updated_at = ? WHERE id = ?",
                (take, now_iso(), row["id"]),
            )
            self._txn(sku_id, row["location_id"], 0, take, row["qty"], "reserve", row["id"])
            picked.append((row["id"], sku_id, take, row["batch"], row["location_id"]))
            remaining -= take
        if remaining > 0:
            # 分配失败必须回滚本行已经预占的部分，保证"失败不留下部分扣减"
            for inventory_id, _sku_id, taken, _batch, _location_id in picked:
                self._unreserve(inventory_id, taken)
            self.db.commit()
            raise InsufficientInventoryError(
                f"SKU {sku['code']} 可用库存不足，仍缺 {remaining}",
                {"skuCode": sku["code"], "required": qty, "missing": remaining},
            )
        return picked

    def _unreserve(self, inventory_id: str, qty: int) -> None:
        row = self.db.query_one("SELECT * FROM inventory WHERE id = ?", (inventory_id,))
        if not row:
            return
        new_reserved = max(0, row["reserved_qty"] - qty)
        self.db.execute(
            "UPDATE inventory SET reserved_qty = ?, version = version + 1, updated_at = ? WHERE id = ?",
            (new_reserved, now_iso(), inventory_id),
        )
        self._txn(row["sku_id"], row["location_id"], 0, -qty, row["qty"], "release", inventory_id)

    # ============================================================ 任务
    def create_task(self, order_id: str, priority: int, pickup_location_id: str, drop_location_id: str,
                    sku_id: str | None = None, pinned_agv_id: str | None = None,
                    order_line_id: str | None = None) -> dict:
        task_id = new_id("task")
        ts = now_iso()
        self.db.execute(
            """INSERT INTO tasks (id, order_id, order_line_id, type, priority, status, pickup_location_id, drop_location_id,
                                  assigned_agv_id, pinned_agv_id, plan_version, retry_count, created_at, updated_at, version)
               VALUES (?,?,?,?,?,?,?,?,NULL,?,0,0,?,?,1)""",
            (task_id, order_id, order_line_id, "pick_and_drop", priority, "pending", pickup_location_id, drop_location_id,
             pinned_agv_id, ts, ts),
        )
        self.db.commit()
        payload = {"orderId": order_id, "priority": priority, "skuId": sku_id}
        # 任务生命周期起点也写入 task_events：统计"任务耗时"以创建 tick 为起点。
        self.db.execute(
            """INSERT INTO task_events (id, task_id, type, from_status, to_status, agv_id, plan_version, payload, occurred_at, tick, seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id("tev"), task_id, "task.created", "", "pending", None, 0,
             dumps(payload), ts, self.tick(), self.bus.seq + 1),
        )
        self.db.commit()
        self._emit("task.created", task_id, payload)
        return self.get_task(task_id)

    def get_task(self, task_id: str) -> dict:
        row = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not row:
            raise NotFoundError(f"任务不存在：{task_id}", {"taskId": task_id})
        task = dict(row)
        pickup = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["pickup_location_id"],))
        drop = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["drop_location_id"],))
        task["pickupCell"] = f"{pickup['x']},{pickup['y']}" if pickup else ""
        task["dropCell"] = f"{drop['x']},{drop['y']}" if drop else ""
        task["events"] = [dict(e) for e in self.db.query(
            "SELECT * FROM task_events WHERE task_id = ? ORDER BY seq", (task_id,))]
        return task

    def list_tasks(self, status=None, agv_id=None, order_id=None, priority_min=None, priority_max=None,
                   page=0, size=config.DEFAULT_PAGE_SIZE) -> dict:
        where, params = [], []
        if status:
            where.append("status = ?")
            params.append(status)
        if agv_id:
            where.append("assigned_agv_id = ?")
            params.append(agv_id)
        if order_id:
            where.append("order_id = ?")
            params.append(order_id)
        if priority_min is not None:
            where.append("priority >= ?")
            params.append(priority_min)
        if priority_max is not None:
            where.append("priority <= ?")
            params.append(priority_max)
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self.db.query_one(f"SELECT COUNT(*) AS c FROM tasks {clause}", tuple(params))["c"]
        rows = self.db.query(
            f"SELECT * FROM tasks {clause} ORDER BY priority DESC, created_at ASC, id ASC LIMIT ? OFFSET ?",
            tuple(params) + (size, page * size),
        )
        return {"items": [dict(r) for r in rows], "page": page, "size": size, "total": total}

    def set_task_priority(self, task_id: str, body: dict) -> dict:
        task = self.get_task(task_id)
        priority = require_int(body, "priority", minimum=1, maximum=10)
        if task["status"] in ("completed", "cancelled", "failed"):
            raise InvalidStateError("终态任务不允许调整优先级", {"status": task["status"]})
        self.db.execute(
            "UPDATE tasks SET priority = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (priority, now_iso(), task_id),
        )
        self.db.commit()
        self._emit("task.priority.changed", task_id, {"priority": priority, "previous": task["priority"]})
        return self.get_task(task_id)

    def cancel_task(self, task_id: str, reason: str = "用户取消") -> dict:
        task = self.get_task(task_id)
        if task["status"] not in ("pending", "assigned"):
            raise InvalidStateError(f"任务状态 {task['status']} 不允许取消", {"status": task["status"]})
        if task["assigned_agv_id"] and self.sim is not None:
            self.sim.release_agv_for_task(task["assigned_agv_id"], task_id)
        self._transition_task(task_id, "cancelled", event_type="task.cancelled", payload={"reason": reason})
        return self.get_task(task_id)

    def reassign_task(self, task_id: str) -> dict:
        """把任务放回任务池等待重新指派。"""
        task = self.get_task(task_id)
        if task["status"] not in ("pending", "assigned", "running"):
            raise InvalidStateError(f"任务状态 {task['status']} 不允许重派", {"status": task["status"]})
        if task["assigned_agv_id"] and self.sim is not None:
            self.sim.release_agv_for_task(task["assigned_agv_id"], task_id)
        self._transition_task(task_id, "pending", event_type="task.cancelled",
                              payload={"reason": "重派", "reassigned": True})
        self.db.execute("UPDATE tasks SET assigned_agv_id = NULL, updated_at = ? WHERE id = ?",
                        (now_iso(), task_id))
        self.db.commit()
        return self.get_task(task_id)

    def fail_task(self, task_id: str, reason: str) -> None:
        task = self.get_task(task_id)
        if task["status"] in ("completed", "cancelled", "failed"):
            return
        self._transition_task(task_id, "failed", event_type="task.failed", payload={"reason": reason})

    def complete_task(self, task_id: str, payload: dict | None = None) -> None:
        task = self.get_task(task_id)
        if task["status"] == "completed":
            return
        self._transition_task(task_id, "completed", event_type="task.completed", payload=payload or {})
        # 按分配记录精确扣减库存（一个明细可能跨批次）
        allocations = self.db.query(
            """SELECT a.* FROM order_allocations a
               JOIN tasks t ON t.order_line_id = a.order_line_id WHERE t.id = ?""",
            (task_id,),
        )
        for allocation in allocations:
            inventory = self.db.query_one("SELECT * FROM inventory WHERE id = ?", (allocation["inventory_id"],))
            if not inventory:
                continue
            deduct = min(allocation["qty"], inventory["reserved_qty"], inventory["qty"])
            new_qty = max(0, inventory["qty"] - deduct)
            new_reserved = max(0, inventory["reserved_qty"] - deduct)
            self.db.execute(
                """UPDATE inventory SET qty = ?, reserved_qty = ?, version = version + 1, updated_at = ? WHERE id = ?""",
                (new_qty, new_reserved, now_iso(), inventory["id"]),
            )
            self._txn(inventory["sku_id"], inventory["location_id"], -deduct, -deduct, new_qty, "outbound", task_id)
        # 订单全部任务完成则订单完成
        remaining = self.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE order_id = ? AND status NOT IN ('completed','cancelled','failed')",
            (task["order_id"],),
        )
        if remaining and remaining["c"] == 0:
            self.db.execute(
                "UPDATE orders SET status = 'completed', updated_at = ?, version = version + 1 WHERE id = ?",
                (now_iso(), task["order_id"]),
            )
            self.db.commit()
            self._emit("order.completed", task["order_id"], {})

    def _transition_task(self, task_id: str, to_status: str, event_type: str, payload: dict | None = None,
                         agv_id: str | None = None, plan_version: int | None = None) -> None:
        task = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise NotFoundError(f"任务不存在：{task_id}", {"taskId": task_id})
        from_status = task["status"]
        if from_status == to_status:
            return
        allowed = TASK_TRANSITIONS.get(from_status, set())
        if to_status not in allowed:
            raise InvalidStateError(
                f"任务不允许从 {from_status} 流转到 {to_status}",
                {"taskId": task_id, "from": from_status, "to": to_status},
            )
        self.db.execute(
            """UPDATE tasks SET status = ?, updated_at = ?, version = version + 1 WHERE id = ?""",
            (to_status, now_iso(), task_id),
        )
        seq = self.bus.seq + 1
        self.db.execute(
            """INSERT INTO task_events (id, task_id, type, from_status, to_status, agv_id, plan_version, payload, occurred_at, tick, seq)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (new_id("tev"), task_id, event_type, from_status, to_status, agv_id, plan_version,
             dumps(payload or {}), now_iso(), self.tick(), seq),
        )
        self.db.commit()
        self._emit(event_type, task_id, {"from": from_status, "to": to_status, **(payload or {})})

    def assign_task(self, task_id: str, agv_id: str, plan_version: int) -> None:
        task = self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))
        if not task:
            raise NotFoundError(f"任务不存在：{task_id}", {"taskId": task_id})
        self.db.execute(
            """UPDATE tasks SET assigned_agv_id = ?, plan_version = ?,
                                updated_at = ?, version = version + 1 WHERE id = ?""",
            (agv_id, plan_version, now_iso(), task_id),
        )
        self.db.commit()
        # 状态流转由 _transition_task 统一处理，这里不能提前改 status，
        # 否则 from==to 会让 task.assigned 事件被吞掉。
        self._transition_task(task_id, "assigned", event_type="task.assigned",
                              payload={"agvId": agv_id, "planVersion": plan_version}, agv_id=agv_id,
                              plan_version=plan_version)

    def start_task(self, task_id: str, agv_id: str, plan_version: int) -> None:
        self._transition_task(task_id, "running", event_type="task.started",
                              payload={"agvId": agv_id}, agv_id=agv_id, plan_version=plan_version)

    def replan_task(self, task_id: str, agv_id: str, plan_version: int, reason: str) -> None:
        self.db.execute(
            "UPDATE tasks SET plan_version = ?, updated_at = ?, version = version + 1 WHERE id = ?",
            (plan_version, now_iso(), task_id),
        )
        self.db.commit()
        self._emit("task.replanned", task_id, {"agvId": agv_id, "planVersion": plan_version, "reason": reason})
