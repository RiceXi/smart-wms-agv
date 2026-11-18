# AI 使用记录：成员二

> 按 `dev/member-2` 分支 `AGENTS.md` 的"AI 过程记录要求"，本文件是实现目录根部的 `ai-notes.md`；
> 最终内容已整理进 `docs/report/member-2.md`。

## 工具信息

- 工具名称：MiniMax Code（本地桌面端）
- 模型版本：MiniMax M3.1 Flash（`MiniMax-M3.1-Flash`）
- 访问日期：2026-11-18
- 使用方式：
  - 主会话直接编写后端（`wms/`）、测试（`tests/`）、文档；
  - 通过子 Agent（`worker`）并行编写前端（`web/`），子 Agent 拿到的是冻结版接口契约
    `docs/api-contract.md`，产出后由主会话做契约核对与联调；
  - 主会话负责全部缺陷定位、修复与验收执行。

## 开发目标

在 `dev/member-2` 分支的 `implementations/member-2/` 目录内，独立完成一套可本地运行的
仓储 WMS + AGV 调度系统，并逐条执行公共契约 `docs/acceptance.md` 的 AC1–AC20。
可观察的完成条件：

1. 干净环境（只装 Python）可启动服务与前端，零第三方依赖；
2. 订单 → 任务 → 路径 → 仿真 → 完成 的闭环可运行；
3. 同格冲突、相向穿边、死锁环、临时封路、低电量充电、急单插队均有可复现的固定场景；
4. 同 seed 重复运行结果一致；
5. 数据重启不丢；
6. AC1–AC20 逐条执行并留下命令级证据；
7. 自动化测试全绿。

## 关键提示词

| # | 提示词要点 | 目标 |
| --- | --- | --- |
| 1 | "先读 `docs/00-project-charter.md` 到 `docs/08-risk-register.md`，再读 `docs/requirement.md`、`docs/data-schema.md`、`docs/acceptance.md`，然后列出实现计划与需要我自己决策的点" | 建立全局约束，避免违反公共契约 |
| 2 | "技术选型只用 Python 标准库 + SQLite + 原生 Canvas，不引入 pip/npm 依赖；理由写进报告" | 满足 AC1 干净环境可启动 |
| 3 | "先冻结 `docs/api-contract.md`（路径、请求/响应形状、枚举、SSE 格式），再让前后端分别实现" | 避免前后端并行开发时的接口漂移 |
| 4 | "把调度算法（时空 A*、预约表、等待图死锁检测）写成不依赖 UI 和数据库的纯模块，并用 unittest 覆盖" | 满足"路径、预约和死锁算法必须可在无 UI 条件下测试" |
| 5 | "写一个调试脚本打印每个 tick 的 AGV 状态、阻塞计数和等待图，用它定位车辆为什么不走" | 定位死锁与卡死根因 |
| 6 | "用子 Agent 按契约实现前端 Canvas 控制台，完成后自查：接口调用是否全部命中契约、有无外链资源" | 并行推进，把接口一致性风险前置 |
| 7 | "为每个场景补一个定时事件机制，让封路场景真的会封路、急单场景真的注入急单" | 修掉"场景定义存在但没有真正触发对应逻辑"的假覆盖 |
| 8 | "逐条执行 AC1–AC20，把命令和实测结果写进 `docs/acceptance.md`，不要把没跑过的写成通过" | 验收真实性 |
| 9 | "先写测试再改实现：测试失败时先判断是测试写错还是实现有 bug" | 避免为了通过而弱化测试 |
| 10 | "改完调度逻辑必须把 8 个固定场景全部重跑一遍，单条用例通过不代表没有引入回归" | 发现 A19 第一版修复把 peak-10 打成 9/10，及时回退并收窄修复范围 |

## AI 输出

