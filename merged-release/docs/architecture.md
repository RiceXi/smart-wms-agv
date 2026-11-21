# SmartWMS 系统架构

## 1. 架构目标

系统需要同时满足四项要求：

1. 业务调度结果真实可复现。
2. 单机环境能够独立运行。
3. 进程重启后数据不丢失。
4. 部署后能够检查健康状态、定位请求和观察关键业务指标。

当前方案采用“单体核心 + 独立基础设施进程”。单体边界保护 AGV 调度、路径预约、运行态和 SQLite 写入的一致性，Nginx、Prometheus 和 Grafana 处理入口与观测职责。

## 2. 组件

| 组件 | 职责 | 当前实现 |
| --- | --- | --- |
| Web 前端 | 地图、订单、任务、AGV、统计操作 | 原生 HTML/CSS/JavaScript、Canvas、SSE |
| HTTP API | REST、SSE、静态资源、健康检查 | Python `ThreadingHTTPServer` |
| 业务服务 | WMS、任务、AGV、仿真、统计 | `WmsService` |
| 路径与预约 | 时空 A*、顶点/边预约、死锁恢复 | `pathfinding.py` |
| 持久化 | 实体、运行态和事件 | SQLite JSON 实体表 |
| 可观测性 | 日志、请求 ID、指标 | `observability.py` |
| 边缘代理 | 统一入口、SSE 代理、安全响应头 | Nginx |
| 指标存储 | 拉取并保存时序指标 | Prometheus |
| 可视化 | 运营面板 | Grafana |
| CI | 测试、压测、契约、镜像和监控配置检查 | GitHub Actions |

## 3. 运行时数据流

### 3.1 订单与调度

```text
创建订单
  -> 释放订单
  -> 预占库存
  -> 创建拣选任务
  -> 按优先级调度
  -> 选择 idle AGV
  -> 时间维 A* 构建取货和放货路径
  -> 写入顶点与有向边预约
  -> tick 推进真实位置
  -> 完成后扣减库存并更新订单
```

### 3.2 实时地图

```text
服务端快照
  -> /api/stream SSE
  -> 浏览器更新状态
  -> requestAnimationFrame 绘制 Canvas

SSE 断开
  -> 5 秒 HTTP 轮询降级
```

### 3.3 观测

```text
HTTP 请求
  -> 生成 X-Request-ID
  -> 记录结构化访问日志
  -> 更新请求计数与耗时指标

Prometheus
  -> GET /metrics
  -> 读取应用和业务状态
  -> Grafana 展示
```

## 4. 可靠性设计

- SQLite 事务保证实体和运行态写入的一致性。
- 多 tick 推进使用批量持久化，避免每 tick 多次提交。
- 健康检查区分 `/health` 和 `/ready`。
- 数据库备使用 SQLite backup API，而不是直接复制正在写入的文件。
- Nginx 对 SSE 关闭代理缓冲并延长读取超时。
- 容器内应用以非 root 用户运行。
- 容器启用 `no-new-privileges`。
- 请求 ID 同时返回客户端并写入结构化日志，便于关联问题。

## 5. 可观测性

主要指标：

| 指标 | 含义 |
| --- | --- |
| `wms_http_requests_total` | 按方法、路由和状态码统计请求 |
| `wms_http_request_duration_seconds_sum` | 请求累计耗时 |
| `wms_http_request_duration_seconds_count` | 请求累计次数 |
| `wms_tick` | 当前仿真 tick |
| `wms_tasks_active` | 活动任务数 |
| `wms_tasks_completed` | 已完成任务数 |
| `wms_agvs` | AGV 总数 |
| `wms_agvs_idle` | 空闲 AGV 数 |
| `wms_agvs_charging` | 充电 AGV 数 |
| `wms_agvs_fault` | 故障或离线 AGV 数 |
| `wms_business_conflicts_prevented` | 冲突阻断次数 |
| `wms_business_deadlocks_detected` | 死锁检测次数 |
| `wms_business_deadlocks_resolved` | 死锁恢复次数 |

路由标签使用模板化路径，例如 `/api/tasks/{id}/cancel`，避免任务 ID 导致指标基数失控。

## 6. 安全边界

- API 当前没有账号体系，适用于本机或受控演示环境。
- Nginx 添加 `X-Content-Type-Options`、`X-Frame-Options` 和 `Referrer-Policy`。
- 请求体上限为 2 MB。
- 容器不以 root 运行。
- Grafana 默认密码仅用于本地演示，部署到共享环境必须覆盖。
- 如需公网部署，应增加身份认证、TLS、网络访问控制、速率限制和审计日志。

## 7. 规模扩展路径

当前单写入架构在单节点下容易保证业务正确性。后续多节点扩容时，不应直接把 SQLite 挂到多个实例，而应按以下顺序演进：

1. 将业务状态迁移到 PostgreSQL。
2. 将预约和任务队列抽象为独立仓储接口。
3. 使用 Redis 承载短期预约、分布式锁和幂等键。
4. 使用 Kafka 传递订单、任务和 AGV 事件。
5. 将 WMS、任务、调度、仿真和查询拆成独立服务。
6. 引入 Eureka、网关、链路追踪和集中日志。

这些属于规模扩展，不是当前基础系统成立的前提。
