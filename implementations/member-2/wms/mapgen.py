"""固定地图生成器。

地图是确定性的：由本模块的规则生成，不含随机数，任何机器上结果一致。
生成的 JSON 会提交到 `fixtures/maps/`，供前端、测试和演示共同使用。

主地图 warehouse-24 的结构：

```text
y=0    充电桩 ×4 角落，拣选/放货工位，横向主通道
y=1..5   A 区货架带（窄巷 x=4,7,10,13,16,19）
y=6    横向通道
y=7..11  B 区货架带
y=12   横向通道
y=13..17 C 区货架带
y=18   横向通道
y=19..22 D 区货架带
y=23   横向通道
```

货架列块为 2 格宽（x=2,3 / 5,6 / 8,9 / 11,12 / 14,15 / 17,18 / 20,21），
块之间留 1 格宽窄巷，用于演示相向穿边与等待冲突。
"""

from __future__ import annotations

from . import config
from .mapdata import Cell, Edge, Station, WarehouseMap

WAREHOUSE_ID = "warehouse-24"
RING_ID = "ring-3"

CORRIDOR_ROWS = (0, 6, 12, 18, 23)
BANDS = ((1, 5, "A"), (7, 11, "B"), (13, 17, "C"), (19, 22, "D"))
SHELF_COLUMNS = ((2, 3), (5, 6), (8, 9), (11, 12), (14, 15), (17, 18), (20, 21))

PICK_CELLS = ((4, 6, "A"), (7, 12, "B"), (10, 18, "C"), (13, 23, "D"))
"""拣选点：每个区域一个主拣选点，坐标必须落在可通行格上。"""

EXTRA_PICK_CELLS = ((2, 4, "A"), (2, 10, "B"), (2, 16, "C"), (2, 22, "D"))
"""备用拣选点：降低高峰期排队（同一区域第二个工位）。"""

DROP_CELLS = ((12, 0), (11, 23), (22, 6), (1, 12))
CHARGER_CELLS = ((0, 0), (23, 0), (0, 23), (23, 23))
CLOSABLE_CELLS = ("2,6", "23,12", "9,18")

INITIAL_AGVS = (
    # 初始位置放在"宽行"（货架带的首尾行）上，避免一上来就堵住通道
    (2, 1), (4, 1), (6, 1), (8, 1), (10, 1), (12, 1), (14, 1), (16, 1), (18, 1), (20, 1),
    (1, 5), (3, 5), (5, 5), (7, 5), (9, 5), (11, 5), (13, 5), (15, 5), (17, 5), (19, 5),
    (2, 11), (4, 11), (6, 11), (8, 11), (10, 11), (12, 11), (14, 11), (16, 11), (18, 11), (20, 11),
    (1, 17), (3, 17), (5, 17), (7, 17), (9, 17), (11, 17), (13, 17), (15, 17), (17, 17), (19, 17),
    (2, 22), (4, 22), (6, 22), (8, 22), (10, 22), (12, 22), (14, 22), (16, 22), (18, 22), (20, 22),
)

PARKING_CELLS = tuple(
    (x, y)
    for bx in (0, 22)
    for by in (0, 22)
    for x in range(bx, bx + 2)
    for y in range(by, by + 2)
)


def _cell_type(x: int, y: int) -> tuple[str, str]:
    """返回 (类型, 区域)。"""
    for px, py, zone in PICK_CELLS + EXTRA_PICK_CELLS:
        if (x, y) == (px, py):
            return config.CELL_PICK, zone
    for px, py in DROP_CELLS:
        if (x, y) == (px, py):
            return config.CELL_DROP, "OUT"
    for px, py in CHARGER_CELLS:
        if (x, y) == (px, py):
            return config.CELL_CHARGER, "CHG"
    for y0, y1, zone in BANDS:
        if y0 <= y <= y1:
            rows = list(range(y0, y1 + 1))
            shelf_rows = rows[1:-1]
            if y in shelf_rows:
                for x0, x1 in SHELF_COLUMNS:
                    if x0 <= x <= x1:
                        return config.CELL_SHELF, zone
    return config.CELL_FLOOR, ""


