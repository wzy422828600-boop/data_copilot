"""Text-to-SQL 层测试：代码块清理、单条 SQL 校验、离线基线，
以及用 mock 的 Anthropic 客户端覆盖在线路径（CI 绝不调用真实 API）。

覆盖点对应需求三：
  - Anthropic API 的 mock 测试；
  - LLM 返回代码块 / 解释文字 / 空响应 / 非法 SQL；
  - 自动纠错重试；
  - 在线失败回退离线并如实告知。
"""
from __future__ import annotations

import sys
import types

import pytest

from data_copilot import config, security, text_to_sql


# ============================ 清理 / 校验 ============================
@pytest.mark.parametrize("raw, expected_start", [
    ("```sql\nSELECT 1 AS a\n```", "SELECT 1 AS a"),
    ("```\nSELECT 2\n```", "SELECT 2"),
    ("SQL: SELECT 3", "SELECT 3"),
    ("SQL：SELECT 4", "SELECT 4"),          # 中文冒号
    ("SELECT 5;", "SELECT 5"),              # 去掉结尾分号
    ("  SELECT 6  ", "SELECT 6"),
])
def test_clean_sql_strips_wrappers(raw, expected_start):
    assert text_to_sql.clean_sql(raw) == expected_start


def test_clean_sql_takes_first_code_block_with_explanation():
    raw = "当然，这是查询：\n```sql\nSELECT COUNT(*) FROM orders\n```\n希望有帮助。"
    assert text_to_sql.clean_sql(raw) == "SELECT COUNT(*) FROM orders"


def test_clean_sql_empty():
    assert text_to_sql.clean_sql("") == ""
    assert text_to_sql.clean_sql("   ") == ""


def test_validate_single_sql_accepts_plain_select():
    sql, why = text_to_sql.validate_single_sql("```sql\nSELECT 1\n```")
    assert sql == "SELECT 1" and why == "ok"


@pytest.mark.parametrize("raw", [
    "",                                     # 空
    "这是一段解释，没有 SQL。",              # 无 SQL（不以 select/with 开头）
    "DELETE FROM orders",                   # 非法写操作
    "SELECT 1; DROP TABLE orders",          # 多语句
])
def test_validate_single_sql_rejects(raw):
    sql, why = text_to_sql.validate_single_sql(raw)
    assert sql is None and why


# ============================ 离线基线 ============================
def test_offline_sql_always_safe():
    for q in ["总销售额", "每月趋势", "各品类毛利", "线上线下", "客单价",
              "会员等级", "畅销产品", "随便问点啥"]:
        assert security.is_safe_sql(text_to_sql.offline_sql(q))[0] is True


def test_generate_offline_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    res = text_to_sql.generate("总销售额是多少", schema="", runner=None)
    assert res.mode == "offline" and res.api_calls == 0 and res.sql


# ============================ mock 的 Anthropic 客户端 ============================
class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    input_tokens, output_tokens = 42, 7


class _Msg:
    def __init__(self, text):
        self.content, self.usage = [_Block(text)], _Usage()


def _install_fake_anthropic(monkeypatch, *, replies=None, exc=None):
    """把 sys.modules['anthropic'] 换成一个可控的假模块。

    replies: 依次返回的文本；exc: 若给出，则 messages.create 抛该异常实例。
    返回一个记录调用次数的列表。
    """
    calls: list[str] = []
    replies = list(replies or [])

    class _Messages:
        def create(self, **kw):
            calls.append(kw.get("messages", [{}])[-1].get("content", ""))
            if exc is not None:
                raise exc
            return _Msg(replies.pop(0) if replies else "SELECT 1")

    class _Client:
        def __init__(self, *a, **k):
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.Anthropic = _Client
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-fake")  # 触发在线分支
    monkeypatch.setattr(config, "MODEL", "mock-model-not-a-real-provider-id")
    return calls


def test_llm_path_parses_code_block(monkeypatch):
    _install_fake_anthropic(monkeypatch,
                            replies=["```sql\nSELECT COUNT(*) AS c FROM orders\n```"])
    res = text_to_sql.generate("多少订单", schema="表 orders(...)", runner=None)
    assert res.mode == "llm"
    assert res.sql == "SELECT COUNT(*) AS c FROM orders"
    assert res.api_calls == 1
    assert res.input_tokens == 42 and res.output_tokens == 7


def test_llm_blocks_when_model_returns_write(monkeypatch):
    _install_fake_anthropic(monkeypatch, replies=["DELETE FROM orders"])
    res = text_to_sql.generate("删库", schema="", runner=None)
    assert res.blocked is True and res.df is None


def test_llm_empty_response_falls_back_offline(monkeypatch):
    _install_fake_anthropic(monkeypatch, replies=[""])
    res = text_to_sql.generate("总销售额", schema="", runner=None)
    # 空响应 -> _call_claude 判为错误 -> 回退离线
    assert res.mode == "llm→offline" and res.notice and res.sql


def test_llm_api_error_falls_back_offline(monkeypatch):
    class RateLimitError(Exception):
        pass
    _install_fake_anthropic(monkeypatch, exc=RateLimitError("boom"))
    res = text_to_sql.generate("总销售额", schema="", runner=None)
    assert res.mode == "llm→offline"
    assert "限流" in res.notice          # 错误被分类为友好文案
    assert res.error and "boom" not in res.notice  # 不泄漏原始堆栈


def test_llm_auto_correction_retry_then_succeeds(monkeypatch):
    # 第一版 SQL 执行失败，第二版成功；应恰好重试一次
    _install_fake_anthropic(
        monkeypatch,
        replies=["SELECT bad_col FROM orders", "SELECT COUNT(*) AS c FROM orders"])

    class _Runner:
        def __init__(self):
            self.n = 0

        def __call__(self, sql):
            self.n += 1
            if "bad_col" in sql:
                raise RuntimeError("no such column: bad_col")
            import pandas as pd
            return pd.DataFrame({"c": [4000]})

    runner = _Runner()
    res = text_to_sql.generate("多少订单", schema="表 orders(...)", runner=runner)
    assert res.retries == 1
    assert res.api_calls == 2
    assert res.df is not None and int(res.df.iloc[0]["c"]) == 4000


def test_llm_retry_capped_then_offline(monkeypatch):
    # 每一版都执行失败；重试次数不得超过配置上限，最终回退离线
    _install_fake_anthropic(
        monkeypatch, replies=["SELECT bad1 FROM orders"] * 5)

    def runner(sql):
        raise RuntimeError("no such column")

    res = text_to_sql.generate("坏问题", schema="表 orders(...)", runner=runner)
    assert res.retries <= config.MAX_CORRECTION_RETRIES
    assert res.mode == "llm→offline" and res.notice
