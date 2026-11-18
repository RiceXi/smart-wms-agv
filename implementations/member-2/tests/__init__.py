"""成员二个人版的自动化测试。

只用标准库 `unittest`，不引入第三方依赖，保证干净环境可直接运行：

```text
cd implementations/member-2
python -m unittest discover -s tests -t tests -v
```

也可用 `python -m tests` 一次性跑完（等价命令见 `tests/__main__.py`）。
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_DIR = TESTS_DIR.parent

for _path in (str(PROJECT_DIR), str(TESTS_DIR)):
    if _path not in sys.path:
        sys.path.insert(0, _path)
