# 开发指南

## 1. 目标

本指南规定本地开发、接口实现、代码结构、测试、日志、AI 记录和提交方式。成员开始编码前必须确认 `docs/03-architecture.md`、`docs/04-domain-model.md` 和 `docs/05-api-event-contract.md` 中的定义。

## 2. 环境基线

| 工具 | 建议版本 | 用途 |
| --- | --- | --- |
| JDK | 21 LTS | 后端与服务 |
| Maven | 3.9+ | Java 构建 |
| Node.js | 22 LTS | 前端和脚本 |
| pnpm | 10+ | 前端依赖 |
| Docker Desktop | 支持 Compose v2 | 本地中间件和服务 |
| MySQL | 8.4 | 业务持久化 |
| Redis | 7.4 | 运行态与预约 |
| Kafka | 3.8 KRaft | 事件流 |
| Playwright | 与 Node 兼容版本 | 浏览器测试 |

版本以 `.tool-versions`、`.nvmrc` 或 Maven 属性最终锁定。新增依赖必须兼容离线演示，并记录在运行说明中。

## 3. 本地启动流程

以下命令是仓库骨架完成后应提供的统一入口，具体实现随 M0 补齐：

```text
./scripts/dev-infra.ps1
./scripts/dev-services.ps1
pnpm --dir apps/web dev
```

基础设施与业务服务分离。只开发调度内核时，不必启动前端和所有业务服务；只开发 UI 时，可以使用契约固定的 fixture server，但演示验收必须连接真实后端。

默认端口：

| 组件 | 端口 |
| --- | --- |
| gateway | 8080 |
| auth | 8081 |
| wms | 8082 |
| task | 8083 |
| dispatch | 8084 |
| sim | 8085 |
| push | 8086 |
| analytics | 8087 |
| web | 5173 |
| MySQL | 3306 |
| Redis | 6379 |
| Kafka | 9092 |
| Prometheus | 9090 |
| Grafana | 3000 |

## 4. 仓库与包结构

### Java 服务

每个服务使用独立 Spring Boot 模块和独立数据库 schema。服务内部按 domain/application/infrastructure 分层，但不要为简单 CRUD 制造无意义抽象。

```text
services/wms/
  src/main/java/.../wms/
    api/             # controller、DTO、错误映射
    application/     # use case、事务边界
    domain/          # 聚合、规则、状态机
    infrastructure/  # repository、消息、外部客户端
  src/test/
```

约束：

- Controller 不直接访问 Repository。
- 领域状态变化通过领域方法完成，不能从多个服务直接更新枚举。
- DTO 与持久化实体分离。
- 跨服务事件使用 `packages/contracts` 生成的类型或 schema。
- 外部调用设置超时、重试上限和幂等键。
- Java 时间使用 `Instant`，地图 tick 使用 `long`。

### Vue 前端

```text
apps/web/src/
  app/               # 路由、全局状态、错误边界
  features/warehouse # 地图与 AGV
  features/tasks     # 任务队列与详情
  features/analytics # 指标与瓶颈
  features/sim       # 控制与回放
  components/        # 复用 UI
  services/          # REST、WebSocket、schema 校验
```

约束：

- 服务端状态和前端 UI 状态分开。
- WebSocket 消息先校验 schema，再进入状态仓库。
- Canvas 渲染层不直接发请求；使用 controller 或 store action。
- 所有动画由统一 tick 时钟驱动，不创建多个互相漂移的定时器。
- 地图尺寸和可交互控件使用稳定布局，避免文本或状态变化引发布局跳动。
- 不使用前端随机数生成 AGV 状态。

## 5. API 实现规则

