"""讓 isolated mode 的 pytest 能找到 comfy_web 套件。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
