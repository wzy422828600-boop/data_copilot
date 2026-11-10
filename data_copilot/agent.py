"""可解释的分析 Agent 编排层。

Agent 负责按顺序调用语义层、SQL 生成器、安全执行器、结果校验、可视化选择器和
洞察模块，并记录每一步的状态与耗时。底层模块仍可单独测试和替换。
"""

from __future__ import annotations

import calendar
import time
from dataclasses import asdict, dataclass, replace
from datetime import date
from pathlib import Path

import pandas as pd

from . import business_analysis, database, insights, semantic_layer, text_to_sql, visualization
from .quality import QualityReport, validate_result
from .semantic_layer import SemanticPlan
from .text_to_sql import SqlResult


@dataclass(frozen=True)
class TraceStep:
    name: str
    status: str
    detail: str
    latency_ms: float

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class AgentResult:
    question: str
    plan: SemanticPlan
    sql_result: SqlResult
    quality: QualityReport
    insight: str
    chart_spec: dict
    trace: list[TraceStep]
    truncated: bool = False
    total_latency_ms: float = 0.0
    analysis: dict | None = None

    @property
    def sql(self) -> str:
        return self.sql_result.sql

    @property
    def df(self) -> pd.DataFrame | None:
        return self.sql_result.df

    @property
    def mode(self) -> str:
        return self.sql_result.mode

    @property
    def blocked(self) -> bool:
        return self.sql_result.blocked

    @property
    def block_reason(self) -> str:
        return self.sql_result.block_reason

    @property
    def error(self) -> str | None:
        return self.sql_result.error


