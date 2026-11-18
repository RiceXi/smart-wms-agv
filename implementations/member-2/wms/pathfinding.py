"""时空 A* 路径规划。

搜索状态是 `(x, y, t)`，动作包含上下左右移动与等待。启发函数用曼哈顿距离，
每步代价恒为 1 个 tick，因此 A* 返回的是最早到达 tick 的路径。

规划时同时检查：

- 格点预约 `(cell, tick)`：避免两车在同一 tick 占用同一格。
- 有向边预约 `(from, to, tick)` 与反向边：避免相向穿边。

规划结果同时给出 `wait_ticks`（冲突等待累计）和 `blocked_by`（首次被谁挡住），
后者用于构建等待图和死锁检测。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from . import config
from .mapdata import WarehouseMap
from .reservations import ReservationEntry, ReservationStore, edge_key, vertex_key


@dataclass
class PlanStep:
    tick: int
    cell: str
    x: int
    y: int
    action: str  # move | wait
    blocked_by: str | None = None

    def to_dict(self) -> dict:
        payload = {"tick": self.tick, "x": self.x, "y": self.y, "action": self.action}
        if self.blocked_by:
            payload["blockedBy"] = self.blocked_by
        return payload


@dataclass
class PathPlan:
    task_id: str
    agv_id: str
    map_version: str
    plan_version: int
    steps: list[PlanStep] = field(default_factory=list)
    edges: list[dict] = field(default_factory=list)
    estimated_ticks: int = 0
    wait_ticks: int = 0
    kind: str = "task"
    goal_cell: str = ""
    blocked_by: str | None = None

    def step_at(self, tick: int) -> PlanStep | None:
        index = tick - self.steps[0].tick if self.steps else -1
        if 0 <= index < len(self.steps):
            return self.steps[index]
        return None

    def reservations(self, map_id: str) -> list[ReservationEntry]:
        """把路径展开成预约条目（格点 + 有向边）。"""
        entries: list[ReservationEntry] = []
        for index, step in enumerate(self.steps):
            entries.append(ReservationEntry(kind="vertex", cell_from=step.cell, cell_to="", tick=step.tick))
            if step.action == "move" and index > 0:
                entries.append(
                    ReservationEntry(kind="edge", cell_from=self.steps[index - 1].cell, cell_to=step.cell, tick=step.tick)
                )
        return entries

    def to_dict(self) -> dict:
        return {
            "taskId": self.task_id,
            "agvId": self.agv_id,
            "mapVersion": self.map_version,
            "planVersion": self.plan_version,
            "ticks": [s.to_dict() for s in self.steps],
            "edges": self.edges,
            "estimatedTicks": self.estimated_ticks,
            "waitTicks": self.wait_ticks,
            "kind": self.kind,
            "goalCell": self.goal_cell,
            "blockedBy": self.blocked_by,
        }


def static_reachable(wm: WarehouseMap, start: str, goal: str, reservations, agv_id: str) -> bool:
    """忽略时间的静态可达性判断（避开静止车辆占位）。

    目的：目标被静止车辆堵死时快速失败，避免时空 A* 在无解空间里反复爆搜。
    """
    if start == goal:
        return True
    seen = {start}
    frontier = [start]
    while frontier:
        nxt = []
        for cell in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in seen:
                    continue
                if reservations.is_static_block(neighbor, agv_id):
                    continue
                if neighbor == goal:
                    return True
                seen.add(neighbor)
                nxt.append(neighbor)
        frontier = nxt
    return False


def manhattan(wm: WarehouseMap, a: str, b: str) -> int:
    cell_a = wm.cells.get(a)
    cell_b = wm.cells.get(b)
    if not cell_a or not cell_b:
        return 0
    return abs(cell_a.x - cell_b.x) + abs(cell_a.y - cell_b.y)


def static_distance(wm: WarehouseMap, start: str, goal: str) -> int | None:
    """忽略时间的 BFS 最短路长度，用于指派打分。"""
    if start == goal:
        return 0
    if not wm.is_passable(start) or not wm.is_passable(goal):
        return None
    frontier = [start]
    seen = {start}
    distance = 0
    while frontier:
        distance += 1
        nxt = []
        for cell in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in seen:
                    continue
                if neighbor == goal:
                    return distance
                seen.add(neighbor)
                nxt.append(neighbor)
        frontier = nxt
    return None


def plan_path(
    wm: WarehouseMap,
    start_cell: str,
    goal_cell: str,
    start_tick: int,
    reservations: ReservationStore,
    agv_id: str,
    priority: int = 0,
    max_ticks: int = config.PLAN_MAX_TICKS,
    max_expansions: int = config.PLAN_MAX_EXPANSIONS,
    task_id: str = "",
    plan_version: int = 1,
    kind: str = "task",
) -> PathPlan | None:
    """规划一条从 start_cell 到 goal_cell 的时空路径。

    失败返回 None（目标不可达或搜索上限耗尽）。
    """
    if start_cell not in wm.cells or not wm.is_passable(goal_cell):
        return None
    # 起点允许不可通行：AGV 可能刚被封在原地（例如施工封路落在它脚下），
    # 它必须能开出去；邻居仍按可通行过滤，因此不会重新进入封路格。
    if start_cell == goal_cell:
        step = PlanStep(tick=start_tick, cell=start_cell, x=wm.cells[start_cell].x, y=wm.cells[start_cell].y, action="wait")
        return PathPlan(
            task_id=task_id, agv_id=agv_id, map_version=wm.version, plan_version=plan_version,
            steps=[step], edges=[], estimated_ticks=0, wait_ticks=0, kind=kind, goal_cell=goal_cell,
        )
    # 静态可达性预检：忽略时间只看静止占位，避免无解时时空 A* 反复爆搜
    if not static_reachable(wm, start_cell, goal_cell, reservations, agv_id):
        return None

    map_id = wm.map_id
    counter = 0
    # 节点记录：cell, tick, blocker(本状态被谁挡住), parent, action
    # 根节点代表"当前 tick 时 AGV 停在 start_cell"，不计入移动步数。
    nodes: list[tuple[str, int, str | None, int, str]] = [(start_cell, start_tick, None, -1, "wait")]
    open_heap: list[tuple[int, int, int, int]] = [(manhattan(wm, start_cell, goal_cell), manhattan(wm, start_cell, goal_cell), start_tick, 0)]
    best: dict[tuple[str, int], int] = {(start_cell, start_tick): 0}
    closed: set[tuple[str, int]] = set()
    expansions = 0
    goal_index = -1

    while open_heap:
        _, _, tick, index = heapq.heappop(open_heap)
        cell, node_tick, blocker, parent, action = nodes[index]
        if (cell, node_tick) in closed:
            continue
        closed.add((cell, node_tick))
        expansions += 1
        if expansions > max_expansions:
            return None

        if cell == goal_cell:
            goal_index = index
            break

        next_tick = node_tick + 1
        if next_tick - start_tick > max_ticks:
            continue

        # ---- 移动动作 ------------------------------------------------
        for neighbor in wm.passable_neighbors(cell):
            # 静止车辆占位的格点必须避让（它不会自己让路）
            if reservations.is_static_block(neighbor, agv_id):
                continue
            vkey = vertex_key(map_id, neighbor, next_tick)
            holder = reservations.owner_of(vkey)
            if holder and holder != agv_id:
                continue  # 目标格已被占用
            ekey = edge_key(map_id, cell, neighbor, next_tick)
            edge_holder = reservations.owner_of(ekey)
            if edge_holder and edge_holder != agv_id:
                continue
            reverse_holder = reservations.owner_of(edge_key(map_id, neighbor, cell, next_tick))
            if reverse_holder and reverse_holder != agv_id:
                continue
            cost = (next_tick - start_tick)
            key = (neighbor, next_tick)
            if key in closed or best.get(key, 1 << 30) <= cost:
                continue
            best[key] = cost
            h = manhattan(wm, neighbor, goal_cell)
            counter += 1
            nodes.append((neighbor, next_tick, None, index, "move"))
            heapq.heappush(open_heap, (cost + h, h, next_tick, len(nodes) - 1))

        # ---- 等待动作 ------------------------------------------------
        # 若本 tick 的移动被他人挡住，则把等待视为可选动作，并记录阻挡者。
        wait_blocker = _find_blocker(wm, reservations, map_id, cell, next_tick, agv_id)
        wkey = vertex_key(map_id, cell, next_tick)
        if not (reservations.owner_of(wkey) and reservations.owner_of(wkey) != agv_id):
            cost = next_tick - start_tick
            key = (cell, next_tick)
            if key not in closed and best.get(key, 1 << 30) > cost:
                best[key] = cost
                h = manhattan(wm, cell, goal_cell)
                counter += 1
                nodes.append((cell, next_tick, wait_blocker, index, "wait"))
                heapq.heappush(open_heap, (cost + h, h, next_tick, len(nodes) - 1))

    if goal_index < 0:
        return None

    # ---- 回溯路径 --------------------------------------------------
    chain: list[int] = []
    cursor = goal_index
    while cursor >= 0:
        chain.append(cursor)
        cursor = nodes[cursor][3]
    chain.reverse()

    steps: list[PlanStep] = []
    edges: list[dict] = []
    wait_ticks = 0
    first_blocker: str | None = None
    for position, node_index in enumerate(chain):
        cell, tick, blocker, _, action = nodes[node_index]
        x, y = wm.cells[cell].x, wm.cells[cell].y
        step_blocker = blocker if (action == "wait" and blocker and position == len(chain) - 1) else None
        steps.append(PlanStep(tick=tick, cell=cell, x=x, y=y, action=action, blocked_by=step_blocker))
        if first_blocker is None and blocker:
            first_blocker = blocker
        if action == "wait" and position > 0:
            wait_ticks += 1
        if action == "move" and position > 0:
            prev_cell = steps[position - 1].cell
            edges.append({"tick": tick, "from": prev_cell, "to": cell})

    return PathPlan(
        task_id=task_id,
        agv_id=agv_id,
        map_version=wm.version,
        plan_version=plan_version,
        steps=steps,
        edges=edges,
        estimated_ticks=steps[-1].tick - steps[0].tick if steps else 0,
        wait_ticks=wait_ticks,
        kind=kind,
        goal_cell=goal_cell,
        blocked_by=first_blocker,
    )


def _find_blocker(wm, reservations, map_id, cell, tick, agv_id) -> str | None:
    """找出在 tick 时刻挡住本格出发的 AGV（用于等待图）。"""
    for neighbor in wm.passable_neighbors(cell):
        holder = reservations.owner_of(vertex_key(map_id, neighbor, tick))
        if holder and holder != agv_id:
            return holder
        holder = reservations.owner_of(edge_key(map_id, cell, neighbor, tick))
        if holder and holder != agv_id:
            return holder
        holder = reservations.owner_of(edge_key(map_id, neighbor, cell, tick))
        if holder and holder != agv_id:
            return holder
    return None


def find_free_cell(
    wm: WarehouseMap,
    start_cell: str,
    occupied: set[str],
    max_depth: int = config.ROLLBACK_SEARCH_DEPTH,
) -> list[str] | None:
    """在不被占用且可通行的格点中 BFS 找一个安全格，返回移动路径（不含起点）。

    用于死锁回退：让牺牲车辆退到一个空位，打破等待环。
    """
    if start_cell not in wm.cells:
        return None
    frontier = [(start_cell, [])]
    seen = {start_cell}
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        nxt = []
        for cell, path in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                new_path = path + [neighbor]
                if neighbor not in occupied:
                    return new_path
                nxt.append((neighbor, new_path))
        frontier = nxt
    return None


def find_parking_path(wm: WarehouseMap, start_cell: str, occupied: set[str]) -> list[str] | None:
    """找一条通往最近空闲待命格的路径（不含起点）。

    待命区是地图上的指定区域（通常是角落服务区），车辆空闲后回到这里，
    既不占用通道，也能让同一条路被反复使用。
    """
    parking = set(wm.parking_cells)
    if not parking:
        return None
    seen = {start_cell}
    frontier = [(start_cell, [])]
    while frontier:
        nxt = []
        for cell, path in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                new_path = path + [neighbor]
                if neighbor in parking and neighbor not in occupied:
                    return new_path
                nxt.append((neighbor, new_path))
        frontier = nxt
    return None


def articulation_points(wm: WarehouseMap) -> set[str]:
    """求地图的割点（移除后会把连通区域切开的格点）。

    回退车辆不能停在割点上，否则会把其它车辆的通道堵死。
    使用 Tarjan 算法，时间复杂度 O(V+E)。
    """
    discovery: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    result: set[str] = set()
    timer = 0

    def dfs(node: str, root: str) -> None:
        nonlocal timer
        timer += 1
        discovery[node] = timer
        low[node] = timer
        children = 0
        for neighbor in wm.passable_neighbors(node):
            if neighbor not in discovery:
                parent[neighbor] = node
                children += 1
                dfs(neighbor, root)
                low[node] = min(low[node], low[neighbor])
                if parent.get(node) is not None and low[neighbor] >= discovery[node]:
                    result.add(node)
            elif parent.get(node) != neighbor:
                low[node] = min(low[node], discovery[neighbor])
        if parent.get(node) is None and children > 1:
            result.add(node)

    for cell in sorted(wm.cells):
        if not wm.is_passable(cell) or cell in discovery:
            continue
        parent[cell] = None
        dfs(cell, cell)
    return result


def find_safe_cell(
    wm: WarehouseMap,
    start_cell: str,
    occupied: set[str],
    goal: str | None = None,
    max_depth: int = config.ROLLBACK_SEARCH_DEPTH,
) -> list[str] | None:
    """寻找更合适的回退格：优先选择"不挡路"的位置。

    评分顺序：

    1. 不能是割点（移除后会把通道切断）。
    2. 不在自己通往目标的路径上，避免回退后立刻又堵回原处。
    3. 有空闲邻居（车辆不会被困死），优于仍在通道中央的格点。
    4. 距离更近的优先，保证回退代价可控。

    返回移动路径（不含起点）；找不到返回 None。
    """
    if start_cell not in wm.cells:
        return None

    candidates: list[tuple[str, list[str], int]] = []
    seen = {start_cell}
    frontier = [(start_cell, [])]
    depth = 0
    while frontier and depth < max_depth:
        depth += 1
        nxt = []
        for cell, path in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in seen:
                    continue
                seen.add(neighbor)
                new_path = path + [neighbor]
                if neighbor not in occupied:
                    candidates.append((neighbor, new_path, depth))
                nxt.append((neighbor, new_path))
        frontier = nxt

    if not candidates:
        return None

    goal_path = _shortest_path_cells(wm, start_cell, goal) if goal else set()
    cuts = articulation_points(wm)

    def score(item):
        cell, _path, distance = item
        is_cut = 1 if cell in cuts else 0
        on_goal = 1 if cell in goal_path else 0
        free_neighbors = sum(1 for n in wm.passable_neighbors(cell) if n not in occupied)
        crowded = 0 if free_neighbors >= 1 else 1
        return (is_cut, on_goal, crowded, distance, cell)

    candidates.sort(key=score)
    return candidates[0][1]


def _shortest_path_cells(wm: WarehouseMap, start: str, goal: str) -> set[str]:
    """起点到终点的最短路径经过的格点集合（用于判断回退格是否挡在目标路径上）。"""
    if start == goal:
        return {start}
    previous: dict[str, str] = {start: start}
    frontier = [start]
    while frontier:
        nxt = []
        for cell in frontier:
            for neighbor in wm.passable_neighbors(cell):
                if neighbor in previous:
                    continue
                previous[neighbor] = cell
                if neighbor == goal:
                    path = {goal}
                    cursor = goal
                    while cursor != start:
                        cursor = previous[cursor]
                        path.add(cursor)
                    return path
                nxt.append(neighbor)
        frontier = nxt
    return set()
