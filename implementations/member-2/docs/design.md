# 设计与算法说明（成员二个人版）

本文描述 `implementations/member-2/` 的架构、数据模型和核心算法。
字段语义遵循仓库公共契约 `docs/data-schema.md`，接口形状遵循 `docs/api-contract.md`。

## 1. 分层结构

```text
                    浏览器（web/：原生 JS + Canvas）
                             │  REST + SSE
                    ┌────────┴────────┐
                    │  server.py       │  HTTP / SSE / 静态资源
                    │  api.py          │  路由与响应装配
                    └────────┬────────┘
                             │  App.lock（仿真全局锁）
        ┌────────────────────┼────────────────────┐
        │                    │                    │
   simulator.py         service.py           analytics.py
   （tick 主循环、        （业务事实唯一        （统计口径）
     运动/电量/充电/       写入层：主数据、
     故障/封路/急单）       库存、订单、任务状态机）
        │                    │                    │
        └── pathfinding.py ┘ └── scheduler.py ┘
             │                    │
          reservations.py      deadlock.py
             │                    │
        └──────────── mapdata.py / mapgen.py ────────────┘
                             │
                          db.py（SQLite）
```

关键约束：

1. **业务事实只有一个写入口**：`service.py`。仿真层不直接改订单/任务状态，
   只能调用 `service` 的状态机方法，因此状态流转规则集中、可测。
2. **仿真时间与真实时间分离**：1 tick = 1 秒仿真时间。真实时间只决定"多久推进一个 tick"，
   不参与任何调度判定，因此回放结果与机器性能无关。
3. **地图不可原地修改**：封路/解封生成新的地图版本（`map-1` → `map-1-r1` → …），
   旧版本保留在 `map_versions` 表中，可用于回放与对账。

## 2. 数据模型

SQLite 表（详见 `wms/db.py` 的 `SCHEMA`）：

| 表 | 用途 |
| --- | --- |
| `sku` / `location` / `inventory` / `inventory_txn` | 主数据与库存；`inventory_txn` 记录每一次出入库、预占与释放 |
| `orders` / `order_lines` / `order_allocations` | 订单、明细与库存分配记录 |
| `tasks` / `task_events` | 任务与任务生命周期事件（创建/指派/开始/完成/失败…） |
| `agvs` / `agv_trail` | AGV 当前状态与轨迹（每 tick 一点） |
| `map_versions` / `map_cells` / `map_edges` / `map_stations` | 地图版本快照 |
| `roadblocks` | 封路记录（可解除，保留历史） |
| `reservations` | 预约表快照（重启后恢复） |
| `events` | 全局事件流水（`seq` 单调递增，供 SSE 与统计对账） |
| `sim_state` / `meta` | 仿真时钟、seed、最后事件序号 |

状态机（`service.TASK_TRANSITIONS`）：

```text
任务：pending → assigned → running → completed
                      ↘ pending（仅重派）
      pending/assigned → cancelled
      assigned/running → failed
订单：draft → allocated → released → completed
                           ↘ cancelled / failed
```

`released` 之后的订单再次 `release` 是幂等的，不会重复扣减库存。

## 3. 地图模型

主地图 `warehouse-24`（24×24）：

- 横向通道 `y ∈ {0, 6, 12, 18, 23}`，纵向窄巷 `x ∈ {4, 7, 10, 13, 16, 19}`。
- 货架块 A–D：货架行 `y ∈ {2,3},{8,9},{14,15},{20,21}`，货架列块
  `x ∈ {2,3},{5,6},{8,9},{11,12},{14,15},{17,18},{20,21}`。**货架格不可通行**。
- 8 个拣选点（PICK-A1/A2/B1/B2/C1/C2/D1/D2）、4 个放货点（DROP-1..4）、4 个充电桩（CHG-1..4）。
- 待命区：四角 2×2 区域，车辆空闲后停靠这里，不占用主通道。

辅地图 `ring-3`（10×8）：三角形环路，用于构造三车环形等待。
T1=(7,3)、T2=(8,3)、T3=(8,4)，(7,3)→(8,4) 为显式对角边；另设退避口袋 (6,3) 与出口 (9,4)。

**货架格不可通行 ⇒ AGV 不能直接开到货架前**。因此任务模型是：
AGV 开到"同区域的拣选点"，在拣选点完成取货确认，再扣除对应货架格上的库存。
同一区域有多个拣选点时，选当前在办任务数最少的一个，避免所有车挤到同一工位。

