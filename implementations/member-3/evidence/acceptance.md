# 成员三验收索引

2026-11-20 修复复验通过。货架取货面全封时仿真继续推进，解封后原任务可完成，持续封闭达到等待上限时订单失败并释放预占。REST 写操作按接口表校验方法与路径。36 项自动化测试和 22 项 Edge 检查通过。 原始输出见 acceptance-after-closure-fix.tap 和 browser-results-f3-fixed-2026-11-20.json。

现场复验中，自动化测试 36 项通过，Edge 检查 22 项通过。文件数据库在 tick 3 重启后恢复订单、任务、库存和车辆状态，6 张订单在 tick 35 前全部完成。[自动化测试日志](runtime-check-20261120.log) · [浏览器检查](browser-results-runtime-check-20261120.json) · [重启记录](runtime-check-restart-20261120.json)

此前 F1、F2 修复验证：网页改查完整持久化历史，FR7按受影响路径及必要停车依赖局部重规划。全套30项通过（23579.4278ms），真实Edge22组检查通过；追加文件数据库与回放持续运行时共23组通过。AC17恢复通过。下表后端条目均在新30项全套中重验，旧文件仅保留历史依据。修复明细见audit-fixes-2026-11-20.md，原审查见audit-2026-11-20.md。

| 用例 | 结果 | 证据 | 备注 |
| --- | --- | --- | --- |
| AC1 本地启动 | 通过 | acceptance.tap：AC1；browser-results.json；clean-start-results.json | 仅运行文件隔离复制、空库启动、HTTP资源200；RPC/SSE重连和真实Edge访问 |
| AC2 主数据 | 通过 | acceptance.tap：AC2 | 新增3商品/5库位，重复/越界/负数拒绝，编辑删除 |
| AC3 出库订单 | 通过 | acceptance.tap：AC3 | 多行、原子预占、幂等释放、FIFO/FEFO、取消释放 |
| AC4 任务状态 | 通过 | acceptance.tap：AC4 | 完整状态顺序，禁止外部篡改与终态重启 |
| AC5 单AGV路径 | 通过 | acceptance.tap：AC5 | 实际取放货、路径连续、障碍/对角拒绝 |
| AC6 同格冲突 | 通过 | acceptance.tap：AC6 | 原子失败无残留，A*等待 |
| AC7 相向穿边 | 通过 | acceptance.tap：AC7/AC8 | 反向边拒绝 |
| AC8 首尾相接 | 通过 | acceptance.tap：AC7/AC8 | 允许前车离开后跟随 |
| AC9 死锁恢复 | 通过 | acceptance.tap：AC9 | 三车实际目标占用形成等待环，算法检测并实际回退；演示按钮明确标注等待图注入 |
| AC10 临时封路 | 常规与取货端扩展用例通过 | acceptance-after-closure-fix.tap | 覆盖派车前及取货途中全封、解封、超时、其他订单继续执行、替代取货面和真实 HTTP 定时器 |
| AC11 充电 | 通过 | acceptance.tap：AC11及封路充电回归 | 3台低电车/2充电位，容量不突破，充满回岗 |
| AC12 急单 | 通过 | acceptance.tap：AC12 | 单车场景中急单先指派 |
| AC13 固定seed | 通过 | acceptance.tap：AC13 | 三次事件、任务与库存完全一致 |
| AC14 实时地图 | 通过 | browser-results.json、dashboard.png、dashboard-mobile-map.png | 真实Canvas车辆坐标/状态/电量逐车对齐后端快照；桌面与手机截图 |
| AC15 暂停与回放 | 通过 | acceptance.tap：AC15；browser-results.json | 网页暂停后tick不变、单步推进；3个历史帧与车辆/事件/地图对齐，回放写锁与返回实时 |
| AC16 持久化 | 通过 | acceptance.tap：AC16 | 独立Node进程重启读取SQLite并继续完成任务 |
| AC17 查询与筛选 | 通过 | acceptance-2026-11-20.tap：AC17及历史分页回归；browser-results-2026-11-20.json | 超200条后按AGV/任务/UTC日期/tick/类型/关键词查回早期事件；完整分页无漏重，DOM输入及旧响应隔离通过 |
| AC18 统计 | 通过 | acceptance.tap：AC18 | 精确公式、取消、设备、流水守恒 |
| AC19 性能记录 | 通过 | benchmark-results.json；soak-results.json；browser-results-disk-sustain-2026-11-20.json；conflict-scenarios-2026-11-20.json | 原有内存基准/墙钟保留；新增五分钟磁盘/回放网页及500完整仓库场景，详见下文及修复报告 |
| AC20 错误与限制 | 通过 | README.md、DESIGN.md、ai-notes.md、个人报告 | 已记录实际错误、修正、未实现范围及实验限制 |

## 复现命令

```powershell
npm test
npm run clean-start-test
npm run benchmark
npm run conflict-scenarios
npm run browser-test
npm run soak
```

初始18项全套通过后，边界审查增加取消/故障/库位引用/充电/在线竞争回归。2026-11-19全套26/26通过，0失败、0跳过，总耗时23265.6948ms，原始结果见acceptance.tap；2026-11-20新增3项FR7局部性及1项AC17完整历史分页回归，全套30/30通过，0失败、0跳过，总耗时23579.4278ms。

网页验收实际提交商品新增/编辑、库位新增/容量编辑、库存及UTC有效期、多明细订单预占/释放。重复编码主动触发一次duplicate_code，表单及全局错误均可见；这是预期校验提示，不是未捕获异常。另验证输入HTML作为文本显示，不创建图片元素或执行事件。性能与截图的精确数值以browser-results.json为准。

墙钟实验实际运行1800.0032213秒，10车/1800tick/174任务，174完成、0失败、0未完成；81000次车辆对检查，安全违规0，最大单tick75.4081ms，fullThirtyMinuteRun=true。内存SQLite且关闭回放，不外推为浏览器或文件落盘的30分钟稳定性。局部500场景、10/30车加速基准及短时FPS分别记录，未混用时间口径。

2026-11-20新增Edge磁盘/回放持续运行：真实墙钟300.2297615秒，10车，tick38→336，44/44任务完成、无失败或未完成，13,410次车辆对安全检查无违规。296个采样最低59FPS，磁盘快照与内存一致，数据库80,154,624字节；完整网页验证321652.161ms、23组断言通过、未捕获异常0。记录在browser-results-disk-sustain-2026-11-20.json（含逐样本数据及截图），不等同于30分钟磁盘/浏览器实测。

新增500完整22×16仓图/10车Engine场景：原同格250及相向边250请求全部被拒绝后安全规划，500/500场景成功（100%），2,846/2,846任务完成，无失败/未完成；1,145,610次车辆对检查无违规，实际耗时461840.0549ms。含不同seed、1～3初始封路、不同订单/优先级/取放点组合、运行中封路解封及部分急单；所有定义和结果在conflict-scenarios-2026-11-20.json。采用内存SQLite、关闭回放、加速tick的受控样本，不是自然死锁或任意拓扑成功概率。
