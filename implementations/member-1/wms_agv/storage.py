from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class Repository:
    """Small JSON-entity store backed by SQLite for local persistence."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
                kind TEXT NOT NULL,
                id TEXT NOT NULL,
                data TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (kind, id)
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def load_entities(self, kind: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT id, data FROM entities WHERE kind = ? ORDER BY id", (kind,)
            ).fetchall()
        return {row["id"]: json.loads(row["data"]) for row in rows}

    def upsert_entity(self, kind: str, entity: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO entities(kind, id, data, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(kind, id) DO UPDATE SET
                    data = excluded.data,
                    updated_at = excluded.updated_at
                """,
                (
                    kind,
                    entity["id"],
                    json.dumps(entity, ensure_ascii=False, separators=(",", ":")),
                    entity.get("updated_at", entity.get("created_at", "")),
                ),
            )
            self._connection.commit()

    def delete_entity(self, kind: str, entity_id: str) -> None:
        with self._lock:
            self._connection.execute(
                "DELETE FROM entities WHERE kind = ? AND id = ?", (kind, entity_id)
            )
            self._connection.commit()

    def get_state(self, key: str, default: Any = None) -> Any:
        with self._lock:
            row = self._connection.execute(
                "SELECT value FROM state WHERE key = ?", (key,)
            ).fetchone()
        return default if row is None else json.loads(row["value"])

    def set_state(self, key: str, value: Any) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO state(key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value, ensure_ascii=False, separators=(",", ":"))),
            )
            self._connection.commit()

    def close(self) -> None:
        with self._lock:
            self._connection.close()
