# 课程推荐技术映射

## 1. 文档目的

本文把《课程设计推荐要求》中的 17 项技术逐项映射到 SmartWMS 的服务、数据流和验收证据。该文档用于技术检查和答辩，不替代项目需求、接口契约和业务验收。

每个技术项只选择一条清晰、可运行的实现路径，避免为了扩大技术清单而重复引入同类组件。

## 2. 选型原则

- 消息服务选择 Kafka，不同时引入 RabbitMQ、RocketMQ 和 ActiveMQ。
- 注册中心选择 Eureka，不同时引入 Zookeeper。
- Hadoop 方向选择 HDFS + MapReduce，HBase 作为可选扩展。
- CI/CD 选择 GitHub Actions。
- 所有技术项必须能指出代码或配置、启动命令和可观察结果。
- 仅在架构图中出现、不能实际运行的技术项不算完成。

## 3. 技术映射

| # | 课程推荐技术 | 项目落点 | 交付证据 | 阶段 |
| --- | --- | --- | --- | --- |
| 1 | Spring Boot | gateway、auth、wms、task、dispatch、sim、push、analytics | 8 个服务可构建、健康检查可访问 | M0-M3 |
| 2 | Spring Cloud | Spring Cloud Gateway、服务调用、配置与注册 | 网关路由、OpenFeign 调用、配置文件 | M0-M3 |
| 3 | SOA / 微服务 | 7 个业务服务加 1 个网关，按领域拆分 | Compose 服务清单、服务边界文档 | M0-M3 |
| 4 | Docker / K8s | Compose 本地全栈、kind 单节点演示 | 镜像、Compose、kind 清单、扩缩容演示 | M3-M6 |
| 5 | Redis | AGV 状态、顶点/边预约、任务优先级、幂等键 | 预约键样例、TTL 测试、状态恢复测试 | M4 |
| 6 | 分布式消息 | Kafka 承载订单、任务、AGV、告警事件 | topic 清单、消费者测试、重复事件去重 | M4-M6 |
| 7 | Eureka | 服务注册、发现与健康状态 | Eureka 服务列表、下线与恢复测试 | M3-M4 |
| 8 | RESTful API | WMS、任务、调度、仿真、分析接口 | OpenAPI、错误码、契约测试 | M0 起持续 |
| 9 | JSON-RPC over HTTP | sim 与 dispatch 的路径、预约和 tick 调用 | RPC 方法表、请求样例、集成测试 | M2 |
| 10 | 负载均衡 | gateway 路由 dispatch 双实例 | 双实例拓扑、轮询结果、实例故障切换测试 | M4-M6 |
| 11 | Hadoop | HDFS 保存轨迹，MapReduce 计算利用率和瓶颈巷道 | 离线作业代码、输入输出样例、作业日志 | M7 |
| 12 | Elasticsearch | 订单、任务、异常和 trace 关联检索 | 索引模板、检索 API、数据同步验证 | M7 |
| 13 | Serverless / FaaS | 日报导出、轨迹压缩或地图快照函数 | 函数代码、触发器、调用日志 | M7 |
| 14 | Ansible | 演示环境目录、依赖、端口和容器准备 | playbook、执行日志、失败与重试说明 | M6 |
| 15 | CI/CD | GitHub Actions 执行构建、测试、镜像和制品留存 | workflow、运行记录、失败门禁 | M1 起持续 |
| 16 | SkyWalking | 急单插入、任务创建和调度链路追踪 | trace 截图、traceId 关联日志、跨服务拓扑 | M6 |
| 17 | Prometheus + Grafana | 任务吞吐、冲突、死锁、延迟和 AGV 利用率 | 指标定义、Prometheus 抓取、Grafana 面板 | M5-M6 |

## 4. 验收证据格式

每个技术项最终应提供：

1. 实现位置：源码、配置或部署文件路径。
2. 启动方式：可复制执行的命令。
3. 观察结果：页面、接口响应、日志、指标或任务输出。
4. 失败路径：至少说明一个异常或降级场景。
5. 文档引用：对应架构、接口或部署章节。

## 5. 与项目优先级的关系

- M0-M4 优先保证 Spring Boot、Spring Cloud、微服务、RESTful API、JSON-RPC、Redis、Kafka、Eureka 和部署基础。
- M5-M6 补齐负载均衡、Ansible、GitHub Actions、SkyWalking、Prometheus 和 Grafana。
- M7 完成 HDFS + MapReduce、Elasticsearch 和 Serverless 的离线分析能力。
- 推荐技术不得挤占路径冲突、死锁恢复、状态一致性和实时可视化等 P0 功能。
