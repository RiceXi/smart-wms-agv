# 领域模型

## 1. 模型边界

领域模型统一业务对象、状态枚举和时间单位。MySQL、Redis、Kafka 和前端只能使用本文定义的主键、字段和状态；展示层可以增加只读派生字段，但不能改变领域含义。

## 2. 通用约定

- 主键：`UUID` 字符串，由创建方生成。
- 时间：数据库存储 UTC ISO 8601，前端本地化显示。
- 日期：仿真日期使用 `YYYY-MM-DD`，不处理跨日任务。
- tick：从 0 开始的整数；默认 1 tick = 1 秒仿真时间。
- 坐标：`(x, y)` 为非负整数；地图 cell 是稳定标识。
- 事件版本：topic 或 `eventType` 带 `.v1` 后缀。
- 乐观并发：可变聚合带 `version` 字段，更新时使用 `WHERE version = ?`。
- 幂等：所有命令接口接受 `Idempotency-Key`；Kafka 消费按 `eventId` 去重。

## 3. WMS 实体

### 3.1 SKU

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | UUID，主键 |
| code | string | 全局唯一，长度 1-64 |
| name | string | 必填，长度 1-128 |
| spec | string | 可空，长度 0-128 |
| batch_rule | enum | `NONE`、`FIFO`、`FEFO` |
| created_at / updated_at | string | ISO 8601 |

### 3.2 Location

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | UUID，主键 |
| code | string | 全局唯一 |
| zone | string | 区域代码 |
| x / y | integer | 地图坐标，非负 |
| type | enum | `FLOOR`、`SHELF`、`PICK_STATION`、`DROP_STATION`、`CHARGER`、`BLOCKED` |
| capacity | integer | 大于 0 |
| map_version | string | 与地图版本一致 |

### 3.3 Inventory

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | UUID，主键 |
| sku_id | string | 外键 |
| location_id | string | 外键 |
| qty | integer | 不得小于 0 |
| batch | string | 可空，同一 SKU/库位批次唯一 |
| expire_at | string | 可空，日期时间 |
| version | integer | 乐观锁 |

同一 `sku_id + location_id + batch` 是唯一库存批次。任何扣减必须在事务内完成，失败时不能留下部分扣减。

### 3.4 OutboundOrder

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | UUID |
| code | string | 业务单号，唯一 |
| type | enum | 当前只实现 `OUTBOUND` |
| status | enum | 见下表 |
| priority | integer | 1-10，数值越大优先级越高 |
| requester | string | 必填 |
| lines | list | 至少一条出库明细 |
| created_at / updated_at | string | ISO 8601 |

订单状态：

| 状态 | 含义 | 允许流转 |
| --- | --- | --- |
| DRAFT | 草稿，尚未占用库存 | `RELEASED`、`CANCELLED` |
| ALLOCATED | 库存和库位已预占 | `RELEASED`、`CANCELLED` |
| RELEASED | 已生成任务事件 | `COMPLETED`、`FAILED` |
| COMPLETED | 所有明细任务完成 | 终态 |
| CANCELLED | 用户取消 | 终态 |
| FAILED | 无法完成任务 | 终态 |

## 4. 任务实体

### 4.1 Task

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | UUID |
| order_id | string | 来源订单 |
| type | enum | 当前只实现 `PICK_AND_DROP` |
| priority | integer | 由订单优先级和人工调整合并 |
| status | enum | 见任务状态机 |
| pickup_location_id | string | 取货库位 |
| drop_location_id | string | 放货点 |
| assigned_agv_id | string | 可空 |
| plan_version | integer | dispatch 生成的路径版本 |
| due_at | string | 可空，超时判定 |
| retry_count | integer | 不得无限增加 |
| version | integer | 乐观锁 |

任务状态机：

```text
PENDING -> ASSIGNED -> RUNNING -> COMPLETED
   |           |           |
   +-----------+           +--> FAILED
   |
   +--> CANCELLED

ASSIGNED -> PENDING    仅在任务重派或 AGV 失效且撤销当前计划后允许
```

约束：

- `PENDING` 只能由 dispatch 置为 `ASSIGNED`，或由授权操作者取消。
- `ASSIGNED` 进入 `RUNNING` 需要 sim 的 AGV 确认事件。
- `RUNNING` 只有 sim 的完成事件可以置为 `COMPLETED`。
- `COMPLETED`、`CANCELLED`、`FAILED` 为终态。
- 每次路径重规划增加 `plan_version`，旧版本事件必须丢弃。
- 任务不能同时指派给两台 AGV。

