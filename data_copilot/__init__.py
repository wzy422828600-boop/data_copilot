"""Text-to-SQL Analytics Assistant (Retail BI Copilot).

一个把中文业务问题翻译成只读 SQL、执行、可视化并给出洞察的小型分析助手。
自然语言 -> SQL -> 只读执行 -> 图表 -> 洞察。
"""
from __future__ import annotations

__version__ = "0.2.0"

__all__ = [
    "config",
    "database",
    "security",
    "text_to_sql",
    "visualization",
    "insights",
]
