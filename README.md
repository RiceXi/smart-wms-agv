# 仓储 WMS 与 AGV 调度系统

软工导论小组作业：三名成员使用不同 AI 工具，基于同一份需求独立完成仓储 WMS 与 AGV 调度系统，随后比较开发过程、实现质量和 AI 使用效果，并完成一个整合优化版本。

当前阶段：个人独立开发。

## 组员与工具分配

| 成员 | GitHub 账号 | 使用 AI 工具 | 开发分支 | 源码目录 |
| --- | --- | --- | --- | --- |
| 成员一 | RiceXi | DeepSeek-V4.1-Flash | dev/member-1 | implementations/member-1/ |
| 成员二 | Zsource-Fuyi | MiniMax M3.1 Flash | dev/member-2 | implementations/member-2/ |
| 成员三 | KingSure000 | Gemini-4-Carbon | dev/member-3 | implementations/member-3/ |

三名成员分别 fork 上游仓库，在自己的 fork 中使用对应 AI 工具和开发分支完成个人版本。个人版本冻结后，差异通过 PR 汇总到上游 `main`，再进入小组对比和整合阶段。

## 项目阶段

1. 需求冻结：确认范围、数据契约、状态机、冲突规则和验收用例。
2. 个人独立开发：三名成员分别完成需求分析、方案设计、编码、测试、运行说明和 AI 记录。
3. 版本冻结：三份实现完成后创建冻结 tag，并通过 PR 合入上游 `main`。
4. 对比分析：比较不同 AI 工具在需求理解、架构选择、代码质量、测试覆盖、错误模式和人工修改量上的差异。
5. 整合优化：选择一个基础版本，吸收其他版本优点，完成可运行、可演示、可评析的最终版本。

当前只执行第 1、2 阶段。对比报告、整合版和最终评析在个人版本冻结后开始。

## 开工顺序

1. 阅读 [AGENTS.md](AGENTS.md)。
2. 阅读 [需求文档](docs/requirement.md)。
3. 阅读 [数据契约](docs/data-schema.md)。
4. 阅读 [验收用例](docs/acceptance.md)。
5. 阅读 [个人开发契约](docs/development-contract.md)。
6. 只在自己的实现目录和个人报告中开发。

## 仓库结构

```text
.
├── README.md
├── AGENTS.md
├── CONTRIBUTING.md
├── docs/
│   ├── requirement.md
│   ├── data-schema.md
│   ├── acceptance.md
│   ├── development-contract.md
│   ├── ai-log-template.md
│   ├── report/
│   │   ├── member-1.md
│   │   ├── member-2.md
│   │   ├── member-3.md
│   │   ├── compare.md
│   │   └── evaluation.md
│   └── assets/
├── implementations/
│   ├── member-1/
│   ├── member-2/
│   └── member-3/
└── merged-release/
```

## 交付物

- 三份可独立运行的仓储 WMS 与 AGV 调度系统。
- 三份个人 AI 辅助开发报告。
- 三份版本冻结记录和验收结果。
- 小组对比讨论报告。
- 一个整合优化版本。
- 软件评析报告。

## 协作边界

- 个人开发阶段禁止修改公共需求、数据契约、验收用例和开发契约。
- 每人只修改自己的实现目录和个人报告。
- 个人版本冻结前不得查看或复制其他成员实现。
- fork 和分支只用于隔离个人开发，不代表个人项目。
- 所有版本必须遵守同一份业务契约和验收标准。
