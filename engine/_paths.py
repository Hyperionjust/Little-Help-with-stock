"""路径引导：把三层结构的各目录注册进 sys.path，让扁平导入继续可用。

为什么这样做而不是改成包相对导入：
迁移自 stock-metrics-pro 的 20 多个模块之间是扁平互相导入的（`from base import Provider`、
`from _util import av`）。改成包相对导入需要动每一个文件，且会破坏"脚本可单独 CLI 调用"这个
已验证的用法。这里用一个引导模块集中注册路径，各模块顶部 `import _paths` 即可，其余代码零改动。

任何新增子目录（如未来的 engine/macro/）只需在 _DIRS 里加一行。
"""
from __future__ import annotations
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../engine
ROOT = os.path.dirname(_HERE)                                # repo 根

_DIRS = [
    ROOT,
    _HERE,
    os.path.join(_HERE, "providers"),
    os.path.join(_HERE, "providers", "free"),
    os.path.join(_HERE, "providers", "paid"),
    os.path.join(_HERE, "pharma"),
    os.path.join(_HERE, "macro"),
    os.path.join(ROOT, "publisher"),
    os.path.join(ROOT, "contracts"),
    os.path.join(ROOT, "analyst"),
]

for _d in _DIRS:
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)
