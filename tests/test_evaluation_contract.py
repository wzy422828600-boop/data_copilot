"""Prevent evaluation mistakes from inflating a product's reported results."""

from types import SimpleNamespace

import pandas as pd

from data_copilot import agent
from scripts import ask, evaluate


def test_comparison_keeps_column_roles():
    left = pd.DataFrame([[1, 9]], columns=["维度", "指标"])
    swapped = pd.DataFrame([[9, 1]], columns=["维度", "指标"])
    assert evaluate.signature(left) != evaluate.signature(swapped)


def test_small_numeric_errors_are_not_rounded_to_a_match():
    assert evaluate.signature(pd.DataFrame({"指标": [1.01]})) != evaluate.signature(
        pd.DataFrame({"指标": [1.04]})
    )


def test_error_or_ambiguous_question_is_not_a_correct_missing_field_refusal():
    for status in ("blocked", "clarify", "ready"):
        fake = SimpleNamespace(
            plan=SimpleNamespace(status=status), quality=SimpleNamespace(status="failed")
        )
        assert not evaluate.looks_unanswerable(fake)


def test_cli_handles_a_clarification_without_dataframe(monkeypatch, capsys):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert ask.main(["ask.py", "11月订单数"]) == 0
    assert "缺少年份" in capsys.readouterr().out


def test_quarter_outside_observed_data_is_not_a_zero_sales_answer(tmp_path):
    import sqlite3

    path = tmp_path / "partial.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE orders(order_date TEXT)")
        conn.execute("INSERT INTO orders VALUES ('2024-01-15')")
    result = agent.analyze("2024年第四季度销售额", db_path=path, schema="orders(order_date)")
    assert result.plan.status == "unanswerable"
    assert "记录范围" in result.insight
