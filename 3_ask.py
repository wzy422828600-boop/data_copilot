"""兼容入口。请优先使用: python scripts/ask.py "你的问题" """
import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "ask.py"),
    run_name="__main__",
)