- 外部请求只通过 gateway。
- gateway 传递 `X-User-Id`、`X-Roles`、`X-Request-Id`，下游服务不信任客户端伪造的同名头。
- 列表默认分页，禁止无上限全表返回。
- 写命令成功返回最终快照或资源 ID；失败返回统一错误码。
- 修改接口使用 `version` 做乐观并发。
- 不在 API 中暴露密码、令牌、内部 SQL 或堆栈。
- OpenAPI 是契约源，不能让控制器和文档长期漂移。

## 6. 事件规则

- 事件是不可变事实，名称使用过去式，例如 `task.created.v1`。
- 事件 ID 由生产方生成，作为消费幂等键。
- 生产者保存事件前先写 outbox；发布失败可重试。
- 消费失败不吞异常；使用重试 topic 和死信策略。
- 事件消费者必须能处理重复、乱序和旧版本。
- 跨服务查询不能依赖事件一定及时到达，关键页面需提供快照或对账接口。

## 7. Redis 与预约

- key 前缀必须包含环境和地图版本。
- 预约 TTL 必须大于预计通过时间，并保留清理兜底。
- 批量预约使用 Lua 脚本或事务，禁止逐键获取后假设成功。
- Redis 丢失后，系统应能从 sim、task、dispatch 快照重建运行态。
- 不允许把 Redis 当唯一业务数据库保存订单或库存事实。

## 8. 仿真规则

- 仿真随机数必须来自固定 seed 的可注入生成器。
- tick 由 sim 主实例推进；其他服务只消费或请求单步。
- 暂停和加速改变推进速度，不改变事件顺序。
- 每次状态变化带 `serverTick`，回放按事件 tick 排序。
- 仿真时钟、数据库时间和前端动画时间不能互相替代。
- 故障注入必须生成可定位事件，不能直接删改前端状态。

## 9. 日志与可观测

日志使用结构化 JSON，至少包含：

```text
timestamp, level, service, requestId, traceId, taskId, agvId, tick, eventType, message
```

约束：

- 不打印令牌、密码和完整个人信息。
- 算法异常记录 seed、plan version、冲突键和 AGV 集合。
- 指标命名稳定，例如 `dispatch_assignment_latency_ms`。
- 关键业务指标与原始事件可对账。
- trace 采样率在性能测试时单独配置，不能把观测开销当成业务延迟。

## 10. 测试要求

| 变更 | 最低测试 |
| --- | --- |
| 领域状态或校验 | 单元测试，包含非法流转 |
| Repository 或事务 | Testcontainers 集成测试 |
| API 字段 | 契约测试与负向校验 |
| 路径与预约 | 固定场景、冲突场景、随机 seed 回归 |
| WebSocket | 快照、增量、重连、旧 seq |
| 页面流程 | Playwright 主流程 |
| 性能相关 | 前后对比数据和可重复命令 |

测试不得依赖真实时间等待。涉及 tick 的逻辑使用可控时钟或显式步进。

## 11. AI 辅助开发记录

统一使用 `docs/templates/ai-log.md` 记录，每人一份：

- 目标与关键提示词。
- AI 给出的方案和代码摘要。
- 错误实现、不存在的 API、忽略的边界。
- 人工修改位置和原因。
- 测试命令、结果和性能数据。

关键功能 PR 必须关联至少一条 AI 日志。记录用于复盘工程判断，不作为代码正确性的证明。

## 12. 提交与评审

- 每个 PR 只解决一个可验证问题。
- 先提交契约和测试，再提交实现。
- 评审优先看状态一致性、并发、幂等、失败路径和可复现性。
- 作者不能批准自己的契约变更。
- 合并前更新 README、运行说明或示例请求。
- 禁止为了通过 CI 删除失败测试；可标记 skip 但必须有 issue 和恢复条件。

## 13. 完成定义

功能完成必须同时满足：

- 业务行为和错误路径符合契约。
- 单元、集成和必要的端到端测试通过。
- 关键状态可从 API 或日志核对。
- 性能变化有测量结果。
- UI 状态与后端一致，不使用假数据。
- 运行说明已更新。
- AI 过程记录已更新。
