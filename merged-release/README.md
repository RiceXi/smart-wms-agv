# SmartWMS 系统基础版

SmartWMS 是一个可本地运行、可容器部署、可监控和可恢复的仓储 WMS 与 AGV 调度系统基础版。核心能力来自成员一冻结版本，本目录增加系统级配置、可观测性、接口契约、部署拓扑、数据库备份恢复和 CI。

当前定位是独立系统基础版，不代表三名成员实现对比已经完成。后续仍可在 `feat/integrate` 阶段吸收其他冻结版本的业务优点。

## 系统能力

- SKU、库位、库存、订单、任务和 AGV 管理。
- 库存预占、订单幂等释放、任务状态机和异常重派。
- 时空 A*、格点预约、有向边预约和等待图死锁恢复。
- tick 仿真、封路重规划、低电量充电、任务统计和事件流。
- Canvas 2D 实时地图、SSE 快照推送、HTTP 轮询降级和 FPS 指标。
- SQLite 持久化与一致性备份恢复。
- JSON 结构化日志、请求 ID、健康检查、就绪检查和 Prometheus 指标。
- OpenAPI 3.1 接口契约。
- Nginx、Prometheus、Grafana 和 Docker Compose 部署拓扑。
- GitHub Actions 自动执行测试、压测冒烟、契约校验和容器构建。

## 架构

```text
Browser
   |
   v
Nginx gateway :8080
   |
   v
SmartWMS app :8000   ---> SQLite volume /data/wms.sqlite
   |        |
   |        +----> JSON logs to stdout/stderr
   |
   +----> /metrics <---- Prometheus :9090 <---- Grafana :3000
```

业务核心保持单体进程，原因是 AGV 仿真、路径预约和 SQLite 写入要求单一写入者。Nginx、Prometheus 和 Grafana 通过清晰的进程边界提供入口治理和可观测性，不引入没有实际用途的微服务或消息队列。

详细设计见 [系统架构](docs/architecture.md)，运行与故障处理见 [运维手册](docs/operations.md)，实际验证边界见 [验证记录](docs/verification.md)。

## 本地运行

环境要求：

- Python 3.10 或更高版本。
- 现代浏览器，支持 Canvas、Fetch、EventSource 和 ES2022。
- 核心运行时没有第三方 Python 依赖。

```powershell
cd merged-release
python app.py
```

访问 `http://127.0.0.1:8000`。

常用环境变量：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SMARTWMS_HOST` | `127.0.0.1` | 监听地址 |
| `SMARTWMS_PORT` | `8000` | 监听端口 |
| `SMARTWMS_DB` | `data/wms.sqlite` | SQLite 文件 |
| `SMARTWMS_AGVS` | `10` | AGV 数量 |
| `SMARTWMS_MAP_WIDTH` | 自动 | 地图宽度 |
| `SMARTWMS_MAP_HEIGHT` | 自动 | 地图高度 |
| `SMARTWMS_LOG_LEVEL` | `info` | `debug`、`info`、`warning`、`error` |
| `SMARTWMS_VERSION` | `1.0.0` | 版本号 |

## 容器部署

要求 Docker Engine 和 Docker Compose v2。

```powershell
cd merged-release
docker compose up --build -d
```

服务入口：

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| SmartWMS | `http://127.0.0.1:8080` | 业务系统入口 |
| Prometheus | `http://127.0.0.1:9090` | 指标查询 |
| Grafana | `http://127.0.0.1:3000` | 监控面板 |

Grafana 默认账号为 `admin`，默认密码为 `smartwms`。演示环境也通过 `GRAFANA_ADMIN_USER` 和 `GRAFANA_ADMIN_PASSWORD` 覆盖。

停止服务：

```powershell
docker compose down
```

删除监控卷：

```powershell
docker compose down --volumes
```

## 测试

```powershell
python -m unittest discover -s tests -v
```

当前结果：`Ran 28 tests in 4.738s / OK`。

稳定性冒烟：

```powershell
python tests\stress_runner.py --agvs 10 --orders 2 --scenarios 5 --ticks 200
```

当前结果：5 个场景全部完成，零同格冲突、零相向穿边、零卡住任务，总耗时 `1.219s`。证据保存在 `test-results/system-foundation-smoke.json`。

接口契约：

```text
static/openapi.json
```

运行中也可访问：

```text
http://127.0.0.1:8000/api/openapi.json
```

## 关键接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/health` | 存活检查 |
| GET | `/ready` | 数据库就绪检查 |
| GET | `/metrics` | Prometheus 指标 |
| GET | `/api/state` | 完整业务快照 |
| GET | `/api/stream` | SSE 实时快照 |
| GET | `/api/openapi.json` | OpenAPI 契约 |
| POST | `/api/orders` | 创建出库订单 |
| POST | `/api/orders/{id}/release` | 释放订单并生成任务 |
| POST | `/api/sim/start` | 启动仿真 |
| POST | `/api/sim/step` | 单步推进 |
| POST | `/api/sim/roadblock` | 注入或解除封路 |
| GET | `/api/analytics` | 统计指标 |

## 数据与备份

默认数据库：

```text
merged-release/data/wms.sqlite
```

创建一致性备份：

```powershell
python scripts\database.py backup --db data\wms.sqlite --output backups\wms-backup.sqlite
```

校验数据库：

```powershell
python scripts\database.py verify --db data\wms.sqlite
```

恢复前必须停止应用：

```powershell
python scripts\database.py restore --db data\wms.sqlite --input backups\wms-backup.sqlite --force
```

## 技术选择说明

本基础版只引入当前架构真正使用并可验证的技术：

- Nginx：边缘代理、安全响应头、SSE 反向代理参数。
- Prometheus：拉取应用指标。
- Grafana：预置运营面板。
- Docker Compose：单机完整拓扑和持久卷。
- GitHub Actions：持续集成。
- OpenAPI：接口契约。
- SQLite backup API：一致性备份和恢复。

Redis、Kafka、Eureka、服务网格和多节点调度属于后续规模扩展。当前系统没有伪造这些链路，也不会仅凭架构图声称已经实现。

## 已知限制

- 当前是单节点、单写入者系统。
- SQLite 适合课程演示和单实例部署，不适合多实例共享写入。
- 30 AGV 高并发、500 场景完整压测和 30 分钟 soak test 仍应作为整合阶段验收项。
- Docker 和监控配置需要在安装 Docker 的环境或 CI 中完成运行验证。
- SSE 是实时主链路，不是 WebSocket。
