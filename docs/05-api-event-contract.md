# 接口与事件契约

## 1. 接口规范

- 外部 API：REST/JSON，统一前缀 `/api/v1`。
- 高频调度：JSON-RPC over HTTP，路径 `/rpc/v1`。
- 实时：WebSocket `/ws/warehouse`。
- 时间：ISO 8601 UTC。
- 标识：UUID 字符串。
- 分页：`page`、`size`，默认 0/20，最大 100。
- 认证：`Authorization: Bearer <token>`。
- 幂等：写命令接受 `Idempotency-Key`。
- 追踪：请求与响应携带 `X-Request-Id`。

成功响应直接返回资源或分页对象。错误响应：

```json
{
  "code": "TASK_INVALID_STATE",
  "message": "任务当前状态不允许取消",
  "details": {"taskId": "task-1", "status": "COMPLETED"},
  "requestId": "..."
}
```

## 2. 错误码

| HTTP | code | 说明 |
| --- | --- | --- |
| 400 | `VALIDATION_FAILED` | 字段格式或必填校验失败 |
| 401 | `UNAUTHENTICATED` | 缺少或无效令牌 |
| 403 | `FORBIDDEN` | 角色无权限 |
| 404 | `RESOURCE_NOT_FOUND` | 资源不存在 |
| 409 | `DUPLICATE_CODE` | 业务编码重复 |
| 409 | `TASK_INVALID_STATE` | 任务状态机不允许当前操作 |
| 409 | `AGV_UNAVAILABLE` | AGV 故障、离线或低电量 |
| 409 | `PATH_CONFLICT` | 预约冲突，附带冲突键 |
| 422 | `INVENTORY_INSUFFICIENT` | 库存不足 |
| 503 | `DEPENDENCY_UNAVAILABLE` | 依赖服务不可用 |

## 3. 认证与用户

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| POST | `/api/v1/auth/login` | 匿名 | 用户名密码登录 |
| POST | `/api/v1/auth/refresh` | 登录用户 | 刷新令牌 |
| GET | `/api/v1/auth/me` | 登录用户 | 当前用户和角色 |
| GET | `/api/v1/users` | ADMIN | 用户列表 |

## 4. WMS

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET/POST | `/api/v1/skus` | OPERATOR+ | 查询或创建 SKU |
| PATCH | `/api/v1/skus/{id}` | OPERATOR+ | 更新 SKU |
| GET/POST | `/api/v1/locations` | OPERATOR+ | 查询或创建库位 |
| GET/POST | `/api/v1/inventory` | OPERATOR+ | 查询或登记库存 |
| POST | `/api/v1/orders` | OPERATOR+ | 创建出库订单 |
| GET | `/api/v1/orders` | VIEWER+ | 按状态、编号分页查询 |
| GET | `/api/v1/orders/{id}` | VIEWER+ | 订单详情和关联任务 |
| POST | `/api/v1/orders/{id}/release` | OPERATOR+ | 分配库存并释放任务 |
| POST | `/api/v1/orders/{id}/cancel` | OPERATOR+ | 取消未完成任务 |

订单创建示例：

```json
{
  "code": "OUT-20261118-001",
  "priority": 5,
  "requester": "demo",
  "lines": [
    {"skuCode": "SKU-001", "qty": 2}
  ]
}
```

## 5. 任务

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/tasks` | VIEWER+ | 按状态、AGV、优先级分页 |
| GET | `/api/v1/tasks/{id}` | VIEWER+ | 任务快照和事件 |
| POST | `/api/v1/tasks/{id}/priority` | OPERATOR+ | 调整优先级 |
| POST | `/api/v1/tasks/{id}/cancel` | OPERATOR+ | 取消允许取消的任务 |
| POST | `/api/v1/tasks/{id}/reassign` | OPERATOR+ | 释放当前 AGV 并重派 |

所有任务命令必须携带 `If-Match` 或 body `version`，版本不匹配返回 409。

## 6. 调度

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/dispatch/agvs` | VIEWER+ | AGV 状态与当前任务 |
| GET | `/api/v1/dispatch/plans/{taskId}` | VIEWER+ | 最新路径计划 |
| POST | `/api/v1/dispatch/roadblocks` | OPERATOR+ | 注入临时封路 |
| DELETE | `/api/v1/dispatch/roadblocks/{id}` | OPERATOR+ | 解除封路 |
| POST | `/api/v1/dispatch/replan` | OPERATOR+ | 触发指定地图范围的重规划 |