| 产出 | 说明 |
| --- | --- |
| `wms/`（19 个功能模块 + 2 个包文件，约 5400 行） | 配置、错误、工具、SQLite 持久化、地图模型与生成、预约表、时空 A*、指派评分、死锁检测、事件总线、业务服务层、tick 仿真引擎、统计、固定场景、应用装配、REST 路由、HTTP 服务、CLI |
| `web/`（3 个文件，2351 行） | Canvas 实时地图、订单/任务/AGV/分析/事件五个面板、仿真控制条、事件注入面板 |
| `tests/`（91 个用例，6 个测试模块） | 领域与状态机、路径与预约、仿真与场景、REST 与 SSE、持久化、fixture 同源 |
| `fixtures/` | 2 张地图 + 8 个场景的 JSON，由 `python -m wms export-fixtures` 导出 |
| `docs/` | 接口契约、设计说明、验收记录、AI 记录、场景结果 JSON、证据文件 |
| `scripts/` | 场景回归、基准采集、REST/SSE 冒烟、5 个开发期排查脚本 |

## 发现的问题

### A. AI 自己引入并由我修复的缺陷（业务逻辑）

| # | 位置 | 问题 | 影响 |
| --- | --- | --- | --- |
| A1 | `service._allocate` | 库存不足时抛异常，但本行已经预占的库存没有回滚 | 订单释放失败后库存被"吃掉"，AC3 不通过 |
| A2 | `service.assign_task` | 先用 SQL 把 `status` 改成 `assigned`，再调用 `_transition_task`；后者看到 `from == to` 直接 return | `task.assigned` 事件被吞掉，事件流缺一类事件，统计口径对不上 |
| A3 | `service.create_task` | 只往 `events` 表写 `task.created`，没往 `task_events` 写 | "平均任务耗时"恒为 0（起点 tick 取不到） |
| A4 | `pathfinding.plan_path` | 起点不可通行时直接返回 `None` | 施工封路正好落在车辆脚下时，车辆被永久困住 |
| A5 | `reservations.acquire` / `release_agv` | 抢占时用 `keep_from_tick=current_tick+1` 释放受害者预约，语义正好相反（保留了冲突 tick 的预约），抢占永远失败 | 高优先级任务无法抢占低优先级车辆的预约 |
| A6 | `reservations.acquire` | 同优先级也允许抢占 | 违反"同格同 tick 必须阻止"，AC6/AC7 不通过 |
| A7 | `simulator._replan_after_map_change` | `active_roadblocks()` 返回 dict，代码却按对象取 `.cell_id` | 封路接口直接 500 |
| A8 | `simulator._replan` | 重规划失败时保留旧计划 | 车辆会沿着旧计划走进已封路的格点，AC10 不通过 |
| A9 | `simulator` 路径规划 | 规划时没有避让"静止占位"的车辆 | 出现"计划进入别人身上"的抖动 |
| A10 | `simulator` 死锁判定 | 瞬时等待也被判成死锁 | 误报死锁并错误回退 |
| A11 | `simulator` 回退格选择 | 回退格可能落在割点上 | 回退车把唯一通道堵死，死锁无法恢复 |
| A12 | `app._bootstrap` | 重启时用基础地图重建 `MapStore`，丢弃已持久化的地图版本 | 重启后封路记录丢失，车辆会走回已封闭的巷道 |
| A13 | `mapdata.map_from_dict` | 解析站点时丢掉 `code` 字段 | fixture 往返不一致 |
| A14 | `db.Database.__init__` | 没有 `busy_timeout` | 同一数据目录跑 `serve` + `bench` 直接报 `database is locked` |
| A15 | `db.execute` | 多条 SQL 用一个 `execute` 传多组参数 | SQLite 不支持，报错信息误导 |
| A16 | `db.reset_all` | 整库重置时外键约束生效导致删除顺序报错 | 场景重置失败 |
| A17 | `service.release_order` | 写回明细批次/库位后没有重新读取明细 | 生成的任务拣选点用旧数据 |
| A18 | `service._allocate` 返回值 | 多返回值下标错位 | 分配记录指向错误库位 |
| A19 | `simulator._replan` | 入口处判断 `if not agv.active_task_id: return`，把所有"没有任务的车辆"排除在重规划之外 | 空闲返程/回退中的车辆会沿旧计划开进已封路的格点，AC10 不通过（封路格上出现轨迹） |
| A20 | `simulator` 冲突计数点 | `stats["conflicts"]` 有 5 处计数点，其中"前往放货点预约冲突"和"初始路径与现有预约冲突"两处只加计数、不发 `conflict.detected` 事件 | 统计口径与事件流不一致：`head-to-head` 场景统计报冲突 1 次，事件流里却查不到该事件 |
| A21 | `server.WmsServer` | 只设了 `allow_reuse_address = True`，没有在绑定前探测端口 | Windows 上 SO_REUSEADDR 允许两个服务"同时"绑定同一端口，浏览器请求被两个程序随机分流——用户打开 `http://127.0.0.1:8080/` 看到的是别的程序（本机就撞上了另一个课程的 HTTP 实验脚本），表现为"我的界面怎么长这样" |

