"""全局常量与可调参数。

所有影响调度结果的数值集中在这里，避免散落在业务代码中。
数值含义见 `docs/design.md` 的"参数与调参"一节。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 版本信息
VERSION = "0.2.0-member-2"
SEED_DEFAULT = 20261118

# ---------------------------------------------------------------- 时间与 tick
TICK_SECONDS = 1
"""1 tick = 1 秒仿真时间。仿真时间与真实时间严格分离。"""

REAL_TIME_PER_TICK = {1: 1.0, 4: 0.25, 8: 0.125}
"""各速度档位下每个 tick 消耗的真实秒数，用于后台线程节流。"""

# ---------------------------------------------------------------- 地图
DEFAULT_MAP_ID = "warehouse-24"
MAP_WIDTH = 24
MAP_HEIGHT = 24

CELL_FLOOR = "floor"
CELL_SHELF = "shelf"
CELL_PICK = "pick"
CELL_DROP = "drop"
CELL_CHARGER = "charger"
CELL_BLOCKED = "blocked"

PASSABLE_TYPES = (CELL_FLOOR, CELL_PICK, CELL_DROP, CELL_CHARGER)
"""可通行格点类型。货架与封路格不可进入。"""

STATION_TYPES = (CELL_PICK, CELL_DROP, CELL_CHARGER)

# ---------------------------------------------------------------- 指派评分
# cost(agv, task) = W_DISTANCE * 归一化距离
#                 + W_LOAD * 活跃任务数
#                 - W_PRIORITY * 任务优先级
#                 + W_BATTERY * 低电量惩罚
W_DISTANCE = 1.0
W_LOAD = 2.0
W_PRIORITY = 1.5
W_BATTERY = 3.0
"""权重写入本文件而不是各服务各自定义，保证三份实现可对比。"""

DISTANCE_NORMALIZER = 1.0
"""曼哈顿距离的归一化系数；地图对角线长度的 1/4 约等于 12，故除以 12。"""

# ---------------------------------------------------------------- 电量与充电
BATTERY_DRAIN_MOVE = 0.6
"""每移动一格消耗电量百分比。"""

BATTERY_DRAIN_IDLE = 0.05
"""待机每 tick 消耗电量百分比。"""

BATTERY_DRAIN_LOADING = 0.1
"""装卸作业每 tick 消耗电量百分比。"""

BATTERY_CHARGE_RATE = 6.0
"""充电每 tick 恢复电量百分比。"""

LOW_BATTERY_THRESHOLD = 20.0
"""低于该值不再接受新任务。"""

CRITICAL_BATTERY = 8.0
"""低于该值且有任务时立刻请求充电，不再接新任务。"""

CHARGE_FULL = 95.0
"""达到该值视为充满并回到 idle。"""

# ---------------------------------------------------------------- 作业节拍
LOADING_TICKS = 2
"""取货确认占用 tick 数。"""

UNLOADING_TICKS = 2
"""放货确认占用 tick 数。"""

# ---------------------------------------------------------------- 路径与预约
PLAN_MAX_TICKS = 400
"""单条路径的最大时间跨度，防止无界搜索。"""

PLAN_MAX_EXPANSIONS = 20000
"""时空 A* 最大扩展节点数，防止单次规划拖垮 tick。

无解场景由 `static_reachable` 预检提前失败，这里只是兜底上限。
"""

RESERVATION_TTL_TICKS = 120
"""预约兜底 TTL：超过该 tick 未确认则自动释放。"""

# ---------------------------------------------------------------- 死锁与回退
MAX_ROLLBACK = 3
"""单车最大回退次数，超过则把任务置为 failed，避免活锁。"""

MAX_BLOCK_TICKS = 90
"""单车最大连续阻塞 tick 数，超过则释放任务并告警。"""

ROLLBACK_SEARCH_DEPTH = 8
"""寻找安全回退格的最大搜索深度（BFS）。"""

DEADLOCK_CHECK_INTERVAL = 1
"""每隔多少 tick 检测一次等待图环。"""

REPLAN_COOLDOWN = 3
"""重规划失败后的冷却 tick 数，避免每 tick 重复做时空搜索。"""

DEADLOCK_MIN_BLOCK_TICKS = 2
"""认定死锁前，环内车辆至少要连续阻塞多少 tick，过滤瞬时等待。"""

DEADLOCK_COOLDOWN = 10
"""同一个等待环的重复处理冷却 tick 数。"""

# ---------------------------------------------------------------- 其它
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 20
EVENT_RETENTION = 5000
"""内存与数据库中保留的事件条数上限。"""

TRAIL_LENGTH = 120
"""每台 AGV 保留的轨迹点数。"""
