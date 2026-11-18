"""测试公共工具：临时数据目录、App 工厂与常用断言辅助。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from wms.app import App  # noqa: E402


class AppTestCase(unittest.TestCase):
    """每个用例一个独立的临时数据目录，避免互相污染。"""

    scenario = "single-agv"
    seed = 20261118

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="wms-test-"))
        self.app = self.make_app(self.scenario, self.seed)

    def tearDown(self) -> None:
        try:
            self.app.close()
        except Exception:  # pragma: no cover - 清理阶段不掩盖真实失败
            pass
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def make_app(self, scenario: str | None = None, seed: int | None = None) -> App:
        # 每个 App 使用独立子目录，避免多个实例争用同一个 SQLite 文件
        index = getattr(self, "_app_index", 0) + 1
        self._app_index = index
        data_dir = self.tmp_dir / f"app-{index}"
        data_dir.mkdir(parents=True, exist_ok=True)
        app = App(data_dir=data_dir)
        if scenario is not None:
            app.reset(scenario or self.scenario, seed if seed is not None else self.seed)
        return app

    # ------------------------------------------------------------ 断言辅助
    def run_ticks(self, app: App, ticks: int) -> App:
        for _ in range(ticks):
            app.sim.step_once()
        return app

    def event_types(self, app: App) -> list[str]:
        return [row["type"] for row in app.db.query("SELECT type FROM events ORDER BY seq")]

    def has_event(self, app: App, event_type: str) -> bool:
        row = app.db.query_one("SELECT COUNT(*) AS c FROM events WHERE type = ?", (event_type,))
        return bool(row and row["c"])

    def task_statuses(self, app: App) -> dict[str, int]:
        rows = app.db.query("SELECT status, COUNT(*) AS c FROM tasks GROUP BY status")
        return {row["status"]: row["c"] for row in rows}

    def order_statuses(self, app: App) -> dict[str, int]:
        rows = app.db.query("SELECT status, COUNT(*) AS c FROM orders GROUP BY status")
        return {row["status"]: row["c"] for row in rows}

    def reserved_qty(self, app: App, sku_code: str = "SKU-001") -> int:
        row = app.db.query_one(
            """SELECT COALESCE(SUM(i.reserved_qty), 0) AS r FROM inventory i
               JOIN sku s ON s.id = i.sku_id WHERE s.code = ?""",
            (sku_code,),
        )
        return int(row["r"])
