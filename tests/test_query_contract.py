"""Regression cases found while reviewing actual product behavior, not an LLM benchmark."""

from datetime import date

import pandas as pd
import pytest

from data_copilot import agent, config, database, semantic_layer, text_to_sql
from data_copilot.quality import validate_result


def equivalent(question, sql):
    result = agent.analyze(question, reference_date=date(2024, 11, 5))
    expected, _ = database.run_query(sql)
    assert result.quality.status == "passed", result.insight
    assert set(result.df.columns) == set(expected.columns)
    cols = list(expected.columns)
    pd.testing.assert_frame_equal(
        result.df[cols].sort_values(cols).reset_index(drop=True),
        expected.sort_values(cols).reset_index(drop=True),
        check_dtype=False,
    )
    return result


@pytest.mark.parametrize(
    "question",
    [
        "2024年线上销量最高的三个品类是什么？",
        "今年线上销量最高的三个品类是什么？",
    ],
)
def test_year_channel_and_top_n_are_all_applied(question):
    result = equivalent(
        question,
        "SELECT p.category AS 品类,SUM(i.quantity) AS 销量 FROM order_items i "
        "JOIN products p ON i.product_id=p.product_id JOIN orders o ON i.order_id=o.order_id "
        "WHERE o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01' "
        "AND o.channel='线上' GROUP BY p.category ORDER BY 销量 DESC LIMIT 3",
    )
    assert "渠道=线上" in result.plan.filters
    assert "channel" not in result.plan.dimensions


def test_multiple_metrics_are_not_silently_reduced_to_one():
    equivalent(
        "各会员等级的销售额和订单数？",
        "SELECT c.segment AS 会员等级,ROUND(SUM(i.quantity*i.unit_price),0) AS 销售额,"
        "COUNT(DISTINCT o.order_id) AS 订单数 FROM order_items i "
        "JOIN orders o ON i.order_id=o.order_id "
        "JOIN customers c ON o.customer_id=c.customer_id GROUP BY c.segment",
    )


def test_two_months_mean_union_not_impossible_intersection():
    equivalent(
        "2024年11月和12月的订单数",
        "SELECT COUNT(*) AS 订单数 FROM orders WHERE order_date >= '2024-11-01' "
        "AND order_date < '2025-01-01'",
    )


def test_city_segment_and_year_are_all_applied():
    equivalent(
        "2024年上海VIP会员的订单数",
        "SELECT COUNT(*) AS 订单数 FROM orders o JOIN customers c ON o.customer_id=c.customer_id "
        "WHERE c.city='上海' AND c.segment='VIP' AND o.order_date>='2024-01-01' "
        "AND o.order_date<'2025-01-01'",
    )


def test_filtered_repurchase_does_not_use_all_history():
    equivalent(
        "2024年线上复购顾客数",
        "SELECT COUNT(*) AS 复购顾客数 FROM (SELECT customer_id FROM orders "
        "WHERE channel='线上' AND order_date>='2024-01-01' AND order_date<'2025-01-01' "
        "GROUP BY customer_id HAVING COUNT(*)>1)",
    )


@pytest.mark.parametrize(
    "question",
    [
        "预测明年的销售额",
        "利润是多少？",
        "每月趋势",
        "11月订单数",
        "第四季度销售额",
        "销售额环比增长率",
        "最近30天销售额",
        "2024年11月到12月销售额",
        "2024年线上顾客数",
        "排除线下的销售额",
        "2024年13月订单数",
        "2024-11-01至2024-11-30销售额",
    ],
)
def test_ambiguous_or_unsupported_requests_ask_instead_of_returning_total(question):
    result = agent.analyze(question)
    assert result.plan.status == "clarify"
    assert result.df is None and result.sql == ""
    assert result.quality.status == "needs_input"


def test_missing_business_data_cannot_be_invented():
    for question in ("库存还有多少？", "销售转化率是多少？"):
        result = agent.analyze(question)
        assert result.plan.status == "unanswerable"


