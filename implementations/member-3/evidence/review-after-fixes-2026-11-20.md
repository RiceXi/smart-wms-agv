# 成员三修复后完整性复审（历史记录）

本文记录 F3 修复前的复现结果。后续回归及最终状态见[验收索引](acceptance.md)。

审查日期：2026-11-20（Asia/Shanghai）；成员KingSure000；分支dev/member-3。只读取公共契约及本人实现/报告，没有读取其他成员实现。本轮是复审，没有修改核心源码或将新失败场景计入通过套件。

## 结论

仍有未完成内容：F3货架取货邻格全部封闭会停止整个仿真，需在冻结前修复并补回归；本地提交、个人Fork推送、负责人冻结及冻结后的跨Fork PR尚未完成。此次复审新增一项阻塞缺陷。

F1完整历史事件查询及F2局部重规划的已修复结论保留。现有30项自动化测试全部通过，不能据此声称所有动态封路边界均满足要求。

## 契约与已有验证

- GitHub main查询为e6071d919b132ec309140be5b9a18b36b84d36a1。重新下载该提交的README、AGENTS、CONTRIBUTING、requirement、data-schema、acceptance、development-contract七份公共文件，与本地统一换行并trimEnd后SHA256均相同。时间及逐文件摘要见review-contract-2026-11-20.json。
- 本轮重跑 `node --test --test-reporter=tap tests/acceptance.test.js`：30/30通过，失败0、跳过0，25250.8741ms。原始输出review-acceptance-2026-11-20.tap。此文件不覆盖本轮另行发现的F3，旧输出保留。
- 上一轮500个完整仓库受控场景全部通过、2,846任务完成、安全违规0；五分钟文件SQLite/回放网页23组通过、44任务完成、最低59FPS。这些是已有证据，本轮未重复运行长测，也未将它们外推到新封路条件。
- 原30分钟墙钟结果属于2026-11-19内存SQLite/关闭回放的代码与场景；当前功能修复后磁盘/网页证据为五分钟。建议最终修复后补当前代码30分钟稳定性证据；公共契约没有另行规定必须测试30分钟FPS，该建议属于补充实验。

## F3（P1，待修）：合法封路后调度异常，tick回滚且超时无法到达

对应需求：FR7动态封路处理、FR8等待/重试上限、FR9 tick仿真。AC10既有绕路/地图版本用例通过，但没有覆盖取货端完全不可达的条件；本轮扩展边界未通过。这不是碰撞违规，也不是AC9三车等待环测试失败。

最小输入：1台AGV，max_wait_ticks=2，max_retries=1；在SHELF-A(5,4)登记新SKU库存5，释放数量1的出库订单；在调度前依次临时封闭(4,4)与(5,3)。两个封路命令均合法且成功。右侧(6,4)及下侧(5,5)原本为货架，因而取货端无可通行邻格。

实测一：连续8次调用Engine.step，全部抛出inaccessible_pickup（“货架 SHELF-A 没有可用相邻取货点”）。每次tick都回滚为0，订单保持released、任务pending、库存预占1；无法推进到等待上限。完整状态和8次结果见review-targeted-2026-11-20.json。

实测二：启动临时端口的真实HTTP服务，所有建单/封路/启动命令均经/api/command。sim.start后running=true、tick=0，等待2.2秒后HTTP快照显示running=false、tick=0，并记录一次simulation.error。手动解封(4,4)后单步才推进到tick1。只验证恢复推进，没有声明已验证该订单最终完成；没有使用浏览器。证据review-http-shelf-closure-2026-11-20.json；可重复执行的复现器：

```powershell
node evidence/review-shelf-closure-repro.mjs
```

复现器使用内存SQLite，使用隔离的数据库。它是确认现存缺陷的审查工具，退出0表示复现断言成立，不代表软件已修复；修复后应改为正式回归并保留这次原始失败证据。

根因（当前源码行号）：

- src/engine.js:249：pickupPoint找不到邻格时抛DomainError。
- src/engine.js:384：dispatch直接调用pickupPoint，未将该动态不可达情况转为单任务等待/有界失败。
- src/engine.js:651：step回滚整个state及tick，重新抛出，导致等待计时无法前进。
- src/server.js:116：定时器异常处理将整个仿真置为暂停，其他任务也无法继续调度。

建议修复与验收：把动态不可达转为局部、可记录的阻塞原因；全局tick及其他任务继续推进，等待或重试达到上限后按契约合法结束并释放库存预占。上限前解封应能重新规划并完成任务。补充pending任务取货邻格全封、解封恢复、超时失败、无关任务可继续，以及已指派/前往取货时地图变更的回归；确保不破坏F2局部性、库存和预约安全。上述建议本轮尚未实现。

## D1：Git交付尚未完成

本地git status显示docs/report/member-3.md修改未提交，成员三实现仍为untracked；remote仅有RiceXi上游origin，未配置个人Fork remote。本地及上游本轮均未查到成员三冻结tag。未登录验证个人Fork或完整PR列表，不能断言它们在GitHub上一律不存在。

修复F3并完成相应验证后，需创建本地提交，在KingSure000个人Fork推送dev/member-3；按CONTRIBUTING由负责人创建v0.2.0-member-3-frozen，冻结后再发跨Fork PR到上游main。开发契约第9节包含冻结tag要求，当前不满足完整完成定义。本轮没有自动提交、推送、建tag或PR。

## 范围与保存

个人阶段可使用轻量单体架构；Spring Cloud、Kafka、Redis、Kubernetes等及对比/整合报告属于后续整合范围，不列为当前个人缺项。完整事件全量读取后筛选、历史事件/帧未归档及数据库增长仍为已披露限制。

本轮新增审查证据并更新成员三README、交付草稿、验收索引、AI记录、个人报告及根进度。未安装软件/插件/skill，没有修改公共规范、其他成员文件或默认工作库。审查时确定的首要修复项为 F3。