### 4.2 TaskEvent

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | string | 事件 ID，消费幂等键 |
| task_id | string | 任务 ID |
| type | string | `CREATED`、`ASSIGNED`、`STARTED`、`REPLANNED`、`COMPLETED`、`FAILED`、`CANCELLED` |
| from_status / to_status | string | 状态变化 |
| agv_id | string | 可空 |
| plan_version | integer | 与任务当前版本一致 |
| payload | object | 事件专属字段 |
| occurred_at | string | UTC |

## 5. AGV 实体

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| id | string | 稳定 ID |
| map_id | string | 所属地图 |
| status | enum | 见下表 |
| x / y | integer | 当前节点坐标 |
| battery | number | 0-100 |
| payload_sku | string | 可空 |
| active_task_id | string | 可空 |
| last_tick | integer | 最后确认的 tick |
| fault_code | string | 可空 |
| version | integer | 状态版本 |

AGV 状态：

| 状态 | 含义 | 可进入 |
| --- | --- | --- |
| IDLE | 空闲，可接任务 | IDLE、TO_PICKUP、CHARGING |
| TO_PICKUP | 前往取货点 | TO_PICKUP、LOADING、IDLE、FAULT |
| LOADING | 取货确认 | TO_DROP_OFF、FAULT |
| TO_DROP_OFF | 前往放货点 | TO_DROP_OFF、UNLOADING、IDLE、FAULT |
| UNLOADING | 放货确认 | IDLE、CHARGING、FAULT |
| CHARGING | 充电 | IDLE、FAULT |
| FAULT | 故障 | IDLE（人工恢复后） |
| OFFLINE | 失联 | IDLE、FAULT |

## 6. 调度实体

### 6.1 PathPlan

```json
{
  "taskId": "task-1",
  "agvId": "agv-07",
  "mapVersion": "map-1",
  "planVersion": 3,
  "ticks": [
    {"tick": 100, "x": 2, "y": 8, "action": "MOVE"},
    {"tick": 101, "x": 3, "y": 8, "action": "MOVE"}
  ],
  "edges": [
    {"tick": 100, "from": "2,8", "to": "3,8"}
  ],
  "estimatedTicks": 24,
  "waitTicks": 2
}
```

### 6.2 Reservation

| 键 | 值 | TTL |
| --- | --- | --- |
| `vertex:{map_id}:{cell}:{tick}` | `agv_id:plan_version` | 到达后释放，并有兜底 TTL |
| `edge:{map_id}:{from}:{to}:{tick}` | `agv_id:plan_version` | 通过后释放，并有兜底 TTL |

预约必须原子获取。失败时调度器返回冲突原因，不得部分保留预约。

### 6.3 Deadlock

等待边由 `blocked_agv -> blocking_agv` 表示。检测到环后：

1. 选择环中优先级最低且可回退的 AGV。
2. 检查回退格是否仍被其他车辆占用。
3. 释放该 AGV 尚未确认的预约。
4. 回退一格或进入安全等待点。
5. 增加 `rollback_count` 并重新规划。

超过 `max_rollback` 或超时后置任务为 `FAILED`，发布告警，不无限重试。

## 7. 地图实体

地图版本不可原地修改。变更封路、节点类型或充电桩后生成新 `map_version`，旧版本保留用于回放。

```text
Map
  id, version, width, height, tick_seconds
  cells[]: cell_id, x, y, type, passable
  edges[]: from_cell, to_cell, bidirectional
  stations[]: type, cell_id, capacity
```

固定演示地图至少包含：

- 30 台 AGV 的初始位置。
- 20 个以上货架。
- 4 个拣选点、2 个放货点、4 个充电位。
- 2 条窄巷和 3 个可动态封闭的节点。
- 一条适合演示相向冲突的路径和一条适合演示循环等待的路径。

## 8. 不变量

1. 一个任务最多有一台 AGV。
2. 一个 tick 内同一格最多一台 AGV。
3. 同一 tick 不允许两车沿同一条边相向移动。
4. 已完成任务不能重新进入运行态。
5. 已取消订单不能生成新任务。
6. 库存扣除和任务创建必须可通过订单 ID 幂等重放。
7. 任何对外展示的 AGV 状态必须来自 sim 的已确认状态。
8. 任何路径重规划必须增加 plan version，并且旧版本不能覆盖新版本。
