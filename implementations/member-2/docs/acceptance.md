# 验收记录：AC1–AC20（成员二个人版）

- 执行环境：Windows 11 家庭版 10.0.26200 x64 / Intel Core Ultra 7 155H / 31.5 GB RAM / Python 3.14.3
- 代码版本：`dev/member-2` 分支 `implementations/member-2/`
- 执行日期：2026-11-18
- 统一复现命令：

```text
cd implementations/member-2
python -m unittest discover -s tests -t tests -v     # 91 个用例
python scripts\run_scenarios.py                      # 8 个固定场景
python scripts\save_bench.py                         # 性能数据
python -m wms serve --port 8099                      # 启动服务与前端
```

证据文件位于 `implementations/member-2/docs/evidence/`：

| 文件 | 内容 |
| --- | --- |
| `unittest-output.txt` | 91 个用例的完整输出（末行 `Ran 91 tests ... OK`） |
| `scenario-run.txt` | 8 个固定场景的回归输出 |
| `scenario-results.json` | 场景结果的机器可读版本 |
| `bench-summary.json` / `bench_*.json` | 性能基准数据 |
| `ui-console.png` | 前端控制台运行截图（120 fps，SSE 已连接） |

## 逐项结果

| 用例 | 结果 | 证据 | 备注 |
| --- | --- | --- | --- |
| AC1 本地启动 | **通过** | `python -m wms serve --port 8099` 后 `GET /api/v1/health` 返回 `{"status":"ok","tick":30,"running":false,"seed":20261118,"version":"0.2.0-member-2"}`；浏览器打开 `http://127.0.0.1:8099/` 正常渲染；`tests/test_api.py::PortConflictTests` 断言端口被别的进程监听时 `serve` 抛 `PORT_IN_USE` 并给出换端口提示 | 干净环境零依赖（只用 Python 标准库），无 pip/npm 安装步骤；端口冲突显式报错，不会静默分流请求 |
| AC2 主数据 | **通过** | `tests/test_domain.py::MasterDataTests`（8 个用例全绿）：创建 3 SKU / 5 库位 / 初始库存；重复 SKU 编码 → `DUPLICATE_CODE`；非法坐标（x=999、y=-3）→ `VALIDATION_FAILED`；负库存 → `VALIDATION_FAILED`；有库存时删 SKU → `CONFLICT` | |
| AC3 出库订单 | **通过** | `tests/test_domain.py::OrderTests`（6 个用例）：多明细订单释放后生成 2 个任务；重复释放后 `reserved_qty` 不变（幂等）；库存不足抛 `INSUFFICIENT_INVENTORY` 且订单仍为 `draft`、无残留预占；取消订单回滚预占 | 分配失败的部分预占回滚是本轮修复的缺陷（见 ai-notes） |
| AC4 任务状态 | **通过** | `tests/test_domain.py::TaskStateMachineTests`（5 个用例）：`pending→assigned→running→completed` 逐级合法流转；`pending→completed` 与终态再流转抛 `INVALID_STATE`；重派回到 `pending` 且清空 `assigned_agv_id` | |
| AC5 单 AGV 路径 | **通过** | `tests/test_pathfinding.py::PathPlanTests::test_plan_is_continuous_and_passable`：规划出的每一步都必须是地图上的真实有向边，且所有格点 `is_passable=True`；`test_unreachable_goal_returns_none` 验证货架格不可达 | 路径不穿越货架与封路格 |
| AC6 同格冲突 | **通过** | `tests/test_pathfinding.py::ReservationTests::test_same_cell_same_tick_conflicts`：两车申请同一格同一 tick → 第二车失败并返回冲突键；`test_batch_acquire_is_atomic`：批量预约整体失败，不留部分占用 | |
| AC7 相向穿边 | **通过** | `test_opposite_edge_conflicts`：A 预约 `12,18→12,17`，B 申请反向 `12,17→12,18` 同 tick → 被阻止；`head-to-head` 场景实测 `agv.blocked` 2 次、`conflict.detected` 1 次，2/2 任务最终完成；`test_conflict_counter_matches_conflict_events` 断言 `stats.conflicts` 与 `conflict.detected` 事件数逐场景相等 | |
| AC8 首尾相接 | **通过** | `test_handoff_next_tick_allowed`：A 占 `12,18`@tick5，B 申请 `12,18`@tick6 → 允许 | |
| AC9 死锁恢复 | **通过** | `cycle-deadlock` 场景（`scenario-results.json`）：`deadlock.detected` 1 次、`rollback.executed` 1 次、`deadlock.resolved` 1 次，2/3 任务完成，1 个按"阻塞时间超过上限"规则显式失败；`tests/test_simulation.py::WaitGraphTests` 验证环检测确定性与牺牲者选择；`test_no_agv_enters_deadlock_forever` 验证恢复后等待图无环 | 符合 AC9 "继续或按规则失败"，失败是显式 `task.failed` + 告警，不是静默卡死 |
| AC10 临时封路 | **通过** | `roadblock` 场景在 tick 25/60/120 三次封路（`12,6`/`12,12`/`12,18`）：`map.roadblock.added` 3 次、地图版本 4 次变更、`task.replanned` 27 次，10/10 任务完成；`tests/test_simulation.py::RoadblockTests` 额外断言封路后无任何 AGV 进入封路格；`test_idle_returning_agv_also_replans` 断言**没有任务的空闲返程车**也会跟随封路重规划（`plan_version` 递增、轨迹不含封路格） | 封路落在车辆脚下时允许它驶离，但不会重新进入 |
| AC11 充电 | **通过** | `low-battery` 场景（初始电量 14%）：`agv.charging` 90 次，3/3 任务完成；`tests/test_simulation.py::ChargingTests` 断言同一时刻充电位上的车辆数不超过充电桩数量 | 充满到 95% 自动回岗 |
| AC12 急单 | **通过** | `rush-order` 场景在 tick 20/90 注入优先级 10/9 的急单（`sim.injected` 2 次），6/6 任务完成；`tests/test_simulation.py::RushOrderTests` 断言待办队列按优先级降序且队首是急单、首次指派优先给急单 | |
| AC13 固定 seed | **通过** | `tests/test_simulation.py::DeterminismTests`：同一 seed 连续运行 3 次，事件序列（类型 + 聚合 id 归一化 + tick）完全一致；任务结果（状态/优先级/拣选点/放货点）一致；AGV 轨迹逐点一致 | 业务 id 是 UUID，因此比较时按首次出现顺序归一化，比较的是事件顺序与内容 |
| AC14 实时地图 | **通过** | 浏览器实测：`docs/evidence/ui-console.png`（24×24 地图、货架/拣选点/放货点/充电位/封路图例、10 台 AGV、120 fps、SSE 已连接）；逐台核对后端 `GET /api/v1/sim/state` 与前端 AGV 面板坐标状态 10/10 完全一致 | 证据见下方"AC14 一致性核对" |
| AC15 暂停与回放 | **通过** | `tests/test_api.py::SimulationApiTests::test_sim_state_and_controls`：`/sim/step`（ticks=3 → tick=3）、`/sim/start`、`/sim/pause`、`/sim/speed`（1/4/8，非法值返回 400）均生效；回放一致性由 `DeterminismTests` 的事件序列比对保证 | |
| AC16 持久化 | **通过** | `tests/test_persistence.py::PersistenceTests`（5 个用例）：写入 SKU/库位/库存/订单并推进 12 tick → 关闭 → 重启 → 主数据、库存、订单状态、任务列表、tick、AGV 坐标与电量全部恢复；地图版本与封路记录恢复；事件条数一致；重启后还能继续推进 tick | |
| AC17 查询与筛选 | **通过** | `tests/test_api.py`：`/tasks?status=pending`、`/tasks?priorityMin=5&priorityMax=5`、`/orders?page=0&size=2`、`/events?type=&agvId=&taskId=&fromTick=&toTick=` 结果与数据库实际记录一致；分页响应含 `items/page/size/total` | |
| AC18 统计 | **通过** | `tests/test_api.py::AnalyticsApiTests::test_summary_matches_records`：peak-10 场景下订单总数 10、任务总数 10、AGV 总数 10，`byStatus` 各自求和等于总数，`completedTasks` 与 `tasks.byStatus.completed` 相等；利用率全部落在 0–100%；吞吐按桶返回并给出窗口与分母 | 口径写在响应里（`window`/`unit`/`definition`），前端不自猜 |
| AC19 性能记录 | **通过** | `docs/evidence/bench-summary.json`：见下方"性能记录" | 全部为实测值 |
| AC20 错误与限制 | **通过** | 错误：所有 4xx/5xx 统一为 `{error:{code,message,details}}`，错误码取自公共契约第 20 条；限制：见下方"已知限制" | 未实现功能与风险已在 `docs/report/member-2.md` 第 11 节列明 |

