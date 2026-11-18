# SmartWMS

仓储 WMS 与 AGV 调度系统是一个三人团队共同开发的单一项目。所有成员围绕同一套架构、契约和产品目标协作，不拆分个人版本或个人项目。

当前阶段：M0 项目基线。

## 项目目标

系统在本机完成“出库订单 -> 生成拣选任务 -> 指派 AGV -> 时空路径规划 -> tick 仿真 -> WebSocket 推送 -> 2D 地图展示 -> 指标分析”的闭环。演示不依赖真实硬件、企业数据或云服务。

最终版本以 2D Canvas 为主线，目标支持 30 台 AGV 同时运行、20 fps 地图刷新、任务指派 P95 小于 200 ms，并在持续仿真中完成冲突消解和死锁恢复。

## 团队成员

| 成员 | GitHub 账号 | 个人集成分支 | 协作视角 |
| --- | --- | --- | --- |
| 成员一 | RiceXi | dev/member-1 | 平台、WMS、任务、契约与集成 |
| 成员二 | Zsource-Fuyi | dev/member-2 | 调度、仿真、算法与性能 |
| 成员三 | KingSure000 | dev/member-3 | 前端、推送、分析、回放与演示 |

成员分别使用自己的 fork 和 `dev/member-N` 集成分支，最终通过跨 fork PR 合入上游 `main`。fork 只是并行工作区，不产生个人版本或个人交付。所有成员都可以修改共享代码，每项变更由作者提交 PR，并至少由一名相关视角成员评审。

## 技术基线

- 后端：Java 21、Spring Boot 3.3、Spring Cloud 2023.0、Maven。
- 前端：Vue 3、TypeScript、Vite、Canvas 2D。
- 数据与消息：MySQL 8、Redis 7、Kafka。
- 接入：Spring Cloud Gateway、Eureka、REST、WebSocket、JSON-RPC over HTTP。
- 部署：Docker Compose；Kubernetes kind、Ansible 在 M5/M6 接入。
- 观测：Prometheus、Grafana、SkyWalking。
- 测试：JUnit 5、Testcontainers、Vitest、Playwright、k6 或等价压测脚本。

## 仓库结构

```text
.
├── apps/
│   └── web/                    # Vue 3 前端
├── services/
│   ├── gateway/                # API 与 WebSocket 统一入口
│   ├── auth/                   # 登录、角色与令牌
│   ├── wms/                    # 商品、库位、库存、订单
│   ├── task/                   # 任务池与任务状态机
│   ├── dispatch/               # 指派、路径、预约、死锁
│   ├── sim/                    # tick 仿真与事件注入
│   ├── push/                   # 实时快照与增量推送
│   └── analytics/              # 指标聚合与查询
├── packages/
│   ├── contracts/              # OpenAPI、事件 schema、错误码
│   └── test-fixtures/          # 地图、订单、AGV、回放样本
├── infra/
│   ├── compose/                # 本地中间件与服务编排
│   ├── docker/                 # 镜像定义
│   ├── k8s/                    # kind 清单元数据
│   └── ansible/                # 演示环境部署
├── scripts/                    # 启动、造数、压测、文档生成
├── tests/
│   ├── e2e/                    # 浏览器端到端
│   ├── performance/            # 调度与前端性能
│   └── scenarios/              # 固定演示剧本
├── evidence/
│   └── ai-logs/                # 三名成员 AI 过程记录
├── docs/                       # 计划、架构、契约、测试和决策
└── .github/                    # CI、PR 模板、评审归属
```

## 开工阅读顺序

1. [AGENTS.md](AGENTS.md)
2. [CONTRIBUTING.md](CONTRIBUTING.md)
3. [项目章程](docs/00-project-charter.md)
4. [开发计划](docs/01-development-plan.md)
5. [协作分工与工作分解](docs/02-work-breakdown.md)
6. [系统架构](docs/03-architecture.md)
7. [领域模型](docs/04-domain-model.md)
8. [接口与事件契约](docs/05-api-event-contract.md)
9. [开发指南](docs/06-development-guide.md)
10. [测试与验收计划](docs/07-test-acceptance-plan.md)

## 当前可执行事项

M0 只建立文档、接口和仓库骨架，不提交临时业务代码。团队先完成以下动作：

1. 以 `RiceXi/smart-wms-agv` 为上游主仓库，三名成员分别 fork 一次。
2. 每个 fork 维护 `dev/member-N` 集成分支，短功能分支完成后合入个人集成分支。
3. 个人集成分支通过跨 fork PR 合入上游 `main`，禁止直接推送上游 `main`。
4. 平台与契约、调度与仿真、交互与推送三条工作流并行准备草案，所有成果最终进入同一产品。
5. 团队在 M0 评审中冻结第一批契约；冻结后修改必须走 ADR 和跨视角评审。

## 完成定义

一个功能只有在代码、契约、测试、运行说明和 AI 过程记录同时完成时才算完成。最终交付必须通过 `docs/07-test-acceptance-plan.md` 中的 P0 验收项，并保留 30 台 AGV 的可重复演示场景。
