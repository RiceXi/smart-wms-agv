from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.database import backup_database, restore_database, validate_database
from wms_agv.service import WmsService


class DatabaseOperationsTests(unittest.TestCase):
    def test_backup_restore_and_verify_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.sqlite"
            backup = root / "backup.sqlite"
            restored = root / "restored.sqlite"

            service = WmsService(source, agv_count=10)
            sku_code = next(iter(service.skus.values()))["code"]
            service.stop()

            self.assertEqual(["entities", "state"], validate_database(source))
            backup_database(source, backup)
            restore_database(backup, restored, force=True)
            self.assertEqual(["entities", "state"], validate_database(restored))

            restored_service = WmsService(restored, seed_demo=False, agv_count=10)
            try:
                self.assertIn(sku_code, {item["code"] for item in restored_service.skus.values()})
            finally:
                restored_service.stop()


if __name__ == "__main__":
    unittest.main()