## 4. 时空 A*（`pathfinding.plan_path`）

节点是 `(格点, tick)`，从 `(start, start_tick)` 出发，代价为经过的 tick 数。

- 邻居只取**可通行**邻居（货架、封路格不可进入）。
- 扩展 `(cell, tick)` 时检查三类预约：
  - `vertex:{map}:{cell}:{tick}` —— 目标格点在该 tick 已被占用；
  - `edge:{map}:{from}:{to}:{tick}` —— 该有向边已被占用；
  - 反向边 `edge:{map}:{to}:{from}:{tick}` —— 对方要反向穿过同一条边（相向穿边）。
- 命中冲突时该状态标记 `blocked_by` 并生成"等待"动作，等待本身也要占一个 tick 的格点预约。
- 上限：`PLAN_MAX_TICKS=400`、`PLAN_MAX_EXPANSIONS=20000`。

两级防护：

1. **静态可达性预检** `static_reachable`：忽略时间做一次 BFS，只避开"静止占位"的车辆。
   目标被静止车辆堵死时立刻返回失败，避免时空 A* 在无解空间里爆搜。
2. 展开数上限兜底。

**起点允许不可通行**：如果施工封路正好落在车辆脚下，它必须能开出去；
邻居仍按可通行过滤，因此不会重新进入封路格。

## 5. 预约表（`reservations.ReservationStore`）

预约键遵循公共契约第 9 节：

```text
顶点：vertex:{map_id}:{cell}:{tick}
有向边：edge:{map_id}:{from_cell}:{to_cell}:{tick}
```

规则：

1. **批量原子获取**：一次申请的一组预约必须全部成功；任一键冲突则整批失败，不留部分占用。
2. **抢占**：请求方优先级**严格高于**持有者时才可抢占，且只能抢占"未来 tick"的预约
   （已经到达的预约是历史事实，不可抢占）。被抢占方释放从冲突 tick 起的全部未来预约，
   调度器随后给它重新规划。
3. **同优先级不抢占**：同优先级冲突时返回失败，由上层选择等待或绕行。
4. **TTL 兜底**：`RESERVATION_TTL_TICKS=120`，过期预约自动释放，防止预约泄漏导致永久阻塞。
5. **静止占位表**：预约键带 tick，无法表达"这辆车一直停在这里"，因此单独维护
   `static_block` 表；规划时必须避让这些格点。规则是"本 tick 确实要移动"的车才可被穿过，
   正在等待的车占住自己的格点。

## 6. 指派评分（`scheduler.py`）

```text
cost(agv, task) = W_DISTANCE * 归一化曼哈顿距离
                 + W_LOAD     * 该车在办任务数
                 - W_PRIORITY * 任务优先级
                 + W_BATTERY  * 低电量惩罚
```

权重集中在 `config.py`（`W_DISTANCE=1.0`、`W_LOAD=2.0`、`W_PRIORITY=1.5`、`W_BATTERY=3.0`），
方便与其它成员的实现对比。任务队列按 `priority DESC, created_at ASC` 排序，
因此急单一定排在队首。

## 7. 死锁检测与恢复（`deadlock.py` + `simulator.py`）

1. 每 tick 结束后重建等待图：边 `blocked_agv → blocking_agv`，
   阻塞对象取自"本 tick 想进入但被占用的格点/有向边的持有者"。
2. 用带颜色标记的**确定性 DFS** 找环（节点与邻居都按 id 排序），同样的等待图永远得到同样的环。
3. 判定死锁需要**连续阻塞 ≥ `DEADLOCK_MIN_BLOCK_TICKS=2`**，过滤"瞬时等待"被误判；
   同一个等待环有 `DEADLOCK_COOLDOWN=10` 的冷却，避免反复处理同一环。
4. 选择牺牲者 `choose_victim`：
   1. 先过滤出存在安全回退格的 AGV；
   2. 再在其中选任务优先级最低者，同优先级按 `rollback_count`、AGV id 排序，保证确定性；
   3. 环中谁都不能回退时，把优先级最低者的任务置为 `failed`，避免活锁。
5. 回退格 `find_safe_cell`：BFS 找可通行且未被占用的格点，并按
   `(是否割点, 是否挡在目标路径上, 是否拥挤, 距离)` 排序。
   **割点用 Tarjan 算法求**（`articulation_points`）——回退车停在割点上会把通道堵死。