def test_current_year_uses_calendar_not_sample_year():
    result = agent.analyze("今年线上销量最高的三个品类", reference_date=date(2026, 11, 5))
    assert "年份=2026" in result.plan.filters
    assert result.plan.status == "ready"
    missing = agent.analyze("2028年线上销量最高的三个品类", reference_date=date(2026, 11, 5))
    assert missing.plan.status == "unanswerable"
    assert "记录范围" in missing.insight


def test_channel_is_preserved_for_both_months_in_growth():
    equivalent(
        "2024年12月相比11月线上销售额环比",
        "WITH m AS (SELECT strftime('%Y-%m',o.order_date) AS m,SUM(i.quantity*i.unit_price) AS s "
        "FROM order_items i JOIN orders o ON i.order_id=o.order_id "
        "WHERE o.channel='线上' GROUP BY m) "
        "SELECT ROUND(100.0*((SELECT s FROM m WHERE m='2024-12')-"
        "(SELECT s FROM m WHERE m='2024-11'))/(SELECT s FROM m WHERE m='2024-11'),1) AS 环比增长率",
    )


def test_previous_month_crosses_year_boundary():
    equivalent(
        "2024年1月销售额环比",
        "WITH m AS (SELECT strftime('%Y-%m',o.order_date) AS m,SUM(i.quantity*i.unit_price) AS s "
        "FROM order_items i JOIN orders o ON i.order_id=o.order_id GROUP BY m) "
        "SELECT ROUND(100.0*((SELECT s FROM m WHERE m='2024-01')-"
        "(SELECT s FROM m WHERE m='2023-12'))/(SELECT s FROM m WHERE m='2023-12'),1) AS 环比增长率",
    )


def test_lowest_ranking_is_ascending():
    equivalent(
        "销量最低的三个品类",
        "SELECT p.category AS 品类,SUM(i.quantity) AS 销量 FROM order_items i "
        "JOIN products p ON i.product_id=p.product_id "
        "GROUP BY p.category ORDER BY 销量 ASC LIMIT 3",
    )


def test_all_planned_columns_are_required():
    plan = semantic_layer.analyze_question("各会员等级销售额和订单数")
    report = validate_result(plan, pd.DataFrame({"会员等级": ["VIP"], "销售额": [10]}))
    assert report.status == "review"
    assert any("订单数" in s for s in report.warnings)


def test_growth_above_100_percent_is_not_invalid():
    plan = semantic_layer.analyze_question("2024年12月销售额环比")
    assert validate_result(plan, pd.DataFrame({"环比增长率": [250.0]})).status == "passed"


def test_loss_margin_below_minus_100_percent_is_valid():
    plan = semantic_layer.analyze_question("整体毛利率")
    assert validate_result(plan, pd.DataFrame({"毛利率": [-150.0]})).status == "passed"


def test_null_aggregate_is_not_displayed_as_zero_or_a_good_answer():
    plan = semantic_layer.analyze_question("总销售额")
    assert validate_result(plan, pd.DataFrame({"销售额": [None]})).status == "no_data"


def test_default_agent_never_calls_model_even_with_a_configured_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")
    monkeypatch.setattr(config, "MODEL", "fake-test-model")

    def forbidden(*args, **kwargs):
        raise AssertionError("默认离线模式不能调用模型")

    monkeypatch.setattr(text_to_sql, "_call_claude", forbidden)
    from data_copilot import insights

    monkeypatch.setattr(insights, "_call_claude", forbidden)
    assert agent.analyze("总销售额").mode == "offline"


def test_mock_online_sql_missing_conditions_is_not_treated_as_answer(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-test-key")
    monkeypatch.setattr(config, "MODEL", "fake-test-model")
    calls = []

    def fake(*args):
        calls.append(1)
        return {
            "text": "SELECT p.category AS 品类,SUM(i.quantity) AS 销量 FROM order_items i "
            "JOIN products p ON i.product_id=p.product_id "
            "GROUP BY p.category ORDER BY 销量 DESC LIMIT 3",
            "error": None,
            "input_tokens": 1,
            "output_tokens": 1,
            "latency_ms": 0,
        }

    monkeypatch.setattr(text_to_sql, "_call_claude", fake)
    result = agent.analyze("2024年线上销量最高的三个品类", use_llm=True)
    assert result.quality.status == "review"
    assert result.chart_spec["kind"] == "none"
    assert len(calls) == 1