def analyze(
    question: str,
    *,
    schema: str | None = None,
    db_path: Path | str | None = None,
    reference_date: date | None = None,
    use_llm: bool = False,
) -> AgentResult:
    """执行完整分析链路，并返回可供 UI、CLI 与评测共享的结构化结果。"""
    started = time.perf_counter()
    trace: list[TraceStep] = []

    t0 = time.perf_counter()
    plan = semantic_layer.analyze_question(question, reference_date=reference_date)
    # Writes are always blocked first. Trusted local analysis still uses guarded
    # read-only connections and never delegates accounting calculations to a model.
    if plan.status != "blocked":
        business = business_analysis.try_answer(
            question, db_path=db_path, reference_date=reference_date
        )
        if business is not None:
            ready = business.plan.status == "ready"
            sql_result = SqlResult(
                question=question,
                sql=business.sql,
                mode="analysis" if ready else "policy",
                df=business.df,
            )
            quality = (
                QualityReport(
                    "passed",
                    100,
                    "本地计算已核对",
                    ("明确期间、按流量/余额/比率口径计算",),
                    (),
                    "合成数据上的数值拆解不等于真实业务因果；未调用在线模型",
                )
                if ready
                else validate_result(business.plan, None)
            )
            return AgentResult(
                question=question,
                plan=business.plan,
                sql_result=sql_result,
                quality=quality,
                insight=business.insight,
                chart_spec={"kind": "none", "reason": "结构化分析报告"},
                trace=[
                    TraceStep(
                        "问题与期间确认",
                        business.plan.status,
                        "；".join(business.notes) if ready else business.insight,
                        0,
                    ),
                    TraceStep(
                        "只读计算与核验",
                        "passed" if ready else "skipped",
                        "本地报表聚合/贡献拆解；API调用=0",
                        _elapsed_ms(t0),
                    ),
                ],
                total_latency_ms=_elapsed_ms(started),
                analysis={"tables": business.tables, "notes": business.notes} if ready else None,
            )
    if plan.status == "ready" and semantic_layer.compile_sql(plan) is None:
        plan = replace(
            plan, status="clarify", reason="当前指标与条件组合还不受支持，请拆成单指标查询。"
        )
    if plan.status == "ready":
        plan = _check_date_coverage(plan, db_path)
    trace.append(
        TraceStep(
            "语义解析",
            plan.status,
            semantic_layer.describe_plan(plan),
            _elapsed_ms(t0),
        )
    )

    if plan.status == "blocked":
        sql_result = SqlResult(
            question=question,
            sql="",
            mode="policy",
            blocked=True,
            block_reason=plan.reason,
        )
        trace.append(TraceStep("安全策略", "blocked", plan.reason, 0.0))
        quality = validate_result(plan, None)
        return _result(
            question,
            plan,
            sql_result,
            quality,
            "请求涉及写操作，已在生成 SQL 前拦截。",
            {"kind": "none", "reason": "请求被拦截"},
            trace,
            False,
            started,
        )

    if schema is None:
        t0 = time.perf_counter()
        schema = database.schema_text(db_path, include_samples=False)
        trace.append(
            TraceStep("Schema 检索", "passed", "仅读取表名与字段，不发送样例行", _elapsed_ms(t0))
        )

    if plan.status == "unanswerable":
        return _unanswerable_result(question, plan, db_path, trace, started)

    if plan.status == "clarify":
        sql_result = SqlResult(question=question, sql="", mode="policy", notice=plan.reason)
        quality = validate_result(plan, None)
        trace.append(TraceStep("澄清判断", "needs_input", plan.reason, 0.0))
        return _result(
            question,
            plan,
            sql_result,
            quality,
            plan.reason,
            {"kind": "none", "reason": "需要补充问题"},
            trace,
            False,
            started,
        )

    enriched_schema = schema + semantic_layer.prompt_context(plan)
    execution: dict[str, bool] = {"truncated": False}

    def runner(sql: str) -> pd.DataFrame:
        df, truncated = database.run_query(sql, db_path=db_path)
        execution["truncated"] = truncated
        return df

    t0 = time.perf_counter()
    sql_result = text_to_sql.generate(
        question, enriched_schema, runner=runner, plan=plan, use_llm=use_llm
    )
    generation_detail = (
        f"模式={sql_result.mode}；API调用={sql_result.api_calls}；自动纠错={sql_result.retries}"
    )
    status = "blocked" if sql_result.blocked else "failed" if sql_result.error else "passed"
    trace.append(TraceStep("SQL 生成与执行", status, generation_detail, _elapsed_ms(t0)))

    t0 = time.perf_counter()
    quality = validate_result(plan, sql_result.df, truncated=execution["truncated"])
    trace.append(
        TraceStep(
            "结果校验",
            quality.status,
            f"结果状态={quality.label}；该检查不是答案正确率",
            _elapsed_ms(t0),
        )
    )

    if sql_result.mode == "llm" and quality.status == "passed":
        # Bounded plan cross-check: useful for omitted conditions in model SQL,
        # but cannot prove that the natural-language parser understood the user.
        reference_sql = semantic_layer.compile_sql(plan)
        try:
            reference_df, _ = database.run_query(reference_sql, db_path=db_path)
            agrees = _same_result(sql_result.df, reference_df)
        except Exception:  # A failed check must never become a trusted answer.
            agrees = False
        if not agrees:
            quality = replace(
                quality,
                status="review",
                label="需复核",
                warnings=quality.warnings
                + ("模型结果与受支持分析计划的结果不一致，暂不生成结论。",),
            )
        trace.append(
            TraceStep(
                "计划结果对照",
                quality.status,
                "仅对照当前结构化计划；不能证明自然语言理解完整",
                0.0,
            )
        )

    t0 = time.perf_counter()
    if sql_result.df is not None and not sql_result.blocked and quality.status == "passed":
        chart_spec = visualization.choose_chart(sql_result.df)
        # Keep conclusions bounded to result templates; online experiments only
        # generate SQL, so query rows are not sent to a second model call.
        insight = insights.summarize(question, sql_result.df, use_llm=False)
        presentation_status = "passed"
        presentation_detail = f"图表类型={chart_spec.get('kind', 'none')}"
    else:
        chart_spec = {"kind": "none", "reason": "没有可展示的结果"}
        insight = (
            sql_result.error
            or sql_result.block_reason
            or "；".join(quality.warnings)
            or "没有可展示的结果。"
        )
        presentation_status = "skipped"
        presentation_detail = chart_spec["reason"]
    trace.append(
        TraceStep("可视化与洞察", presentation_status, presentation_detail, _elapsed_ms(t0))
    )

    return _result(
        question,
        plan,
        sql_result,
        quality,
        insight,
        chart_spec,
        trace,
        execution["truncated"],
        started,
    )


