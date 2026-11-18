# 数据契约

本文件定义三份实现共同遵守的字段、状态和规则。存储技术可以不同，但字段语义必须一致。

## 1. 通用约定

- 主键使用字符串 UUID。
- 时间使用 ISO 8601 UTC。
- 地图坐标为非负整数 `(x, y)`。
- tick 从 0 开始，默认 1 tick = 1 秒仿真时间。
- 枚举值在存储和 API 中使用小写英文。
- 所有可变实体带 `created_at`、`updated_at` 和 `version`。

## 2. SKU

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 主键 |
| code | string | 是 | 全局唯一编码 |
| name | string | 是 | 商品名称 |
| spec | string | 否 | 规格 |
| batch_rule | enum | 是 | `none`、`fifo`、`fefo` |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |

## 3. 库位

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 主键 |
| code | string | 是 | 唯一编码 |
| zone | string | 是 | 区域 |
| x | integer | 是 | 横坐标 |
| y | integer | 是 | 纵坐标 |
| type | enum | 是 | `floor`、`shelf`、`pick`、`drop`、`charger`、`blocked` |
| capacity | integer | 是 | 容量，必须大于 0 |
| map_version | string | 是 | 地图版本 |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |

## 4. 库存

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 主键 |
| sku_id | string | 是 | SKU id |
| location_id | string | 是 | 库位 id |
| qty | integer | 是 | 数量，不得小于 0 |
| reserved_qty | integer | 是 | 已预占数量 |
| batch | string | 否 | 批次 |
| expire_at | string | 否 | 有效期 |
| version | integer | 是 | 乐观锁版本 |

可用库存 = `qty - reserved_qty`。

## 5. 出库订单

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 主键 |
| code | string | 是 | 唯一业务单号 |
| type | enum | 是 | 当前只支持 `outbound` |
| status | enum | 是 | 见订单状态 |
| priority | integer | 是 | 1-10，数值越大优先级越高 |
| requester | string | 是 | 创建人或演示用户 |
| lines | list | 是 | 至少一条出库明细 |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |

订单状态：

| 状态 | 含义 | 允许流转 |
| --- | --- | --- |
| draft | 草稿 | allocated、cancelled |
| allocated | 已预占库存 | released、cancelled |
| released | 已生成任务 | completed、failed |
| completed | 全部任务完成 | 终态 |
| cancelled | 已取消 | 终态 |
| failed | 执行失败 | 终态 |

## 6. 任务

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 主键 |
| order_id | string | 是 | 来源订单 |
| type | enum | 是 | 当前只支持 `pick_and_drop` |
| priority | integer | 是 | 1-10 |
| status | enum | 是 | 见任务状态 |
| pickup_location_id | string | 是 | 取货库位 |
| drop_location_id | string | 是 | 放货库位 |
| assigned_agv_id | string | 否 | 当前 AGV |
| plan_version | integer | 是 | 路径计划版本 |
| retry_count | integer | 是 | 重试次数 |
| created_at | string | 是 | 创建时间 |
| updated_at | string | 是 | 更新时间 |

任务状态：

```text
pending -> assigned -> running -> completed
   |           |           |
   +-----------+           +--> failed
   |
   +--> cancelled

assigned -> pending  仅在重派或撤销原计划后允许
```

规则：

- 一个任务最多绑定一台 AGV。
- `running` 只能由仿真的 AGV 确认事件进入。
- 任务完成只能由仿真的完成事件触发。
- `completed`、`cancelled`、`failed` 为终态。
- 路径重规划必须增加 `plan_version`。

## 7. AGV

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | string | 是 | 稳定标识 |
| map_id | string | 是 | 所属地图 |
| status | enum | 是 | 见 AGV 状态 |
| x | integer | 是 | 当前横坐标 |
| y | integer | 是 | 当前纵坐标 |
| battery | number | 是 | 0-100 |
| active_task_id | string | 否 | 当前任务 |
| payload_sku | string | 否 | 载荷 |
| last_tick | integer | 是 | 最后确认 tick |
| fault_code | string | 否 | 故障代码 |
| version | integer | 是 | 状态版本 |

AGV 状态：

| 状态 | 含义 |
| --- | --- |
| idle | 空闲 |
| to_pickup | 前往取货 |
| loading | 取货确认 |
| to_dropoff | 前往放货 |
| unloading | 放货确认 |
| charging | 充电 |
| fault | 故障 |
| offline | 离线 |

## 8. 路径计划

```json
{
  "taskId": "task-1",
  "agvId": "agv-07",
  "mapVersion": "map-1",
  "planVersion": 3,
  "ticks": [
    {"tick": 100, "x": 2, "y": 8, "action": "move"},
    {"tick": 101, "x": 3, "y": 8, "action": "move"}
  ],
  "edges": [
    {"tick": 100, "from": "2,8", "to": "3,8"}
  ],
  "estimatedTicks": 24,
  "waitTicks": 2
}
```

路径必须连续。相邻路径点的坐标差只能是 0 或 1，且停留动作计入等待。

## 9. 预约

| 键 | 值 | 说明 |
| --- | --- | --- |
| `vertex:{map}:{cell}:{tick}` | `agv:planVersion` | 格点预约 |
| `edge:{map}:{from}:{to}:{tick}` | `agv:planVersion` | 有向边预约 |

规则：

- 预约必须原子获取。
- 预约失败不得保留部分键。
- AGV 到达或通过后释放预约。
- 未确认预约必须有兜底超时。
- 同一 tick 不允许同格占用或相向穿边。

## 10. 地图

地图版本不可原地修改。封路、节点类型或容量变化后生成新 `map_version`。

地图至少包含：

- 宽、高和 tick 秒数。
- 节点坐标、类型和可通行属性。
- 有向边及是否双向。
- 拣选点、放货点和充电位容量。
- 至少一条窄巷和三个可封闭节点。

## 11. 事件

事件统一包含：

```json
{
  "id": "uuid",
  "type": "task.status.changed",
  "occurredAt": "2026-11-18T12:00:00Z",
  "tick": 720,
  "aggregateId": "task-1",
  "payload": {}
}
```

关键事件：

- 订单释放、取消和完成。
- 任务创建、指派、开始、重规划、完成、失败和取消。
- AGV 状态、位置、电量、阻塞、故障和充电。
- 地图封路、解封和版本变化。
- 冲突、死锁、回退和恢复。

## 12. 状态与数据不变量

1. 一个 tick 内同一格最多一台 AGV。
2. 同一 tick 不允许相向穿边。
3. 库存和预占数量不能为负。
4. 已完成任务不能被重新启动。
5. 已取消订单不能生成新任务。
6. 订单重复释放必须幂等。
7. 前端展示的 AGV 状态必须来自仿真事实。
8. 旧 `plan_version` 的事件不能覆盖新计划。
