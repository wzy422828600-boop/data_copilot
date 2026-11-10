"""自动可视化：按结果形状选择折线 / 柱状 / 分组柱状 / KPI，并做中文友好格式化。"""
from __future__ import annotations

import math
import os
import re

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

# 中文字体：一串候选，matplotlib 自动用系统里存在的第一个（跨 macOS/Win/Linux）
matplotlib.rcParams["font.sans-serif"] = [
    "Songti SC", "PingFang SC", "STHeiti", "Arial Unicode MS",
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "sans-serif"]
matplotlib.rcParams["axes.unicode_minus"] = False

_TIME_RE = re.compile(r"月|日期|时间|年|季度|date|month|year|quarter", re.IGNORECASE)
_PCT_RE = re.compile(r"率|占比|百分比|percent|ratio|%", re.IGNORECASE)
PRESENTATION_REVISION = 2


def metric_unit(column: str) -> str:
    """Use declared metric units, without guessing unknown numeric columns."""
    from data_copilot.semantic_layer import METRICS

    declared = re.search(r"[（(](元/笔|万元|亿元|元|%|倍|笔|人|件)[）)]$", column)
    if declared:
        return declared.group(1)
    for metric in METRICS.values():
        if column.removeprefix("总") == metric.label:
            return metric.unit
    if _is_pct(column):
        return "%"
    return ""


def format_metric(value, column: str) -> str:
    if pd.isna(value) or not math.isfinite(float(value)):
        return "—"
    unit = metric_unit(column)
    if unit == "%":
        return f"{value:,.2f}%"
    if unit in {"元", "元/笔"}:
        return f"{value:,.2f} {unit}"
    return format_number(value) + (f" {unit}" if unit else "")


def table_height(df: pd.DataFrame) -> int:
    """Header and real rows only; large results scroll."""
    return min(420, 38 + 35 * max(1, len(df)))


def format_table(df: pd.DataFrame):
    formatters = {
        c: (lambda value, column=c: format_metric(value, str(column)))
        for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
    }
    return df.style.format(formatters, na_rep="—")


def _is_pct(col: str) -> bool:
    return bool(_PCT_RE.search(str(col)))


def format_number(x: float, pct: bool = False) -> str:
    """金额/数值千分位；百分比补 %。"""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return "—"
    if pct:
        return f"{x:,.1f}%"
    if isinstance(x, float) and not float(x).is_integer():
        return f"{x:,.1f}"
    return f"{x:,.0f}"


def choose_chart(df: pd.DataFrame) -> dict:
    """返回图表方案 spec：kind ∈ {none, kpi, line, bar, grouped_bar}。"""
    if df is None or df.empty:
        return {"kind": "none", "reason": "空结果"}
    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])
               and df[c].replace([float("inf"), -float("inf")], float("nan")).notna().any()]
    if not numeric:
        return {"kind": "none", "reason": "无数值列"}
    non_numeric = [c for c in df.columns if c not in numeric]

    # 单行结果 -> KPI 卡片（单个或少数几个指标）
    if len(df) == 1:
        return {"kind": "kpi", "metrics": numeric}

    x = non_numeric[0] if non_numeric else None
    if x is None:
        return {"kind": "none", "reason": "无类别维度"}
    ys = numeric
    if len({metric_unit(str(y)) for y in ys}) > 1:
        return {"kind": "none", "reason": "指标单位不同，请选择一个指标查看图表", "metrics": ys}
    if _TIME_RE.search(str(x)):
        return {"kind": "line", "x": x, "ys": ys}
    if len(ys) == 1:
        return {"kind": "bar", "x": x, "ys": ys}
    return {"kind": "grouped_bar", "x": x, "ys": ys}


def make_chart(df: pd.DataFrame, path: str, title: str = "",
               top_n: int = 15) -> str | None:
    """按 spec 出图并存到 path；KPI/空结果返回 None（由前端用卡片展示）。"""
    spec = choose_chart(df)
    kind = spec["kind"]
    if kind in ("none", "kpi"):
        return None

    x, ys = spec["x"], spec["ys"]
    data = df.replace([float("inf"), -float("inf")], float("nan")).copy()
    if kind == "line":
        data = data.sort_values(x, kind="stable")
    # 类别过多时按第一个数值列取 Top-N
    if kind in ("bar", "grouped_bar") and len(data) > top_n:
        data = data.sort_values(ys[0], ascending=False).head(top_n)
        title = (title or f"{ys[0]} Top {top_n}")

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    colors = ["#1f3a5f", "#3f6ea5", "#6f9ac9", "#a7c3e0"]

    if kind == "line":
        for i, y in enumerate(ys):
            ax.plot(data[x].astype(str), data[y], marker="o",
                    color=colors[i % len(colors)], label=str(y))
        if len(ys) > 1:
            ax.legend()
    elif kind == "bar":
        ax.bar(data[x].astype(str), data[ys[0]], color=colors[1])
    else:  # grouped_bar
        import numpy as np
        idx = np.arange(len(data))
        w = 0.8 / len(ys)
        for i, y in enumerate(ys):
            ax.bar(idx + i * w, data[y], width=w,
                   color=colors[i % len(colors)], label=str(y))
        ax.set_xticks(idx + 0.4 - w / 2)
        ax.set_xticklabels(data[x].astype(str))
        ax.legend()

    pct = all(_is_pct(y) for y in ys)
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: format_number(v, pct)))
    ax.set_xlabel(str(x))
    unit = metric_unit(str(ys[0]))
    ax.set_ylabel("、".join(str(y) for y in ys) + (f"（{unit}）" if unit else ""))
    ax.set_title(title or f"{'、'.join(map(str, ys))}（按{x}）")
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
    return path
