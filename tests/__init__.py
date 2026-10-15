"""测试包：把项目根加入 sys.path，保证从任意目录 discover 都能导入 src。"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
