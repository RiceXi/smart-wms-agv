# 协作规范

## 分支模型

`main` 始终保持可构建、可启动。三名成员使用长期个人集成分支：

- `dev/member-1`
- `dev/member-2`
- `dev/member-3`

单个功能从个人分支创建短分支：

```text
feat/member-2/dispatch-reservation
fix/member-3/websocket-reconnect
docs/member-1/api-contract
test/member-2/deadlock-cycle
```

短分支合并回个人分支后，再以 PR 合并到 `main`。禁止在 `main` 直接提交。

## 提交信息

使用以下格式：

```text
<type>(member-N/<scope>): <summary>
```

允许的 `type`：`feat`、`fix`、`test`、`docs`、`refactor`、`perf`、`build`、`ci`、`chore`。

示例：

```text
feat(member-2/dispatch): reserve vertex and edge time slots
fix(member-1/task): reject invalid task state transition
test(member-3/web): cover websocket reconnect snapshot
```

## PR 要求

一个 PR 只处理一个可验证目标，并包含：

- 背景与变更范围。
- 契约或数据库变更影响。
- 测试命令与实际结果。
- UI 变更截图或性能数据。
- 风险和回滚方式。
- AI 过程记录条目。

跨服务 PR 必须列出调用方、被调用方、事件 topic 和兼容策略。涉及 `packages/contracts/` 的 PR 至少需要另外两名成员批准。

## 合并顺序

优先按依赖方向合并：

1. 契约和测试数据。
2. 数据模型与后端服务。
3. 消息和推送。
4. 前端。
5. 部署、观测和演示脚本。

契约先合并，代码后合并。禁止把契约和大量无关实现塞进同一个 PR。

## 每次提交前

- 拉取 `main` 和个人分支最新提交。
- 运行受影响模块的测试。
- 更新 AI 日志中的问题与修复。
- 在任务看板更新阻塞项。

## 里程碑集成

满足一个里程碑的退出条件时进行共同集成：

- 从最新 `main` 合入三条个人分支。
- 用固定 seed 启动完整场景。
- 记录失败用例、冲突次数、死锁次数和性能。
- 形成下一里程碑唯一的最高优先级技术目标。

## 安全与数据

- 不提交真实个人信息、令牌、密钥和生产数据。
- 演示数据由脚本生成，可重新创建。
- 不将 MySQL、Redis、Kafka、快照和轨迹原始文件提交到 Git。
