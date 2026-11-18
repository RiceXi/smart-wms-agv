# 验收证据目录

本目录存放 AC1–AC20 的可复现证据。

| 文件 | 内容 | 生成命令 |
| --- | --- | --- |
| `unittest-output.txt` | 88 个用例的完整输出（末行 `Ran 88 tests ... OK`） | `python scripts\save_test_evidence.py` |
| `scenario-run.txt` | 8 个固定场景的回归输出 | `python scripts\run_scenarios.py` |
| `bench-summary.json` | 三个场景的性能基准汇总 | `python scripts\save_bench.py` |
| `bench_single-agv.json` / `bench_peak-10.json` / `bench_peak-30.json` | 单场景基准明细 | 同上 |
| `ui-console.png` | 前端控制台在 `peak-10` 场景、8 倍速运行时的截图（帧率 120 fps、SSE 已连接） | 浏览器实测 |

`../scenario-results.json` 是场景结果的机器可读版本。
