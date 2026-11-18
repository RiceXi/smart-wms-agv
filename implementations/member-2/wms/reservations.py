"""预约表。

预约键遵循公共契约 `docs/data-schema.md` 第 9 节：

- 格点：`vertex:{map}:{cell}:{tick}`
- 有向边：`edge:{map}:{from}:{to}:{tick}`

规则：

1. 批量预约必须原子获取：任一键冲突则整批失败，不允许部分占用。
2. 到达或通过后释放，并有兜底 TTL。
3. 同一 tick 不允许同格占用，也不允许相向穿边（反向边键同样参与冲突判定）。
"""

from __future__ import annotations

from dataclasses import dataclass

from . import config


def vertex_key(map_id: str, cell: str, tick: int) -> str:
    return f"vertex:{map_id}:{cell}:{tick}"


def edge_key(map_id: str, from_cell: str, to_cell: str, tick: int) -> str:
    return f"edge:{map_id}:{from_cell}:{to_cell}:{tick}"


@dataclass(frozen=True)
class ReservationEntry:
    """一条待申请的预约。"""

    kind: str  # vertex | edge
    cell_from: str
    cell_to: str
    tick: int

    def key(self, map_id: str) -> str:
        if self.kind == "vertex":
            return vertex_key(map_id, self.cell_from, self.tick)
        return edge_key(map_id, self.cell_from, self.cell_to, self.tick)


@dataclass
class Reservation:
    """已生效的预约。"""

    key: str
    kind: str
    cell_from: str
    cell_to: str
    tick: int
    agv_id: str
    plan_version: int
    priority: int
    created_tick: int
    expires_tick: int