JSON-RPC 方法：

| 方法 | 调用方 | 参数 | 返回 |
| --- | --- | --- | --- |
| `dispatch.assign` | task | task、候选 AGV、map version | assignment |
| `dispatch.planPath` | sim | task、agv、map version | PathPlan |
| `dispatch.releaseReservation` | sim | agv、plan version、tick | 释放结果 |
| `dispatch.reportBlocked` | sim | agv、blocking agv、tick | 处理结果 |
| `dispatch.health` | 运维 | 无 | 内核状态 |

## 7. 仿真

| 方法 | 路径 | 角色 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/v1/sim/state` | VIEWER+ | 当前 tick、速度、车辆和事件 |
| POST | `/api/v1/sim/reset` | OPERATOR+ | 重置到指定场景和 seed |
| POST | `/api/v1/sim/start` | OPERATOR+ | 启动或继续 |
| POST | `/api/v1/sim/pause` | OPERATOR+ | 暂停 |
| POST | `/api/v1/sim/step` | OPERATOR+ | 推进指定 tick 数 |
| POST | `/api/v1/sim/speed` | OPERATOR+ | 1x、4x、8x |
| POST | `/api/v1/sim/inject` | OPERATOR+ | 封路、故障、急单等事件 |

重置示例：

```json
{
  "scenarioId": "peak-30",
  "seed": 20261118,
  "mapVersion": "map-1"
}
```

## 8. 分析

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/analytics/summary` | 订单、任务、AGV、告警汇总 |
| GET | `/api/v1/analytics/throughput?window=5m` | 任务吞吐 |
| GET | `/api/v1/analytics/agv-utilization` | AGV 利用率 |
| GET | `/api/v1/analytics/bottlenecks?top=10` | 瓶颈巷道 TopN |
| GET | `/api/v1/analytics/tasks/{id}/timeline` | 任务时间线 |

指标必须带单位、窗口和计算口径。前端不能自行猜测百分比分母。

## 9. WebSocket

连接：

```text
/ws/warehouse?token=<access-token>
```

消息 envelope：

```json
{
  "type": "snapshot",
  "seq": 120,
  "serverTick": 720,
  "sentAt": "2026-11-18T12:00:00Z",
  "payload": {}
}
```

消息类型：

| type | 说明 |
| --- | --- |
| `snapshot` | 当前地图、AGV、任务、告警的完整快照 |
| `agv.delta` | 车辆位置、状态、电量变化 |
| `task.delta` | 任务状态变化 |
| `map.delta` | 封路或地图版本变化 |
| `alert` | 冲突、死锁、故障和超时 |
| `sim.status` | tick、速度和运行状态 |
| `heartbeat` | 服务端心跳 |

客户端重连后必须重新请求快照，不能只依赖 last sequence 追赶。后端保留最近 60 秒增量；超出窗口时直接发快照。

## 10. Kafka topics

| Topic | 生产方 | 消费方 | 关键字段 |
| --- | --- | --- | --- |
| `wms.order.released.v1` | wms | task、analytics | orderId、lines、priority |
| `task.lifecycle.v1` | task | dispatch、analytics | taskId、from、to、agvId |
| `agv.state.v1` | sim | push、analytics | agvId、x、y、status、battery、tick |
| `dispatch.plan.v1` | dispatch | sim、push | taskId、agvId、planVersion、ticks |
| `dispatch.alert.v1` | dispatch | push、analytics | type、severity、agvIds、cell |
| `sim.event.v1` | sim | analytics | tick、eventType、payload |
| `map.version.v1` | wms 或模拟器 | dispatch、sim、push | mapId、version、changes |

事件键使用聚合 ID，保证同一任务或 AGV 的事件有序。事件 payload 不包含数据库实体内部字段。

## 11. 契约变更

新增字段默认向后兼容。删除字段、改变含义或将枚举改为不同语义必须升版本，并提供迁移期。所有 schema 放在 `packages/contracts/`，PR 必须包含兼容性说明、示例和负向校验用例。