6. 回退后释放该车的预约并重新规划，任务状态回到 `assigned`（`assigned → pending` 只用于重派）。

## 8. 仿真主循环（`simulator.step_once`）

一个 tick 的顺序：

1. `self.tick += 1`
2. 处理到点的注入事件（场景定时事件 + 手动注入）
3. 指派待办任务（按优先级打分）
4. 推进每台 AGV：移动 / 装卸确认 / 放货确认 / 充电 / 故障 / 离线
5. 释放过期预约，刷新静止占位表
6. 重建等待图 → 检测死锁 → 选牺牲者 → 回退重规划
7. 检查阻塞超时（`MAX_BLOCK_TICKS=90`）→ 任务置 `failed` 并告警
8. 持久化运行态（AGV、时钟、预约）

节拍常量：`LOADING_TICKS=2`、`UNLOADING_TICKS=2`、`TICK_SECONDS=1`。

电量模型：移动 `-0.6%/tick`，待机 `-0.05%/tick`，装卸 `-0.1%/tick`，充电 `+6%/tick`；
低于 `LOW_BATTERY_THRESHOLD=20` 不再接新任务，低于 `CRITICAL_BATTERY=8` 立即请求充电，
充满到 `CHARGE_FULL=95` 回岗。充电桩容量用 `_charger_holders` 保证不被突破。

## 9. 事件流与前端

- `events` 表是唯一事实源，`seq` 单调递增；SSE 每次推送本 tick 产生的全部事件。
- 事件结构遵循公共契约第 11 节：`{id, type, tick, aggregateId, payload, occurredAt, seq}`。
- 前端每收到一条 `state` 消息就拉一次全量快照（`GET /api/v1/sim/state`），
  保证地图与后端一致，不做增量猜测。
- 断线重连必须重新拉全量快照（契约第 11 节要求）。

## 10. 与课程推荐技术栈的对应关系

| 本个人版 | 课程推荐技术栈中的对应物 |
| --- | --- |
| `wms/app.py` 进程内单例 + 全局锁 | Spring Boot 单体服务 + 分布式锁（多实例时） |
| `wms/service.py` 业务写入层 | Service 层 + 事务（`@Transactional`） |
| `wms/db.py`（SQLite） | MySQL + MyBatis/JPA |
| `wms/events.py` 内存事件总线 + `events` 表 | Kafka 事件流（`events` 表即 outbox） |
| `wms/server.py` SSE `/api/v1/stream` | Kafka topic + WebSocket 推送 |
| `wms/reservations.py` 进程内预约表 | Redis 分布式锁/有序集合（预约键天然可映射成 ZSET 成员） |
| `wms/simulator.py` tick 主循环 | 调度服务（Spring Cloud）+ 定时任务 |
| `wms/api.py` 路由 | Spring MVC Controller |
| `wms/analytics.py` 统计 | 统计服务 / 数据仓库汇总 |

关键差异：个人版把 Kafka、Redis、Eureka 的职责都压缩进单进程，
好处是"干净环境零依赖可跑"，代价是无法多实例水平扩展。

## 11. 参数与调参

全部参数集中在 `wms/config.py`，改一个常量即可重跑场景回归。
最常调的几个：

| 参数 | 默认 | 含义 |
| --- | --- | --- |
| `W_DISTANCE/W_LOAD/W_PRIORITY/W_BATTERY` | 1.0/2.0/1.5/3.0 | 指派评分权重 |
| `BATTERY_DRAIN_MOVE` / `BATTERY_CHARGE_RATE` | 0.6 / 6.0 | 移动耗电与充电速率（%/tick） |
| `LOADING_TICKS` / `UNLOADING_TICKS` | 2 / 2 | 装卸确认占用 tick |
| `PLAN_MAX_TICKS` / `PLAN_MAX_EXPANSIONS` | 400 / 20000 | 时空 A* 搜索上限 |
| `RESERVATION_TTL_TICKS` | 120 | 预约 TTL 兜底 |
| `DEADLOCK_MIN_BLOCK_TICKS` / `DEADLOCK_COOLDOWN` | 2 / 10 | 死锁判定门槛与冷却 |
| `MAX_ROLLBACK` / `MAX_BLOCK_TICKS` | 3 / 90 | 单车最大回退次数、最大连续阻塞 tick |
| `REPLAN_COOLDOWN` | 3 | 重规划失败后的冷却 tick |
