# 成员二个人版 API 与数据契约（冻结版 v1）

本文件是 `implementations/member-2/` 内部实现与前端之间的唯一接口依据。
字段语义遵守仓库公共契约 `docs/data-schema.md`；本文件只补充接口路径、请求/响应形状和枚举取值。

> **命名约定**：所有请求体与响应体的字段名统一为 camelCase。
> 数据库列名是 snake_case，由 `util.row_to_camel` 在 API 层统一转换，业务代码不直接暴露下划线字段。

## 1. 通用约定

- 前缀：所有业务接口为 `/api/v1`，静态页面为 `/`，事件流为 `/api/v1/stream`。
- 编码：请求与响应均为 `application/json; charset=utf-8`。
- 时间：ISO 8601 UTC 字符串，例如 `2026-11-18T12:00:00Z`。
- tick：从 0 开始的整数，仿真推进一次 tick = 1 秒仿真时间。
- 坐标：`x`、`y` 为非负整数；格点 id 字符串为 `"x,y"`，例如 `"12,7"`。
- 枚举：一律小写英文，值域见第 9 节。
- 分页：列表接口接受 `page`（默认 0）、`size`（默认 20，最大 200），响应含 `items`、`page`、`size`、`total`。
- 错误：HTTP >= 400 时响应体固定为

```json
{
  "error": {
    "code": "VALIDATION_FAILED",
    "message": "人类可读的中文原因",
    "details": {"字段": "值"}
  }
}
```

- 错误码：`VALIDATION_FAILED`、`NOT_FOUND`、`DUPLICATE_CODE`、`INVALID_STATE`、`CONFLICT`、`INSUFFICIENT_INVENTORY`、`INTERNAL_ERROR`。

## 2. 健康检查与元信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/health` | `{"status":"ok","tick":128,"running":true,"seed":20261118,"version":"0.2.0-member-2"}` |
| GET | `/api/v1/meta` | `{"tickSeconds":1,"mapId":"warehouse-24","mapVersion":"map-1","width":24,"height":24,"seed":20261118}` |

## 3. 主数据

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/skus` | 查询 SKU，参数 `q`（编码/名称模糊）、`page`、`size` |
| POST | `/api/v1/skus` | 新增 SKU，body 见下 |
| GET | `/api/v1/skus/{id}` | SKU 详情 |
| PUT | `/api/v1/skus/{id}` | 全量更新 SKU |
| DELETE | `/api/v1/skus/{id}` | 删除 SKU，存在库存时返回 409 |
| GET | `/api/v1/locations` | 查询库位，参数 `type`、`zone`、`q` |
| POST | `/api/v1/locations` | 新增库位 |
| GET | `/api/v1/locations/{id}` | 库位详情 |
| PUT | `/api/v1/locations/{id}` | 更新库位 |
| DELETE | `/api/v1/locations/{id}` | 删除库位，被库存或未完成任务引用时返回 409 |
| GET | `/api/v1/inventory` | 查询库存，参数 `skuId`、`locationId` |
| POST | `/api/v1/inventory` | 登记库存（`qty` 增加或覆盖同批次） |
| POST | `/api/v1/inventory/{id}/adjust` | 调整数量，body `{"delta":-5,"reason":"盘点"}` |
| GET | `/api/v1/inventory/txns` | 库存流水，参数 `skuId`、`locationId`、`page`、`size` |

SKU 请求体：

```json
{"code":"SKU-001","name":"折叠箱","spec":"60x40","batchRule":"fifo"}
```

库位请求体：

```json
{"code":"PICK-01","zone":"A","x":3,"y":3,"type":"pick","capacity":2}
```

库存请求体：

```json
{"skuId":"...","locationId":"...","qty":100,"batch":"B20261101","expireAt":"2027-01-01T00:00:00Z"}
```

## 4. 订单

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/orders` | 查询订单，参数 `status`、`code`（模糊）、`page`、`size` |
| POST | `/api/v1/orders` | 创建订单，body 见下 |
| GET | `/api/v1/orders/{id}` | 订单详情，含明细、任务列表、库存预占 |
| POST | `/api/v1/orders/{id}/release` | 释放订单：预占库存并生成任务（幂等） |
| POST | `/api/v1/orders/{id}/cancel` | 取消订单并回滚预占库存 |

订单请求体：

```json
{
  "code": "OUT-20261118-001",
  "priority": 5,
  "requester": "demo",
  "dropLocationCode": "DROP-01",
  "lines": [{"skuCode":"SKU-001","qty":2}]
}
```

订单响应体：

```json
{
  "id":"...","code":"OUT-20261118-001","type":"outbound","status":"released","priority":5,
  "requester":"demo","lines":[{"skuId":"...","skuCode":"SKU-001","qty":2,"batch":"B1","locationId":"..."}],
  "taskIds":["..."],"createdAt":"...","updatedAt":"...","version":2
}
```

