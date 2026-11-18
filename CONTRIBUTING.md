# 协作约定

## Fork 与分支

上游主仓库：`https://github.com/RiceXi/smart-wms-agv.git`

三名成员分别 fork 上游仓库，并维护以下分支：

- `main`：公共文档、冻结后的个人版本和后续整合成果。
- `dev/member-1`：成员一使用 DeepSeek-V4.1-Flash 的个人开发分支。
- `dev/member-2`：成员二使用 MiniMax M3.1 Flash 的个人开发分支。
- `dev/member-3`：成员三使用 Gemini-4-Carbon 的个人开发分支。
- `feat/integrate`：三份个人版本冻结后创建，用于整合版。

## 个人阶段规则

- 每人只修改自己的实现目录和个人报告。
- 公共文档由项目负责人维护。
- 个人版本冻结前，不参考其他人的代码。
- 个人开发在各自 fork 的 `dev/member-N` 中完成。
- 不得向上游 `main` 直接推送。
- 个人版本完成后，通过跨 fork PR 提交到上游 `main`。

## 提交信息

使用以下格式：

```text
<type>(<scope>): <summary>
```

允许的 `type`：`feat`、`fix`、`docs`、`test`、`refactor`、`perf`、`build`、`chore`。

示例：

```text
feat(wms): implement inventory allocation
fix(dispatch): prevent opposite edge traversal
test(sim): cover tick replay with fixed seed
docs(member-2): add AI error analysis
```

## 个人版本提交

个人版本完成前至少包含：

- 实现源码。
- 运行说明。
- 测试和验收结果。
- `ai-notes.md`。
- 个人报告。

个人版本冻结后，由项目负责人创建 tag：

```text
v0.2.0-member-1-frozen
v0.2.0-member-2-frozen
v0.2.0-member-3-frozen
```

## 跨 Fork PR

个人版本冻结后，从个人 fork 的 `dev/member-N` 向上游 `main` 创建 PR。PR 必须包含：

- 个人版本范围与运行方式。
- 需求分析、设计说明和代码结构。
- 验收用例执行结果。
- AI 工具、关键提示词和错误修改记录。
- 已知限制和不支持范围。

个人冻结前不创建整合 PR，避免不同阶段内容混在一起。

## 整合阶段

三份个人版本冻结后：

1. 拉取并核对三份实现。
2. 填写 `docs/report/compare.md`。
3. 在 `feat/integrate` 中完成整合版。
4. 将整合版放入 `merged-release/`。
5. 填写 `docs/report/evaluation.md`。
6. 通过 PR 合入上游 `main`。
