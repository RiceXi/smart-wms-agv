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
Ran 28 tests in 4.738s
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

## SQLite 运维

已实际执行：

```text
verify source -> backup -> restore -> verify restored
```

原库、备份和恢复库均包含 `entities`、`state` 表。

## 未完成运行验证

本机没有 Docker 和监控组件，因此以下内容只完成配置编写和 YAML 校验：

- `docker compose config`。
- 应用镜像构建。
- Nginx 容器启动。
- Prometheus 容器启动和实际抓取。
- Grafana 容器启动和面板加载。

CI 工作流已经配置这些步骤，需要在安装 Docker 的环境或 GitHub Actions 中取得最终运行证据。不得在课程报告中把未运行的容器组件标记为已运行验收。