### B. 接口与前端不一致（子 Agent 产出，主会话修正）

| # | 问题 | 修正 |
| --- | --- | --- |
| B1 | 后端主数据接口返回 snake_case（`batch_rule`、`sku_id`、`drop_location_id`），前端按 camelCase 读 | 在 `util.row_to_camel` 统一转换，API 层所有响应改 camelCase |
| B2 | 订单响应缺 `lines` 与 `taskIds`，前端订单表格显示"合计 0 · 任务 0" | `service.attach_order_details` 批量装配明细与任务列表，快照与列表接口都补齐 |
| B3 | 封路记录字段名不一致（`cell_id` vs `cellId`），前端地图高亮失效 | `get_map` 统一输出 `{id, cellId, reason, active, createdAt}` |
| B4 | 告警结构只有 `kind`，前端期望 `severity`/`code`/`agvId` | 告警增加 `severity`/`code`/`agvId` 字段 |
| B5 | SSE `state` 消息结构前端要求"单条事件或事件数组"二选一 | 冻结为 `{seq, tick, events:[...]}`，前端两种都兼容 |
| B6 | `GET /agvs/{id}` 的"当前计划"字段名前端用 `plan` | 后端补 `plan` 字段（`planVersion`/`ticks`/`edges`/`goalCell`/`blockedBy`） |
| B7 | `priorityMin`/`priorityMax` 是否闭区间未定义 | 明确为闭区间并写回契约 |
| B8 | `/sim/start`、`/sim/pause` 是否需要 body 未定义 | 明确 body 可省略，写回契约 |
| B9 | 缺少 `GET /skus/{id}`、`GET /locations/{id}` | 补上，删除后 GET 返回 404 而不是"方法不支持" |

### C. 场景与工具链问题

| # | 问题 | 修正 |
| --- | --- | --- |
| C1 | `roadblock` 场景定义了"封路"却没有封路动作，场景名不副实 | 给场景加 `events` 定时事件机制，tick 25/60/120 三次封路 |
| C2 | `rush-order` 场景同样没有急单注入 | 同上，tick 20/90 插入优先级 10/9 的急单 |
| C3 | `fixtures/` 与代码可能漂移 | 新增 `tests/test_fixtures.py`，导出结果必须与代码定义完全一致 |
| C4 | SSE 测试用 `response.read(n)` 阻塞 | 改为按行读取直到拿到 `data:` 行 |
| C5 | 调试脚本把数据写进仓库 `data/` | 实现目录内加 `.gitignore` 忽略 `data/`、`*.db`、`*.log` |

## 人工修改

