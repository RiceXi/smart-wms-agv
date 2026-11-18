"""SQLite 持久化层。

设计要点：

- 业务主数据（SKU / 库位 / 库存 / 订单 / 任务）落库，程序重启不丢。
- 运行态（AGV 状态、预约、仿真时钟）每 tick 落库，重启后可从快照恢复。
- 地图版本不可原地修改：每次封路/解封都写入新的 `map_versions` 及其格点与边，旧版本保留用于回放。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sku (
    id          TEXT PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    spec        TEXT,
    batch_rule  TEXT NOT NULL DEFAULT 'none',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS location (
    id          TEXT PRIMARY KEY,
    code        TEXT NOT NULL UNIQUE,
    zone        TEXT NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    type        TEXT NOT NULL,
    capacity    INTEGER NOT NULL,
    map_version TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    id           TEXT PRIMARY KEY,
    sku_id       TEXT NOT NULL REFERENCES sku(id),
    location_id  TEXT NOT NULL REFERENCES location(id),
    qty          INTEGER NOT NULL DEFAULT 0,
    reserved_qty INTEGER NOT NULL DEFAULT 0,
    batch        TEXT NOT NULL DEFAULT '',
    expire_at    TEXT,
    version      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    UNIQUE (sku_id, location_id, batch)
);

CREATE TABLE IF NOT EXISTS inventory_txn (
    id            TEXT PRIMARY KEY,
    sku_id        TEXT NOT NULL,
    location_id   TEXT NOT NULL,
    delta         INTEGER NOT NULL,
    reserved_delta INTEGER NOT NULL DEFAULT 0,
    qty_after     INTEGER NOT NULL,
    txn_type      TEXT NOT NULL,
    ref_id        TEXT,
    tick          INTEGER NOT NULL DEFAULT 0,
    occurred_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id               TEXT PRIMARY KEY,
    code             TEXT NOT NULL UNIQUE,
    type             TEXT NOT NULL DEFAULT 'outbound',
    status           TEXT NOT NULL,
    priority         INTEGER NOT NULL,
    requester        TEXT NOT NULL,
    drop_location_id TEXT REFERENCES location(id),
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    version          INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS order_lines (
    id          TEXT PRIMARY KEY,
    order_id    TEXT NOT NULL REFERENCES orders(id),
    sku_id      TEXT NOT NULL REFERENCES sku(id),
    qty         INTEGER NOT NULL,
    batch       TEXT NOT NULL DEFAULT '',
    location_id TEXT REFERENCES location(id),
    pickup_location_id TEXT REFERENCES location(id),
    assigned_agv_id TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id                 TEXT PRIMARY KEY,
    order_id           TEXT NOT NULL REFERENCES orders(id),
    order_line_id      TEXT REFERENCES order_lines(id),
    type               TEXT NOT NULL DEFAULT 'pick_and_drop',
    priority           INTEGER NOT NULL,
    status             TEXT NOT NULL,
    pickup_location_id TEXT NOT NULL REFERENCES location(id),
    drop_location_id   TEXT NOT NULL REFERENCES location(id),
    assigned_agv_id    TEXT,
    pinned_agv_id      TEXT,
    plan_version       INTEGER NOT NULL DEFAULT 0,
    retry_count        INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    version            INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS order_allocations (
    id           TEXT PRIMARY KEY,
    order_line_id TEXT NOT NULL REFERENCES order_lines(id),
    inventory_id TEXT NOT NULL REFERENCES inventory(id),
    qty          INTEGER NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_events (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    type         TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT,
    agv_id       TEXT,
    plan_version INTEGER,
    payload      TEXT NOT NULL DEFAULT '{}',
    occurred_at  TEXT NOT NULL,
    tick         INTEGER NOT NULL DEFAULT 0,
    seq          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agvs (
    id            TEXT PRIMARY KEY,
    map_id        TEXT NOT NULL,
    status        TEXT NOT NULL,
    x             INTEGER NOT NULL,
    y             INTEGER NOT NULL,
    battery       REAL NOT NULL,
    active_task_id TEXT REFERENCES tasks(id),
    payload_sku   TEXT,
    last_tick     INTEGER NOT NULL DEFAULT 0,
    fault_code    TEXT,
    version       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agv_trail (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    agv_id TEXT NOT NULL,
    tick   INTEGER NOT NULL,
    x      INTEGER NOT NULL,
    y      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS map_versions (
    id            TEXT PRIMARY KEY,
    map_id        TEXT NOT NULL,
    version       TEXT NOT NULL,
    width         INTEGER NOT NULL,
    height        INTEGER NOT NULL,
    tick_seconds  INTEGER NOT NULL DEFAULT 1,
    parent_version TEXT,
    reason        TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (map_id, version)
);

CREATE TABLE IF NOT EXISTS map_cells (
    map_version TEXT NOT NULL,
    cell_id     TEXT NOT NULL,
    x           INTEGER NOT NULL,
    y           INTEGER NOT NULL,
    type        TEXT NOT NULL,
    passable    INTEGER NOT NULL,
    capacity    INTEGER NOT NULL DEFAULT 1,
    zone        TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (map_version, cell_id)
);

CREATE TABLE IF NOT EXISTS map_edges (
    map_version  TEXT NOT NULL,
    from_cell    TEXT NOT NULL,
    to_cell      TEXT NOT NULL,
    bidirectional INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (map_version, from_cell, to_cell)
);

CREATE TABLE IF NOT EXISTS map_stations (
    map_version TEXT NOT NULL,
    type        TEXT NOT NULL,
    cell_id     TEXT NOT NULL,
    capacity    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (map_version, type, cell_id)
);

CREATE TABLE IF NOT EXISTS roadblocks (
    id         TEXT PRIMARY KEY,
    map_id     TEXT NOT NULL,
    cell_id    TEXT NOT NULL,
    reason     TEXT NOT NULL DEFAULT '',
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    removed_at TEXT
);

CREATE TABLE IF NOT EXISTS reservations (
    key         TEXT PRIMARY KEY,
    map_id      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    cell_from   TEXT,
    cell_to     TEXT,
    tick        INTEGER NOT NULL,
    agv_id      TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    created_tick INTEGER NOT NULL,
    expires_tick INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id           TEXT PRIMARY KEY,
    type         TEXT NOT NULL,
    tick         INTEGER NOT NULL,
    aggregate_id TEXT,
    payload      TEXT NOT NULL DEFAULT '{}',
    occurred_at  TEXT NOT NULL,
    seq          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sim_state (
    id             INTEGER PRIMARY KEY CHECK (id = 1),
    tick           INTEGER NOT NULL DEFAULT 0,
    running        INTEGER NOT NULL DEFAULT 0,
    speed          INTEGER NOT NULL DEFAULT 1,
    seed           INTEGER NOT NULL DEFAULT 20261118,
    scenario_id    TEXT NOT NULL DEFAULT 'single-agv',
    map_version    TEXT NOT NULL DEFAULT 'map-1',
    last_event_seq INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_tick ON events(tick);
CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_agv ON tasks(assigned_agv_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_res_agv ON reservations(agv_id);
CREATE INDEX IF NOT EXISTS idx_trail_agv ON agv_trail(agv_id);
"""


