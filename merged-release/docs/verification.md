# SmartWMS 系统基础版验证记录

验证日期：2026-11-22

环境：

- Windows
- Python 3.14.5
- Node.js
- 本机未安装 Docker、Nginx、Prometheus 和 Grafana

## 自动化测试

命令：

```powershell
python -m unittest discover -s tests -v
```

结果：

```text
Ran 28 tests in 4.805s
OK
```

覆盖范围：

- 原 WMS、订单、库存、任务和 AGV 业务测试。
- 路径、格点预约、有向边预约和等待图。
- 固定 seed、HTTP、持久化和压测脚本。
- 健康检查、就绪检查和安全响应头。
- Prometheus HTTP 与业务指标。
- OpenAPI 契约。
- 请求 ID 与结构化请求日志。
- SQLite 备份、恢复和数据校验。

## 稳定性冒烟

命令：

```powershell
python tests\stress_runner.py --agvs 10 --orders 2 --scenarios 5 --ticks 200 --output test-results\system-foundation-smoke.json
```

结果：

- 场景成功：5/5。
- 顶点冲突：0。
- 边冲突：0。
- 卡住任务：0。
- 总耗时：1.219 秒。

证据文件：

```text
test-results/system-foundation-smoke.json
```

## 运行时接口

实际启动：

```powershell
python app.py --port 8765 --db data\system-foundation.sqlite --log-level warning
```

验证结果：

| 接口 | 结果 |
| --- | --- |
| `/health` | `status=ok`，返回版本和 tick |
| `/ready` | 数据库检查为 `ok` |
| `/metrics` | 返回 Prometheus 指标，包括业务和 HTTP 请求指标 |
| `/api/openapi.json` | 返回 SmartWMS API 1.0.0，共 33 个路径 |

响应头：

- `X-Request-ID` 已返回并写入结构化日志。
- `X-Content-Type-Options: nosniff`。
- `X-Frame-Options: DENY`。
- `Referrer-Policy: no-referrer`。

## 配置语法

使用临时 YAML 解析器验证：

- `docker-compose.yml`。
- `deploy/prometheus/prometheus.yml`。
- Grafana 数据源和面板 provisioning。
- `.github/workflows/system-foundation-ci.yml`。

结果：5 个 YAML 文件全部通过语法解析。

## GitHub Actions

- 工作流：`system-foundation-ci`
- 运行：`44948642855`
- 结果：通过

CI 在 Ubuntu 和 Docker 环境中实际执行：

- Python 完整测试。
- 稳定性冒烟。
- JavaScript 语法。
- OpenAPI 和压测结果 JSON 校验。
- `docker compose config`。
- 应用镜像构建。
- Prometheus `promtool` 配置校验。

运行记录：

```text
https://github.com/RiceXi/smart-wms-agv/actions/runs/44948642855
```

## SQLite 运维

已实际执行：

```text
verify source -> backup -> restore -> verify restored
```

原库、备份和恢复库均包含 `entities`、`state` 表。

## 运行验证边界

本机没有 Docker 和监控组件。GitHub Actions 已经完成 Compose 模型、应用镜像和 Prometheus 配置验证，但以下内容仍需要在安装 Docker 的环境中做端到端启动验收：

- `docker compose up` 完整四服务启动。
- Nginx 实际反向代理和 SSE 连接。
- Prometheus 实际抓取应用指标。
- Grafana 数据源和面板加载。

上述运行项未完成前，不得在课程报告中把完整容器栈标记为端到端运行验收。
