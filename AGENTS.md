# Agent 开发说明

本仓库用于仓储 WMS 与 AGV 调度系统的三人协作开发。任何成员或 AI 编码代理在改代码前必须遵守本文件。

## 开工前必须阅读

按以下顺序阅读：

1. `README.md`
2. `CONTRIBUTING.md`
3. `docs/00-project-charter.md`
4. `docs/01-development-plan.md`
5. `docs/02-work-breakdown.md`
6. `docs/03-architecture.md`
7. `docs/04-domain-model.md`
8. `docs/05-api-event-contract.md`
9. `docs/06-development-guide.md`
10. `docs/07-test-acceptance-plan.md`
11. `docs/08-risk-register.md`

## 代码归属

每个成员只直接修改自己的主要目录。跨目录修改必须先提交接口变更说明，并由接口归属人评审。

### 成员一：RiceXi

- `services/gateway/`
- `services/auth/`
- `services/wms/`
- `services/task/`
- `infra/compose/`
- `infra/docker/`
- `.github/workflows/`
- `scripts/dev-*`

### 成员二：Zsource-Fuyi

- `services/dispatch/`
- `services/sim/`
- `packages/test-fixtures/`
- `tests/scenarios/`
- `tests/performance/dispatch-*`
- `scripts/seed-*`

### 成员三：KingSure000

- `apps/web/`
- `services/push/`
- `services/analytics/`
- `tests/e2e/`
- `tests/performance/web-*`
- `scripts/demo-*`

### 共同目录

- `packages/contracts/`：提案人提交 PR，成员一负责最终合并；涉及调度字段时必须由成员二确认，涉及展示字段时必须由成员三确认。
- `docs/03-architecture.md`、`docs/04-domain-model.md`、`docs/05-api-event-contract.md`：任何成员可提案，至少两人批准后合并。
- `docs/01-development-plan.md`、`docs/02-work-breakdown.md`：成员一维护，其他成员通过 PR 调整。
- `evidence/ai-logs/member-N.md`：仅本人修改。

## 禁止事项

- 不得直接向 `main` 推送。
- 不得提交 `.env`、令牌、数据库文件、录屏原文件和大体积轨迹数据。
- 不得用 `Thread.sleep`、随机结果或前端动画掩盖调度错误。
- 不得在前端伪造 AGV 状态、任务完成状态或性能指标。
- 不得绕过契约修改其他服务的字段含义。
- 不得删除或弱化失败的测试来换取构建通过。

## 实现要求

- 所有服务必须提供健康检查、结构化日志和可复现的本地启动方式。
- 时间统一使用 UTC 存储，前端按本地时区显示；仿真 tick 使用整数。
- 跨服务写操作必须带幂等键；消费 Kafka 事件必须按事件 ID 去重。
- 路径、预约和死锁算法必须可在无 UI 条件下测试。
- 固定场景必须使用明确 seed，确保回放与测试可重复。
- 新增外部依赖必须说明用途、内存占用和离线运行方式。

## AI 过程记录

每名成员持续记录：

- 日期、AI 工具和模型版本。
- 关键提示词及其目标。
- AI 生成的错误代码、不存在的 API 或错误假设。
- 人工修改位置、原因和验证方式。
- 重要测试结果与性能数据。

记录写入 `evidence/ai-logs/member-N.md`，并在 PR 描述中附对应记录条目。

## 完成标准

- 本机按 README 或开发指南可启动。
- `main` 上所有必需检查通过。
- 新增行为有单元、集成或端到端测试。
- 接口和事件 schema 已同步更新。
- AI 过程记录已补齐。
- 功能在固定演示场景中可重复展示。