def build_warehouse_map() -> WarehouseMap:
    """生成 24x24 主仓库地图。"""
    cells: dict[str, Cell] = {}
    for y in range(config.MAP_HEIGHT):
        for x in range(config.MAP_WIDTH):
            ctype, zone = _cell_type(x, y)
            cid = f"{x},{y}"
            cells[cid] = Cell(
                id=cid, x=x, y=y, type=ctype,
                passable=ctype in config.PASSABLE_TYPES,
                capacity=2 if ctype in (config.CELL_PICK, config.CELL_DROP) else 1,
                zone=zone,
            )

    edges: list[Edge] = []
    for y in range(config.MAP_HEIGHT):
        for x in range(config.MAP_WIDTH):
            if not cells[f"{x},{y}"].passable:
                continue
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if nx >= config.MAP_WIDTH or ny >= config.MAP_HEIGHT:
                    continue
                if cells[f"{nx},{ny}"].passable:
                    edges.append(Edge(f"{x},{y}", f"{nx},{ny}", True))

    stations: list[Station] = []
    zone_counter: dict[str, int] = {}
    for x, y, zone in PICK_CELLS + EXTRA_PICK_CELLS:
        zone_counter[zone] = zone_counter.get(zone, 0) + 1
        stations.append(Station(config.CELL_PICK, f"{x},{y}", 2, code=f"PICK-{zone}{zone_counter[zone]}"))
    for index, (x, y) in enumerate(DROP_CELLS, start=1):
        stations.append(Station(config.CELL_DROP, f"{x},{y}", 3, code=f"DROP-{index}"))
    for index, (x, y) in enumerate(CHARGER_CELLS, start=1):
        stations.append(Station(config.CELL_CHARGER, f"{x},{y}", 1, code=f"CHG-{index}"))

    wm = WarehouseMap(
        map_id=WAREHOUSE_ID, version="map-1", width=config.MAP_WIDTH, height=config.MAP_HEIGHT,
        tick_seconds=config.TICK_SECONDS, cells=cells, edges=edges, stations=stations,
        parking_cells=[f"{x},{y}" for x, y in PARKING_CELLS],
    )
    wm.build_adjacency()
    return wm


def initial_agv_positions(count: int = 30) -> list[tuple[int, int]]:
    return list(INITIAL_AGVS[:count])


# ---------------------------------------------------------------- 三角环地图

RING_TRIANGLE = {"T1": (7, 3), "T2": (8, 3), "T3": (8, 4)}
RING_BAY = (9, 4)
RING_POCKET = (6, 3)
"""第二个退避口袋：环路外侧的死胡同，回退车辆停这里不挡通道。"""
RING_ROOM_CELLS = [(x, y) for y in (6, 7) for x in range(5, 10)] + [(9, 5)]
RING_BLOCKED = ((7, 2), (7, 4), (8, 2), (9, 3), (8, 5))


def build_ring_map() -> WarehouseMap:
    """生成用于演示三车环形等待的小地图。

    结构：T1-T2-T3 组成三角形环路（T3-T1 为显式对角边），T3 旁边有一个
    安全回退格 (9,4)，环路与外部只通过该格连通。适合演示等待图成环与回退恢复。
    """
    width, height = 10, 8
    passable = set(RING_ROOM_CELLS)
    passable.add(RING_BAY)
    passable.add(RING_POCKET)
    for name, (x, y) in RING_TRIANGLE.items():
        passable.add((x, y))

    cells: dict[str, Cell] = {}
    for y in range(height):
        for x in range(width):
            cid = f"{x},{y}"
            if (x, y) in passable:
                cells[cid] = Cell(id=cid, x=x, y=y, type=config.CELL_FLOOR, passable=True, capacity=1)
            else:
                cells[cid] = Cell(id=cid, x=x, y=y, type=config.CELL_BLOCKED, passable=False, capacity=1)

    stations = [
        Station(config.CELL_PICK, "7,3", 1, code="PICK-R1"),
        Station(config.CELL_PICK, "8,3", 1, code="PICK-R2"),
        Station(config.CELL_PICK, "8,4", 1, code="PICK-R3"),
        Station(config.CELL_DROP, "5,6", 2, code="DROP-R1"),
        Station(config.CELL_CHARGER, "9,7", 1, code="CHG-R1"),
    ]
    # 站点格点的类型要同步到格点上，前端与库位生成都依赖格点类型
    station_type = {station.cell_id: station.type for station in stations}
    for cid, station_kind in station_type.items():
        cell = cells[cid]
        cells[cid] = Cell(id=cell.id, x=cell.x, y=cell.y, type=station_kind, passable=True,
                          capacity=next(s.capacity for s in stations if s.cell_id == cid), zone="R")

    edges: list[Edge] = []
    for y in range(height):
        for x in range(width):
            if not cells[f"{x},{y}"].passable:
                continue
            for dx, dy in ((1, 0), (0, 1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height and cells[f"{nx},{ny}"].passable:
                    edges.append(Edge(f"{x},{y}", f"{nx},{ny}", True))

    # 三角形第三条边：T3 <-> T1（对角显式边），地图支持任意有向/双向边
    edges.append(Edge("8,4", "7,3", True))

    wm = WarehouseMap(
        map_id=RING_ID, version="map-1", width=width, height=height,
        tick_seconds=config.TICK_SECONDS, cells=cells, edges=edges, stations=stations,
        parking_cells=[f"{x},{y}" for x, y in RING_ROOM_CELLS if (x, y) not in ((5, 6), (9, 7))],
    )
    wm.build_adjacency()
    return wm


def build_map(map_id: str) -> WarehouseMap:
    if map_id == RING_ID:
        return build_ring_map()
    return build_warehouse_map()
