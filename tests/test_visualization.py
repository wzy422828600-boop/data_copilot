"""可视化选择与格式化测试（对应需求五：按结果形状选图、中文格式化）。"""
from __future__ import annotations

import pandas as pd

from data_copilot import visualization as viz


def test_choose_kpi_for_single_row():
    df = pd.DataFrame({"总销售额": [1234567.0]})
    spec = viz.choose_chart(df)
    assert spec["kind"] == "kpi" and spec["metrics"] == ["总销售额"]


def test_choose_line_for_time_dimension():
    df = pd.DataFrame({"月份": ["2024-01", "2024-02"], "销售额": [10, 20]})
    assert viz.choose_chart(df)["kind"] == "line"


def test_choose_bar_for_category_single_metric():
    df = pd.DataFrame({"品类": ["女装", "男装"], "销售额": [30, 20]})
    assert viz.choose_chart(df)["kind"] == "bar"


def test_choose_grouped_bar_for_category_multi_metric():
    df = pd.DataFrame({"会员等级": ["VIP", "普通"], "销售额": [30, 20], "毛利": [3, 5]})
    assert viz.choose_chart(df)["kind"] == "grouped_bar"


def test_mixed_units_require_metric_selection_not_shared_axis():
    df = pd.DataFrame({"月份": ["2024-01", "2024-02"],
                       "销售额": [30, 20], "毛利率": [3, 5]})
    spec = viz.choose_chart(df)
    assert spec["kind"] == "none"
    assert spec["metrics"] == ["销售额", "毛利率"]


def test_nulls_and_financial_display_units():
    assert viz.choose_chart(pd.DataFrame({"毛利": [None]}))["kind"] == "none"
    assert viz.format_metric(pd.NA, "毛利") == "—"
    assert viz.format_metric(float("inf"), "毛利") == "—"
    assert viz.format_metric(-152350, "毛利") == "-152,350.00 元"
    assert viz.format_metric(0, "毛利率") == "0.00%"
    assert viz.format_metric(12, "订单数") == "12 笔"
    assert viz.metric_unit("净利润（万元）") == "万元"
    assert viz.table_height(pd.DataFrame({"毛利": [1]})) < 100
    assert viz.table_height(pd.DataFrame({"毛利": range(100)})) <= 420


def test_choose_none_for_empty_or_nonnumeric():
    assert viz.choose_chart(pd.DataFrame())["kind"] == "none"
    assert viz.choose_chart(pd.DataFrame({"a": ["x", "y"]}))["kind"] == "none"


def test_format_number_thousands_and_pct():
    assert viz.format_number(1234567) == "1,234,567"
    assert viz.format_number(12.5, pct=True) == "12.5%"
    assert viz.format_number(None) == "—"
    assert viz.format_number(float("nan")) == "—"


def test_make_chart_line_writes_file(tmp_path):
    df = pd.DataFrame({"月份": ["2024-01", "2024-02"], "销售额": [10.0, 20.0]})
    out = tmp_path / "sub" / "chart.png"           # 目录不存在，应自建
    p = viz.make_chart(df, str(out), title="每月销售额趋势")
    assert p and out.exists()


def test_make_chart_none_for_kpi(tmp_path):
    df = pd.DataFrame({"客单价": [321.5]})
    assert viz.make_chart(df, str(tmp_path / "x.png")) is None


def test_make_chart_top_n_truncation(tmp_path):
    df = pd.DataFrame({"产品": [f"p{i}" for i in range(40)],
                       "销售额": list(range(40, 0, -1))})
    # 不应报错，且能出图（内部截断到 top_n）
    assert viz.make_chart(df, str(tmp_path / "top.png"), top_n=15)