def default_data_dir() -> Path:
    """数据目录，可用环境变量 `WMS_DATA_DIR` 覆盖（测试用临时目录）。"""
    env = os.environ.get("WMS_DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent / "data").resolve()


class Database:
    """轻量 SQLite 封装：单连接 + 事务辅助。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        # 另一个进程（如正在运行的 serve）占用同一个库时，等待而不是立刻报"database is locked"
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------ 基础操作
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, rows: list[tuple]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, rows)

    def query(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, params).fetchone()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------ 维护
    def reset_all(self) -> None:
        """清空全部业务表，用于场景重置。"""
        tables = [
            "sku", "location", "inventory", "inventory_txn", "orders", "order_lines",
            "tasks", "task_events", "agvs", "agv_trail", "map_versions", "map_cells",
            "map_edges", "map_stations", "roadblocks", "reservations", "events",
            "sim_state", "meta",
        ]
        self.conn.commit()
        # 整库重置时先关闭外键约束，避免删除顺序触发约束错误
        self.conn.execute("PRAGMA foreign_keys=OFF")
        for table in tables:
            self.conn.execute(f"DELETE FROM {table}")
        self.conn.commit()
        self.conn.execute("PRAGMA foreign_keys=ON")

    def drop_all(self) -> None:
        """删除所有表，用于测试重建。"""
        rows = self.query("SELECT name FROM sqlite_master WHERE type='table'")
        for row in rows:
            self.conn.execute(f"DROP TABLE IF EXISTS {row['name']}")
        self.conn.commit()
