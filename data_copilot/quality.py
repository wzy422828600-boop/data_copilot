"""查询结果的轻量可信度校验。

校验只能发现明显异常，不能证明 SQL 在语义上绝对正确。这个边界会在 UI 和 README
中明确展示，避免把“执行成功”误写成“答案正确”。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

from .semantic_layer import DIMENSIONS, METRICS, SemanticPlan


@dataclass(frozen=True)
class QualityReport:
    status: str
    score: int
    label: str
    checks: tuple[str, ...]
    warnings: tuple[str, ...]
    limitations: str = (
        "此处检查结果结构与取值，不是答案正确率或概率；"
        "不能证明所有自然语言条件已被识别，也不能证明因果关系"
    )

    def as_dict(self) -> dict:
        return asdict(self)


def validate_result(
    plan: SemanticPlan,
    df: pd.DataFrame | None,
    *,
    truncated: bool = False,
) -> QualityReport:
    """检查结果形状、字段、数值范围和 Top-N 约束，返回可解释评分。"""
    if plan.status == "blocked":
        return QualityReport(
            "blocked",
            100,
            "安全拦截",
            ("只读策略已在 SQL 生成前拦截写操作意图",),
            (),
        )
    if plan.status == "clarify":
        return QualityReport("needs_input", 0, "需要补充", (), (plan.reason,))
    if df is None:
        return QualityReport("failed", 0, "不可用", (), ("查询没有产生结果集",))
    if df.empty:
        return QualityReport(
            "no_data", 0, "无可用数据", (), ("没有符合筛选条件的记录；不能推断真实业务为零",)
        )

    text_blob = " ".join(df.astype(str).fillna("").to_numpy().ravel())
    if plan.status == "unanswerable" or "无法回答" in text_blob:
        return QualityReport(
            "unanswerable",
            100,
            "数据不足",
            ("所需字段或期间不在可用数据范围内，系统未编造答案",),
            (),
        )

    checks: list[str] = ["SQL 已在只读连接中成功执行"]
    warnings: list[str] = []
    score = 100

    numeric = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    if plan.metrics and numeric:
        checks.append("结果包含可计算的数值指标")
    elif plan.metrics:
        warnings.append("未识别到数值指标列")
        score -= 35

    expected = [METRICS[m].label for m in plan.metrics]
    expected.extend(DIMENSIONS[d].label for d in plan.dimensions)
    matched = [label for label in expected if label in df.columns]
    missing = [label for label in expected if label not in df.columns]
    if not missing and matched:
        checks.append("返回字段与语义计划匹配：" + "、".join(matched))
    elif missing:
        warnings.append("缺少计划字段或列名未对齐：" + "、".join(missing))
        score -= 15

    present_metrics = [
        name
        for name in expected
        if name in df.columns and name not in [DIMENSIONS[d].label for d in plan.dimensions]
    ]
    if present_metrics and df[present_metrics].isna().all().all():
        return QualityReport(
            "no_data",
            0,
            "无可用数据",
            tuple(checks),
            ("指标全为空：可能没有匹配记录，或环比基期缺失/为零。不能把空值当成0。",),
        )

    if plan.intent == "kpi" and len(df) != 1:
        warnings.append("整体 KPI 预期返回 1 行，当前结果形状异常")
        score -= 25
    if plan.dimensions and len(df.columns) < 2:
        warnings.append("分维度分析缺少维度列或指标列")
        score -= 30
    if plan.top_n and len(df) > plan.top_n:
        warnings.append(f"结果超过计划中的 Top {plan.top_n} 行")
        score -= 20

    for metric in plan.metrics:
        label = METRICS[metric].label
        if label not in df.columns:
            continue
        values = pd.to_numeric(df[label], errors="coerce").dropna()
        if metric == "margin_rate" and (values > 100).any():
            warnings.append("在正销售额、非负成本口径下，毛利率不应高于100%")
            score -= 25
        elif metric == "growth_rate" and (values < -100).any():
            warnings.append("在非负销售额且基期为正的口径下，增长率不应低于-100%")
            score -= 25

    nonnegative = {"revenue", "order_count", "customer_count", "quantity", "aov"}
    for metric in nonnegative.intersection(plan.metrics):
        label = METRICS[metric].label
        if label not in df.columns:
            continue
        values = pd.to_numeric(df[label], errors="coerce").dropna()
        if (values < 0).any():
            warnings.append("非负业务指标出现负值")
            score -= 30
        else:
            checks.append("非负指标取值范围正常")

    if truncated:
        warnings.append("结果达到行数上限，图表与洞察仅基于截断结果")
        score -= 15

    score = max(0, score)
    label = "需复核" if warnings else "结构检查通过"
    status = "review" if warnings else "passed"
    return QualityReport(status, score, label, tuple(checks), tuple(warnings))
