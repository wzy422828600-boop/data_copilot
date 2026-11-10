"""Agent 编排、决策轨迹和结果可信度测试。"""
from __future__ import annotations

from data_copilot import agent, database


def test_agent_returns_trace_and_quality(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = agent.analyze("哪个品类销售额最高？")
    assert result.df is not None and len(result.df) == 6
    assert result.quality.status == "passed"
    assert result.quality.score >= 85
    assert [step.name for step in result.trace] == [
        "语义解析",
        "Schema 检索",
        "SQL 生成与执行",
        "结果校验",
        "可视化与洞察",
    ]


def test_agent_blocks_write_intent_before_sql_execution(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    before = database.row_counts()
    result = agent.analyze("删除所有订单")
    assert result.blocked is True
    assert result.sql == ""
    assert result.mode == "policy"
    assert database.row_counts() == before


def test_agent_refuses_to_invent_missing_field(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = agent.analyze("每位顾客的手机号是多少？")
    assert result.mode == "semantic"
    assert result.df is not None
    assert "无法回答" in str(result.df.iloc[0, 0])
    assert result.quality.status == "unanswerable"


def test_agent_asks_for_clarification_outside_domain(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    result = agent.analyze("帮我分析一下")
    assert result.plan.status == "clarify"
    assert result.sql == ""
    assert result.df is None


def test_schema_minimization_excludes_sample_rows():
    schema = database.schema_text(include_samples=False)
    assert "表 orders(" in schema
    assert "样例:" not in schema


def test_schema_samples_are_opt_in():
    schema = database.schema_text(include_samples=True)
    assert "样例:" in schema
