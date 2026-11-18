"""等待图与死锁环检测。

等待边表示 `blocked_agv -> blocking_agv`。检测到环后，按公共契约
`docs/data-schema.md` 第 6.3 节选择"环中优先级最低且可回退"的 AGV 作为牺牲者：

1. 先过滤出存在安全回退格的 AGV。
2. 再在其中选优先级最低者，同优先级按 id 排序，保证确定性。
3. 若环中没有任何 AGV 可以回退，则按上限策略把优先级最低者的任务置为 failed，
   避免活锁。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WaitGraph:
    edges: dict[str, set[str]] = field(default_factory=dict)

    def set_blocker(self, agv_id: str, blocker_id: str | None) -> None:
        """记录一条等待边；blocker 为空表示该车当前没有被挡住。"""
        if blocker_id and blocker_id != agv_id:
            self.edges.setdefault(agv_id, set()).add(blocker_id)
        else:
            self.edges.setdefault(agv_id, set())

    def blocker_of(self, agv_id: str) -> str | None:
        blockers = self.edges.get(agv_id)
        if not blockers:
            return None
        return sorted(blockers)[0]

    def find_cycle(self) -> list[str] | None:
        """返回一个等待环（节点序列），没有环返回 None。

        使用带颜色标记的确定性 DFS，节点与邻居均按 id 排序，
        保证同样的等待图永远得到同样的环。
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {node: WHITE for node in self.edges}
        stack: list[str] = []

        def dfs(node: str) -> list[str] | None:
            color[node] = GRAY
            stack.append(node)
            for neighbor in sorted(self.edges.get(node, set())):
                state = color.get(neighbor, WHITE)
                if state == GRAY:
                    # 找到环：截取栈中从 neighbor 开始的部分
                    index = stack.index(neighbor)
                    return stack[index:]
                if state == WHITE:
                    found = dfs(neighbor)
                    if found:
                        return found
            stack.pop()
            color[node] = BLACK
            return None

        for node in sorted(self.edges):
            if color.get(node, WHITE) == WHITE:
                cycle = dfs(node)
                if cycle:
                    return cycle
        return None

    def all_cycles(self) -> list[list[str]]:
        """找出全部简单环（用于统计与告警）。"""
        cycles: list[list[str]] = []
        seen_signatures: set[tuple[str, ...]] = set()
        for start in sorted(self.edges):
            stack: list[str] = []
            visited: set[str] = set()

            def dfs(node: str) -> None:
                stack.append(node)
                visited.add(node)
                for neighbor in sorted(self.edges.get(node, set())):
                    if neighbor == start:
                        signature = tuple(sorted(stack))
                        if signature not in seen_signatures:
                            seen_signatures.add(signature)
                            cycles.append(list(stack))
                        continue
                    if neighbor not in visited and neighbor < start:
                        dfs(neighbor)
                stack.pop()
                visited.discard(node)

            dfs(start)
        return cycles


def choose_victim(cycle: list[str], agvs: dict, can_rollback: dict[str, bool]) -> str | None:
    """在环中选择牺牲者。

    - 优先选择可回退（存在安全回退格）的车辆。
    - 其次选择任务优先级最低者。
    - 同优先级按 AGV id 排序，保证确定性。
    """
    rollbackable = [agv_id for agv_id in cycle if can_rollback.get(agv_id)]
    pool = rollbackable or list(cycle)
    if not pool:
        return None

    def sort_key(agv_id: str):
        agv = agvs.get(agv_id)
        priority = agv.task_priority if agv is not None and agv.task_priority is not None else 0
        rollbacks = getattr(agv, "rollback_count", 0) if agv is not None else 0
        return (priority, rollbacks, agv_id)

    return sorted(pool, key=sort_key)[0]
