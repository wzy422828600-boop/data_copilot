"""共享 fixture：把项目根加入 path，并保证演示数据库存在。"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from data_copilot import config  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def ensure_db() -> None:
    """整轮测试前确保数据库存在（不存在就生成）。"""
    if not config.DB_PATH.exists():
        subprocess.run(
            [sys.executable, os.path.join(ROOT, "scripts", "make_sample_data.py")],
            check=True,
        )
