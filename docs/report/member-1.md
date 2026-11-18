# 个人开发分析报告：成员一

## 1. 工具与开发概述

- GitHub：RiceXi
- AI 工具：DeepSeek-V4.1-Flash
- 使用方式：通过对话式提示完成需求拆解、领域建模、代码生成、测试设计和错误复盘，再由本人执行、检查并修正。
- 个人版本分支：dev/member-1
- 源码目录：implementations/member-1/
- 技术栈：Python 3.14.5、标准库、SQLite、原生 HTML/CSS/JavaScript、Canvas 2D、SSE。

## 2. 需求分析

个人版本围绕一条闭环实现：

```text
出库订单 -> 创建任务 -> 指派 AGV -> 时空路径 -> tick 仿真 -> 地图完成 -> 统计
```

需求重点不是页面数量，而是业务事实一致。订单释放必须预占库存，重复释放必须幂等；任务必须经过 `pending -> assigned -> running -> completed`；AGV 每 tick 只能移动一格；同一格同一 tick 不能被两车占用；有向边用于阻止相向穿边；完成事件才能扣减实际库存。

个人版覆盖公共契约中的 WMS 主数据、库存、订单、任务、AGV、冻结路径、冲突处理、封路、充电、持久化、实时地图和统计。Kafka、Redis、微服务和课程分布式技术放入整合版，不在个人版伪造实现。

## 3. 开发计划

1. 阅读公共需求、数据契约、验收用例和开发契约。
2. 先实现领域状态和 SQLite 实体存储。
3. 实现时间维 A*、格点预约和有向边预约。
4. 实现订单、库存、任务和 AGV 指派闭环。
5. 实现后台 tick 仿真、死锁检测、封路和充电。
6. 实现 REST API、Canvas 地图和操作页面。
7. 编写自动化测试并逐项执行验收。
8. 记录 AI 错误、人工修改、性能和限制。

## 4. 技术选型与架构设计

个人版选择单体应用，原因是个人开发阶段需要低依赖、可离线、易冻结。后端使用 `ThreadingHTTPServer` 提供 REST 和静态文件，核心服务通过 `RLock` 串行化状态变更，后台仿真线程按 tick 推进。

实时数据以 SSE 快照为主链路，每 500 ms 推送一次；页面保留 5 秒 HTTP 轮询作为断线降级。AGV 数量可通过 `--agvs` 配置，已验证 30 台种子和任务闭环。

模块边界：

- `pathfinding.py`：地图、预约表、时间维 A*、等待图环检测。
- `storage.py`：SQLite JSON 实体表和运行态键值表。
- `service.py`：WMS、库存、订单、任务、AGV、调度、仿真、死锁和分析。
- `api.py`：HTTP 路由、静态文件、参数解析和错误映射。
- `app.py`：命令行启动和退出。
- `static/`：操作面板、任务队列、2D Canvas 地图、统计和日志。

## 5. 数据模型与核心流程

SQLite 使用 `entities(kind,id,data)` 保存可查询实体，使用 `state(key,value)` 保存 tick、速度、封路、路径计划和计数器。每个实体带 `created_at`、`updated_at` 和 `version`。

调度流程：

1. 订单释放按有效期优先分配库存并增加 `reserved_qty`。
2. 每个库存来源生成一个任务。
3. 调度器按优先级和创建时间处理 pending 任务。
4. 对每台 idle AGV 构建候选路径。
5. 选择预计 ticks 最短的 AGV。
6. 路径同时预约 `(cell,tick)` 和 `(from,to,tick)`。
7. tick 推进时更新位置、状态、电量和流量。
8. 卸载完成后扣减库存、释放预占、完成订单。

货架格不可通行，任务目标自动转化到货架相邻的可通行格。相向穿边通过反向边预约检查阻止。等待图检测到环后，选择优先级较低的车辆撤销计划和任务，重新进入 pending。

## 6. 关键提示词与迭代过程

主要提示词：

- “读取 README、需求、数据契约、验收用例和个人开发契约，在成员一目录独立完成一个本地可运行版本。”
- “使用 Python 标准库和 SQLite，实现 REST API、时间维 A*、顶点/边预约、tick 仿真、死锁恢复、充电和 Canvas 地图。”
- “为路径冲突、订单幂等、状态机、持久化、封路和 HTTP 健康检查编写自动化测试。”
- “实际启动服务并检查页面，不得仅凭代码推断可运行。”

迭代顺序为：领域存储 -> 路径搜索 -> 订单任务 -> 仿真 -> API -> UI -> 测试 -> 真实运行修正。

## 7. AI 生成效果分析

AI 对标准库 HTTP 服务、SQLite 实体表、A* 搜索和 Canvas 绘制给出了可复用骨架，明显加快了从需求到可运行原型的速度。对于路径预约、任务状态和库存预占等跨模块关系，AI 首轮实现仍需要人工验证。

效果较好的部分：

- 时间维搜索和等待动作。
- 有向边冲突检查。
- REST 路由和统一错误结构。
- 固定 seed 下路径几何稳定。
- 页面信息密度和操作入口。

需要人工判断的部分：

- 什么行为才构成完整任务完成。
- 货架目标与 AGV 可通行格的边界。
- 预约表中的资源所有者一致性。
- 异常场景是否真的可恢复。

