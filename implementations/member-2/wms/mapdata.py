"""地图模型、地图版本与封路管理。

地图是二维离散网格：

- 格点有稳定 id `x,y`、坐标、类型和可通行属性。
- 边按方向建模，`bidirectional=false` 表示单向边。
- 地图版本不可原地修改：封路或解封都会生成新版本，旧版本保留用于回放。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .errors import ValidationError
from .util import cell_id, new_id, now_iso


@dataclass(frozen=True)
class Cell:
    id: str
    x: int
    y: int
    type: str
    passable: bool
    capacity: int = 1
    zone: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "type": self.type,
            "passable": self.passable,
            "capacity": self.capacity,
            "zone": self.zone,
        }


@dataclass(frozen=True)
class Edge:
    from_cell: str
    to_cell: str
    bidirectional: bool = True

    def to_dict(self) -> dict:
        return {"from": self.from_cell, "to": self.to_cell, "bidirectional": self.bidirectional}


@dataclass(frozen=True)
class Station:
    type: str
    cell_id: str
    capacity: int = 1
    code: str = ""

    def to_dict(self) -> dict:
        payload = {"type": self.type, "cellId": self.cell_id, "capacity": self.capacity}
        if self.code:
            payload["code"] = self.code
        return payload


@dataclass
class WarehouseMap:
    """某一具体版本的仓库地图。"""

    map_id: str
    version: str
    width: int
    height: int
    tick_seconds: int
    cells: dict[str, Cell]
    edges: list[Edge]
    stations: list[Station]
    adjacency: dict[str, list[str]] = field(default_factory=dict)
    parking_cells: list[str] = field(default_factory=list)
    """待命区格点：车辆空闲时停靠到这里，不占用通道。"""
    parent_version: str | None = None
    reason: str = ""

    # ------------------------------------------------------------ 构建
    def build_adjacency(self) -> None:
        """由边列表重建出边邻接表（有向：from -> to）。"""
        adjacency: dict[str, list[str]] = {cid: [] for cid in self.cells}
        for edge in self.edges:
            if edge.from_cell in adjacency and edge.to_cell in self.cells:
                adjacency[edge.from_cell].append(edge.to_cell)
            if edge.bidirectional and edge.to_cell in adjacency and edge.from_cell in self.cells:
                adjacency[edge.to_cell].append(edge.from_cell)
        for cid in adjacency:
            adjacency[cid].sort()
        self.adjacency = adjacency

    # ------------------------------------------------------------ 查询
    def cell(self, cid: str) -> Cell | None:
        return self.cells.get(cid)

    def cell_at(self, x: int, y: int) -> Cell | None:
        return self.cells.get(cell_id(x, y))

    def is_passable(self, cid: str) -> bool:
        cell = self.cells.get(cid)
        return bool(cell and cell.passable)

    def neighbors(self, cid: str) -> list[str]:
        return self.adjacency.get(cid, [])

    def passable_neighbors(self, cid: str) -> list[str]:
        return [n for n in self.neighbors(cid) if self.is_passable(n)]

    def stations_of(self, station_type: str) -> list[Station]:
        return [s for s in self.stations if s.type == station_type]

    def station_at(self, cid: str) -> Station | None:
        for station in self.stations:
            if station.cell_id == cid:
                return station
        return None

    def to_dict(self) -> dict:
        return {
            "mapId": self.map_id,
            "version": self.version,
            "width": self.width,
            "height": self.height,
            "tickSeconds": self.tick_seconds,
            "cells": [self.cells[cid].to_dict() for cid in sorted(self.cells, key=_cell_sort_key)],
            "edges": [edge.to_dict() for edge in self.edges],
            "stations": [s.to_dict() for s in self.stations],
            "parkingCells": list(self.parking_cells),
            "parentVersion": self.parent_version,
        }


def _cell_sort_key(cid: str):
    x, y = cid.split(",")
    return (int(y), int(x))


# ---------------------------------------------------------------- 解析与序列化


def map_from_dict(payload: dict) -> WarehouseMap:
    """从 fixture JSON 构造地图对象，并做一致性校验。"""
    for field_name in ("mapId", "version", "width", "height"):
        if field_name not in payload:
            raise ValidationError(f"地图 fixture 缺少字段 {field_name}", {"field": field_name})

    cells: dict[str, Cell] = {}
    for raw in payload.get("cells", []):
        cid = raw.get("id") or cell_id(int(raw["x"]), int(raw["y"]))
        ctype = raw.get("type", config.CELL_FLOOR)
        passable = raw.get("passable")
        if passable is None:
            passable = ctype in config.PASSABLE_TYPES
        cells[cid] = Cell(
            id=cid,
            x=int(raw["x"]),
            y=int(raw["y"]),
            type=ctype,
            passable=bool(passable),
            capacity=int(raw.get("capacity", 1)),
            zone=raw.get("zone", ""),
        )

    edges: list[Edge] = []
    for raw in payload.get("edges", []):
        edges.append(
            Edge(
                from_cell=raw["from"],
                to_cell=raw["to"],
                bidirectional=bool(raw.get("bidirectional", True)),
            )
        )

    stations = [
        Station(type=s["type"], cell_id=s["cellId"], capacity=int(s.get("capacity", 1)),
                code=str(s.get("code", "")))
        for s in payload.get("stations", [])
    ]

    wm = WarehouseMap(
        map_id=payload["mapId"],
        version=payload["version"],
        width=int(payload["width"]),
        height=int(payload["height"]),
        tick_seconds=int(payload.get("tickSeconds", config.TICK_SECONDS)),
        cells=cells,
        edges=edges,
        stations=stations,
        parking_cells=[str(c) for c in payload.get("parkingCells", [])],
        parent_version=payload.get("parentVersion"),
    )
    wm.build_adjacency()
    return wm


def map_to_dict(wm: WarehouseMap) -> dict:
    return wm.to_dict()


def load_map_file(path: Path | str) -> WarehouseMap:
    path = Path(path)
    if not path.exists():
        raise ValidationError(f"地图文件不存在：{path}", {"path": str(path)})
    payload = json.loads(path.read_text(encoding="utf-8"))
    return map_from_dict(payload)


def dump_map_file(wm: WarehouseMap, path: Path | str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(wm.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 地图版本管理


class MapStore:
    """管理当前地图版本与封路记录。

    规则：地图版本不可原地修改。任何封路/解封都会基于当前版本生成新版本，
    并把新版本的格点与边落库，旧版本保留以便回放。
    """

    def __init__(self, db, base_map: WarehouseMap):
        self.db = db
        self.base = base_map
        self.current = base_map
        self._roadblock_seq = 0

    # ------------------------------------------------------------ 持久化
    def persist_version(self, wm: WarehouseMap, reason: str = "") -> None:
        ts = now_iso()
        self.db.execute(
            """INSERT OR REPLACE INTO map_versions
               (id, map_id, version, width, height, tick_seconds, parent_version, reason, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (f"{wm.map_id}:{wm.version}", wm.map_id, wm.version, wm.width, wm.height,
             wm.tick_seconds, wm.parent_version, reason, ts),
        )
        self.db.execute("DELETE FROM map_cells WHERE map_version = ?", (wm.version,))
        self.db.execute("DELETE FROM map_edges WHERE map_version = ?", (wm.version,))
        self.db.execute("DELETE FROM map_stations WHERE map_version = ?", (wm.version,))
        self.db.executemany(
            "INSERT INTO map_cells (map_version, cell_id, x, y, type, passable, capacity, zone) VALUES (?,?,?,?,?,?,?,?)",
            [(wm.version, c.id, c.x, c.y, c.type, 1 if c.passable else 0, c.capacity, c.zone) for c in wm.cells.values()],
        )
        self.db.executemany(
            "INSERT INTO map_edges (map_version, from_cell, to_cell, bidirectional) VALUES (?,?,?,?)",
            [(wm.version, e.from_cell, e.to_cell, 1 if e.bidirectional else 0) for e in wm.edges],
        )
        self.db.executemany(
            "INSERT INTO map_stations (map_version, type, cell_id, capacity) VALUES (?,?,?,?)",
            [(wm.version, s.type, s.cell_id, s.capacity) for s in wm.stations],
        )
        self.db.commit()

    def latest_version(self) -> str | None:
        """最近一次落库的地图版本号。"""
        row = self.db.query_one(
            "SELECT version FROM map_versions ORDER BY created_at DESC, rowid DESC LIMIT 1")
        return row["version"] if row else None

    def restore_current(self) -> bool:
        """恢复最近一次落库的地图版本。

        重启后必须回到"封路前"的地图状态，否则封路记录会丢失，
        正在运行的 AGV 会走回已经封闭的巷道。
        """
        version = self.latest_version()
        if not version:
            return False
        wm = self.load_version(version)
        if wm is None:
            return False
        # 待命区等运行期字段不在地图版本表里，从基础地图继承
        wm.parking_cells = list(self.base.parking_cells)
        self.current = wm
        return True

    def load_version(self, version: str) -> WarehouseMap | None:
        """从数据库还原某个历史地图版本。"""
        row = self.db.query_one("SELECT * FROM map_versions WHERE version = ?", (version,))
        if not row:
            return None
        cells = {}
        for c in self.db.query("SELECT * FROM map_cells WHERE map_version = ?", (version,)):
            cells[c["cell_id"]] = Cell(
                id=c["cell_id"], x=c["x"], y=c["y"], type=c["type"],
                passable=bool(c["passable"]), capacity=c["capacity"], zone=c["zone"],
            )
        edges = [
            Edge(e["from_cell"], e["to_cell"], bool(e["bidirectional"]))
            for e in self.db.query("SELECT * FROM map_edges WHERE map_version = ?", (version,))
        ]
        stations = [
            Station(s["type"], s["cell_id"], s["capacity"])
            for s in self.db.query("SELECT * FROM map_stations WHERE map_version = ?", (version,))
        ]
        wm = WarehouseMap(
            map_id=row["map_id"], version=row["version"], width=row["width"], height=row["height"],
            tick_seconds=row["tick_seconds"], cells=cells, edges=edges, stations=stations,
            parent_version=row["parent_version"],
        )
        wm.build_adjacency()
        return wm

    # ------------------------------------------------------------ 封路
    def add_roadblock(self, cells: list[str], reason: str = "") -> tuple[WarehouseMap, str]:
        """封闭若干格点，返回 (新版本地图, 封路记录 id)。"""
        if not cells:
            raise ValidationError("封路至少需要一个格点", {"cells": cells})
        new_cells = dict(self.current.cells)
        new_stations = list(self.current.stations)
        for cid in cells:
            cell = new_cells.get(cid)
            if cell is None:
                raise ValidationError(f"格点不存在：{cid}", {"cell": cid})
            new_cells[cid] = Cell(
                id=cell.id, x=cell.x, y=cell.y, type=config.CELL_BLOCKED, passable=False,
                capacity=cell.capacity, zone=cell.zone,
            )
            new_stations = [s for s in new_stations if s.cell_id != cid]

        keep_edges = [
            e for e in self.current.edges
            if e.from_cell not in cells and e.to_cell not in cells
        ]
        new_map = self._derive(new_cells, keep_edges, new_stations, reason or "临时封路")
        self.current = new_map

        record_ids = []
        ts = now_iso()
        for cid in cells:
            rid = new_id("rb")
            self.db.execute(
                """INSERT INTO roadblocks (id, map_id, cell_id, reason, active, created_at)
                   VALUES (?,?,?,?,1,?)""",
                (rid, new_map.map_id, cid, reason, ts),
            )
            record_ids.append(rid)
        self.db.commit()
        return new_map, record_ids[0]

    def remove_roadblock(self, record_id: str) -> tuple[WarehouseMap, str]:
        """解除封路。"""
        row = self.db.query_one("SELECT * FROM roadblocks WHERE id = ? AND active = 1", (record_id,))
        if not row:
            raise ValidationError(f"封路记录不存在或已解除：{record_id}", {"id": record_id})
        cid = row["cell_id"]
        new_cells = dict(self.current.cells)
        original = self.base.cells.get(cid)
        if original is not None:
            new_cells[cid] = original
        new_edges = list(self.current.edges)
        for edge in list(new_edges):
            if edge.from_cell == cid or edge.to_cell == cid:
                new_edges.remove(edge)
        new_edges.extend(self._edges_for(cid))
        new_stations = list(self.current.stations)
        if original is not None and original.type in config.STATION_TYPES:
            new_stations.append(Station(original.type, cid, original.capacity, original.code))
        new_map = self._derive(new_cells, new_edges, new_stations, "解除封路")
        self.current = new_map
        self.db.execute(
            "UPDATE roadblocks SET active = 0, removed_at = ? WHERE id = ?", (now_iso(), record_id)
        )
        self.db.commit()
        return new_map, cid

    def active_roadblocks(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM roadblocks WHERE active = 1 ORDER BY created_at, id")
        return [dict(row) for row in rows]

    # ------------------------------------------------------------ 内部
    def _edges_for(self, cid: str) -> list[Edge]:
        """基础地图中与该格点相连的边（用于解封后恢复）。"""
        return [e for e in self.base.edges if e.from_cell == cid or e.to_cell == cid]

    def _derive(self, cells: dict[str, Cell], edges: list[Edge], stations: list[Station], reason: str) -> WarehouseMap:
        version = self._next_version()
        wm = WarehouseMap(
            map_id=self.base.map_id,
            version=version,
            width=self.base.width,
            height=self.base.height,
            tick_seconds=self.base.tick_seconds,
            cells=cells,
            edges=edges,
            stations=stations,
            parent_version=self.current.version,
            reason=reason,
        )
        wm.build_adjacency()
        self.persist_version(wm, reason)
        return wm

    def _next_version(self) -> str:
        self._roadblock_seq += 1
        return f"{self.base.version}-r{self._roadblock_seq}"
