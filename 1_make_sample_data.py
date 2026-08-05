"""兼容入口。请优先使用: python scripts/make_sample_data.py"""
import os
import runpy

runpy.run_path(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "make_sample_data.py"),
    run_name="__main__",
)
