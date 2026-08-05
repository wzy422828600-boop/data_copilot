"""端到端离线链路（对应需求七的"新鲜克隆可跑通"）。

conftest 会在数据库缺失时自动生成（等价于新鲜克隆后的首次运行），
本测试把「schema → NL→SQL → 只读执行 → 出图 → 洞察」整条链路跑一遍，
全程强制离线，绝不调用真实 API。
"""
from __future__ import annotations

from data_copilot import database, insights, text_to_sql, visualization


def test_full_offline_pipeline(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)   # 强制离线

    def runner(sql: str):
        df, _ = database.run_query(sql)
        return df

    schema = database.schema_text()
    assert all(t in schema for t in ["customers", "products", "orders", "order_items"])

    res = text_to_sql.generate("哪个品类销售额最高？", schema, runner=runner)
    assert res.mode == "offline"
    assert res.df is not None and not res.blocked and res.error is None
    assert "品类" in res.df.columns and len(res.df) == 6

    chart = visualization.make_chart(res.df, str(tmp_path / "e2e.png"),
                                     title="哪个品类销售额最高？")
    assert chart and (tmp_path / "e2e.png").exists()

    text = insights.summarize("哪个品类销售额最高？", res.df)
    assert text and "最高" in text