## 8. AI 错误、幻觉与人工修改

| 问题 | 初始错误 | 人工修改 | 验证 |
| --- | --- | --- | --- |
| 货架不可达 | 直接把货架格作为 A* 目标 | 改为选择货架相邻可通行格，AGV 在旁装卸 | 单 AGV 闭环测试 |
| 预约所有者冲突 | idle AGV 与路径使用不同 owner | 统一使用 AGV id | 订单实际可指派 |
| 候选路径污染预约表 | 未选中的候选也提前写预约 | 只返回候选，选中后由重建预约表确认 | 多候选指派测试 |
| 相向穿边用例 | 测试只检查最终路径，未区分等待 tick | 检查首步行是否为 wait | 路径测试通过 |
| 充电计划 | 初始仅有 charging 状态 | 增加充电桩目标 path plan 和逐 tick 移动 | 低电量测试通过 |
| Windows 本地端口 | 默认沙箱禁止绑定临时端口 | 在明确的本机服务权限下执行 HTTP 测试 | 20 项测试通过 |

上述错误记录同时保存在 `implementations/member-1/ai-notes.md`。

## 9. 代码结构说明

```text
member-1/
├── app.py
├── run.ps1
├── requirements.txt
├── README.md
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
├── screenshots/
│   └── dashboard.png
└── tests/
    ├── test_acceptance.py
    ├── test_pathfinding.py
    └── test_service.py
```

## 10. 验收结果

实际执行命令：

```powershell
python -m unittest discover -s tests -v
```

结果：`Ran 22 tests in 4.324s / OK`。

| 用例 | 结果 | 证据 |
| --- | --- | --- |
| AC1 本地启动 | 通过 | Web 服务运行于 `127.0.0.1:8765`，`/health` 返回 `ok` |
| AC2 主数据 | 通过 | 种子 3 SKU、10 库位、10 AGV；重复编码和非法坐标测试通过 |
| AC3 出库订单 | 通过 | 订单释放幂等、释放后取消和预占释放测试通过 |
| AC4 任务状态 | 通过 | 终态任务取消被 `TASK_INVALID_STATE` 拒绝 |
| AC5 单 AGV 路径 | 通过 | 单任务从 assigned 经 running 到 completed |
| AC6 同格冲突 | 通过 | 顶点预约测试通过 |
| AC7 相向穿边 | 通过 | 反向边预约测试通过 |
| AC8 首尾相接 | 通过 | tick 后一格进入允许 |
| AC9 死锁恢复 | 通过 | 三车等待环检测、victim 回退、counter 增加 |
| AC10 临时封路 | 通过 | 地图版本变化、受影响任务重规划 |
| AC11 充电 | 通过 | 电量 10% 自动生成 charge plan |
| AC12 急单 | 通过 | 任务优先级修改接口和优先级队列排序 |
| AC13 固定 seed | 通过 | 两次相同场景的路径几何一致 |
| AC14 实时地图 | 通过 | Edge 实际加载 Canvas；SSE 快照推送和轮询降级均可用 |
| AC15 暂停与回放 | 通过 | 暂停后 step 精确推进 2 tick |
| AC16 持久化 | 通过 | 关闭服务后重新打开 SQLite，新增 SKU 仍存在 |
| AC17 查询与筛选 | 通过 | API 支持状态/AGV 查询；页面支持状态、AGV、优先级和订单状态筛选 |
| AC18 统计 | 通过 | 完成订单后统计显示 1 个完成订单和 1 个完成任务 |
| AC19 性能记录 | 通过 | 30 AGV、10 订单、释放平均 152.910 ms、最大 176.866 ms |
| AC20 错误与限制 | 通过 | 未实现和风险已在 README、AI 记录和本报告列出 |

界面检查截图：`implementations/member-1/screenshots/dashboard.png`。

## 11. 性能与限制

实测环境：

- Windows 本机。
- Python 3.14.5。
- 30 台 AGV。
- 10 个订单、10 个任务。
- 释放订单平均 152.910 ms，最大 176.866 ms。
- 300 tick 推进耗时 6.425 秒。
- 10 个任务全部完成，无活动任务残留。

限制：

- 未测量 30 台 AGV 场景的最终前端 fps。
- 未执行 500 场景随机压测和 30 分钟 soak test。
- 死锁演示是可重复构造场景，不是工业级随机交通覆盖。
- 未实现 WebSocket、Kafka、Redis、Eureka、微服务和课程分布式技术，这些属于整合版；个人版使用 SSE 完成实时推送。
- 3D 和真实 AGV 硬件不在个人版范围。

## 12. 自我评价

个人版完成了可运行、可操作、可持久化、可重复测试的闭环。最有价值的工程点是把冲突从二维格点扩展到时间轴和有向边，并用固定场景验证，而不是靠页面动画表现。

不足是规模测试和工业级死锁覆盖还不足。个人版本适合作为三人对比的基础实现，但在整合阶段仍需对实时推送、分布式状态、压力和长时间稳定性做专门验证。

## 13. 运行说明

```powershell
cd implementations/member-1
python app.py
```

打开：

```text
http://127.0.0.1:8000
```

重置数据：

```powershell
Remove-Item .\data\wms.sqlite
python app.py
```

默认数据库位置：

```text
implementations/member-1/data/wms.sqlite
```
