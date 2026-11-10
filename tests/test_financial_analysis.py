"""Independent accounting/query contracts, not a broad language-model accuracy test."""

import hashlib
import shutil
import sqlite3
from datetime import date

import pytest

from data_copilot import agent, business_analysis, config, database
from data_copilot.finance_catalog import ACCOUNTS, BY_CODE
from scripts.upgrade_demo_data import upgrade, validate

REF = date(2026, 11, 5)


def scalar(sql, params=()):
    with database.readonly_connection() as conn:
        return conn.execute(sql, params).fetchone()[0]


def ask(q, **kwargs):
    result = agent.analyze(q, reference_date=REF, **kwargs)
    assert result.plan.status == "ready", result.insight
    assert result.analysis is not None and result.sql_result.api_calls == 0
    return result


@pytest.mark.parametrize("account", ACCOUNTS, ids=lambda a: a.code)
def test_dictionary_items_work_without_per_question_hardcoded_answers(account):
    result = ask(f"财务项目：2026年10月{account.label}是多少？")
    expected = scalar(
        "SELECT amount_cents/100.0 FROM financial_values WHERE month=? AND code=?",
        ("2026-10", account.code),
    )
    assert result.df.iloc[0]["本期值"] == pytest.approx(expected, abs=0.005)


def test_all_months_reconcile_and_order_settlements_cover_every_order():
    with database.readonly_connection(guarded=False) as conn:
        assert all(v == 0 for v in validate(conn).values())
        assert conn.execute("SELECT COUNT(*) FROM order_finance").fetchone()[0] == 11557
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()


def test_default_period_is_visible_and_not_all_history():
    result = ask("净利润为多少")
    assert any("最新完整模拟月2026-10" in n for n in result.analysis["notes"])
    assert result.df.iloc[0]["本期值"] == 42356.88


def test_current_year_is_ytd_and_full_year_cannot_be_claimed():
    result = ask("今年净利润")
    expected = scalar(
        "SELECT SUM(amount_cents)/100.0 FROM financial_values "
        "WHERE month LIKE '2026-%' AND code='net_profit'"
    )
    assert result.df.iloc[0]["本期值"] == pytest.approx(expected)
    assert any("并非完整全年" in n for n in result.analysis["notes"])
    assert agent.analyze("2026年全年净利润").plan.status == "clarify"


def test_balance_is_last_month_not_a_sum():
    result = ask("2026年总资产")
    expected = scalar(
        "SELECT amount_cents/100.0 FROM financial_values "
        "WHERE month='2026-10' AND code='total_assets'"
    )
    summed = scalar(
        "SELECT SUM(amount_cents)/100.0 FROM financial_values "
        "WHERE month LIKE '2026-%' AND code='total_assets'"
    )
    assert expected != summed
    assert result.df.iloc[0]["本期值"] == pytest.approx(expected)


def test_period_ratio_recomputes_components_instead_of_averaging_monthly_ratios():
    result = ask("2026年第三季度净利率")
    numerator = scalar(
        "SELECT SUM(amount_cents) FROM financial_values "
        "WHERE month BETWEEN '2026-07' AND '2026-09' AND code='net_profit'"
    )
    denominator = scalar(
        "SELECT SUM(amount_cents) FROM financial_values "
        "WHERE month BETWEEN '2026-07' AND '2026-09' AND code='revenue'"
    )
    assert result.df.iloc[0]["本期值"] == pytest.approx(numerator / denominator * 100, abs=0.005)


def test_flow_report_opening_and_closing_balances_are_not_summed():
    result = ask("2026年第三季度现金流量表")
    rows = result.df.set_index("项目")["本期金额"]
    first = scalar(
        "SELECT amount_cents/100.0 FROM financial_values "
        "WHERE month='2026-07' AND code='opening_cash'"
    )
    last = scalar(
        "SELECT amount_cents/100.0 FROM financial_values "
        "WHERE month='2026-09' AND code='closing_cash'"
    )
    assert rows[BY_CODE["opening_cash"].label] == pytest.approx(first)
    assert rows[BY_CODE["closing_cash"].label] == pytest.approx(last)
    assert rows[BY_CODE["cash_change"].label] == pytest.approx(last - first)


def test_report_has_year_opening_and_explicit_comparison_period():
    result = ask("2026年10月比9月资产负债表")
    rows = result.df.set_index("项目")
    assert rows.loc["货币资金", "年初余额"] == pytest.approx(
        scalar(
            "SELECT amount_cents/100.0 FROM financial_values WHERE month='2025-12' AND code='cash'"
        )
    )
    assert rows.loc["货币资金", "比较期末余额"] == pytest.approx(
        scalar(
            "SELECT amount_cents/100.0 FROM financial_values WHERE month='2026-09' AND code='cash'"
        )
    )


def test_net_profit_bridge_adds_up_and_does_not_adopt_false_premise():
    result = ask("2026年10月净利润为什么下降了")
    bridge = result.analysis["tables"][1]["df"]
    assert bridge["对净利润变化的贡献"].sum() == pytest.approx(
        result.df.iloc[0]["变化值"], abs=0.01
    )
    assert "增加" in result.insight  # This dataset's profit actually increased.


def test_cash_profit_bridge_adds_up_to_operating_cash():
    result = ask("为什么2026年第三季度净利润与经营现金流不一致")
    rows = result.df["金额（元）"]
    assert rows.iloc[:-1].sum() == pytest.approx(rows.iloc[-1], abs=0.01)


