"""tick 仿真引擎。

每个 tick 的处理顺序（与 `docs/03-architecture.md` 第 4.2 节一致）：

1. 应用本 tick 注入的事件。
2. 按优先级给待处理任务指派 AGV。
3. 各 AGV 沿已批准路径推进一步（含装卸、充电、电量变化）。
4. 构建等待图，检测死锁环并执行回退恢复。
5. 维护预约（释放已走过与过期的预约）。
6. 持久化运行态快照。

确定性保证：

- 每 tick 的处理顺序只依赖 AGV id 与任务 id 排序，不依赖字典遍历顺序。
- 仿真时间只有 tick，没有真实时间参与业务判断。
- 相同地图 + 相同输入 + 相同 seed 得到相同的事件序列。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from . import config
from .deadlock import WaitGraph, choose_victim
from .errors import NotFoundError, ValidationError
from .mapdata import MapStore, WarehouseMap
from .pathfinding import PathPlan, PlanStep, find_parking_path, find_safe_cell, plan_path, static_distance
from .reservations import Reservation, ReservationEntry, ReservationStore, edge_key, vertex_key
from .util import cell_id, dumps, new_id, now_iso, round2

AGV_STATUS_TRANSITIONS = {
    "idle": {"to_pickup", "charging", "fault", "offline"},
    "to_pickup": {"to_pickup", "loading", "idle", "fault", "offline"},
    "loading": {"to_dropoff", "fault"},
    "to_dropoff": {"to_dropoff", "unloading", "idle", "fault", "offline"},
    "unloading": {"idle", "charging", "fault"},
    "charging": {"idle", "fault", "offline"},
    "fault": {"idle"},
    "offline": {"idle", "fault"},
}


@dataclass
class AgvRuntime:
    id: str
    map_id: str
    status: str = "idle"
    x: int = 0
    y: int = 0
    battery: float = 100.0
    active_task_id: str | None = None
    payload_sku: str | None = None
    last_tick: int = 0
    fault_code: str | None = None
    version: int = 1
    task_priority: int | None = None
    rollback_count: int = 0
    block_ticks: int = 0
    plan: PathPlan | None = None
    plan_version: int = 0
    leg: str = ""            # pickup | dropoff | charge | retreat
    dwell_ticks: int = 0
    dwell_kind: str | None = None
    trail: list[tuple[int, int, int]] = field(default_factory=list)
    charger_cell: str | None = None
    blocked_target: str | None = None
    """上一次被挡住时试图进入的格点，用于构建等待图。"""
    replan_cooldown: int = 0
    """重规划失败后的冷却 tick 数，避免每 tick 重复搜索。"""
    needs_replan: bool = False
    replan_reason: str = ""

    @property
    def cell(self) -> str:
        return cell_id(self.x, self.y)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mapId": self.map_id,
            "status": self.status,
            "x": self.x,
            "y": self.y,
            "battery": round2(self.battery),
            "activeTaskId": self.active_task_id,
            "payloadSku": self.payload_sku,
            "lastTick": self.last_tick,
            "faultCode": self.fault_code,
            "version": self.version,
            "taskPriority": self.task_priority,
            "rollbackCount": self.rollback_count,
            "planVersion": self.plan_version,
            "chargingStationId": self.charger_cell,
            "trail": [{"tick": t, "x": x, "y": y} for t, x, y in self.trail[-config.TRAIL_LENGTH:]],
        }


class Simulator:
    """仿真主循环。"""

    def __init__(self, db, maps: MapStore, bus, service):
        self.db = db
        self.maps = maps
        self.bus = bus
        self.service = service
        self.lock = threading.RLock()

        self.reservations = ReservationStore(maps.current.map_id)
        self.agvs: dict[str, AgvRuntime] = {}
        self.tick = 0
        self.running = False
        self.speed = 1
        self.seed = config.SEED_DEFAULT
        self.scenario_id = "single-agv"
        self.wait_graph = WaitGraph()
        self.stats = {
            "conflicts": 0,
            "deadlocks": 0,
            "recoveries": 0,
            "rollbacks": 0,
            "completedTasks": 0,
            "failedTasks": 0,
            "cancelledTasks": 0,
        }
        self.alerts: list[dict] = []
        self.contention: dict[str, int] = {}
        """格点阻塞计数，用于瓶颈巷道 TopN 统计。"""
        self._charger_holders: dict[str, set[str]] = {}
        self._last_deadlock_signature: tuple[str, ...] | None = None
        self._last_deadlock_tick = -999
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pending_injections: list[dict] = []

    # ============================================================ 生命周期
    def start_thread(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="sim-loop", daemon=True)
        self._thread.start()

    def stop_thread(self) -> None:
        self._stop.set()
        self.running = False

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            with self.lock:
                speed = self.speed if self.running else 1
                should_run = self.running
            delay = config.REAL_TIME_PER_TICK.get(speed, 1.0)
            time.sleep(delay)
            if not should_run:
                continue
            with self.lock:
                if self.running:
                    self.step_once()

    # ============================================================ 运行控制
    def start(self) -> None:
        with self.lock:
            self.running = True
            self._emit("sim.status", None, {"running": True, "speed": self.speed})

    def pause(self) -> None:
        with self.lock:
            self.running = False
            self._emit("sim.status", None, {"running": False, "speed": self.speed})

    def set_speed(self, speed: int) -> None:
        if speed not in config.REAL_TIME_PER_TICK:
            raise ValidationError("速度只支持 1、4、8", {"speed": speed})
        with self.lock:
            self.speed = speed
            self._emit("sim.status", None, {"running": self.running, "speed": speed})

    def step(self, ticks: int = 1) -> int:
        if ticks < 1 or ticks > 10000:
            raise ValidationError("单步 tick 数必须在 1-10000 之间", {"ticks": ticks})
        with self.lock:
            for _ in range(ticks):
                self.step_once()
            return self.tick

    # ============================================================ 主循环
    def step_once(self) -> None:
        """推进一个 tick。"""
        self.tick += 1
        tick = self.tick

        # 1. 注入事件
        self._apply_injections()

        # 2. 任务指派
        self._assign_pending_tasks()

        # 3. AGV 推进
        for agv in sorted(self.agvs.values(), key=lambda a: a.id):
            self._advance_agv(agv)

        # 3.5 刷新静止车辆占位表
        self._update_static_blocks()

        # 4. 死锁检测与恢复
        self._handle_deadlock()

        # 5. 预约维护
        self.reservations.release_past(tick)
        self.reservations.release_expired(tick)

        # 6. 运行态持久化
        self._persist_runtime()

    # ============================================================ 指派
    def _assign_pending_tasks(self) -> None:
        tasks = self.db.query(
            "SELECT * FROM tasks WHERE status = 'pending' ORDER BY priority DESC, created_at ASC, id ASC"
        )
        for task in tasks:
            if task["assigned_agv_id"]:
                continue
            pickup = self.db.query_one("SELECT * FROM location WHERE id = ?", (task["pickup_location_id"],))
            if not pickup:
                continue
            pickup_cell = cell_id(pickup["x"], pickup["y"])
            candidates = []
            for agv in sorted(self.agvs.values(), key=lambda a: a.id):
                if agv.status != "idle" or agv.active_task_id:
                    continue
                if agv.battery <= config.LOW_BATTERY_THRESHOLD:
                    continue
                if task["pinned_agv_id"] and agv.id != task["pinned_agv_id"]:
                    continue
                load = self.db.query_one(
                    "SELECT COUNT(*) AS c FROM tasks WHERE assigned_agv_id = ? AND status IN ('assigned','running')",
                    (agv.id,),
                )["c"]
                from .scheduler import score_agv
                candidates.append(score_agv(self.maps.current, agv, task["priority"], pickup_cell, load))
            from .scheduler import select_best
            best = select_best(candidates)
            if best is None:
                continue
            agv = self.agvs[best.agv_id]
            self._assign_to_agv(agv, task, pickup_cell, "pickup")

    def _assign_to_agv(self, agv: AgvRuntime, task, goal_cell: str, leg: str) -> bool:
        """给 AGV 指派任务并生成第一段路径。"""
        plan_version = agv.plan_version + 1
        plan = plan_path(
            self.maps.current,
            agv.cell,
            goal_cell,
            self.tick,
            self.reservations,
            agv.id,
            priority=task["priority"],
            task_id=task["id"],
            plan_version=plan_version,
        )
        if plan is None:
            # 目标被占用属于暂时性阻塞：任务留在池中等待，不算冲突
            if self._goal_is_temporarily_blocked(agv, goal_cell):
                return False
            self._emit("conflict.detected", task["id"], {
                "agvId": agv.id, "reason": "无可用路径", "goal": goal_cell,
            })
            self.stats["conflicts"] += 1
            return False

        ok, conflicts, preempted = self.reservations.acquire(
            agv.id, plan_version, task["priority"], plan.reservations(self.maps.current.map_id), self.tick
        )
        if not ok:
            self.stats["conflicts"] += 1
            self._emit("conflict.detected", task["id"], {
                "agvId": agv.id, "reason": "预约冲突", "keys": conflicts[:5],
            })
            return False
        for victim in preempted:
            self._mark_needs_replan(victim, "被高优先级任务抢占")

        agv.plan = plan
        agv.plan_version = plan_version
        agv.leg = leg
        agv.active_task_id = task["id"]
        agv.task_priority = task["priority"]
        agv.needs_replan = False
        agv.replan_reason = ""
        self._set_status(agv, "to_pickup" if leg == "pickup" else "to_dropoff")
        self.service.assign_task(task["id"], agv.id, plan_version)
        return True

    # ============================================================ AGV 推进
    def _advance_agv(self, agv: AgvRuntime) -> None:
        if agv.status in ("fault", "offline"):
            return

        if agv.dwell_ticks > 0:
            self._run_dwell(agv)
            return

        if agv.status == "charging":
            self._run_charging(agv)
            return

        plan = agv.plan
        if plan is None:
            if agv.needs_replan and agv.active_task_id:
                agv.block_ticks += 1
                if agv.replan_cooldown > 0:
                    agv.replan_cooldown -= 1
                    self._hold_cell(agv)
                    self._check_block_timeout(agv)
                    return
                self._replan(agv, agv.replan_reason or "需要重新规划")
                return
            # 空闲：无任务则只消耗少量电量，并持续占住当前格点
            if self._park_away_from_station(agv):
                return
            agv.battery = max(0.0, agv.battery - config.BATTERY_DRAIN_IDLE)
            agv.last_tick = self.tick
            self._hold_cell(agv)
            if agv.battery <= config.LOW_BATTERY_THRESHOLD:
                self._start_charging(agv)
            return

        step = self._step_at(plan, self.tick)
        if step is None:
            self._on_plan_finished(agv)
            return

        if agv.needs_replan:
            if agv.replan_cooldown > 0:
                # 上一轮重规划失败，先等待若干 tick 再试，避免反复搜索
                agv.replan_cooldown -= 1
                self._hold_cell(agv)
                return
            self._replan(agv, agv.replan_reason or "需要重新规划")
            return

        if step.action == "wait":
            # 等待：确认等待是否还有必要
            if self._wait_can_be_shortened(agv, plan):
                self._replan(agv, "等待条件已解除")
                return
            agv.battery = max(0.0, agv.battery - config.BATTERY_DRAIN_IDLE)
            agv.last_tick = self.tick
            agv.block_ticks += 1
            blocker = self._live_blocker(agv)
            self.contention[agv.cell] = self.contention.get(agv.cell, 0) + 1
            if blocker:
                self._emit("agv.blocked", agv.id, {"blocker": blocker, "cell": agv.cell})
            self._check_block_timeout(agv)
            return

        # 移动
        target = step.cell
        occupant = self._occupant_of(target)
        if occupant and occupant != agv.id:
            # 目标格点被其它车辆实际占用：本 tick 不能移动，等待后重新规划
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = "目标格点被占用"
            agv.block_ticks += 1
            agv.blocked_target = target
            # 立刻占住当前格点：本 tick 之后别人不能规划进入
            self.reservations.set_static_block(agv.cell, agv.id)
            self._hold_cell(agv)
            self._emit("agv.blocked", agv.id, {"blocker": occupant, "cell": target})
            self._check_block_timeout(agv)
            return
        holder = self.reservations.owner_of(vertex_key(self.maps.current.map_id, target, self.tick))
        if holder and holder != agv.id:
            self._replan(agv, "目标格点被抢占")
            return
        edge_holder = self.reservations.owner_of(
            edge_key(self.maps.current.map_id, self._previous_cell(plan, step), target, self.tick)
        )
        if edge_holder and edge_holder != agv.id:
            self._replan(agv, "边被占用")
            return

        if agv.active_task_id and self._task_status(agv.active_task_id) == "assigned":
            self.service.start_task(agv.active_task_id, agv.id, agv.plan_version)

        agv.x = step.x
        agv.y = step.y
        agv.last_tick = self.tick
        agv.block_ticks = 0
        agv.blocked_target = None
        # 本 tick 要移动，不再占住原格
        self.reservations.clear_static_block(agv.id)
        agv.trail.append((self.tick, agv.x, agv.y))
        if len(agv.trail) > config.TRAIL_LENGTH:
            agv.trail.pop(0)
        self._emit("agv.position.changed", agv.id, {
            "x": agv.x, "y": agv.y, "status": agv.status, "battery": round2(agv.battery),
            "taskId": agv.active_task_id, "planVersion": agv.plan_version,
        })

    def _step_at(self, plan: PathPlan, tick: int) -> PlanStep | None:
        for step in plan.steps:
            if step.tick == tick:
                return step
        return None

    def _previous_cell(self, plan: PathPlan, step: PlanStep) -> str:
        previous = None
        for candidate in plan.steps:
            if candidate.tick == step.tick:
                break
            previous = candidate.cell
        return previous or plan.steps[0].cell

    def _wait_can_be_shortened(self, agv: AgvRuntime, plan: PathPlan) -> bool:
        """当前 tick 需要等待，但下一段移动的目标格点已经空闲 => 重新规划更划算。"""
        future = [s for s in plan.steps if s.tick > self.tick]
        move_step = next((s for s in future if s.action == "move"), None)
        if move_step is None:
            return False
        # 目标格点被别的车辆实际占着时，等待是必要的，不能靠重规划解决
        occupant = self._occupant_of(move_step.cell)
        if occupant and occupant != agv.id:
            return False
        holder = self.reservations.owner_of(vertex_key(self.maps.current.map_id, move_step.cell, self.tick + 1))
        return holder is None or holder == agv.id

    # ============================================================ 作业节拍
    def _run_dwell(self, agv: AgvRuntime) -> None:
        """装卸确认占用 tick。"""
        agv.dwell_ticks -= 1
        agv.battery = max(0.0, agv.battery - config.BATTERY_DRAIN_LOADING)
        agv.last_tick = self.tick
        if agv.dwell_ticks > 0:
            return
        if agv.dwell_kind == "load":
            agv.payload_sku = self._task_sku(agv.active_task_id)
            self._set_status(agv, "to_dropoff")
            self._plan_next_leg(agv)
        elif agv.dwell_kind == "unload":
            self._finish_task(agv)

    def _on_plan_finished(self, agv: AgvRuntime) -> None:
        """当前路径段执行完毕。"""
        plan = agv.plan
        if plan is None:
            return
        goal = plan.goal_cell
        if agv.leg == "retreat":
            # 回退结束：重新规划当前任务
            self._replan_task_from_current(agv, "回退后重新规划")
            return
        if agv.leg == "parking":
            self._set_status(agv, "idle")
            return
        if agv.leg == "charge":
            self._set_status(agv, "charging")
            agv.dwell_ticks = 0
            return
        if agv.leg == "pickup":
            self._set_status(agv, "loading")
            agv.dwell_kind = "load"
            agv.dwell_ticks = config.LOADING_TICKS
            return
        if agv.leg == "dropoff":
            self._set_status(agv, "unloading")
            agv.dwell_kind = "unload"
            agv.dwell_ticks = config.UNLOADING_TICKS
            return

    def _plan_next_leg(self, agv: AgvRuntime) -> None:
        """取货完成后规划前往放货点。"""
        task = self._task(agv.active_task_id)
        if not task:
            return
        drop = self.db.query_one("SELECT * FROM location WHERE id = ?", (task["drop_location_id"],))
        if not drop:
            return
        goal = cell_id(drop["x"], drop["y"])
        if self._goal_is_temporarily_blocked(agv, goal):
            # 放货点暂时被占用：等待对方离开再规划，不让任务失败
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = "放货点暂时被占用"
            agv.replan_cooldown = config.REPLAN_COOLDOWN
            agv.block_ticks += 1
            self._hold_cell(agv)
            self._check_block_timeout(agv)
            return
        plan_version = agv.plan_version + 1
        plan = plan_path(
            self.maps.current, agv.cell, goal, self.tick, self.reservations,
            agv.id, priority=task["priority"], task_id=task["id"], plan_version=plan_version,
        )
        if plan is None:
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = "无法规划前往放货点的路径"
            agv.replan_cooldown = config.REPLAN_COOLDOWN
            agv.block_ticks += 1
            self._hold_cell(agv)
            self._check_block_timeout(agv)
            return
        ok, conflicts, _ = self.reservations.acquire(
            agv.id, plan_version, task["priority"], plan.reservations(self.maps.current.map_id), self.tick
        )
        if not ok:
            self.stats["conflicts"] += 1
            self._emit("conflict.detected", task["id"], {
                "agvId": agv.id, "reason": "前往放货点时预约冲突", "keys": conflicts[:5],
            })
            self._replan(agv, "前往放货点时预约冲突")
            return
        self.reservations.release_agv(agv.id, plan_version=agv.plan_version)
        agv.plan = plan
        agv.plan_version = plan_version
        agv.leg = "dropoff"
        self.service.replan_task(task["id"], agv.id, plan_version, "前往放货点")

    def _finish_task(self, agv: AgvRuntime) -> None:
        task_id = agv.active_task_id
        self.service.complete_task(task_id, {"agvId": agv.id})
        self.stats["completedTasks"] += 1
        agv.active_task_id = None
        agv.payload_sku = None
        agv.task_priority = None
        agv.plan = None
        agv.leg = ""
        agv.rollback_count = 0
        agv.block_ticks = 0
        if agv.battery <= config.LOW_BATTERY_THRESHOLD:
            self._start_charging(agv)
            return
        if self._park_away_from_station(agv):
            return
        self._set_status(agv, "idle")

    def _park_away_from_station(self, agv: AgvRuntime) -> bool:
        """任务结束后离开工位并回到待命区，避免停在工位或通道上挡住其它车辆。"""
        cell = self.maps.current.cells.get(agv.cell)
        if cell is None:
            return False
        occupied = {other.cell for other in self.agvs.values() if other.id != agv.id}
        parking = set(self.maps.current.parking_cells)
        needs_parking = cell.type != config.CELL_FLOOR or agv.cell in parking
        if not needs_parking:
            return False
        path = find_parking_path(self.maps.current, agv.cell, occupied)
        if not path:
            path = find_safe_cell(self.maps.current, agv.cell, occupied, max_depth=4)
        if not path:
            return False
        plan = self._build_retreat_plan(agv, path)
        if plan is None:
            return False
        agv.plan = plan
        agv.plan_version += 1
        agv.leg = "parking"
        return True

    def _fail_task(self, agv: AgvRuntime, reason: str) -> None:
        if agv.active_task_id:
            self.service.fail_task(agv.active_task_id, reason)
            self.stats["failedTasks"] += 1
            self._alert("task_failed", f"任务失败：{reason}", [agv.id])
        agv.active_task_id = None
        agv.task_priority = None
        agv.plan = None
        agv.leg = ""
        agv.rollback_count = 0
        self.reservations.release_agv(agv.id)
        self._set_status(agv, "idle")

    # ============================================================ 充电
    def _start_charging(self, agv: AgvRuntime) -> None:
        charger = self._select_charger(agv)
        if charger is None:
            self._set_status(agv, "idle")
            return
        plan_version = agv.plan_version + 1
        plan = plan_path(
            self.maps.current, agv.cell, charger, self.tick, self.reservations,
            agv.id, priority=0, task_id="", plan_version=plan_version, kind="charge",
        )
        if plan is None:
            self._set_status(agv, "idle")
            return
        self.reservations.release_agv(agv.id, plan_version=agv.plan_version)
        ok, _conflicts, _ = self.reservations.acquire(
            agv.id, plan_version, 0, plan.reservations(self.maps.current.map_id), self.tick
        )
        if not ok:
            self._set_status(agv, "idle")
            return
        agv.plan = plan
        agv.plan_version = plan_version
        agv.leg = "charge"
        agv.charger_cell = charger
        self._charger_holders.setdefault(charger, set()).add(agv.id)
        self._set_status(agv, "charging")
        self._emit("agv.charging", agv.id, {"charger": charger, "battery": round2(agv.battery)})

    def _select_charger(self, agv: AgvRuntime) -> str | None:
        """选择有空闲容量的充电桩。"""
        best = None
        best_distance = None
        for station in sorted(self.maps.current.stations_of(config.CELL_CHARGER), key=lambda s: s.cell_id):
            holders = self._charger_holders.get(station.cell_id, set())
            if len(holders) >= station.capacity:
                continue
            distance = static_distance(self.maps.current, agv.cell, station.cell_id)
            if distance is None:
                continue
            if best_distance is None or distance < best_distance:
                best = station.cell_id
                best_distance = distance
        return best

    def _run_charging(self, agv: AgvRuntime) -> None:
        """充电推进。"""
        agv.last_tick = self.tick
        if agv.cell != agv.charger_cell:
            # 还没到充电桩，继续沿路径移动
            plan = agv.plan
            if plan is None:
                self._set_status(agv, "idle")
                return
            step = self._step_at(plan, self.tick)
            if step is None:
                self._set_status(agv, "charging")
                agv.dwell_ticks = 0
                return
            if step.action == "move":
                agv.x, agv.y = step.x, step.y
                agv.trail.append((self.tick, agv.x, agv.y))
                self._emit("agv.position.changed", agv.id, {
                    "x": agv.x, "y": agv.y, "status": agv.status, "battery": round2(agv.battery),
                })
            return
        agv.battery = min(100.0, agv.battery + config.BATTERY_CHARGE_RATE)
        self._emit("agv.charging", agv.id, {"battery": round2(agv.battery), "charger": agv.charger_cell})
        if agv.battery >= config.CHARGE_FULL:
            holders = self._charger_holders.get(agv.charger_cell)
            if holders:
                holders.discard(agv.id)
            agv.charger_cell = None
            agv.plan = None
            agv.leg = ""
            self._set_status(agv, "idle")

    # ============================================================ 重规划
    def _mark_needs_replan(self, agv_id: str, reason: str) -> None:
        agv = self.agvs.get(agv_id)
        if agv is None:
            return
        agv.needs_replan = True
        agv.replan_reason = reason

    def _replan(self, agv: AgvRuntime, reason: str) -> None:
        """基于当前地图版本重新规划当前任务。"""
        if not agv.active_task_id:
            # 无任务的车辆（返程 / 回退 / 去充电）只在"地图变化"这条路径上重规划，
            # 其它原因（目标被占、边被占）保持原计划，由重试机制处理。
            agv.needs_replan = False
            return
        task = self._task(agv.active_task_id)
        if not task:
            return
        leg = agv.leg if agv.leg in ("pickup", "dropoff") else "pickup"
        if leg == "pickup":
            location = self.db.query_one("SELECT * FROM location WHERE id = ?", (task["pickup_location_id"],))
        else:
            location = self.db.query_one("SELECT * FROM location WHERE id = ?", (task["drop_location_id"],))
        if not location:
            return
        goal = cell_id(location["x"], location["y"])

        # 目标暂时被占用时不必规划：等待对方离开再重试，避免无谓的搜索开销
        if self._goal_is_temporarily_blocked(agv, goal):
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = "目标暂时被占用，等待后重试"
            agv.replan_cooldown = config.REPLAN_COOLDOWN
            agv.block_ticks += 1
            self._hold_cell(agv)
            self._check_block_timeout(agv)
            return

        plan_version = agv.plan_version + 1
        plan = plan_path(
            self.maps.current, agv.cell, goal, self.tick, self.reservations,
            agv.id, priority=task["priority"], task_id=task["id"], plan_version=plan_version,
        )
        if plan is None:
            # 暂时找不到路径（前方排队或被占）：等待并重试，超过阻塞上限再判失败
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = f"重规划失败，等待重试：{reason}"
            agv.replan_cooldown = config.REPLAN_COOLDOWN
            agv.block_ticks += 1
            self._hold_cell(agv)
            self._check_block_timeout(agv)
            return
        ok, conflicts, _ = self.reservations.acquire(
            agv.id, plan_version, task["priority"], plan.reservations(self.maps.current.map_id), self.tick
        )
        if not ok:
            self.stats["conflicts"] += 1
            self._emit("conflict.detected", task["id"], {"agvId": agv.id, "reason": "重规划时预约冲突"})
            agv.plan = None
            agv.needs_replan = True
            agv.replan_reason = reason
            agv.replan_cooldown = config.REPLAN_COOLDOWN
            return
        # 新预约获取成功后再释放旧预约，避免出现"没有预约也没有计划"的空档
        self.reservations.release_agv(agv.id, plan_version=agv.plan_version)
        agv.plan = plan
        agv.plan_version = plan_version
        agv.needs_replan = False
        agv.leg = leg
        self.service.replan_task(task["id"], agv.id, plan_version, reason)

    def _replan_idle_after_map_change(self, agv: AgvRuntime) -> None:
        """地图变化后，没有任务的车辆（返程 / 回退 / 去充电）也必须重规划。

        回归缺陷：这类车辆原先完全跳过重规划，会沿着旧计划开进已经封路的格点。
        目标优先级：充电桩 → 原计划终点 → 最近的空闲待命格 → 任意安全格；
        都不可用时就地停下等待地图恢复（停下是安全的，开进封路格不是）。
        """
        occupied = {other.cell for other in self.agvs.values() if other.id != agv.id}
        kind = agv.plan.kind if agv.plan is not None else "retreat"
        goal = ""
        if agv.leg == "charge" and agv.charger_cell:
            goal = agv.charger_cell
        elif agv.plan is not None and agv.plan.goal_cell:
            goal = agv.plan.goal_cell

        if goal and self.maps.current.is_passable(goal):
            plan_version = agv.plan_version + 1
            candidate = plan_path(
                self.maps.current, agv.cell, goal, self.tick, self.reservations,
                agv.id, priority=0, task_id="", plan_version=plan_version, kind=kind,
            )
            if candidate is not None:
                ok, _conflicts, _preempted = self.reservations.acquire(
                    agv.id, plan_version, 0, candidate.reservations(self.maps.current.map_id), self.tick
                )
                if ok:
                    self.reservations.release_agv(agv.id, plan_version=agv.plan_version)
                    agv.plan = candidate
                    agv.plan_version = plan_version
                    agv.needs_replan = False
                    return

        # 目标不可用或规划失败：找一个安全格停下（与回退逻辑一致）
        path = find_parking_path(self.maps.current, agv.cell, occupied)
        if not path:
            path = find_safe_cell(self.maps.current, agv.cell, occupied, max_depth=4)
        if path:
            retreat = self._build_retreat_plan(agv, path)
            if retreat is not None:
                agv.plan = retreat
                agv.plan_version += 1
                agv.needs_replan = False
                return
        # 实在找不到安全格：就地停下，占住当前格点等待地图恢复
        agv.plan = None
        agv.needs_replan = False
        self._hold_cell(agv)

    def _replan_task_from_current(self, agv: AgvRuntime, reason: str) -> None:
        agv.leg = "pickup"
        self._replan(agv, reason)

    # ============================================================ 死锁
    def _live_blocker(self, agv: AgvRuntime) -> str | None:
        """当前真正挡住该 AGV 的车辆。"""
        plan = agv.plan
        if plan is None:
            # 暂时没有计划（等待重规划）：阻塞对象是上次试图进入的格点上的车辆
            if agv.blocked_target:
                occupant = self._occupant_of(agv.blocked_target)
                if occupant and occupant != agv.id:
                    return occupant
            if not agv.active_task_id:
                return None
            task = self._task(agv.active_task_id)
            if not task:
                return None
            leg = agv.leg if agv.leg in ("pickup", "dropoff") else "pickup"
            location_id = task["pickup_location_id"] if leg == "pickup" else task["drop_location_id"]
            location = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (location_id,))
            if not location:
                return None
            occupant = self._occupant_of(cell_id(location["x"], location["y"]))
            return occupant if occupant != agv.id else None
        future = [s for s in plan.steps if s.tick > self.tick]
        if not future:
            return None
        move_step = next((s for s in future if s.action == "move"), future[0])
        target = move_step.cell
        tick = move_step.tick
        # 1) 目标格点被实际占用的车辆：这是最直接的阻塞关系
        occupant = self._occupant_of(target)
        if occupant and occupant != agv.id:
            return occupant
        map_id = self.maps.current.map_id
        # 2) 预约持有者
        holder = self.reservations.owner_of(vertex_key(map_id, target, tick))
        if holder and holder != agv.id:
            return holder
        previous = self._previous_cell(plan, move_step)
        holder = self.reservations.owner_of(edge_key(map_id, target, previous, tick))
        if holder and holder != agv.id:
            return holder
        holder = self.reservations.owner_of(edge_key(map_id, previous, target, tick))
        if holder and holder != agv.id:
            return holder
        return None

    def _occupant_of(self, cell: str) -> str | None:
        """当前实际停在某个格点上的 AGV。"""
        for other in self.agvs.values():
            if other.cell == cell:
                return other.id
        return None

    def _agv_goal_cell(self, agv: AgvRuntime) -> str | None:
        """AGV 当前阶段的目标格点（取货点或放货点）。"""
        task = self._task(agv.active_task_id)
        if not task:
            return None
        leg = agv.leg if agv.leg in ("pickup", "dropoff") else "pickup"
        location_id = task["pickup_location_id"] if leg == "pickup" else task["drop_location_id"]
        location = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (location_id,))
        if not location:
            return None
        return cell_id(location["x"], location["y"])

    def _goal_is_temporarily_blocked(self, agv: AgvRuntime, goal: str) -> bool:
        """目标格点只是暂时被占用（别人挡路）而不是真正不可达。"""
        occupant = self._occupant_of(goal)
        if occupant and occupant != agv.id:
            return True
        return self.reservations.is_static_block(goal, agv.id)

    def _handle_deadlock(self) -> None:
        graph = WaitGraph()
        for agv in sorted(self.agvs.values(), key=lambda a: a.id):
            if agv.status in ("fault", "offline", "charging"):
                continue
            graph.set_blocker(agv.id, self._live_blocker(agv))
        self.wait_graph = graph
        cycle = graph.find_cycle()
        if not cycle:
            return
        # 只有环内车辆都持续阻塞一段时间才认定为死锁，避免把瞬时等待误判成死锁
        if not all(self.agvs[a].block_ticks >= config.DEADLOCK_MIN_BLOCK_TICKS for a in cycle if a in self.agvs):
            return

        signature = tuple(sorted(cycle))
        if signature == self._last_deadlock_signature and self.tick - self._last_deadlock_tick < config.DEADLOCK_COOLDOWN:
            return
        self._last_deadlock_signature = signature
        self._last_deadlock_tick = self.tick

        self.stats["deadlocks"] += 1
        self._emit("deadlock.detected", None, {"cycle": cycle})
        self._alert("deadlock", f"检测到等待环：{' -> '.join(cycle)}", cycle)

        occupied = {a.cell for a in self.agvs.values()}
        can_rollback: dict[str, bool] = {}
        for agv_id in cycle:
            agv = self.agvs[agv_id]
            own = agv.cell
            path = find_safe_cell(
                self.maps.current, own, occupied - {own}, goal=self._agv_goal_cell(agv)
            )
            can_rollback[agv_id] = path is not None

        victim_id = choose_victim(cycle, self.agvs, can_rollback)
        if victim_id is None:
            self.stats["recoveries"] += 1
            self._emit("deadlock.resolved", None, {"cycle": cycle, "action": "no-victim"})
            return
        victim = self.agvs[victim_id]
        if not can_rollback.get(victim_id):
            # 环内没有可以回退的车辆：按上限策略放弃优先级最低的任务
            self._fail_task(victim, "死锁且无法回退")
            self.stats["recoveries"] += 1
            self._emit("deadlock.resolved", None, {"cycle": cycle, "victim": victim_id, "action": "fail-task"})
            return

        victim.rollback_count += 1
        if victim.rollback_count > config.MAX_ROLLBACK:
            self._fail_task(victim, "回退次数超过上限")
            self.stats["recoveries"] += 1
            self._emit("deadlock.resolved", None, {"cycle": cycle, "victim": victim_id, "action": "max-rollback"})
            return

        own = victim.cell
        path = find_safe_cell(
            self.maps.current, own, occupied - {own}, goal=self._agv_goal_cell(victim)
        )
        if not path:
            self._fail_task(victim, "找不到安全回退格")
            return

        # 先释放牺牲者自己尚未确认的预约，再申请回退路径
        self.reservations.release_agv(victim.id, keep_from_tick=self.tick + 1)
        retreat = self._build_retreat_plan(victim, path)
        if retreat is None:
            self._fail_task(victim, "回退路径不可达")
            return
        victim.plan = retreat
        victim.plan_version += 1
        victim.leg = "retreat"
        victim.needs_replan = False
        self.stats["rollbacks"] += 1
        self.stats["recoveries"] += 1
        self._emit("rollback.executed", victim.id, {"path": path, "rollbackCount": victim.rollback_count})
        self._emit("deadlock.resolved", None, {"cycle": cycle, "victim": victim.id, "action": "rollback"})

    def _build_retreat_plan(self, agv: AgvRuntime, path: list[str]) -> PathPlan | None:
        """把回退路径包装成一条简单计划（只做移动）。"""
        steps = [PlanStep(tick=self.tick, cell=agv.cell, x=agv.x, y=agv.y, action="wait")]
        edges = []
        for index, cid in enumerate(path):
            cell = self.maps.current.cells.get(cid)
            if cell is None:
                return None
            steps.append(PlanStep(tick=self.tick + index + 1, cell=cid, x=cell.x, y=cell.y, action="move"))
            edges.append({"tick": self.tick + index + 1, "from": path[index - 1] if index else agv.cell, "to": cid})
        plan = PathPlan(
            task_id=agv.active_task_id or "", agv_id=agv.id, map_version=self.maps.current.version,
            plan_version=agv.plan_version + 1, steps=steps, edges=edges,
            estimated_ticks=len(path), wait_ticks=0, kind="retreat", goal_cell=path[-1],
        )
        ok, _conflicts, _ = self.reservations.acquire(
            agv.id, plan.plan_version, agv.task_priority or 0, plan.reservations(self.maps.current.map_id), self.tick
        )
        return plan if ok else None

    def _check_block_timeout(self, agv: AgvRuntime) -> None:
        """连续阻塞超过上限时放弃任务，避免活锁。"""
        if agv.block_ticks <= config.MAX_BLOCK_TICKS:
            return
        self._fail_task(agv, "阻塞时间超过上限")

    def install_initial_plan(self, agv: AgvRuntime, task, steps: list[dict], goal: str) -> bool:
        """安装场景给定的初始路径（用于固定冲突/死锁场景）。

        steps 形如 `[{"cell": "4,3", "action": "move"}, ...]`，tick 从 0 递增。
        """
        plan_steps: list[PlanStep] = []
        for index, raw in enumerate(steps):
            cid = raw["cell"]
            cell = self.maps.current.cells.get(cid)
            if cell is None:
                return False
            plan_steps.append(
                PlanStep(tick=index, cell=cid, x=cell.x, y=cell.y, action=raw.get("action", "move"))
            )
        if not plan_steps:
            return False
        edges = []
        for index in range(1, len(plan_steps)):
            if plan_steps[index].action == "move":
                edges.append({
                    "tick": plan_steps[index].tick,
                    "from": plan_steps[index - 1].cell,
                    "to": plan_steps[index].cell,
                })
        plan = PathPlan(
            task_id=task["id"], agv_id=agv.id, map_version=self.maps.current.version,
            plan_version=1, steps=plan_steps, edges=edges,
            estimated_ticks=len(plan_steps) - 1, wait_ticks=0, kind="task", goal_cell=goal,
        )
        ok, conflicts, _ = self.reservations.acquire(
            agv.id, 1, task["priority"], plan.reservations(self.maps.current.map_id), self.tick
        )
        if not ok:
            # 初始路径与已有预约冲突：退回常规流程，由时空 A* 自行等待绕行
            self.stats["conflicts"] += 1
            self._emit("conflict.detected", task["id"], {
                "agvId": agv.id, "reason": "初始路径与现有预约冲突", "keys": conflicts[:5],
            })
            agv.active_task_id = task["id"]
            agv.task_priority = task["priority"]
            agv.needs_replan = True
            agv.replan_reason = "初始路径与现有预约冲突"
            self._set_status(agv, "to_pickup")
            return False
        agv.plan = plan
        agv.plan_version = 1
        agv.leg = "pickup"
        agv.active_task_id = task["id"]
        agv.task_priority = task["priority"]
        self._set_status(agv, "to_pickup")
        return True

    # ============================================================ 注入
    def _apply_injections(self) -> None:
        """处理排队的注入事件（atTick 到达时执行）。"""
        if not self._pending_injections:
            return
        pending = self._pending_injections
        self._pending_injections = []
        for item in pending:
            if item.get("atTick") is not None and int(item["atTick"]) > self.tick:
                self._pending_injections.append(item)
                continue
            self.inject_event(item)

    def inject_event(self, body: dict) -> dict:
        """立即执行一次事件注入。"""
        kind = body.get("type")
        if kind in ("fault", "offline"):
            target = body.get("target")
            if not target:
                raise ValidationError("缺少 target（AGV id）", {"field": "target"})
            return self.inject_fault(target, body.get("code", "E-SENSOR"), kind)
        if kind == "recover":
            target = body.get("target")
            if not target:
                raise ValidationError("缺少 target（AGV id）", {"field": "target"})
            return self.recover_agv(target)
        if kind == "roadblock":
            cells = body.get("cells")
            if not isinstance(cells, list) or not cells:
                raise ValidationError("封路需要 cells 数组", {"field": "cells"})
            self.apply_roadblock([str(c) for c in cells], body.get("reason", ""))
            return {"mapVersion": self.maps.current.version, "cells": cells}
        if kind == "rush_order":
            code = body.get("code") or f"OUT-RUSH-{self.tick:04d}"
            priority = int(body.get("priority", 10))
            sku_code = body.get("skuCode", "SKU-001")
            qty = int(body.get("qty", 1))
            requester = body.get("requester", "rush")
            order = self.service.create_order({
                "code": code, "priority": priority, "requester": requester,
                "lines": [{"skuCode": sku_code, "qty": qty}],
            })
            released = self.service.release_order(order["id"])
            self._emit("sim.injected", code, {"type": "rush_order", "priority": priority, "orderId": code})
            return released
        raise ValidationError("不支持的注入类型", {"type": kind})

    # ============================================================ 故障与恢复
    def inject_fault(self, agv_id: str, code: str = "E-SENSOR", status: str = "fault") -> dict:
        agv = self.agvs.get(agv_id)
        if agv is None:
            raise NotFoundError(f"AGV 不存在：{agv_id}", {"agvId": agv_id})
        with self.lock:
            previous = agv.status
            agv.fault_code = code
            agv.plan = None
            agv.leg = ""
            agv.dwell_ticks = 0
            self.reservations.release_agv(agv.id)
            self._set_status(agv, status, force=True)
            agv.fault_code = code
            self._emit("agv.fault", agv.id, {"code": code, "status": status, "previous": previous})
            if agv.active_task_id:
                task_id = agv.active_task_id
                self.service.reassign_task(task_id)
                agv.active_task_id = None
                agv.task_priority = None
                self._emit("task.cancelled", task_id, {"reason": "AGV 故障", "agvId": agv.id})
            return agv.to_dict()

    def recover_agv(self, agv_id: str) -> dict:
        agv = self.agvs.get(agv_id)
        if agv is None:
            raise NotFoundError(f"AGV 不存在：{agv_id}", {"agvId": agv_id})
        with self.lock:
            agv.fault_code = None
            agv.plan = None
            agv.leg = ""
            agv.dwell_ticks = 0
            self.reservations.release_agv(agv.id)
            self._set_status(agv, "idle", force=True)
            self._emit("agv.state.changed", agv.id, {"status": "idle", "reason": "人工恢复"})
            return agv.to_dict()

    def release_agv_for_task(self, agv_id: str, task_id: str) -> None:
        """任务被取消或重派时释放占用它的 AGV。"""
        agv = self.agvs.get(agv_id)
        if agv is None:
            return
        if agv.active_task_id == task_id:
            agv.active_task_id = None
            agv.task_priority = None
            agv.plan = None
            agv.leg = ""
            agv.dwell_ticks = 0
            agv.rollback_count = 0
            self.reservations.release_agv(agv.id)
            if agv.status not in ("fault", "offline"):
                self._set_status(agv, "idle")

    # ============================================================ 封路
    def apply_roadblock(self, cells: list[str], reason: str = "") -> None:
        with self.lock:
            old_version = self.maps.current.version
            new_map, record_id = self.maps.add_roadblock(cells, reason)
            self.reservations.map_id = new_map.map_id
            self._emit("map.roadblock.added", record_id, {"cells": cells, "reason": reason})
            self._emit("map.version.changed", new_map.map_id, {
                "from": old_version, "to": new_map.version, "cells": cells,
            })
            self._replan_after_map_change(old_version)

    def remove_roadblock(self, record_id: str) -> None:
        with self.lock:
            old_version = self.maps.current.version
            new_map, cell = self.maps.remove_roadblock(record_id)
            self.reservations.map_id = new_map.map_id
            self._emit("map.roadblock.removed", record_id, {"cell": cell})
            self._emit("map.version.changed", new_map.map_id, {
                "from": old_version, "to": new_map.version, "cells": [cell],
            })
            self._replan_after_map_change(old_version)

    def _replan_after_map_change(self, old_version: str) -> None:
        """地图变化后，只重规划受影响路径（未受影响的保留）。"""
        new_map = self.maps.current
        blocked = {row["cell_id"] for row in self.maps.active_roadblocks()}
        for agv in sorted(self.agvs.values(), key=lambda a: a.id):
            if agv.plan is None:
                continue
            remaining = [s for s in agv.plan.steps if s.tick > self.tick]
            affected = any(s.cell in blocked or not new_map.is_passable(s.cell) for s in remaining)
            if not affected:
                continue
            if agv.active_task_id:
                self._replan(agv, "地图版本变化")
            else:
                # 空闲车辆也要重规划，否则会沿着旧计划开进封路格
                self._replan_idle_after_map_change(agv)
            self._emit("map.version.changed", new_map.map_id, {
                "agvId": agv.id, "replanned": True, "version": new_map.version,
            })

    # ============================================================ 工具
    def _hold_cell(self, agv: AgvRuntime) -> None:
        """车辆静止时持续占住当前格点，避免其它车规划进入同一格。"""
        entries = [ReservationEntry(kind="vertex", cell_from=agv.cell, cell_to="", tick=self.tick)]
        self.reservations.acquire(
            agv.id, agv.plan_version, agv.task_priority or 0, entries, self.tick, allow_preempt=False
        )

    def _update_static_blocks(self) -> None:
        """刷新静止占位表。

        规则：只有"本 tick 确实要移动"的车辆才可以被别人规划穿过；
        正在等待（计划为空、计划到点、或下一步是等待）的车辆占住当前格点，
        其它车辆的规划必须避让，避免出现"计划进入别人身上"的抖动。
        """
        for agv in self.agvs.values():
            plan = agv.plan
            will_move = False
            if plan is not None:
                for step in plan.steps:
                    if step.tick < self.tick:
                        continue
                    if step.tick == self.tick:
                        will_move = step.action == "move"
                    break
            if will_move:
                self.reservations.clear_static_block(agv.id)
            else:
                self.reservations.set_static_block(agv.cell, agv.id)

    def _set_status(self, agv: AgvRuntime, status: str, force: bool = False) -> None:
        if agv.status == status:
            return
        allowed = AGV_STATUS_TRANSITIONS.get(agv.status, set())
        if not force and status not in allowed:
            self._emit("agv.state.changed", agv.id, {
                "from": agv.status, "to": status, "rejected": True,
                "reason": "状态流转不被允许",
            })
            return
        previous = agv.status
        agv.status = status
        agv.version += 1
        self._emit("agv.state.changed", agv.id, {"from": previous, "to": status, "taskId": agv.active_task_id})

    def _task(self, task_id: str | None):
        if not task_id:
            return None
        return self.db.query_one("SELECT * FROM tasks WHERE id = ?", (task_id,))

    def _task_status(self, task_id: str) -> str:
        row = self._task(task_id)
        return row["status"] if row else ""

    def _task_sku(self, task_id: str | None) -> str | None:
        row = self._task(task_id)
        if not row:
            return None
        line = self.db.query_one(
            "SELECT sku_id FROM order_lines WHERE order_id = ? ORDER BY id LIMIT 1", (row["order_id"],)
        )
        if not line:
            return None
        sku = self.db.query_one("SELECT code FROM sku WHERE id = ?", (line["sku_id"],))
        return sku["code"] if sku else None

    def _emit(self, event_type: str, aggregate_id: str | None, payload: dict) -> None:
        event = self.bus.publish(event_type, aggregate_id, payload, self.tick)
        self.db.execute(
            """INSERT INTO events (id, type, tick, aggregate_id, payload, occurred_at, seq)
               VALUES (?,?,?,?,?,?,?)""",
            (event["id"], event["type"], event["tick"], event["aggregateId"], dumps(payload),
             event["occurredAt"], event["seq"]),
        )

    def _alert(self, kind: str, message: str, agv_ids: list[str] | None = None) -> None:
        severity = "error" if kind in ("task_failed", "deadlock", "fault", "offline") else "warn"
        alert = {
            "id": new_id("alert"),
            "kind": kind,
            "code": kind,
            "severity": severity,
            "message": message,
            "tick": self.tick,
            "agvIds": agv_ids or [],
            "agvId": (agv_ids or [None])[0],
            "occurredAt": now_iso(),
        }
        self.alerts.append(alert)
        if len(self.alerts) > 200:
            self.alerts.pop(0)
        self._emit("alert", alert["id"], {"kind": kind, "message": message, "agvIds": agv_ids or []})

    # ============================================================ 运行态持久化
    def _persist_runtime(self) -> None:
        ts = now_iso()
        for agv in self.agvs.values():
            self.db.execute(
                """UPDATE agvs SET status=?, x=?, y=?, battery=?, active_task_id=?, payload_sku=?,
                                    last_tick=?, fault_code=?, version=?, updated_at=? WHERE id=?""",
                (agv.status, agv.x, agv.y, round2(agv.battery), agv.active_task_id, agv.payload_sku,
                 agv.last_tick, agv.fault_code, agv.version, ts, agv.id),
            )
            self.db.execute("DELETE FROM agv_trail WHERE agv_id = ?", (agv.id,))
            if agv.trail:
                self.db.executemany(
                    "INSERT INTO agv_trail (agv_id, tick, x, y) VALUES (?,?,?,?)",
                    [(agv.id, t, x, y) for t, x, y in agv.trail[-config.TRAIL_LENGTH:]],
                )
        self.db.execute(
            """INSERT INTO sim_state (id, tick, running, speed, seed, scenario_id, map_version, last_event_seq, updated_at)
               VALUES (1,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET tick=excluded.tick, running=excluded.running, speed=excluded.speed,
                   seed=excluded.seed, scenario_id=excluded.scenario_id, map_version=excluded.map_version,
                   last_event_seq=excluded.last_event_seq, updated_at=excluded.updated_at""",
            (self.tick, 1 if self.running else 0, self.speed, self.seed, self.scenario_id,
             self.maps.current.version, self.bus.seq, ts),
        )
        self.reservations.persist(self.db)

    def load_runtime(self) -> None:
        """从数据库恢复运行态（重启后继续）。"""
        rows = self.db.query("SELECT * FROM agvs")
        self.agvs = {}
        for row in rows:
            trail = [
                (t["tick"], t["x"], t["y"])
                for t in self.db.query(
                    "SELECT tick, x, y FROM agv_trail WHERE agv_id = ? ORDER BY tick", (row["id"],)
                )
            ]
            agv = AgvRuntime(
                id=row["id"], map_id=row["map_id"], status=row["status"], x=row["x"], y=row["y"],
                battery=row["battery"], active_task_id=row["active_task_id"], payload_sku=row["payload_sku"],
                last_tick=row["last_tick"], fault_code=row["fault_code"], version=row["version"], trail=trail,
            )
            if agv.active_task_id:
                task = self._task(agv.active_task_id)
                agv.task_priority = task["priority"] if task else None
            self.agvs[agv.id] = agv

        state = self.db.query_one("SELECT * FROM sim_state WHERE id = 1")
        if state:
            self.tick = state["tick"]
            self.speed = state["speed"]
            self.seed = state["seed"]
            self.scenario_id = state["scenario_id"]
            self.bus.set_seq(state["last_event_seq"])

        reservations = [
            Reservation(
                key=r["key"], kind=r["kind"], cell_from=r["cell_from"], cell_to=r["cell_to"], tick=r["tick"],
                agv_id=r["agv_id"], plan_version=r["plan_version"], priority=r["priority"],
                created_tick=r["created_tick"], expires_tick=r["expires_tick"],
            )
            for r in self.db.query("SELECT * FROM reservations")
        ]
        self.reservations.restore(reservations)
        self.reservations.map_id = self.maps.current.map_id

    # ============================================================ 快照
    def snapshot(self, include_alerts: bool = True) -> dict:
        from .service import task_to_dict

        tasks = [task_to_dict(t) for t in self.db.query(
            "SELECT * FROM tasks ORDER BY priority DESC, created_at ASC, id ASC")]
        for task in tasks:
            pickup = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["pickupLocationId"],))
            drop = self.db.query_one("SELECT x, y FROM location WHERE id = ?", (task["dropLocationId"],))
            task["pickupCell"] = f"{pickup['x']},{pickup['y']}" if pickup else ""
            task["dropCell"] = f"{drop['x']},{drop['y']}" if drop else ""
        orders = self.service.all_orders()
        stats = dict(self.stats)
        stats["completedTasks"] = self.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'completed'")["c"]
        stats["failedTasks"] = self.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'failed'")["c"]
        stats["cancelledTasks"] = self.db.query_one(
            "SELECT COUNT(*) AS c FROM tasks WHERE status = 'cancelled'")["c"]
        return {
            "tick": self.tick,
            "running": self.running,
            "speed": self.speed,
            "seed": self.seed,
            "scenarioId": self.scenario_id,
            "mapVersion": self.maps.current.version,
            "agvs": [self.agvs[a].to_dict() for a in sorted(self.agvs)],
            "tasks": tasks,
            "orders": orders,
            "alerts": list(self.alerts[-50:]) if include_alerts else [],
            "stats": stats,
            "lastEventSeq": self.bus.seq,
        }
