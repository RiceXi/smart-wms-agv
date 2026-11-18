# ADR 0001 单仓库与技术栈

## 状态

已接受。

## 背景

三名成员需要并行开发 WMS、调度、仿真、推送和前端。服务数量多，但团队规模小，接口变化快。独立仓库会增加版本同步、联调和契约漂移成本。

## 决策

采用单仓库管理：

- Java 服务位于 `services/`。
- Vue 前端位于 `apps/web/`。
- 共享契约与测试夹具位于 `packages/`。
- 部署与文档位于 `infra/`、`docs/`。
- 使用 Spring Boot、Spring Cloud、MySQL、Redis、Kafka 和 Docker Compose。
- 2D Canvas 为实时地图主线，Three.js 仅作为 P2 加分项。

## 结果

- 契约、实现和测试可以在一个 PR 中更新。
- Maven 和 pnpm 可以共享根级版本属性。
- CI 可一次构建全部服务。
- 单仓库会增加构建范围，需要通过受影响模块和缓存控制成本。
- 微服务仍是逻辑和部署边界，不拆成独立 Git 仓库。
