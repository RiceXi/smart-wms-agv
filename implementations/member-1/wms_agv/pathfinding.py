from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Iterable


Point = tuple[int, int]


@dataclass
class GridMap:
    width: int
    height: int
    obstacles: set[Point] = field(default_factory=set)
    blocked: set[Point] = field(default_factory=set)

    def passable(self, point: Point) -> bool:
        x, y = point
        return (
            0 <= x < self.width
            and 0 <= y < self.height
            and point not in self.obstacles
            and point not in self.blocked
        )


class ReservationTable:
    """Reserves both vertices and directed edges in simulation time."""

    def __init__(self) -> None:
        self.vertices: dict[tuple[int, int, int], str] = {}
        self.edges: dict[tuple[int, int, int, int, int], str] = {}

    def add_vertex(self, point: Point, tick: int, owner: str) -> bool:
        key = (point[0], point[1], tick)
        current = self.vertices.get(key)
        if current is not None and current != owner:
            return False
        self.vertices[key] = owner
        return True

    def add_edge(self, start: Point, end: Point, tick: int, owner: str) -> bool:
        key = (start[0], start[1], end[0], end[1], tick)
        reverse = (end[0], end[1], start[0], start[1], tick)
        current = self.edges.get(key)
        opposite = self.edges.get(reverse)
        if current is not None and current != owner:
            return False
        if opposite is not None and opposite != owner:
            return False
        self.edges[key] = owner
        return True

    def vertex_free(self, point: Point, tick: int, owner: str) -> bool:
        current = self.vertices.get((point[0], point[1], tick))
        return current is None or current == owner

    def edge_free(self, start: Point, end: Point, tick: int, owner: str) -> bool:
        reverse = (end[0], end[1], start[0], start[1], tick)
        current = self.edges.get((start[0], start[1], end[0], end[1], tick))
        opposite = self.edges.get(reverse)
        return (current is None or current == owner) and (
            opposite is None or opposite == owner
        )

    def vertex_owner(self, point: Point, tick: int) -> str | None:
        return self.vertices.get((point[0], point[1], tick))

    def edge_owner(self, start: Point, end: Point, tick: int) -> str | None:
        forward = self.edges.get((start[0], start[1], end[0], end[1], tick))
        reverse = self.edges.get((end[0], end[1], start[0], start[1], tick))
        return forward or reverse

    def reserve_plan(self, steps: list[dict], owner: str) -> bool:
        """Reserve a complete path atomically."""
        if not steps:
            return False
        new_vertices: list[tuple[int, int, int]] = []
        new_edges: list[tuple[int, int, int, int, int]] = []
        previous: dict | None = None

        for step in steps:
            point = (int(step["x"]), int(step["y"]))
            tick = int(step["tick"])
            vertex_key = (point[0], point[1], tick)
            existing = self.vertices.get(vertex_key)
            if existing is not None and existing != owner:
                return False

            if previous is not None and step["action"] == "move":
                prev_point = (int(previous["x"]), int(previous["y"]))
                edge_key = (
                    prev_point[0],
                    prev_point[1],
                    point[0],
                    point[1],
                    int(previous["tick"]),
                )
                reverse = (
                    point[0],
                    point[1],
                    prev_point[0],
                    prev_point[1],
                    int(previous["tick"]),
                )
                for key in (edge_key, reverse):
                    existing_edge = self.edges.get(key)
                    if existing_edge is not None and existing_edge != owner:
                        return False
                    new_edges.append(key)

            new_vertices.append(vertex_key)
            previous = step

        for key in new_vertices:
            self.vertices[key] = owner
        for key in new_edges:
            self.edges[key] = owner
        return True


def _manhattan(a: Point, b: Point) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def find_leg(
    grid: GridMap,
    reservations: ReservationTable,
    owner: str,
    start: Point,
    goal: Point,
    start_tick: int,
    max_ticks: int = 240,
    hold_ticks: int = 0,
) -> list[dict] | None:
    """Find a deterministic time-space A* path for one AGV leg."""
    if not grid.passable(start) or not grid.passable(goal):
        return None
    if start == goal:
        return [
            {"tick": start_tick, "x": start[0], "y": start[1], "action": "start"}
        ]

    counter = 0
    frontier: list[tuple[int, int, int, int, int, int]] = []
    heapq.heappush(
        frontier,
        (_manhattan(start, goal), 0, start_tick, counter, start[0], start[1]),
    )
    parents: dict[
        tuple[int, int, int],
        tuple[tuple[int, int, int] | None, str, str | None],
    ] = {
        (start[0], start[1], start_tick): (None, "start", None)
    }
    best_g: dict[tuple[int, int, int], int] = {(start[0], start[1], start_tick): 0}

    while frontier:
        _, g, tick, _, x, y = heapq.heappop(frontier)
        state = (x, y, tick)
        if g != best_g.get(state):
            continue
        hold_available = all(
            reservations.vertex_free(goal, tick + offset, owner)
            for offset in range(1, hold_ticks + 1)
        )
        if (x, y) == goal and hold_available:
            path: list[dict] = []
            cursor: tuple[int, int, int] | None = state
            while cursor is not None:
                px, py, pt = cursor
                previous, action, blocked_by = parents[cursor]
                step = {"tick": pt, "x": px, "y": py, "action": action}
                if blocked_by:
                    step["blocked_by"] = blocked_by
                path.append(step)
                cursor = previous
            path.reverse()
            return path
        if tick - start_tick >= max_ticks:
            continue

        next_tick = tick + 1
        candidates: list[tuple[Point, str]] = []
        blocked_owners: set[str] = set()
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            target = (x + dx, y + dy)
            blocker = reservations.vertex_owner(target, next_tick)
            if blocker is None or blocker == owner:
                blocker = reservations.edge_owner((x, y), target, tick)
            if blocker and blocker != owner:
                blocked_owners.add(blocker)
            candidates.append((target, "move"))
        candidates.append(((x, y), "wait"))

        for target, action in candidates:
            if not grid.passable(target):
                continue
            if not reservations.vertex_free(target, next_tick, owner):
                continue
            if action == "move":
                if not reservations.edge_free((x, y), target, tick, owner):
                    continue
            new_g = g + 1
            target_state = (target[0], target[1], next_tick)
            if new_g >= best_g.get(target_state, 10**9):
                continue
            best_g[target_state] = new_g
            parents[target_state] = (
                state,
                action,
                min(blocked_owners) if action == "wait" and blocked_owners else None,
            )
            counter += 1
            heapq.heappush(
                frontier,
                (
                    new_g + _manhattan(target, goal),
                    new_g,
                    next_tick,
                    counter,
                    target[0],
                    target[1],
                ),
            )
    return None


def find_cycles(wait_graph: dict[str, str | None]) -> list[list[str]]:
    """Return all distinct cycles in an AGV wait-for graph."""
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []
    cycles: list[list[str]] = []

    def visit(node: str) -> None:
        if node in visiting:
            index = stack.index(node)
            cycle = stack[index:]
            canonical = min(
                tuple(cycle[i:] + cycle[:i]) for i in range(len(cycle))
            )
            result = list(canonical)
            if result not in cycles:
                cycles.append(result)
            return
        if node in visited:
            return
        visiting.add(node)
        stack.append(node)
        target = wait_graph.get(node)
        if target and target in wait_graph:
            visit(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(wait_graph):
        visit(node)
    return cycles


def traffic_points(steps: Iterable[dict]) -> Iterable[Point]:
    for step in steps:
        if step["action"] == "move":
            yield (int(step["x"]), int(step["y"]))