## 5. 任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/tasks` | 查询任务，参数 `status`、`agvId`、`orderId`、`priorityMin`、`priorityMax`、`page`、`size` |
| GET | `/api/v1/tasks/{id}` | 任务详情 |
| POST | `/api/v1/tasks/{id}/priority` | body `{"priority":9}` |
| POST | `/api/v1/tasks/{id}/cancel` | 取消任务（`pending`/`assigned`/`running` 允许，其余 409） |
| POST | `/api/v1/tasks/{id}/reassign` | 重派：释放当前 AGV 并回到任务池 |

`priorityMin` 与 `priorityMax` 均为**闭区间**（含端点）；只传其一表示单边约束；
两者都不传表示不按优先级过滤。`status` 取值为单个小写枚举值，不是数组。

任务响应体：

```json
{
  "id":"...","orderId":"...","type":"pick_and_drop","priority":5,"status":"assigned",
  "pickupLocationId":"...","dropLocationId":"...","assignedAgvId":"AGV-01",
  "planVersion":1,"retryCount":0,"pickupCell":"3,3","dropCell":"20,3",
  "createdAt":"...","updatedAt":"...","version":3
}
```

## 6. AGV 与地图

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/agvs` | AGV 列表，参数 `status` |
| GET | `/api/v1/agvs/{id}` | AGV 详情，含当前计划与轨迹 |
| GET | `/api/v1/map` | 当前地图（格点、边、站点、版本） |
| POST | `/api/v1/map/roadblocks` | 封路，body `{"cells":["12,7"],"reason":"临时施工"}`，生成新地图版本 |
| DELETE | `/api/v1/map/roadblocks/{id}` | 解除封路并生成新地图版本 |

AGV 响应体：

```json
{
  "id":"AGV-01","mapId":"warehouse-24","status":"to_pickup","x":4,"y":6,"battery":96.5,
  "activeTaskId":"...","payloadSku":"SKU-001","lastTick":128,"faultCode":null,"version":42,
  "chargingStationId":null,"trail":[{"tick":120,"x":3,"y":6}],
  "plan":{"taskId":"...","agvId":"AGV-01","mapVersion":"map-1","planVersion":3,
          "ticks":[{"tick":129,"x":5,"y":6,"action":"move"}],
          "edges":[{"tick":129,"from":"4,6","to":"5,6"}],
          "estimatedTicks":6,"waitTicks":0,"kind":"task","goalCell":"12,7","blockedBy":null}
}
```

- `plan` 是"当前计划"字段，无计划时为 `null`。
- `trail` 为最近 `TRAIL_LENGTH`（120）个轨迹点。
- `kind` 取值：`task`（任务路径）、`charge`（去充电桩）、`retreat`（回退）。

地图响应体（节选）：

```json
{
  "mapId":"warehouse-24","version":"map-1","width":24,"height":24,"tickSeconds":1,
  "cells":[{"id":"3,3","x":3,"y":3,"type":"pick","passable":true,"capacity":2,"zone":"A"}],
  "edges":[{"from":"3,3","to":"4,3","bidirectional":true}],
  "stations":[{"type":"pick","cellId":"3,3","capacity":2},{"type":"drop","cellId":"20,3","capacity":2},{"type":"charger","cellId":"0,22","capacity":1}],
  "roadblocks":[{"id":"rb-1","cellId":"12,7","reason":"临时施工","active":true}]
}
```

## 7. 仿真控制

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/sim/state` | 当前仿真状态（全量快照） |
| POST | `/api/v1/sim/reset` | body `{"scenarioId":"single-agv","seed":20261118}`，重置地图与数据；`seed` 可省略 |
| POST | `/api/v1/sim/start` | 启动/继续 |
| POST | `/api/v1/sim/pause` | 暂停 |
| POST | `/api/v1/sim/step` | body `{"ticks":1}`，推进指定 tick 数 |
| POST | `/api/v1/sim/speed` | body `{"speed":1}`，取值 1/4/8 |
| POST | `/api/v1/sim/inject` | body `{"type":"fault","target":"AGV-03","code":"E-SENSOR"}` |

`/sim/start`、`/sim/pause` 的 body 可省略（发空 body 或 `{}` 均可）。

注入事件类型：

| type | 字段 | 说明 |
| --- | --- | --- |
| `fault` | `target`（AGV id）、`code` | AGV 进入 `fault` |
| `offline` | `target` | AGV 进入 `offline` |
| `roadblock` | `cells`（格点 id 数组） | 封路并生成新地图版本 |
| `rush_order` | `priority`（默认 10）、`skuCode`、`qty` | 插入高优先级订单并立即释放 |
| `recover` | `target` | 故障/离线 AGV 恢复为 `idle` |

仿真状态响应体：

```json
{
  "tick": 128, "running": false, "speed": 1, "seed": 20261118,
  "scenarioId": "peak-10", "mapVersion": "map-1",
  "agvs": [], "tasks": [], "orders": [], "alerts": [],
  "stats": {"conflicts":0,"deadlocks":0,"recoveries":0,"rollbacks":0,"completedTasks":0,"failedTasks":0},
  "lastEventSeq": 512
}
```

