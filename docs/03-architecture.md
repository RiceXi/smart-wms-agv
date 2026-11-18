# 系统架构

## 1. 架构目标

系统采用“业务服务 + 调度内核 + tick 仿真 + 实时展示”的分层设计。服务边界服务于独立开发、可测试和可观测，不为了微服务数量拆分过细。

核心状态由后端持有：WMS 持有主数据和订单，task 持有任务流程，dispatch 持有指派与路径，sim 持有 AGV 实时运动，push 只转发状态，web 只展示状态。

## 2. 组件图

```text
Browser
  |
  v
Spring Cloud Gateway
  |---- REST ----> auth / wms / task / dispatch / sim / analytics
  |---- WebSocket -> push
  |
  v
Eureka

wms --order event--> task --task event--> dispatch
dispatch --assignment/path--> sim
sim --tick state--> dispatch (replan check)
sim --state event--> push --WebSocket--> Browser
wms/task/dispatch/sim/analytics --event--> Kafka
analytics --metrics--> Prometheus
```

## 3. 服务职责

| 服务 | 职责 | 不负责 |
| --- | --- | --- |
| gateway | 路由、认证上下文、限流、request ID | 业务状态 |
| auth | 用户、角色、登录、令牌校验 | 仓库业务权限规则 |
| wms | 商品、库位、库存、出库订单 | AGV 运动和路径 |
| task | 任务池、优先级、状态机、重派和超时 | 具体路线计算 |
| dispatch | AGV 选择、时空 A*、预约、死锁、充电、封路 | 订单主数据 |
| sim | tick、AGV 运动、电量、装卸、事件注入和回放 | 重新定义任务状态 |
| push | 快照缓存、增量广播、重连恢复 | 编造调度结果 |
| analytics | 指标聚合、查询、离线分析入口 | 修改业务状态 |

## 4. 核心数据流

### 4.1 出库订单

1. 用户向 wms 创建 `OUTBOUND` 订单。
2. wms 校验库存和库位后，将订单置为 `RELEASED`。
3. wms 发布 `wms.order.released.v1`。
4. task 消费事件，为每个有效明细创建 `PENDING` 任务，并发布 `task.created.v1`。
5. dispatch 读取任务，按优先级、距离、电量和负载计算候选 AGV。
6. dispatch 返回 assignment 和带时间戳路径，任务进入 `ASSIGNED`，sim 接收运动计划。
7. sim 完成取货和放货后发布完成事件，task 将任务置为 `COMPLETED`。
8. wms 在所有明细完成后将订单置为 `COMPLETED`。

### 4.2 tick 循环

```text
for each tick:
  apply injected events
  confirm current reservations
  move AGVs along approved edges
  run loading/unloading transitions
  update battery and charging
  publish agv state delta
  trigger replan if blocked, faulted or rerouted
```

调度和仿真使用同一 `tick_seconds` 常量。默认 1 tick = 1 秒仿真时间。前端只对相邻快照做视觉插值，不改变服务端时间戳。

## 5. 地图与占用模型

地图采用二维离散网格：

- 节点类型：`FLOOR`、`SHELF`、`PICK_STATION`、`DROP_STATION`、`CHARGER`、`BLOCKED`。
- 每个节点有稳定 `cell_id`、`x`、`y`、类型和可进入属性。
- AGV 每 tick 只能停在节点或沿一条有向边移动。
- 边按方向建模，支持临时封路。
- 路径点包含 `(x, y, t)`。
- 预约同时覆盖顶点 `(cell, t)` 和边 `(from, to, t)`，用于防止相向穿边。

## 6. 调度内核

### 6.1 指派

候选分数由距离、当前负载、任务优先级、电量和可用状态组成。P0 使用确定性贪心；小规模批次可增加局部交换，优先保证可解释和可测试。

```text
cost(agv, task) =
  w_distance * estimated_distance
  + w_load * active_task_count
  - w_priority * task_priority
  + w_battery * low_battery_penalty
```

权重和归一化方式写入 `packages/contracts`，不能在不同服务中各自定义。

### 6.2 路径

时空 A* 的节点状态为 `(x, y, t)`，动作包含上下左右和等待。若下一顶点或当前边已被更高优先级请求占用，则当前状态跳过。

路径返回：

- `task_id`
- `agv_id`
- `plan_version`
- `ticks`
- 带时间戳的节点和边列表
- estimate：距离、预计 tick、冲突等待 tick

### 6.3 冲突与死锁

优先级从高到低预约。预约带 TTL，并在 AGV 到达对应 tick 后由 sim 确认或释放。

死锁检测周期构建等待图 `AGV -> blocking AGV`。发现环后选择优先级最低的车辆回退一个安全格，释放相关预约并重新规划。每个回退动作必须受次数和超时限制，避免活锁。

### 6.4 封路与充电

封路事件产生新的 map version。dispatch 只重规划受影响的路径，路径版本递增。电量低于阈值时，任务完成后进入充电队列；充电桩容量从地图模型读取。

## 7. 数据与一致性

- MySQL：主数据、订单、任务快照、审计记录。
- Redis：AGV 最新状态、预约表、充电队列、幂等键。
- Kafka：跨服务事实事件；生产方使用 outbox 或可重试发布。
- Elasticsearch：事件检索和异常回溯。
- HDFS / MinIO：仿真轨迹、快照和离线作业输入。

所有事件包含：

```json
{
  "eventId": "uuid",
  "eventType": "task.created.v1",
  "occurredAt": "2026-11-18T12:00:00Z",
  "producer": "task",
  "aggregateId": "task-123",
  "traceId": "...",
  "payload": {}
}
```

消费方按 `eventId` 去重；同一 aggregate 的事件按版本号拒绝旧消息。跨服务流程每小时执行一次对账，修复丢失事件或超时状态。

## 8. 安全与权限

角色：`ADMIN`、`OPERATOR`、`VIEWER`。

- ADMIN 可管理用户、地图和演示场景。
- OPERATOR 可创建订单、注入事件、暂停/继续仿真。
- VIEWER 只读地图、任务和分析。

网关验证令牌并注入用户上下文。服务间调用使用短期服务令牌或受信网络策略；生产部署不信任客户端传入的角色字段。

## 9. 运行模式

### 本地开发

MySQL、Redis、Kafka、Eureka 由 Compose 启动；服务可由 IDE 单独运行；前端连接 gateway。

### 演示

Compose 启动全部服务；Grafana、SkyWalking 按需启动；固定 seed 和演示数据由脚本导入。

### 性能测试

禁用无关面板和 trace 采样过高配置，记录机器规格、JVM 参数、GPU/浏览器版本、并发任务数和测试时长。

## 10. 架构约束

- 任何服务不得直接写其他服务拥有的业务表。
- dispatch 不在数据库持久化所有中间搜索结果；最新状态和预约以 Redis 为主。
- sim 是 AGV 运动状态的唯一事实来源。
- web 只能渲染后端事件；视觉插值不得改变任务或 AGV 的最终状态。
- P2 中间件不能成为启动 P0 系统的硬依赖。
