"""跑全量测试并把输出落盘为验收证据。

用法：python scripts\\save_test_evidence.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "evidence" / "unittest-output.txt"


def main() -> int:
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "tests", "-v"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    elapsed = time.perf_counter() - started
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(proc.stdout + proc.stderr, encoding="utf-8")
    tail = [line for line in proc.stdout.splitlines() if line.startswith("Ran ") or line == "OK"]
    print(f"退出码 {proc.returncode}，耗时 {elapsed:.1f}s，输出已写入 {OUT.name}")
    for line in tail:
        print(line)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
