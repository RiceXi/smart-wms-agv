import unittest

from wms_agv.pathfinding import (
    GridMap,
    ReservationTable,
    find_cycles,
    find_leg,
)


class PathfindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.grid = GridMap(6, 5)
        self.reservations = ReservationTable()

    def test_path_avoids_obstacle(self) -> None:
        self.grid.obstacles.add((2, 2))
        path = find_leg(
            self.grid, self.reservations, "agv-a", (1, 2), (3, 2), start_tick=0
        )
        self.assertIsNotNone(path)
        self.assertEqual((path[-1]["x"], path[-1]["y"]), (3, 2))
        self.assertNotIn((2, 2), [(step["x"], step["y"]) for step in path])

    def test_vertex_reservation_blocks_same_tick(self) -> None:
        self.assertTrue(self.reservations.add_vertex((2, 1), 1, "agv-a"))
        path = find_leg(
            self.grid, self.reservations, "agv-b", (1, 1), (3, 1), start_tick=0
        )
        self.assertIsNotNone(path)
        self.assertNotEqual((2, 1, 1), (path[1]["x"], path[1]["y"], path[1]["tick"]))

    def test_opposite_edge_is_blocked(self) -> None:
        self.assertTrue(self.reservations.add_edge((2, 1), (1, 1), 0, "agv-a"))
        path = find_leg(
            self.grid, self.reservations, "agv-b", (1, 1), (2, 1), start_tick=0
        )
        self.assertIsNotNone(path)
        self.assertEqual("wait", path[1]["action"])

    def test_head_to_tail_is_allowed(self) -> None:
        self.assertTrue(self.reservations.add_vertex((2, 1), 1, "agv-a"))
        path = find_leg(
            self.grid, self.reservations, "agv-b", (1, 1), (3, 1), start_tick=0
        )
        self.assertIsNotNone(path)
        self.assertTrue(
            any(
                step["tick"] == 2 and (step["x"], step["y"]) == (2, 1)
                for step in path
            )
        )

    def test_detects_wait_cycle(self) -> None:
        graph = {"a": "b", "b": "c", "c": "a", "d": None}
        self.assertEqual([["a", "b", "c"]], find_cycles(graph))

    def test_wait_step_records_blocking_agv(self) -> None:
        self.grid = GridMap(5, 3)
        for point in ((1, 1), (2, 0), (3, 1), (2, 2)):
            self.reservations.add_vertex(point, 1, "agv-block")
        path = find_leg(
            self.grid,
            self.reservations,
            "agv-self",
            (2, 1),
            (4, 1),
            start_tick=0,
        )
        self.assertIsNotNone(path)
        wait = next(step for step in path if step["action"] == "wait")
        self.assertEqual("agv-block", wait["blocked_by"])


if __name__ == "__main__":
    unittest.main()
