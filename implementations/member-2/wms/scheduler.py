"""AGV 指派。

评分函数与 `docs/03-architecture.md` 第 6.1 节一致：

    cost(agv, task) =
        w_distance   * 归一化距离
      + w_load       * 活跃任务数
      - w_priority   * 任务优先级
      + w_battery    * 低电量惩罚

权重集中在 `config.py`，不在各服务各自定义。候选按 (cost, agv_id) 排序，
保证同样的输入永远得到同样的指派结果。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config
from .mapdata import WarehouseMap
from .pathfinding import static_distance


@dataclass
class Candidate:
    agv_id: str
    cost: float
    distance: int
    load: int
    priority: int
    battery: float
    reachable: bool

    def to_dict(self) -> dict:
        return {
            "agvId": self.agv_id,
            "cost": round(self.cost, 4),
            "distance": self.distance,
            "load": self.load,
            "priority": self.priority,
            "battery": round(self.battery, 2),
            "reachable": self.reachable,
        }


def distance_normalizer(wm: WarehouseMap) -> float:
    """距离归一化系数：地图曼哈顿对角线的一半，避免距离项压过其他项。"""
    return max(1.0, (wm.width + wm.height) / 4.0)


def score_agv(wm: WarehouseMap, agv, task_priority: int, pickup_cell: str, active_tasks: int) -> Candidate:
    """给一台 AGV 打分。"""
    distance = static_distance(wm, _cell_of(agv), pickup_cell)
    reachable = distance is not None
    distance_value = float(distance if distance is not None else 0)
    low_battery_penalty = max(0.0, (config.LOW_BATTERY_THRESHOLD - float(agv.battery)) / config.LOW_BATTERY_THRESHOLD)
    cost = (
        config.W_DISTANCE * (distance_value / distance_normalizer(wm))
        + config.W_LOAD * active_tasks
        - config.W_PRIORITY * task_priority
        + config.W_BATTERY * low_battery_penalty
    )
    return Candidate(
        agv_id=agv.id,
        cost=cost,
        distance=int(distance_value),
        load=active_tasks,
        priority=task_priority,
        battery=float(agv.battery),
        reachable=reachable,
    )


def _cell_of(agv) -> str:
    return f"{agv.x},{agv.y}"


def select_best(candidates: list[Candidate]) -> Candidate | None:
    """在候选中选择最优 AGV。

    规则：不可达候选剔除；电量过低候选剔除；其余按 (cost, agv_id) 取最小，
    保证结果可复现。
    """
    usable = [c for c in candidates if c.reachable and c.battery > config.LOW_BATTERY_THRESHOLD]
    if not usable:
        return None
    usable.sort(key=lambda c: (round(c.cost, 6), c.agv_id))
    return usable[0]
