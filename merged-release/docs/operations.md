# SmartWMS 运维手册

## 1. 本机启动

```powershell
cd merged-release
python app.py
```

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-WebRequest http://127.0.0.1:8000/metrics
```

## 2. 配置

优先级为命令行参数高于环境变量高于默认值。

```powershell
$env:SMARTWMS_HOST = "0.0.0.0"
$env:SMARTWMS_PORT = "8000"
$env:SMARTWMS_DB = "data/wms.sqlite"
$env:SMARTWMS_AGVS = "10"
$env:SMARTWMS_LOG_LEVEL = "info"
python app.py
```

## 3. 容器拓扑

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f app gateway
```

检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8080/ready
Invoke-WebRequest http://127.0.0.1:8080/metrics
```

监控入口：

- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`

Grafana 默认口令只用于本地演示。共享环境必须设置新的密码。

## 4. 数据库备份

备份可以在应用运行时执行：

```powershell
python scripts\database.py backup --db data\wms.sqlite --output backups\wms-backup.sqlite
```

脚本使用 SQLite backup API，输出文件完成后会生成业务表清单。

建议策略：

- 演示前执行一次。
- 注入故障、批量释放订单或大规模仿真前执行一次。
- 生产化环境按小时或每日备份，并设置保留周期。

## 5. 数据库恢复

恢复前停止应用：

```powershell
python scripts\database.py restore --db data\wms.sqlite --input backups\wms-backup.sqlite --force
```

恢复后启动并检查：

```powershell
python app.py
Invoke-RestMethod http://127.0.0.1:8000/ready
```

## 6. 监控与告警建议

建议至少配置以下告警：

- `/ready` 连续失败 3 次。
- HTTP 5xx 比例在 5 分钟内超过 2%。
- 活动任务数持续增长且完成任务数不增长。
- 死锁检测持续增加，恢复数不增长。
- 容器重启次数异常。
- SQLite 所在磁盘剩余空间过低。

## 7. 故障排查

### 应用启动失败

检查端口、数据库目录权限和日志：

```powershell
python app.py --log-level debug
```

### 前端状态不更新

检查：

1. `/api/state` 是否返回新快照。
2. `/api/stream` 是否保持连接。
3. Nginx 是否对 SSE 开启 `proxy_buffering off`。

### 就绪检查失败

`/ready` 会执行数据库 ping。检查数据文件是否存在、是否被其他进程占用、目录是否可写。

### 指标没有数据

检查：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/metrics
```

Prometheus 默认每 5 秒抓取 `app:8000/metrics`。

## 8. 发布流程

1. 运行完整测试。
2. 运行稳定性冒烟。
3. 校验 OpenAPI JSON。
4. 构建镜像。
5. 校验 Compose 和 Prometheus 配置。
6. 启动新版本并检查 `/ready`。
7. 检查 Grafana 请求量、错误率和业务指标。
8. 保留上一版本数据库备份。

## 9. 当前未覆盖项

- 多节点高可用。
- TLS 自动签发。
- 集中日志系统。
- 身份认证和细粒度授权。
- 自动告警通知渠道。
- 30 分钟持续运行和 500 场景完整压测。
