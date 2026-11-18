"""一次性运行全部测试：``python -m tests``。"""

from __future__ import annotations

import sys
import unittest


def main() -> int:
    loader = unittest.TestLoader()
    suite = loader.discover(start_dir=str(__import__("pathlib").Path(__file__).resolve().parent),
                            top_level_dir=str(__import__("pathlib").Path(__file__).resolve().parent))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
