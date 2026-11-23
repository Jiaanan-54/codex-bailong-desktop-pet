"""白龙桌宠 macOS 启动入口"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))

if __name__ == '__main__':
    from enhanced import main
    raise SystemExit(main())