| 位置 | 修改 | 原因 | 验证 |
| --- | --- | --- | --- |
| `wms/service.py` `_allocate` | 分配失败时回滚本行已预占的库存并提交 | A1 | `test_release_without_stock_raises_and_keeps_state` |
| `wms/service.py` `assign_task` | 去掉 SQL 里对 `status` 的直接赋值，交给 `_transition_task` | A2 | `test_rush_order_assigned_before_normal_tasks` + 场景事件计数出现 `task.assigned` |
| `wms/service.py` `create_task` | 额外向 `task_events` 写入 `task.created` | A3 | `test_summary_matches_records` 中 `avgTaskTicks` 非 0 |
| `wms/pathfinding.py` `plan_path` | 允许起点不可通行（只放宽起点，仍按可通行过滤邻居） | A4 | `RoadblockTests` 封路后无 AGV 停在封路格 |
| `wms/reservations.py` `acquire` / `release_agv` | 新增 `release_from_tick` 参数；抢占要求优先级严格更高 | A5/A6 | `test_high_priority_preempts_low_priority_future_reservation`、`test_same_cell_same_tick_conflicts` |
| `wms/simulator.py` `_replan_after_map_change` | 改用字典取值 `row["cell_id"]` | A7 | `test_roadblock_triggers_replan_and_is_respected` |
| `wms/simulator.py` `_replan` | 规划失败时清空 `agv.plan` 并进入冷却重试 | A8 | 同上 |
| `wms/simulator.py` `_update_static_blocks` | 规划时避让静止占位 | A9 | `test_static_block_prevents_planning_through_parked_agv` |
| `wms/simulator.py` 死锁判定 | 增加 `DEADLOCK_MIN_BLOCK_TICKS=2` 与 `DEADLOCK_COOLDOWN=10` | A10 | `cycle-deadlock` 场景只报 1 次死锁而不是反复报 |
| `wms/pathfinding.py` `find_safe_cell` | 用 Tarjan 求割点并排除 | A11 | `test_safe_cell_avoids_articulation_points` |
| `wms/app.py` `_bootstrap` | 重启时按库中记录的 `map_id` 重建基础地图并 `restore_current()` | A12 | `test_map_version_restored` |
| `wms/mapdata.py` `map_from_dict` | 解析站点时保留 `code` | A13 | `test_map_fixture_roundtrip` |
| `wms/db.py` | 增加 `PRAGMA busy_timeout=5000`；`reset_all` 先关闭外键再删除 | A14/A16 | `bench` 与 `serve` 共用目录不再报错；场景重置 8/8 通过 |
| `wms/util.py` + `wms/api.py` | 新增 `row_to_camel`，所有 API 响应统一 camelCase；订单补 `lines`/`taskIds` | B1/B2/B3 | 浏览器端到端核对：订单表格显示 `SKU-001 × 2 · 合计 2 · 任务 1` |
| `wms/simulator.py` `_alert` | 告警增加 `severity`/`code`/`agvId` | B4 | 前端告警列表显示级别与 AGV |
| `wms/api.py` | 补 `GET /skus/{id}`、`GET /locations/{id}`；封路输出 camelCase | B3/B9 | `test_sku_lifecycle` |
| `wms/scenarios.py` | 场景增加 `events` 字段；封路/急单场景补定时事件 | C1/C2 | 场景回归输出 `map.roadblock.added: 3`、`task.replanned: 27`、`sim.injected: 2` |
| `wms/simulator.py` `_replan_after_map_change` | 空闲车辆分支改调新方法 `_replan_idle_after_map_change`；`_replan` 本身保持"无任务即返回" | A19 | `test_idle_returning_agv_also_replans`：AGV-01 由 (1,20) 绕行至 (0,19)→(0,22)，`plan_version` 4→5，封路格上无轨迹 |
| `wms/simulator.py` 两处冲突分支 | 补发 `conflict.detected` 事件（含 `agvId`/`reason`/`keys`） | A20 | `test_conflict_counter_matches_conflict_events`：`head-to-head` 计数 1 = 事件 1；`peak-30` 计数 16 = 事件 16 |
| `wms/server.py` `serve()` | 新增 `detect_port_conflict()`：绑定前先用普通套接字探测，再确认端口上确有监听者（区分 TIME_WAIT 残留）；冲突时抛 `PortInUseError`；默认端口 8080 改为 8099 | A21 | `tests/test_api.py::PortConflictTests`（3 个用例）；实测在 8080 被占用时启动直接报错退出 |
| `tests/test_fixtures.py` | 新增 fixture 同源校验 | C3 | `Ran 91 tests ... OK` |
| `implementations/member-2/.gitignore` | 忽略 `data/`、`*.db`、`*.log` | C5 | `git status` 不出现数据文件 |

