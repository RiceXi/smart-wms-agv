# SmartWMS + AGV 调度（成员二个人版）

仓储 WMS 与 AGV 调度系统的**个人独立实现**。仓库整体需求见 `docs/requirement.md`，
公共数据契约见 `docs/data-schema.md`，验收用例见 `docs/acceptance.md`。

- 作者：成员二 Zsource-Fuyi
- 分支：`dev/member-2`
- 目录：`implementations/member-2/`

## 1. 技术选型

| 项     | 选择                                   | 理由                                                    |
| ------ | -------------------------------------- | ------------------------------------------------------- |
| 运行时 | Python 3.11+（只用标准库）             | 干净环境零安装即可启动，满足 AC1；无 pip 依赖，离线可跑 |
| 存储   | SQLite（标准库 `sqlite3`，WAL 模式） | 单文件、事务安全、便于"重启后数据不丢"的验收            |
| 后端   | `http.server.ThreadingHTTPServer`    | 标准库即可提供并发 HTTP + SSE                           |
| 前端   | 原生 HTML/CSS/JS + Canvas              | 无 npm 依赖；地图用 Canvas 绘制，帧率 120 fps           |
| 测试   | `unittest`（标准库）                 | `python -m unittest discover -s tests` 直接跑         |

课程推荐技术栈（Spring Boot / Kafka / Redis / Eureka）由整合版承担，本个人版不引入。
两者的对应关系见 `docs/design.md` 第 7 节。

## 2. 目录结构

```text
implementations/member-2/
├── README.md              本文件
├── ai-notes.md            AI 过程记录（分支 AGENTS.md 要求放在实现目录根部）
├── docs/
│   ├── api-contract.md    冻结版接口契约（前后端唯一依据）
│   ├── design.md          架构与算法设计
│   ├── acceptance.md      AC1–AC20 逐条执行记录与证据
│   ├── scenario-results.json  8 个固定场景的实测结果
│   └── evidence/          截图等证据文件
├── fixtures/
│   ├── maps/              地图 JSON（由代码导出，勿手改）
│   └── scenarios/         场景 JSON（由代码导出，勿手改）
├── scripts/
│   ├── run_scenarios.py       场景回归 + 结果落盘
│   ├── save_bench.py          性能基准采集 + 落盘
│   ├── save_test_evidence.py  跑全量测试并把输出落盘为证据
│   ├── smoke_api.py           REST/SSE 冒烟
│   └── debug_*.py             开发期排查脚本
├── tests/                 自动化测试（88 个用例）
├── web/                   前端（index.html / app.js / style.css）
└── wms/                   后端包
    ├── app.py             应用上下文（装配、场景重置、注入）
    ├── api.py             REST 路由与处理器
    ├── server.py          HTTP 服务 + SSE + 静态资源
    ├── service.py         业务事实写入层（主数据/库存/订单/任务状态机）
    ├── simulator.py       tick 仿真主循环
    ├── pathfinding.py     时空 A*、割点、回退格、待命区寻路
    ├── reservations.py    顶点/有向边预约表、抢占
    ├── deadlock.py        等待图与牺牲者选择
    ├── scheduler.py       任务指派评分
    ├── mapdata.py         地图版本与封路管理
    ├── mapgen.py          地图生成
    ├── analytics.py       统计口径
    ├── scenarios.py       8 个固定场景定义
    ├── db.py              SQLite schema 与封装
    ├── events.py          事件总线
    ├── errors.py          结构化错误
    ├── util.py            通用工具
    ├── config.py          全部可调参数
    └── cli.py             命令行入口
```

## 3. 快速开始

前置条件：**只需要 Python 3.11 及以上**，不需要 pip install，不需要 Node。

```text
cd implementations/member-2

# 1) 启动服务（含前端页面，默认 http://127.0.0.1:8099）
python -m wms serve --port 8099

# 2) 浏览器打开
#    http://127.0.0.1:8099/
```

首次启动会自动导入 `single-agv` 场景并建库（SQLite 文件位于 `implementations/member-2/data/wms.db`）。

常用命令：

```text
python -m wms serve  --host 127.0.0.1 --port 8080   # 启动服务
python -m wms seed   --scenario peak-10             # 导入指定场景
python -m wms demo   --scenario peak-10 --ticks 300 # 无界面跑一段仿真
python -m wms bench  --scenario peak-30 --ticks 600 # 采集性能数据
python -m wms export-fixtures                       # 重新导出 fixtures
```

数据目录可用 `--data-dir` 或环境变量 `WMS_DATA_DIR` 覆盖：

```text
python -m wms --data-dir D:\tmp\wms-data serve --port 8080
```

## 4. 运行测试

```text
cd implementations/member-2
python -m unittest discover -s tests -t tests -v
```

预期结果：`Ran 88 tests ... OK`。

场景回归（同时把结果写入 `docs/scenario-results.json`）：

```text
python scripts\run_scenarios.py
```

## 5. 固定演示场景

| 场景 id            | 说明           | 关键看点                                        |
| ------------------ | -------------- | ----------------------------------------------- |
| `single-agv`     | 单 AGV 闭环    | 订单 → 任务 → 路径 → 仿真 → 完成            |
| `peak-10`        | 10 台并发      | 个人版规模基线，10/10 完成、0 死锁              |
| `peak-30`        | 30 台并发      | 整合版规模基线，能检测并恢复死锁                |
| `head-to-head`   | 窄巷相向冲突   | 禁止穿边、等待与绕行                            |
| `cycle-deadlock` | 三车环形等待   | 等待图成环 → 选牺牲者 → 回退重规划            |
| `roadblock`      | 临时封路重规划 | tick 25/60/120 三次封路，受影响路径升版本重规划 |
| `low-battery`    | 低电量充电     | 充电队列、充电位容量不突破                      |
| `rush-order`     | 急单插入       | tick 20/90 插入优先级 10/9 的急单               |

在页面顶部"重置"按钮的下拉框里可以直接切换场景。

## 6. 事件注入（演示用）

页面顶部"事件注入"支持以下类型（后端 `POST /api/v1/sim/inject`）：

| type           | 字段                               | 说明                          |
| -------------- | ---------------------------------- | ----------------------------- |
| `fault`      | `target`、`code`               | AGV 进入 `fault`            |
| `offline`    | `target`                         | AGV 进入 `offline`          |
| `recover`    | `target`                         | 故障/离线 AGV 恢复为 `idle` |
| `roadblock`  | `cells`、`reason`              | 封路并生成新地图版本          |
| `rush_order` | `priority`、`skuCode`、`qty` | 插入高优先级订单并立即释放    |

## 7. 已知限制

- 单进程单库：同一数据目录同时跑 `serve` 和其它命令会争用 SQLite（已加 `busy_timeout` 等待，但建议分开目录）。
- 30 台 AGV 场景有 7 个任务按"阻塞时间超过上限"规则失败，这是显式失败而非静默卡死；个人版目标规模为 10 台。
- 没有鉴权、没有多租户、没有分布式部署；这些由整合版的课程推荐技术栈承担。
