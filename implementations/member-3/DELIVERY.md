# 成员三版本交付说明

成员为 KingSure000，个人开发分支为 `dev/member-3`。以下路径以仓库根目录为基准。

## GitHub 提交范围

| 路径 | 交付内容 |
| --- | --- |
| `implementations/member-3/src/`、`public/`、`scenarios/` | 服务端源码、网页资源和固定演示场景，三个目录均位于成员三实现目录内 |
| `implementations/member-3/package.json`、`启动成员三系统.cmd`、`.gitattributes`、`.gitignore` | 运行时要求、启动入口、批处理换行规则和本地文件排除规则，均位于成员三实现目录内 |
| `implementations/member-3/tests/`、`scripts/`、`evidence/` | 自动化测试、性能与浏览器验证脚本、验收记录及原始证据，均位于成员三实现目录内 |
| `implementations/member-3/*.md` | 运行说明、接口说明、设计文档、AI 开发记录和交付说明 |
| `docs/report/member-3.md` | 成员三个人开发分析报告 |

本地运行数据位于实现目录的 `runtime/`，包括 SQLite 数据库、进程锁和浏览器配置。该目录与仓库根目录的 `PROGRESS_MEMBER3.md` 均排除在提交范围之外。验收索引引用的原始测试日志按文件名保留。仓库保留现场复验的四幅界面截图及 AC14 引用的两幅初版截图。各轮浏览器测试的断言及采样结果均保存在对应 JSON 文件中。

## 接收与运行

从个人 Fork 的 `dev/member-3` 分支获取源码后，在成员三实现目录中执行 `node src/server.js`。运行环境要求 Node.js 24 或更高版本。Windows 用户也可使用随附的启动脚本。详细步骤见[运行说明](README.md#运行环境与启动)。

采用 ZIP 交付演示程序时，压缩包须保留 `src/`、`public/`、`scenarios/` 的目录结构，并包含 `package.json`、`启动成员三系统.cmd` 和 `README.md`。首次启动时，程序自动初始化接收端数据库。课程验收使用上表所列完整交付内容。

## 验收材料

- 接口和调度设计见[本地接口说明](API.md)和[需求分析与设计](DESIGN.md)。
- 验收索引按 AC1～AC20 列出测试结果、原始证据和适用范围。历史审查中的失败记录用于追溯修复过程。[验收索引](evidence/acceptance.md)
- AI 开发记录包含关键提示词、实现错误、修复方法和人工参与范围。[AI 开发记录](ai-notes.md)
- 个人报告汇总需求、实现、测试、性能及已知限制。[个人开发分析报告](../../docs/report/member-3.md)

## 提交与冻结

在仓库根目录执行以下命令可预览待提交文件。

```sh
git status --short --branch
git add --dry-run -- implementations/member-3 docs/report/member-3.md
```

提交信息采用 `<type>(<scope>): <summary>` 格式。个人版本提交至 KingSure000 的 Fork，分支为 `dev/member-3`。项目负责人核验后创建 `v0.2.0-member-3-frozen` 标签。冻结后，从个人分支向上游 `main` 发起跨 Fork PR，在说明中列明版本范围、运行方式、验收结果、AI 开发记录及已知限制。流程依据见[协作约定](../../CONTRIBUTING.md)。
