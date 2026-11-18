"""fixture 与代码同源：导出的 JSON 必须和 Python 定义完全一致。

防止"改了代码忘了重新导出 fixture"这种隐性漂移。
"""

from __future__ import annotations

import json
import unittest

from helpers import PROJECT_DIR
from wms.mapgen import build_map
from wms.scenarios import SCENARIOS, list_scenarios

FIXTURES_DIR = PROJECT_DIR / "fixtures"


class MapFixtureTests(unittest.TestCase):
    def test_map_fixtures_match_code(self):
        for map_id in ("warehouse-24", "ring-3"):
            path = FIXTURES_DIR / "maps" / f"{map_id}.json"
            self.assertTrue(path.exists(), f"缺少地图 fixture：{path}")
            exported = json.loads(path.read_text(encoding="utf-8"))
            expected = build_map(map_id).to_dict()
            self.assertEqual(exported, expected, f"{map_id} 的 fixture 与代码不一致，请重新执行 export-fixtures")

    def test_map_fixture_roundtrip(self):
        from wms.mapdata import map_from_dict

        for map_id in ("warehouse-24", "ring-3"):
            exported = json.loads((FIXTURES_DIR / "maps" / f"{map_id}.json").read_text(encoding="utf-8"))
            rebuilt = map_from_dict(exported)
            self.assertEqual(rebuilt.to_dict(), exported)


class ScenarioFixtureTests(unittest.TestCase):
    def test_scenario_fixtures_match_code(self):
        for scenario_id, scenario in SCENARIOS.items():
            path = FIXTURES_DIR / "scenarios" / f"{scenario_id}.json"
            self.assertTrue(path.exists(), f"缺少场景 fixture：{path}")
            exported = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(exported, scenario.to_dict(),
                             f"{scenario_id} 的 fixture 与代码不一致，请重新执行 export-fixtures")

    def test_scenario_index_matches(self):
        path = FIXTURES_DIR / "scenarios" / "index.json"
        self.assertTrue(path.exists())
        exported = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(exported, list_scenarios())

    def test_every_scenario_has_expectations(self):
        for scenario in SCENARIOS.values():
            self.assertTrue(scenario.expect, f"{scenario.id} 缺少预期结果声明")
            self.assertTrue(scenario.description)


if __name__ == "__main__":
    unittest.main()
