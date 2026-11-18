# AI 使用过程记录

## 基本信息

- 工具：DeepSeek-V4.1-Flash
- 成员：RiceXi
- 分支：dev/member-1
- 日期：2026-11-18

## 关键提示词

1. 阅读公共需求、数据契约、验收用例和个人开发契约，在 `implementations/member-1/` 内独立完成一个本地可运行版本。
2. 使用 Python 标准库和 SQLite，避免外部依赖；实现时空 A*、格点/边预约、死锁检测、tick 仿真、REST API 和 Canvas UI。
3. 完成自动化测试、运行说明、AI 错误记录和个人报告，不修改公共契约。

## AI 生成效果

AI 快速生成了服务分层、SQLite 实体存储、A* 搜索、仿真循环、REST 路由和 Canvas 页面骨架。核心路径和订单流程经过测试后可用。

## AI 错误与人工修改

| 问题 | AI 初始行为 | 人工修改 | 验证 |
| --- | --- | --- | --- |
| 货架目标不可达 | A* 直接把货架格作为拣选目标，路径始终为空 | 增加货架相邻可通行格选择，AGV 停在货架旁装卸 | `test_demo_order_completes_in_simulation` |
| 预约所有者不一致 | idle AGV 持有者与路径计划持有者使用不同键，导致自身格子阻塞 | 路径、idle 和 charge 预约统一使用 AGV id | 自动化测试和实际运行 |
| 候选路径提前写入预约表 | 比较多个候选 AGV 时，未选中的候选也可能占用预约 | 路径构建只返回候选，选中后由下一轮预约重建 | 订单可正常指派 |
| 线测端口受限 | 本地端口在默认沙箱内被拒绝 | HTTP 集成测试在明确的本地运行审批下执行 | 20 个测试通过 |
| 充电计划未持久化结构 | 初始实现只能改变状态，没有去充电桩路径 | 增加 charge plan、目标充电位和逐 tick 移动/充电 | `test_low_battery_enters_charging` |

## 人工判断

- 保持个人开发边界，只写成员一目录和报告。
- 选择标准库实现，保证离线、无依赖和容易冻结。
- 采用 SQLite JSON 实体表，减少 ORM 依赖，同时保留持久化。
- 用有向边预约解决“同 tick 相向穿过同一条边”的隐性冲突。
- 将死锁演示做成可重复场景，而不是依赖随机偶发卡死。

## 主要测试结果

```text
Ran 20 tests in 3.014s
OK
```

真实 HTTP 冒烟：

```text
health=ok
SKUs=3
locations=10
AGVs=10
order.status=released
task.statuses=running
```

性能采样：

```text
10 AGV
10 orders
release average=23.393 ms
release max=24.415 ms
200 tick advance: 0.870 s
completed tasks=10
```

## 未验证项

- 30 台 AGV 的最终前端帧率。
- 500 个随机场景的完整压力测试。
- 连续 30 分钟死锁 soak test。
- 真实 Kafka、Redis 和微服务链路，属于整合版范围。
