"""control-plane 测试配置。

服务不以可分发包安装（tool.uv package=false），
此处把服务目录加入 sys.path，使 ``import app.*`` 在仓库根运行 pytest 时可用。
"""

from __future__ import annotations

import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[1]

if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))