- `orders[]` 中的每一项都带 `lines`（明细数组，每项含 `skuId`/`skuCode`/`skuName`/`qty`/`batch`/`locationId`/`pickupLocationId`）
  与 `taskIds`（任务 id 数组），前端订单表格直接使用这两个字段。
- `alerts[]` 中每一项为
  `{"id":"...","kind":"deadlock","code":"deadlock","severity":"error","message":"...","tick":128,"agvIds":["AGV-01"],"agvId":"AGV-01","occurredAt":"..."}`。
  `severity` 取值 `error` / `warn` / `info`。

## 8. 事件与统计

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/events` | 事件查询，参数 `type`、`agvId`、`taskId`、`fromTick`、`toTick`、`page`、`size` |
| GET | `/api/v1/analytics/summary` | 汇总：订单/任务/AGV 计数、冲突数、死锁数、恢复数、平均耗时 |
| GET | `/api/v1/analytics/throughput` | 吞吐，参数 `window`（tick 数，默认 600） |
| GET | `/api/v1/analytics/utilization` | 每台 AGV 利用率 |
| GET | `/api/v1/analytics/bottlenecks` | 瓶颈巷道 TopN，参数 `top`（默认 10） |
| GET | `/api/v1/analytics/tasks/{id}/timeline` | 单任务时间线 |
| GET | `/api/v1/scenarios` | 内置固定场景列表 |

事件响应体：

```json
{"id":"...","type":"agv.position.changed","tick":128,"aggregateId":"AGV-01","occurredAt":"...","payload":{}}
```

统计响应体（summary 节选）：

```json
{
  "orders":{"total":12,"byStatus":{"draft":1,"released":3,"completed":8}},
  "tasks":{"total":20,"byStatus":{"completed":15,"running":2,"failed":1}},
  "agvs":{"total":10,"byStatus":{"idle":4,"to_pickup":3,"charging":1}},
  "conflicts":6,"deadlocks":2,"recoveries":2,"rollbacks":3,
  "avgTaskTicks":42.5,"completedTasks":15,"cancelledTasks":0,"failedTasks":1
}
```

## 9. 枚举取值

| 枚举 | 取值 |
| --- | --- |
| `batchRule` | `none`、`fifo`、`fefo` |
| 库位 `type` | `floor`、`shelf`、`pick`、`drop`、`charger`、`blocked` |
| 订单 `status` | `draft`、`allocated`、`released`、`completed`、`cancelled`、`failed` |
| 任务 `status` | `pending`、`assigned`、`running`、`completed`、`cancelled`、`failed` |
| AGV `status` | `idle`、`to_pickup`、`loading`、`to_dropoff`、`unloading`、`charging`、`fault`、`offline` |
| 路径动作 | `move`、`wait` |
| 事件 `type` | 见第 10 节 |

## 10. 事件类型清单

`order.released`、`order.cancelled`、`order.completed`、`task.created`、`task.assigned`、`task.started`、
`task.replanned`、`task.completed`、`task.failed`、`task.cancelled`、`task.priority.changed`、
`agv.state.changed`、`agv.position.changed`、`agv.fault`、`agv.charging`、`agv.blocked`、
`map.roadblock.added`、`map.roadblock.removed`、`map.version.changed`、
`conflict.detected`、`deadlock.detected`、`deadlock.resolved`、`rollback.executed`、
`sim.reset`、`sim.injected`、`reservation.conflict`。

## 11. 实时事件流（SSE）

`GET /api/v1/stream`，`Content-Type: text/event-stream; charset=utf-8`，每条消息形如：

```text
event: state
data: {"seq":513,"tick":129,"events":[{"id":"evt-...","type":"agv.position.changed","tick":129,"aggregateId":"AGV-01","payload":{...},"occurredAt":"...","seq":513}]}

```

- `state` 消息的 `data` 是一个 JSON 对象，固定包含三个字段：
  | 字段 | 类型 | 含义 |
  | --- | --- | --- |
  | `seq` | int | 事件总序号，客户端可据此判断是否漏消息 |
  | `tick` | int | 当前仿真 tick |
  | `events` | array | 本次推送包含的全部事件，单个事件结构见第 10 节 |
- 服务端每 tick 推送一条 `state` 消息，`events` 数组包含该 tick 产生的全部事件。
  客户端可以兼容"单条事件对象"与"事件数组"两种形态，但服务端统一使用上面的对象形态。
- 事件结构固定为 `{id, type, tick, aggregateId, payload, occurredAt, seq}`。
- 客户端断线后重连必须重新 `GET /api/v1/sim/state` 取全量快照，不允许仅依赖增量追赶。
- 心跳：每 5 秒发送 `:keepalive` 注释行。
- 传输使用 chunked，连接保持打开。
