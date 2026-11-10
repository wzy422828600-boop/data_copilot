"""业务语义层：指标识别、答案边界和 SQL 编译。"""
from __future__ import annotations

import pytest

from data_copilot import database, semantic_layer, text_to_sql
from evaluation.questions import load


def _signature(df) -> list[tuple[str, ...]]:
    rows = []
    for _, row in df.iterrows():
        values = []
        for value in row:
            if isinstance(value, (int, float)):
                values.append(str(round(float(value), 1)))
            else:
                values.append(str(value))
        rows.append(tuple(sorted(values)))
    return sorted(rows)


def test_plan_exposes_metric_dimension_filter_and_top_n():
    plan = semantic_layer.analyze_question("VIP会员销售额最高的5个产品")
    assert plan.status == "ready"
    assert plan.metrics == ("revenue",)
    assert plan.dimensions == ("product",)
    assert "会员等级=VIP" in plan.filters
    assert plan.top_n == 5


@pytest.mark.parametrize("question", [
    "删除所有订单",
    "把所有产品价格更新为0",
    "DROP TABLE customers",
])
def test_natural_language_write_intent_is_blocked(question):
    plan = semantic_layer.analyze_question(question)
    assert plan.status == "blocked"
    assert "只读" in plan.reason


@pytest.mark.parametrize("question, missing", [
    ("顾客手机号是什么？", "手机号"),
    ("每个产品还有多少库存？", "库存"),
    ("退货率是多少？", "退货"),
])
def test_missing_field_is_explicitly_unanswerable(question, missing):
    plan = semantic_layer.analyze_question(question)
    assert plan.status == "unanswerable"
    assert missing in plan.reason


@pytest.mark.parametrize(
    "case",
    [q for q in load() if not q.get("should_reject") and not q.get("unanswerable")],
    ids=lambda q: q["id"],
)
def test_semantic_fallback_matches_gold_result(case):
    candidate_sql = text_to_sql.offline_sql(case["question"])
    candidate, _ = database.run_query(candidate_sql)
    gold, _ = database.run_query(case["gold_sql"])
    assert _signature(candidate) == _signature(gold)


def test_prompt_context_contains_governed_metric_only():
    plan = semantic_layer.analyze_question("每月销售额趋势")
    context = semantic_layer.prompt_context(plan)
    assert "SUM(order_items.quantity * order_items.unit_price)" in context
    assert "分析维度：月份" in context
    assert "手机号" not in context
