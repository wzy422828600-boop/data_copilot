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
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # 强制离线
    return AppTest.from_file(APP, default_timeout=30).run()


def _subheaders(at) -> list[str]:
    return [s.value for s in at.subheader]


def test_first_load_does_not_analyze(monkeypatch):
    at = _fresh(monkeypatch)
    assert not at.exception
    # 首屏应提示用户操作，且没有任何分析结果小标题
    assert any("点击" in i.value for i in at.info)
    assert "① 生成的 SQL" not in _subheaders(at)


def test_submit_triggers_analysis(monkeypatch):
    at = _fresh(monkeypatch)
    at.text_input(key="question").set_value("总销售额是多少？")
    submit = next(b for b in at.button if b.label == "分析")
    submit.click().run()
    assert not at.exception
    subs = _subheaders(at)
    assert "① 生成的 SQL" in subs and "② 查询结果" in subs


def test_example_button_updates_question(monkeypatch):
    at = _fresh(monkeypatch)
    # 点第二个示例按钮（第一个示例就是默认值，改动不可见）
    ex = at.button(key="ex_1")
    target = ex.label
    ex.click().run()
    assert at.session_state["question"] == target
    # 仅点示例、未点「分析」，仍不应出现分析结果
    assert "① 生成的 SQL" not in _subheaders(at)
