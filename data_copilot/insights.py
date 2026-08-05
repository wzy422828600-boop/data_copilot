"""结果洞察：有 API Key 用 Claude 总结，否则用离线模板报出关键数字。"""
from __future__ import annotations

import pandas as pd

from . import config
from .text_to_sql import _call_claude
from .visualization import _is_pct, choose_chart, format_number

_SYS = ("你是数据分析师。用一到两句中文，基于给定查询结果给出关键洞察"
        "并点出具体数字，不要复述整张表。")


def summarize(question: str, df: pd.DataFrame) -> str:
    """一两句中文洞察。在线失败或无 key 时用离线模板，绝不抛异常。"""
    if df is None or df.empty:
        return "查询没有返回任何结果。"
    if config.llm_available():
        table = df.head(15).to_string(index=False)
        call = _call_claude(_SYS, f"问题：{question}\n结果：\n{table}",
                            config.MAX_TOKENS_INSIGHT)
        if not call["error"] and call["text"]:
            return call["text"]
        # 在线失败 -> 落到离线模板（不打断用户）
    return offline_summary(df)


def offline_summary(df: pd.DataFrame) -> str:
    """离线模板：单值直接报数；多行报出排名第一那行。"""
    spec = choose_chart(df)
    if spec["kind"] == "kpi":
        col = spec["metrics"][0]
        return f"[离线模板] {col}为 {format_number(df.iloc[0][col], _is_pct(col))}。"
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    non_numeric = [c for c in df.columns if c not in numeric]
    if numeric and non_numeric:
        val = numeric[0]
        top = df.sort_values(val, ascending=False).iloc[0]
        return (f"[离线模板] {non_numeric[0]}为「{top[non_numeric[0]]}」时"
                f"{val}最高，为 {format_number(top[val], _is_pct(val))}。")
    return f"[离线模板] 查询返回 {len(df)} 行结果。"
