"""固定演示场景。

每个场景都固定：地图版本、seed、初始实体（AGV、库存、订单）、预期关键事件。
场景是演示与回归测试的共同基准，任何人都可以用同一个 seed 得到同样的结果。

场景里显式给出"初始计划"（assignments），用于精确构造冲突与死锁的初始状态：
正常流程里规划器会主动避让被占用的格点，无法自然形成互相等待的死锁，
因此这类场景按公共契约"保存初始实体"的要求，直接给出初始路径。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .mapdata import WarehouseMap

WAREHOUSE = "warehouse-24"
RING = "ring-3"


@dataclass
class Scenario:
    id: str
    title: str
    description: str
    map_id: str
    seed: int
    skus: list[dict] = field(default_factory=list)
    locations: list[dict] = field(default_factory=list)
    inventory: list[dict] = field(default_factory=list)
    agvs: list[dict] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    assignments: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    """定时注入的事件：`{"atTick": 30, "type": "roadblock", "cells": ["10,7"], "reason": "..."}`。"""
    expect: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "map": self.map_id,
            "seed": self.seed,
            "skus": self.skus,
            "locations": self.locations,
            "inventory": self.inventory,
            "agvs": self.agvs,
            "orders": self.orders,
            "assignments": self.assignments,
            "events": self.events,
            "expect": self.expect,
        }


DEFAULT_SKUS = [
    {"code": "SKU-001", "name": "折叠周转箱", "spec": "60x40x30", "batchRule": "fifo"},
    {"code": "SKU-002", "name": "标签纸卷", "spec": "100mm", "batchRule": "fefo"},
    {"code": "SKU-003", "name": "缓冲垫片", "spec": "标准", "batchRule": "none"},
]


def _orders(prefix: str, count: int, priority: int = 5,
            skus: tuple[str, ...] = ("SKU-001", "SKU-002", "SKU-003"), qty: int = 2) -> list[dict]:
    """生成订单。默认在三个区域的 SKU 之间轮转，避免所有任务挤到同一个拣选点。"""
    return [
        {
            "code": f"OUT-{prefix}-{index + 1:03d}",
            "priority": priority,
            "requester": "demo",
            # 四个放货点轮转，避免所有任务挤到同一个放货点
            "dropLocationCode": f"DROP-{(index % 4) + 1}",
            "lines": [{"skuCode": skus[index % len(skus)], "qty": qty}],
        }
        for index in range(count)
    ]


def _warehouse_agvs(count: int, battery: float = 100.0) -> list[dict]:
    from .mapgen import initial_agv_positions

    positions = initial_agv_positions(30)
    return [
        {"id": f"AGV-{index + 1:02d}", "x": x, "y": y, "battery": battery}
        for index, (x, y) in enumerate(positions[:count])
    ]


def _inventory_for(qty: int = 200) -> list[dict]:
    """三个区域各放一个 SKU 的库存，让拣选点分布在 A/B/C 区。"""
    return [
        {"skuCode": "SKU-001", "locationCode": "SHELF-A-001", "qty": qty, "batch": "B-A-2026"},
        {"skuCode": "SKU-002", "locationCode": "SHELF-B-001", "qty": qty, "batch": "B-B-2026"},
        {"skuCode": "SKU-003", "locationCode": "SHELF-C-001", "qty": qty, "batch": "B-C-2026"},
    ]


SCENARIOS: dict[str, Scenario] = {
    "single-agv": Scenario(
        id="single-agv",
        title="单 AGV 闭环",
        description="1 台 AGV、1 个出库订单，验证订单->任务->路径->仿真->完成的闭环。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=[{"id": "AGV-01", "x": 12, "y": 18, "battery": 100.0}],
        orders=_orders("SINGLE", 1),
        expect={"completedTasks": 1, "deadlocks": 0},
    ),
    "peak-10": Scenario(
        id="peak-10",
        title="10 台并发",
        description="10 台 AGV、10 个订单，个人版规模基线场景。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=_warehouse_agvs(10),
        orders=_orders("PEAK10", 10),
        expect={"completedTasks": 10},
    ),
    "peak-30": Scenario(
        id="peak-30",
        title="30 台并发",
        description="30 台 AGV、30 个订单，整合版规模基线场景。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=_warehouse_agvs(30),
        orders=_orders("PEAK30", 30),
        expect={"completedTasks": 30},
    ),
    "head-to-head": Scenario(
        id="head-to-head",
        title="窄巷相向冲突",
        description="两台 AGV 在 1 格宽窄巷相向而行，验证禁止穿边、等待与回退恢复。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=[
            {"id": "AGV-A", "x": 4, "y": 2, "battery": 100.0, "priority": 5},
            {"id": "AGV-B", "x": 4, "y": 4, "battery": 100.0, "priority": 3},
        ],
        orders=[
            {
                "code": "OUT-HEAD-001",
                "priority": 5,
                "requester": "demo",
                "lines": [{"skuCode": "SKU-001", "qty": 1, "pickupLocationCode": "PICK-A1"}],
            },
            {
                "code": "OUT-HEAD-002",
                "priority": 5,
                "requester": "demo",
                "lines": [{"skuCode": "SKU-001", "qty": 1, "pickupLocationCode": "PICK-H1"}],
            },
        ],
        locations=[{"code": "PICK-H1", "zone": "A", "x": 4, "y": 0, "type": "pick", "capacity": 1}],
        assignments=[
            {
                "agvId": "AGV-A",
                "orderCode": "OUT-HEAD-001",
                "lineIndex": 0,
                "plan": {
                    "goal": "4,6",
                    "steps": [["4,2", "wait"], ["4,3", "move"], ["4,4", "move"], ["4,5", "move"], ["4,6", "move"]],
                },
            },
            {
                "agvId": "AGV-B",
                "orderCode": "OUT-HEAD-002",
                "lineIndex": 0,
                "plan": {
                    "goal": "4,0",
                    "steps": [
                        ["4,4", "wait"], ["4,4", "wait"], ["4,4", "wait"],
                        ["4,3", "move"], ["4,2", "move"], ["4,1", "move"], ["4,0", "move"],
                    ],
                },
            },
        ],
        expect={"deadlocks": 1, "rollbacks": 1, "headOnBlocked": True},
    ),
    "cycle-deadlock": Scenario(
        id="cycle-deadlock",
        title="三车环形等待",
        description="三台 AGV 在三角形环路互相等待，等待图成环后按优先级回退恢复。",
        map_id=RING,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        locations=[{"code": "SHELF-R1", "zone": "R", "x": 6, "y": 6, "type": "shelf", "capacity": 1}],
        inventory=[{"skuCode": "SKU-001", "locationCode": "SHELF-R1", "qty": 100, "batch": "B-R-2026"}],
        agvs=[
            {"id": "AGV-A", "x": 7, "y": 3, "battery": 100.0, "priority": 5},
            {"id": "AGV-B", "x": 8, "y": 3, "battery": 100.0, "priority": 5},
            {"id": "AGV-C", "x": 8, "y": 4, "battery": 100.0, "priority": 1},
        ],
        orders=[
            {
                "code": "OUT-CYCLE-001",
                "priority": 5,
                "requester": "demo",
                "lines": [{"skuCode": "SKU-001", "qty": 1, "pickupLocationCode": "PICK-R2"}],
            },
            {
                "code": "OUT-CYCLE-002",
                "priority": 5,
                "requester": "demo",
                "lines": [{"skuCode": "SKU-001", "qty": 1, "pickupLocationCode": "PICK-R3"}],
            },
            {
                "code": "OUT-CYCLE-003",
                "priority": 1,
                "requester": "demo",
                "lines": [{"skuCode": "SKU-001", "qty": 1, "pickupLocationCode": "PICK-R1"}],
            },
        ],
        assignments=[
            {
                "agvId": "AGV-A",
                "orderCode": "OUT-CYCLE-001",
                "lineIndex": 0,
                "plan": {"goal": "8,3", "steps": [["7,3", "wait"], ["8,3", "move"]]},
            },
            {
                "agvId": "AGV-B",
                "orderCode": "OUT-CYCLE-002",
                "lineIndex": 0,
                "plan": {"goal": "8,4", "steps": [["8,3", "wait"], ["8,4", "move"]]},
            },
            {
                "agvId": "AGV-C",
                "orderCode": "OUT-CYCLE-003",
                "lineIndex": 0,
                "plan": {"goal": "7,3", "steps": [["8,4", "wait"], ["7,3", "move"]]},
            },
        ],
        expect={"deadlocks": 1, "rollbacks": 1, "cycleLength": 3, "completedTasks": 3},
    ),
    "roadblock": Scenario(
        id="roadblock",
        title="临时封路重规划",
        description="运行中封闭正在使用的巷道，验证受影响路径升版本重规划且不进入封路格。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=_warehouse_agvs(10),
        orders=_orders("ROAD", 10),
        events=[
            {"atTick": 25, "type": "roadblock", "cells": ["12,6"], "reason": "巷道临时施工"},
            {"atTick": 60, "type": "roadblock", "cells": ["12,12"], "reason": "货物占道"},
            {"atTick": 120, "type": "roadblock", "cells": ["12,18"], "reason": "临时封路"},
        ],
        expect={"replans": 1, "roadblocks": 3},
    ),
    "low-battery": Scenario(
        id="low-battery",
        title="低电量充电",
        description="多台 AGV 电量低于阈值，验证进入充电队列、充电位容量不被突破。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=_warehouse_agvs(6, battery=14.0),
        orders=_orders("CHARGE", 3),
        expect={"charging": 1},
    ),
    "rush-order": Scenario(
        id="rush-order",
        title="急单插入",
        description="运行中插入高优先级订单，验证任务队列与指派顺序体现优先级变化。",
        map_id=WAREHOUSE,
        seed=config.SEED_DEFAULT,
        skus=DEFAULT_SKUS,
        inventory=_inventory_for(),
        agvs=_warehouse_agvs(4),
        orders=_orders("RUSH", 4, priority=2),
        events=[
            {"atTick": 20, "type": "rush_order", "priority": 10, "skuCode": "SKU-001", "qty": 1,
             "code": "OUT-RUSH-0001"},
            {"atTick": 90, "type": "rush_order", "priority": 9, "skuCode": "SKU-002", "qty": 1,
             "code": "OUT-RUSH-0002"},
        ],
        expect={"rushPriority": 10, "rushOrders": 2},
    ),
}


def list_scenarios() -> list[dict]:
    return [
        {
            "id": scenario.id,
            "title": scenario.title,
            "description": scenario.description,
            "map": scenario.map_id,
            "seed": scenario.seed,
            "expect": scenario.expect,
        }
        for scenario in SCENARIOS.values()
    ]


def get_scenario(scenario_id: str) -> Scenario:
    scenario = SCENARIOS.get(scenario_id)
    if scenario is None:
        from .errors import NotFoundError

        raise NotFoundError(f"场景不存在：{scenario_id}", {"scenarioId": scenario_id, "available": list(SCENARIOS)})
    return scenario


def locations_from_map(wm: WarehouseMap) -> list[dict]:
    """由地图格点生成库位主数据。

    工位（拣选点/放货点/充电桩）使用地图里定义的编码，货架按区域编号。
    """
    result: list[dict] = []
    counters: dict[str, int] = {}
    for station in wm.stations:
        cell = wm.cells.get(station.cell_id)
        if cell is None:
            continue
        result.append({
            "code": station.code or f"{station.type.upper()}-{station.cell_id}",
            "zone": cell.zone or ("R" if wm.map_id == RING else "Z"),
            "x": cell.x,
            "y": cell.y,
            "type": station.type,
            "capacity": station.capacity,
        })
    for cell in sorted(wm.cells.values(), key=lambda c: (c.y, c.x)):
        if cell.type != config.CELL_SHELF:
            continue
        zone = cell.zone or "Z"
        counters[zone] = counters.get(zone, 0) + 1
        result.append({
            "code": f"SHELF-{zone}-{counters[zone]:03d}",
            "zone": zone,
            "x": cell.x,
            "y": cell.y,
            "type": cell.type,
            "capacity": cell.capacity,
        })
    return result