## AC14 一致性核对

后端 `GET /api/v1/sim/state`（peak-10，seed 20261118，推进 30 tick）：

```text
AGV-01 unloading (1,21) batt=99.6      AGV-06 loading    (2,16) batt=99.9
AGV-02 unloading (16,0) batt=99.6      AGV-07 to_dropoff (15,6) batt=99.8
AGV-03 to_dropoff (10,23) batt=99.8    AGV-08 to_dropoff (1,12) batt=99.8
AGV-04 to_dropoff (1,3) batt=99.7      AGV-09 to_dropoff (10,17) batt=99.8
AGV-05 to_dropoff (13,12) batt=99.8    AGV-10 to_dropoff (2,7) batt=99.8
```

前端 AGV 面板（同一时刻抓取 DOM 文本）：

```text
AGV-01 放货确认 坐标 1,21     AGV-06 取货确认 坐标 2,16
AGV-02 放货确认 坐标 16,0     AGV-07 前往放货 坐标 15,6
AGV-03 前往放货 坐标 10,23    AGV-08 前往放货 坐标 1,12
AGV-04 前往放货 坐标 1,3      AGV-09 前往放货 坐标 10,17
AGV-05 前往放货 坐标 13,12    AGV-10 前往放货 坐标 2,7
```

10/10 完全一致。前端不做任何状态推测，位置与状态全部来自后端快照。