def _unanswerable_result(
    question: str,
    plan: SemanticPlan,
    db_path: Path | str | None,
    trace: list[TraceStep],
    started: float,
) -> AgentResult:
    sql = semantic_layer.compile_sql(plan) or "SELECT '无法回答' AS note"
    t0 = time.perf_counter()
    df, truncated = database.run_query(sql, db_path=db_path)
    sql_result = SqlResult(question=question, sql=sql, mode="semantic", df=df, notice=plan.reason)
    trace.append(
        TraceStep("答案边界", "unanswerable", f"{plan.reason}；未调用模型", _elapsed_ms(t0))
    )
    quality = validate_result(plan, df, truncated=truncated)
    chart_spec = {"kind": "none", "reason": "现有数据不足以回答"}
    return _result(
        question,
        plan,
        sql_result,
        quality,
        plan.reason.rstrip("。；，") + "；未生成推测性答案。",
        chart_spec,
        trace,
        truncated,
        started,
    )


def _result(
    question: str,
    plan: SemanticPlan,
    sql_result: SqlResult,
    quality: QualityReport,
    insight: str,
    chart_spec: dict,
    trace: list[TraceStep],
    truncated: bool,
    started: float,
) -> AgentResult:
    return AgentResult(
        question=question,
        plan=plan,
        sql_result=sql_result,
        quality=quality,
        insight=insight,
        chart_spec=chart_spec,
        trace=trace,
        truncated=truncated,
        total_latency_ms=(time.perf_counter() - started) * 1000,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _check_date_coverage(plan: SemanticPlan, db_path) -> SemanticPlan:
    periods = [f.split("=", 1)[1] for f in plan.filters if f.startswith("月份=")]
    if plan.intent == "period_growth" and len(periods) == 1:
        year, month = map(int, periods[0].split("-"))
        periods.append(f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}")
    ranges = []
    if periods:
        for item in periods:
            year, month = map(int, item.split("-"))
            ranges.append(
                (date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
            )
    else:
        for item in plan.filters:
            if item.startswith("年份="):
                year = int(item.split("=", 1)[1])
                quarters = [int(f[-1]) for f in plan.filters if f.startswith("季度=")]
                if quarters:
                    for quarter in quarters:
                        month = quarter * 3
                        ranges.append(
                            (
                                date(year, month - 2, 1),
                                date(year, month, calendar.monthrange(year, month)[1]),
                            )
                        )
                else:
                    ranges.append((date(year, 1, 1), date(year, 12, 31)))
    if not ranges:
        return plan
    lo, hi = database.order_date_range(db_path)
    if lo is None or hi is None:
        return replace(
            plan, status="unanswerable", reason="数据库没有订单记录，无法回答所请求期间。"
        )
    start, end = date.fromisoformat(lo[:10]), date.fromisoformat(hi[:10])
    if any(right < start or left > end for left, right in ranges):
        requested = "、".join(f"{left}至{right}" for left, right in ranges)
        return replace(
            plan,
            status="unanswerable",
            intent="out_of_coverage",
            reason=f"订单记录范围为{start}至{end}，请求期间为{requested}，有期间不在记录范围内；"
            "不能把缺少记录解释为真实销售额为零，请改用覆盖期间。",
        )
    return plan


def _same_result(candidate, reference) -> bool:
    if candidate is None or set(candidate.columns) != set(reference.columns):
        return False
    columns = list(reference.columns)
    left = candidate[columns].sort_values(columns, na_position="last").reset_index(drop=True)
    right = reference.sort_values(columns, na_position="last").reset_index(drop=True)
    try:
        pd.testing.assert_frame_equal(
            left, right, check_dtype=False, check_exact=False, rtol=1e-6, atol=1e-5
        )
        return True
    except AssertionError:
        return False