class ReservationStore:
    """内存态预约表。仿真主循环单线程推进，写入天然原子。

    每次 tick 结束会把仍然有效的预约同步到 SQLite，便于重启恢复。
    """

    def __init__(self, map_id: str):
        self.map_id = map_id
        self._by_key: dict[str, Reservation] = {}
        self._by_agv: dict[str, set[str]] = {}
        self._static_blocks: dict[str, str] = {}
        """静止车辆占位：不会自行移动的车辆所在格点，规划时必须避让。

        预约键是时间相关的（cell, tick），无法表达"这辆车一直停在这里"，
        因此单独维护静态占位表，保证不会出现两车停进同一格。
        """

    # ------------------------------------------------------------ 静态占位
    def set_static_block(self, cell: str, agv_id: str) -> None:
        self._static_blocks[cell] = agv_id

    def clear_static_block(self, agv_id: str) -> None:
        for cell in [c for c, owner in self._static_blocks.items() if owner == agv_id]:
            del self._static_blocks[cell]

    def is_static_block(self, cell: str, agv_id: str | None = None) -> bool:
        owner = self._static_blocks.get(cell)
        return owner is not None and owner != agv_id

    def static_blocks(self) -> dict[str, str]:
        return dict(self._static_blocks)


    # ------------------------------------------------------------ 查询
    def get(self, key: str) -> Reservation | None:
        return self._by_key.get(key)

    def owner_of(self, key: str) -> str | None:
        reservation = self._by_key.get(key)
        return reservation.agv_id if reservation else None

    def entries_for(self, agv_id: str) -> list[Reservation]:
        keys = self._by_agv.get(agv_id, set())
        return sorted(
            (self._by_key[k] for k in keys if k in self._by_key),
            key=lambda r: (r.tick, r.kind, r.cell_from, r.cell_to),
        )

    def conflict_keys(self, entries: list[ReservationEntry], agv_id: str) -> list[str]:
        """返回会与给定预约集合冲突的键（含反向边）。"""
        conflicts: list[str] = []
        for entry in entries:
            key = entry.key(self.map_id)
            holder = self._by_key.get(key)
            if holder and holder.agv_id != agv_id:
                conflicts.append(key)
                continue
            if entry.kind == "edge":
                reverse = edge_key(self.map_id, entry.cell_to, entry.cell_from, entry.tick)
                holder = self._by_key.get(reverse)
                if holder and holder.agv_id != agv_id:
                    conflicts.append(reverse)
        return conflicts

    def blockers_of(self, entries: list[ReservationEntry], agv_id: str) -> dict[str, str]:
        """返回冲突键 -> 持有者 AGV。"""
        blockers: dict[str, str] = {}
        for entry in entries:
            key = entry.key(self.map_id)
            holder = self._by_key.get(key)
            if holder and holder.agv_id != agv_id:
                blockers[key] = holder.agv_id
            if entry.kind == "edge":
                reverse = edge_key(self.map_id, entry.cell_to, entry.cell_from, entry.tick)
                holder = self._by_key.get(reverse)
                if holder and holder.agv_id != agv_id:
                    blockers[reverse] = holder.agv_id
        return blockers

    # ------------------------------------------------------------ 写入
    def acquire(
        self,
        agv_id: str,
        plan_version: int,
        priority: int,
        entries: list[ReservationEntry],
        current_tick: int,
        allow_preempt: bool = True,
    ) -> tuple[bool, list[str], list[str]]:
        """原子申请一组预约。

        返回 (是否成功, 冲突键列表, 被抢占的 AGV 列表)。

        抢占规则：高优先级任务可以抢占低优先级 AGV 的**未来**预约，
        被抢占的 AGV 需要重新规划。冲突发生在过去 tick 时不抢占，直接失败。
        """
        conflicts = self.conflict_keys(entries, agv_id)
        if not conflicts:
            self._write(agv_id, plan_version, priority, entries, current_tick)
            return True, [], []

        if not allow_preempt:
            return False, conflicts, []

        preempted: dict[str, int] = {}
        for key in conflicts:
            holder = self._by_key.get(key)
            if holder is None or holder.agv_id == agv_id:
                continue
            if holder.priority >= priority:
                # 同优先级或更高优先级的预约不可抢占：必须等待或绕行
                return False, conflicts, []
            if holder.tick <= current_tick:
                # 已经到达的预约不可抢占（历史事实）
                return False, conflicts, []
            preempted[holder.agv_id] = min(preempted.get(holder.agv_id, holder.tick), holder.tick)

        for victim, from_tick in preempted.items():
            # 被抢占的车辆要释放"从冲突 tick 起"的未来预约，
            # 它的历史部分保留（已经是事实），随后由调用方安排重新规划。
            self.release_agv(victim, release_from_tick=from_tick)

        # 抢占后重新检测，确认整批可获取
        remaining = self.conflict_keys(entries, agv_id)
        if remaining:
            return False, remaining, sorted(preempted)
        self._write(agv_id, plan_version, priority, entries, current_tick)
        return True, [], sorted(preempted)

    def _write(self, agv_id: str, plan_version: int, priority: int, entries: list[ReservationEntry], current_tick: int) -> None:
        for entry in entries:
            key = entry.key(self.map_id)
            reservation = Reservation(
                key=key,
                kind=entry.kind,
                cell_from=entry.cell_from,
                cell_to=entry.cell_to,
                tick=entry.tick,
                agv_id=agv_id,
                plan_version=plan_version,
                priority=priority,
                created_tick=current_tick,
                expires_tick=entry.tick + config.RESERVATION_TTL_TICKS,
            )
            self._by_key[key] = reservation
            self._by_agv.setdefault(agv_id, set()).add(key)

    def release_agv(self, agv_id: str, plan_version: int | None = None,
                    keep_from_tick: int | None = None, release_from_tick: int | None = None) -> int:
        """释放某台 AGV 的预约，返回释放条数。

        - 指定 plan_version 时只释放该版本的预约。
        - 指定 keep_from_tick 时保留 tick >= 该值的预约（用于已确认的历史部分）。
        - 指定 release_from_tick 时释放 tick >= 该值的预约（用于被抢占后腾出未来时段）。
        """
        keys = list(self._by_agv.get(agv_id, set()))
        removed = 0
        for key in keys:
            reservation = self._by_key.get(key)
            if reservation is None:
                continue
            if plan_version is not None and reservation.plan_version != plan_version:
                continue
            if keep_from_tick is not None and reservation.tick >= keep_from_tick:
                continue
            if release_from_tick is not None and reservation.tick < release_from_tick:
                continue
            del self._by_key[key]
            removed += 1
        if agv_id in self._by_agv:
            self._by_agv[agv_id] = {k for k in self._by_agv[agv_id] if k in self._by_key}
            if not self._by_agv[agv_id]:
                del self._by_agv[agv_id]
        return removed

    def release_expired(self, current_tick: int) -> int:
        """释放超过兜底 TTL 的预约，防止预约泄漏导致车辆永久阻塞。"""
        expired = [key for key, r in self._by_key.items() if r.expires_tick < current_tick]
        for key in expired:
            reservation = self._by_key.pop(key)
            bucket = self._by_agv.get(reservation.agv_id)
            if bucket:
                bucket.discard(key)
                if not bucket:
                    del self._by_agv[reservation.agv_id]
        return len(expired)

    def release_past(self, current_tick: int, keep_last: int = 1) -> int:
        """释放已经走过的时间片预约，保留最近 keep_last 个 tick 便于冲突回溯。"""
        threshold = current_tick - keep_last
        stale = [
            key for key, r in self._by_key.items()
            if r.tick <= threshold
        ]
        for key in stale:
            reservation = self._by_key.pop(key)
            bucket = self._by_agv.get(reservation.agv_id)
            if bucket:
                bucket.discard(key)
                if not bucket:
                    del self._by_agv[reservation.agv_id]
        return len(stale)

    def clear(self) -> None:
        self._by_key.clear()
        self._by_agv.clear()

    # ------------------------------------------------------------ 持久化
    def snapshot(self) -> list[Reservation]:
        return list(self._by_key.values())

    def restore(self, reservations: list[Reservation]) -> None:
        self.clear()
        for reservation in reservations:
            self._by_key[reservation.key] = reservation
            self._by_agv.setdefault(reservation.agv_id, set()).add(reservation.key)

    def persist(self, db) -> None:
        db.execute("DELETE FROM reservations")
        rows = [
            (r.key, self.map_id, r.kind, r.cell_from, r.cell_to, r.tick, r.agv_id,
             r.plan_version, r.priority, r.created_tick, r.expires_tick)
            for r in self._by_key.values()
        ]
        if rows:
            db.executemany(
                """INSERT INTO reservations
                   (key, map_id, kind, cell_from, cell_to, tick, agv_id, plan_version, priority, created_tick, expires_tick)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
        db.commit()
