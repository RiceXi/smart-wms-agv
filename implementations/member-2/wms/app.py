"""应用上下文：组装数据库、地图、仿真、事件与业务服务。

其它模块（API、CLI、测试）都通过 `App` 使用系统，避免各自重复初始化。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import config, scenarios as scenario_module
from .analytics import Analytics
from .db import Database, default_data_dir
from .errors import NotFoundError, ValidationError
from .events import EventBus
from .mapdata import MapStore
from .mapgen import build_map
from .reservations import ReservationStore
from .scenarios import Scenario, locations_from_map
from .service import WmsService
from .simulator import AgvRuntime, Simulator
from .util import cell_id, now_iso

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = PACKAGE_DIR.parent
FIXTURES_DIR = PROJECT_DIR / "fixtures"
WEB_DIR = PROJECT_DIR / "web"


class App:
    """系统上下文。"""

    def __init__(self, data_dir: Path | str | None = None, db_path: Path | str | None = None,
                 scenario_id: str | None = None, seed: int | None = None):
        self.data_dir = Path(data_dir) if data_dir else default_data_dir()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = Path(db_path) if db_path else self.data_dir / "wms.db"

        self.db = Database(self.db_path)
        self.bus = EventBus()
        self.maps = MapStore(self.db, build_map(scenario_module.WAREHOUSE))
        self.service = WmsService(self.db, self.bus, tick_provider=lambda: self.sim.tick)
        self.sim = Simulator(self.db, self.maps, self.bus, self.service)
        self.service.sim = self.sim
        self.analytics = Analytics(self.db, self.sim)
        self.lock = self.sim.lock

        self._bootstrap(scenario_id, seed)

    # ------------------------------------------------------------ 启动
    def _bootstrap(self, scenario_id: str | None, seed: int | None) -> None:
        """首次启动时导入默认场景；已有数据则恢复运行态。"""
        has_data = self.db.query_one("SELECT COUNT(*) AS c FROM sku")["c"] > 0
        if not has_data:
            self.reset(scenario_id or "single-agv", seed or config.SEED_DEFAULT)
            return
        # 已有数据：恢复地图版本与运行态
        row = self.db.query_one(
            "SELECT map_id FROM map_versions ORDER BY created_at DESC, rowid DESC LIMIT 1")
        map_id = row["map_id"] if row else scenario_module.WAREHOUSE
        self.maps = MapStore(self.db, build_map(map_id))
        self.sim.maps = self.maps
        if not self.maps.restore_current():
            self.maps.persist_version(self.maps.current, "启动恢复")
        self.sim.load_runtime()

    # ------------------------------------------------------------ 重置
    def reset(self, scenario_id: str = "single-agv", seed: int | None = None) -> dict:
        """重置到指定场景：清库、导入地图与初始实体、重置仿真时钟。"""
        scenario = scenario_module.get_scenario(scenario_id)
        seed = int(seed if seed is not None else scenario.seed)

        with self.lock:
            self.db.reset_all()
            self.bus.set_seq(0)

            # 地图
            base_map = build_map(scenario.map_id)
            self.maps = MapStore(self.db, base_map)
            self.sim.maps = self.maps
            self.maps.persist_version(base_map, "场景初始化")
            self.sim.reservations = ReservationStore(base_map.map_id)

            # 仿真状态
            self.sim.tick = 0
            self.sim.running = False
            self.sim.speed = 1
            self.sim.seed = seed
            self.sim.scenario_id = scenario.id
            self.sim.agvs = {}
            self.sim.alerts = []
            self.sim.contention = {}
            self.sim.stats = {
                "conflicts": 0, "deadlocks": 0, "recoveries": 0, "rollbacks": 0,
                "completedTasks": 0, "failedTasks": 0, "cancelledTasks": 0,
            }

            # 库位主数据
            for location in locations_from_map(base_map):
                self.service.create_location(location)
            for location in scenario.locations:
                self.service.create_location(location)

            # SKU 与库存
            for sku in scenario.skus:
                self.service.create_sku(sku)
            for item in scenario.inventory:
                sku = self.db.query_one("SELECT id FROM sku WHERE code = ?", (item["skuCode"],))
                location = self.db.query_one("SELECT id FROM location WHERE code = ?", (item["locationCode"],))
                if not sku or not location:
                    raise ValidationError(
                        "场景库存引用的 SKU 或库位不存在",
                        {"skuCode": item["skuCode"], "locationCode": item["locationCode"]},
                    )
                self.service.add_inventory({
                    "skuId": sku["id"],
                    "locationId": location["id"],
                    "qty": item["qty"],
                    "batch": item.get("batch", ""),
                })

            # AGV
            for spec in scenario.agvs:
                self._create_agv(spec)

            # 订单与任务
            for order in scenario.orders:
                created = self.service.create_order(order)
                self.service.release_order(created["id"])

            # 固定初始计划
            for assignment in scenario.assignments:
                self._apply_assignment(assignment)

            # 场景定时事件：到达 atTick 时自动注入
            self.sim._pending_injections = [dict(event) for event in scenario.events]

            self.sim._persist_runtime()
            self.sim._emit("sim.reset", scenario.id, {"seed": seed, "mapVersion": base_map.version})
            return self.sim.snapshot()

    def _create_agv(self, spec: dict) -> AgvRuntime:
        agv_id = spec["id"]
        ts = now_iso()
        self.db.execute(
            """INSERT INTO agvs (id, map_id, status, x, y, battery, active_task_id, payload_sku,
                                 last_tick, fault_code, version, created_at, updated_at)
               VALUES (?,?,?,?,?,?,NULL,NULL,0,NULL,1,?,?)""",
            (agv_id, self.maps.current.map_id, "idle", int(spec["x"]), int(spec["y"]),
             float(spec.get("battery", 100.0)), ts, ts),
        )
        agv = AgvRuntime(
            id=agv_id, map_id=self.maps.current.map_id, status="idle",
            x=int(spec["x"]), y=int(spec["y"]), battery=float(spec.get("battery", 100.0)),
        )
        self.sim.agvs[agv_id] = agv
        return agv

    def _apply_assignment(self, assignment: dict) -> None:
        """把任务指派给指定 AGV，并安装场景给定的初始路径。"""
        agv = self.sim.agvs.get(assignment["agvId"])
        if agv is None:
            raise NotFoundError(f"场景指定的 AGV 不存在：{assignment['agvId']}", {"agvId": assignment["agvId"]})
        order = self.db.query_one("SELECT * FROM orders WHERE code = ?", (assignment["orderCode"],))
        if not order:
            raise NotFoundError(f"场景指定的订单不存在：{assignment['orderCode']}", {"code": assignment["orderCode"]})
        lines = self.db.query("SELECT * FROM order_lines WHERE order_id = ? ORDER BY id", (order["id"],))
        line_index = int(assignment.get("lineIndex", 0))
        if line_index >= len(lines):
            raise ValidationError("场景指定的订单明细越界", {"lineIndex": line_index})
        task = self.db.query_one(
            "SELECT * FROM tasks WHERE order_id = ? ORDER BY id LIMIT 1 OFFSET ?", (order["id"], line_index)
        )
        if not task:
            raise NotFoundError("场景指定的任务不存在", {"code": assignment["orderCode"]})

        self.service.assign_task(task["id"], agv.id, 1)
        plan_spec = assignment.get("plan")
        if plan_spec:
            steps = [{"cell": cell, "action": action} for cell, action in plan_spec["steps"]]
            self.sim.install_initial_plan(agv, task, steps, plan_spec.get("goal", ""))

    # ------------------------------------------------------------ 注入
    def inject(self, body: dict) -> dict:
        """事件注入：故障、离线、封路、急单、恢复。"""
        return self.sim.inject_event(body)

    # ------------------------------------------------------------ 关闭
    def close(self) -> None:
        self.sim.stop_thread()
        self.db.commit()
        self.db.close()
