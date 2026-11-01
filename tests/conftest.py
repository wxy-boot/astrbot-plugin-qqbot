"""测试公共配置：

- 把仓库根目录加入 ``sys.path``，便于直接 ``import qbot`` / ``import main``；
- 把 AstrBot 根目录指向临时目录，避免导入 AstrBot 时在仓库里生成 ``data/``。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault(
    "ASTRBOT_ROOT", tempfile.mkdtemp(prefix="astrbot-test-root-")
)