## 场景回归结果

`python scripts\run_scenarios.py` 的完整输出见 `docs/evidence/scenario-run.txt`，
机器可读版本见 `docs/scenario-results.json`。

| 场景 | tick | AGV | 任务结果 | 订单结果 | 冲突 | 死锁 | 回退 | 失败 | 耗时 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| single-agv | 200 | 1 | 1 完成 | 1 完成 | 0 | 0 | 0 | 0 | 0.10 s |
| peak-10 | 400 | 10 | 10 完成 | 10 完成 | 0 | 0 | 0 | 0 | 0.65 s |
| peak-30 | 600 | 30 | 23 完成 / 7 失败 | 23 完成 / 7 已释放 | 16 | 3 | 3 | 7 | 2.90 s |
| head-to-head | 200 | 2 | 2 完成 | 2 完成 | 1 | 0 | 0 | 0 | 0.14 s |
| cycle-deadlock | 200 | 3 | 2 完成 / 1 失败 | 2 完成 / 1 已释放 | 0 | 1 | 1 | 1 | 0.16 s |
| roadblock | 300 | 10 | 10 完成 | 10 完成 | 0 | 0 | 0 | 0 | 0.71 s |
| low-battery | 300 | 6 | 3 完成 | 3 完成 | 0 | 0 | 0 | 0 | 0.39 s |
| rush-order | 300 | 4 | 6 完成 | 6 完成 | 0 | 0 | 0 | 0 | 0.44 s |

关于 `peak-30` 的 7 个失败：这 7 个是 30 台车在 24×24 地图上的资源竞争导致
"连续阻塞 tick 超过 `MAX_BLOCK_TICKS=90`"，系统按既定规则把任务置为 `failed`
并产生告警（`alert` 10 条、`task.failed` 7 条），**不是静默卡死**。
个人版的目标规模是 10 台（`peak-10` 10/10 完成、0 死锁）；30 台是整合版的规模基线，
此处如实记录差距。

## 性能记录（AC19）

机器：Intel Core Ultra 7 155H / 31.5 GB RAM / Windows 11 家庭版 / Python 3.14.3。
仿真 600 tick 的实测结果（`docs/evidence/bench-summary.json`）：

| 场景 | AGV 数 | 任务数 | 总耗时 | ms/tick | 指派延迟（avg/max tick） | Python 堆峰值 |
| --- | --- | --- | --- | --- | --- | --- |
| single-agv | 1 | 1 | 0.36 s | 0.60 | 1.0 / 1 | 0.62 MB |
| peak-10 | 10 | 10 | 1.31 s | 2.18 | 1.0 / 1 | 1.79 MB |
| peak-30 | 30 | 30 | 5.52 s | 9.20 | 1.0 / 1 | 2.27 MB |

- **指派延迟**定义：`task.assigned` 的 tick − `task.created` 的 tick。
  三个场景平均都是 1 tick（即任务创建后的下一个 tick 就被指派）。
- **前端帧率**：8 倍速运行 peak-10 场景时，页面 FPS 计数器稳定显示 **120 fps**
  （受显示器刷新率上限约束），浏览器控制台无 error/warning。
- **测试时长**：全量 91 个用例 `Ran 91 tests in 23.7 s`（证据文件 `unittest-output.txt`）。
  机器完全空闲时约 23–35 s；同机并发跑 `serve` 或其它仿真任务时会退化到 100 s 量级（CPU 争用），
  结果不受影响。

## 已知限制（AC20）

1. 单进程单库：同一数据目录同时跑 `serve` 与其它命令会争用 SQLite（已加 `busy_timeout=5000`
   等待，但建议分开目录）。
2. 30 台 AGV 规模下有 7 个任务按规则失败，个人版目标规模为 10 台（`peak-10` 全通过）。
3. 无鉴权、无多租户、无分布式部署、无真实 AGV 通信协议（MQTT/ROS）——
   这些属于整合版课程推荐技术栈的范围。
4. 死锁恢复只处理"等待图成环"这一类死锁；资源饥饿、优先级反转等未专门建模。
5. 前端未做移动端适配与国际化。
