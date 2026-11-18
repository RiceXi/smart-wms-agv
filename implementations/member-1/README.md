# SmartWMS Member 01

成员一独立实现的仓储 WMS 与 AGV 调度系统。使用 DeepSeek-V4.1-Flash 辅助完成需求分析、设计、编码、测试和报告。

## 功能

- SKU、库位、库存和出库订单管理。
- 订单释放、库存预占和幂等释放。
- 任务池、优先级、取消、重派和任务状态机。
- 10 台 AGV 的本地 tick 仿真。
- 时空 A* 路径规划。
- 格点和有向边预约，阻止同格冲突与相向穿边。
- 等待图死锁检测、回退和重派。
- 临时封路与路径重规划。
- 低电量阈值、充电位容量和充电恢复。
- 10-30 台可配置 AGV，默认 10 台。
- 2D Canvas 仓库地图、路径、AGV 状态、任务、事件和分析。
- SSE 实时快照推送，并保留 5 秒 HTTP 轮询降级。
- 释放后的订单可在没有完成任务时安全取消。
- SQLite 本地持久化。
- REST API 与原生 Web 界面。

## 环境

- Python 3.10 或更高版本。
- 不需要第三方 Python 包。
- 现代浏览器，支持 Canvas、Fetch 和 ES2022。

本次实际验证环境：Windows、Python 3.14.5、Microsoft Edge。

## 启动

在 `implementations/member-1/` 目录执行：

```powershell
python app.py
```

或：

```powershell
.\run.ps1
```

默认访问地址：

```text
http://127.0.0.1:8000
```

指定端口和数据文件：

```powershell
python app.py --port 8765 --db .\data\wms.sqlite
```

启动 30 台 AGV：

```powershell
python app.py --port 8765 --agvs 30 --db .\data\wms-30.sqlite
```

## 数据位置

默认 SQLite 文件：

```text
implementations/member-1/data/wms.sqlite
```

数据库保存 SKU、库位、库存、订单、任务、AGV、事件、仿真 tick、预约计划和封路状态。数据文件不会提交到 Git。

## 测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖：

- 路径避开货架和封路格。
- 同格预约冲突。
- 相向穿边阻止。
- 首尾相接允许。
- 等待图环检测。
- 订单释放幂等。
- 任务状态约束。
- 低电量充电。
- 封路重规划。
- 持久化重启。
- HTTP 健康检查和状态接口。

最终结果：22 个测试全部通过。

## 页面操作

- `运行仿真`：启动后台 tick 线程。
- `暂停`、`单步`、`1x/4x/8x`：控制仿真。
- `生成演示订单`：自动创建并释放一个出库订单。
- `选择封路点`：开启后点击地图格点封闭或解除封路。
- `死锁演示`：构造三车等待环并执行检测与回退。
- 点击 AGV：查看状态、电量、载荷与等待信息。
- `注入故障`、`恢复车辆`、`低电量`：验证异常和充电路径。
- 主数据区支持新增、编辑、删除 SKU 和库位；支持新增库存。
- 订单区支持创建、释放和取消草稿订单。
- 任务队列支持优先级增加、取消和重派。

## API

主要接口：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/health` | 健康检查 |
| GET | `/api/state` | 完整快照 |
| GET | `/api/stream` | SSE 实时快照流 |
| POST | `/api/skus` | 创建 SKU |
| PUT/DELETE | `/api/skus/{id}` | 更新或删除 SKU |
| POST | `/api/locations` | 创建库位 |
| PUT/DELETE | `/api/locations/{id}` | 更新或删除库位 |
| POST | `/api/inventory` | 登记库存 |
| POST | `/api/orders` | 创建订单 |
| POST | `/api/orders/{id}/release` | 释放订单 |
| POST | `/api/orders/{id}/cancel` | 取消草稿或释放后订单 |
| POST | `/api/tasks/{id}/priority` | 调整优先级 |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| POST | `/api/tasks/{id}/reassign` | 重派任务 |
| POST | `/api/sim/start` | 启动仿真 |
| POST | `/api/sim/pause` | 暂停仿真 |
| POST | `/api/sim/step` | 单步推进 |
| POST | `/api/sim/speed` | 设置速度 |
| POST | `/api/sim/roadblock` | 注入或解除封路 |
| POST | `/api/sim/deadlock` | 运行死锁演示 |
| GET | `/api/analytics` | 查询统计 |

## 代码结构

```text
member-1/
├── app.py
├── run.ps1
├── requirements.txt
├── ai-notes.md
├── wms_agv/
│   ├── api.py
│   ├── pathfinding.py
│   ├── service.py
│   └── storage.py
├── static/
│   ├── index.html
│   ├── styles.css
│   └── app.js
└── tests/
    ├── test_acceptance.py
    ├── test_pathfinding.py
    └── test_service.py
```

## 实现限制

- 个人版采用单体进程和 SQLite，不实现 Kafka、Redis、Eureka 和微服务拆分。
- 地图固定为 12x8，默认 10 台 AGV。
- 支持命令行配置最多 30 台 AGV；默认页面仍使用 10 台，便于稳定演示。
- 实时主链路为 SSE；每 5 秒保留 HTTP 轮询作为降级。
- Canvas 渲染已做视觉检查，但未用专业性能工具测量 30 台规模帧率。
- 死锁演示使用构造等待环验证检测与恢复，不代表复杂随机交通中的完整工业级死锁覆盖。
- 3D、3D 物理碰撞、真实 AGV 和外部地图不在个人版范围。