## 验证

| 命令或场景 | 结果 | 证据 |
| --- | --- | --- |
| `python -m unittest discover -s tests -t tests -v` | 91 个用例全绿 | `docs/evidence/unittest-output.txt` |
| `python scripts\run_scenarios.py` | 8 个场景全部跑通，关键指标见下表 | `docs/evidence/scenario-run.txt`、`docs/scenario-results.json` |
| `python scripts\save_bench.py` | 性能数据落盘 | `docs/evidence/bench-summary.json` |
| `python -m wms serve --port 8099` + 浏览器 | 页面正常渲染，SSE 已连接，120 fps，控制台 0 error/0 warning | `docs/evidence/ui-console.png` |
| `python scripts\smoke_api.py` | 25 项 REST/SSE 冒烟全通过 | 脚本输出 |
| 前后端 AGV 位置核对 | 10/10 一致 | `docs/acceptance.md` AC14 节 |
| 场景结果 | peak-10 10/10 完成 0 死锁；cycle-deadlock 检测 1 次死锁并回退恢复 | `docs/scenario-results.json` |

## 复盘

最能体现工程判断的几个决策：

1. **先冻结接口契约再并行开发**。前端子 Agent 拿到的是一份写死了字段名、枚举值和
   SSE 格式的契约文档，因此两边同时开工也没有产生结构性冲突；剩下的问题
   （B1–B9）全部是"契约没写到的细节"，逐条补齐即可。
2. **把"调度算法必须可在无 UI 条件下测试"当成硬约束**。时空 A*、预约表、等待图
   三个模块不碰数据库、不碰 HTTP，因此可以写纯单元测试，定位问题时不依赖前端。
   本轮多个真实缺陷（A5、A6、A10、A11）都是先被单元测试逼出来的。
3. **区分"瞬时等待"与"死锁"**。初版一遇到互相等待就判死锁并回退，结果是
   peak-10 场景出现大量无谓回退。加入"连续阻塞 ≥ 2 tick"与"同环冷却"之后，
   peak-10 的死锁数稳定为 0，cycle-deadlock 稳定为 1。
4. **失败必须是显式的**。30 台场景里 7 个任务失败，我选择按"阻塞时间超过上限"
   把任务置为 `failed` 并告警，而不是让它们无限等待。AC9 允许"继续或按规则失败"，
   显式失败比静默卡死更符合验收要求，也更容易在报告里说清楚差距。
5. **修复要按最小影响面收敛，而不是按症状收敛**。A19 的第一版修法是把 `_replan`
   改成"任何无任务车辆都重规划"，确实让封路格不再有轨迹，但 `peak-10` 场景立刻
   从 10/10 完成退化成 9/10——多出来的重规划把待命区车辆互相顶开。我把它回退，
   收窄成"仅在地图版本变化这条路径上，对无任务车辆重规划"（新方法
   `_replan_idle_after_map_change`），两个场景同时满足。这次回退是本轮最有用的一次
   手工干预：**AI 给出的修复要靠固定场景回归来判对错，而不是靠单条用例通过来判对错**。

下次应该补充的上下文与测试：

- 测试应覆盖"两个进程同时访问同一个 SQLite 文件"，本轮只在真实使用中才发现
  `database is locked`，补一条会提前暴露。
- 场景定义应该有一个"这个场景必须产生哪些事件"的机器可校验断言，
  本轮是先跑一遍看事件计数，再把期望写进 `expect`，属于事后补齐。
- 前端子 Agent 的产出需要更严格地核对"响应字段名"，不能只看"请求打到了哪个路径"。