@pytest.mark.parametrize(
    "q",
    [
        "销售额为什么下降了",
        "2026年10月线上销售额为什么下降",
        "2026年9月上海销售额分析",
        "2026年10月美妆销售额变化分析",
        "2026年第三季度VIP销售额分析",
    ],
)
def test_sales_contributions_reconcile_in_filtered_and_unfiltered_queries(q):
    result = ask(q)
    summary = result.df
    change = summary.iloc[1]["销售额（元）"] - summary.iloc[0]["销售额（元）"]
    assert result.analysis["tables"][1]["df"]["对销售额变化的贡献（元）"].sum() == pytest.approx(
        change, abs=0.01
    )
    for table in result.analysis["tables"][2:]:
        assert table["df"]["销售额变化（元）"].sum() == pytest.approx(change, abs=0.01)


def test_statement_revenue_is_after_refunds_and_is_not_order_gmv():
    result = ask("2026年10月营业收入")
    expected = scalar(
        "SELECT SUM(f.sales_cents-f.refund_cents)/100.0 FROM order_finance f "
        "JOIN orders o ON f.order_id=o.order_id WHERE o.order_date LIKE '2026-10%'"
    )
    gmv = scalar(
        "SELECT SUM(f.sales_cents)/100.0 FROM order_finance f JOIN orders o "
        "ON f.order_id=o.order_id WHERE o.order_date LIKE '2026-10%'"
    )
    assert result.df.iloc[0]["本期值"] == pytest.approx(expected)
    assert expected < gmv


def test_units_and_percentage_points_are_distinguished():
    yuan = ask("2026年10月净利润").df.iloc[0]["本期值"]
    ten_thousands = ask("2026年10月净利润是多少万元").df.iloc[0]
    assert ten_thousands["单位"] == "万元"
    assert ten_thousands["本期值"] == pytest.approx(yuan / 10000, abs=0.005)
    assert ask("2026年10月净利率同比").df.iloc[0]["变化单位"] == "百分点"


def test_alias_and_period_expenses_do_not_sum_subtotal_and_leaves():
    result = ask("2026年10月期间费用与营业总收入分别是多少")
    assert len(result.df) == 2
    expected = scalar(
        "SELECT SUM(amount_cents)/100.0 FROM financial_values WHERE month='2026-10' "
        "AND code IN ('selling_expense','admin_expense','rd_expense','finance_expense')"
    )
    assert result.df.set_index("指标").loc["期间费用", "本期值"] == pytest.approx(expected)


def test_refund_query_and_revenue_bridge_match_order_settlements():
    result = ask("2026年10月退款金额是多少")
    expected = scalar(
        "SELECT SUM(f.refund_cents)/100.0 FROM order_finance f "
        "JOIN orders o ON f.order_id=o.order_id WHERE o.order_date LIKE '2026-10%'"
    )
    assert result.df.iloc[0]["本期值"] == pytest.approx(expected)
    bridge = ask("2026年10月营业收入为什么不等于销售额").df["金额（元）"]
    assert bridge.iloc[0] - bridge.iloc[1] == pytest.approx(bridge.iloc[2], abs=0.01)


@pytest.mark.parametrize(
    "q",
    [
        "2026净利润",
        "2026年上半年营业收入",
        "去年同期净利润",
        "今年和去年营业收入对比",
        "2026年第一季度和第二季度净利润对比",
    ],
)
def test_period_paraphrases_are_handled(q):
    ask(q)


def test_plain_two_month_sales_comparison_runs_decomposition():
    assert ask("2026年10月比9月销售额").plan.intent == "sales_diagnosis"


def test_two_months_separately_are_not_collapsed_to_a_two_month_sum():
    result = ask("2026年9月和10月净利润分别多少")
    assert "比较期值" in result.df.columns
    assert result.df.iloc[0]["本期值"] == 42356.88
    assert result.df.iloc[0]["比较期值"] == 40534.67


@pytest.mark.parametrize(
    "q",
    [
        "11月净利润",
        "2026年13月净利润",
        "2028年净利润",
        "本月净利润",
        "2026年全年营业收入",
        "2026-10-20净利润",
        "预测明年净利润",
        "上海净利润",
        "线上净利润",
        "利润是多少",
        "排除所得税后的净利润",
        "最近25个月营业收入",
        "2019年净利润",
        "1999年净利润",
        "2026年10月存货多少件",
        "应收账款周转率是多少",
    ],
)
def test_unsupported_periods_and_unallocated_scopes_are_not_silently_ignored(q):
    result = agent.analyze(q, reference_date=REF)
    assert result.plan.status == "clarify"
    assert result.df is None


def test_zero_ratio_denominator_returns_no_value():
    assert (
        business_analysis.amount(
            {"2026-10": {"current_assets": 1, "current_liabilities": 0}},
            "current_ratio",
            ("2026-10",),
        )
        is None
    )


def test_missing_account_is_not_zero_filled(tmp_path):
    target = tmp_path / "missing.db"
    shutil.copyfile(config.DB_PATH, target)
    with sqlite3.connect(target) as conn:
        conn.execute("DELETE FROM financial_values WHERE month='2026-10' AND code='net_profit'")
    result = agent.analyze("2026年10月净利润", db_path=target)
    assert result.plan.status == "clarify" and "缺少" in result.insight


def test_financial_write_intent_still_blocked_before_execution():
    result = agent.analyze("把净利润更新为100000")
    assert result.blocked and result.sql == ""


def test_upgrade_is_idempotent_and_analyses_do_not_write():
    before = hashlib.sha256(config.DB_PATH.read_bytes()).hexdigest()
    assert upgrade()["already_current"]
    ask("净利润为多少")
    ask("销售额为什么下降")
    after = hashlib.sha256(config.DB_PATH.read_bytes()).hexdigest()
    assert before == after
