"""Streamlit 核心状态逻辑测试（对应需求一.1 与七）。

用 Streamlit 官方 headless 测试框架 AppTest 真正跑一遍 app.py：
  - 首屏不执行分析（不调用模型）；
  - 只有点击「分析」提交表单后才执行；
  - 示例按钮能更新输入框。
强制离线（不设 ANTHROPIC_API_KEY），因此绝不会调用真实 API。
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app.py")


def _fresh(monkeypatch) -> AppTest:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")  # 禁止 dotenv 重新加载真实 key
    return AppTest.from_file(APP, default_timeout=30).run()


def _subheaders(at) -> list[str]:
    return [s.value for s in at.subheader]


def test_first_load_does_not_analyze(monkeypatch):
    at = _fresh(monkeypatch)
    assert not at.exception
    # 首屏应提示用户操作，且没有任何分析结果小标题
    assert any("点击" in i.value for i in at.info)
    assert "查询结果" not in _subheaders(at)
    assert "analysis_result" not in at.session_state


def test_submit_triggers_analysis(monkeypatch):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value("总销售额是多少？")
    submit = next(b for b in at.button if b.label == "分析")
    submit.click().run()
    assert not at.exception
    subs = _subheaders(at)
    assert "查询结果" in subs
    assert any(m.label == "销售额" and "元" in m.value for m in at.metric)
    assert any(e.label == "取数 SQL 与计算口径" for e in at.expander)


def test_example_button_updates_question(monkeypatch):
    at = _fresh(monkeypatch)
    # 点第二个示例按钮（第一个示例就是默认值，改动不可见）
    ex = at.button(key="ex_1")
    target = ex.label
    ex.click().run()
    assert at.session_state["question"] == target
    # 仅点示例、未点「分析」，仍不应出现分析结果
    assert "查询结果" not in _subheaders(at)
    assert "analysis_result" not in at.session_state


def test_screenshot_query_shows_currency_card_and_compact_optional_details(monkeypatch):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value("2024年10月的毛利润是多少")
    next(b for b in at.button if b.label == "分析").click().run()
    assert not at.exception
    assert [(m.label, m.value) for m in at.metric] == [("毛利", "152,350.00 元")]
    details = next(e for e in at.expander if e.label == "查看结果明细")
    assert not details.proto.expanded
    assert details.dataframe[0].value.shape == (1, 1)
    assert any("未扣模拟退款" in c.value for c in at.caption)
    assert "③ 可视化" not in _subheaders(at)


@pytest.mark.parametrize("question,label,target", [
    ("2024年各品类销售额和毛利率", "图表指标（不同单位分别展示）", "毛利率"),
    ("最近三个月营业收入和净利率趋势", "趋势指标（不同单位分别展示）", "净利率（%）"),
])
def test_mixed_units_can_switch_chart_without_rerunning_query(monkeypatch, question, label, target):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value(question)
    next(b for b in at.button if b.label == "分析").click().run()
    assert not at.exception
    result = at.session_state["analysis_result"]
    selector = next(s for s in at.selectbox if s.label == label)
    selector.set_value(target).run()
    assert not at.exception
    assert next(s for s in at.selectbox if s.label == label).value == target
    assert at.session_state["analysis_result"] is result


@pytest.mark.parametrize(
    "question,reason",
    [
        ("11月订单数", "缺少年份"),
        ("2028年线上销售额", "记录范围"),
    ],
)
def test_boundary_request_has_no_kpi_or_chart(monkeypatch, question, reason):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value(question)
    next(b for b in at.button if b.label == "分析").click().run()
    assert not at.exception
    assert any(reason in i.value for i in at.info)
    assert "查询结果" not in _subheaders(at)
    assert all("可信度" not in metric.label for metric in at.metric)


@pytest.mark.parametrize(
    "question, intent",
    [("净利润为多少", "financial_analysis"), ("销售额为什么下降了", "sales_diagnosis")],
)
def test_financial_and_business_analysis_render_and_persist(monkeypatch, question, intent):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value(question)
    next(b for b in at.button if b.label == "分析").click().run()
    assert not at.exception
    assert at.session_state["analysis_result"].plan.intent == intent
    assert "分析结论" in _subheaders(at)
    at.selectbox[0].set_value("2026-09").run()
    assert not at.exception
    assert at.session_state["analysis_result"].plan.intent == intent
