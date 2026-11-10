"""自然语言 -> 只读 SQL。

有 ANTHROPIC_API_KEY 时调用 Claude；否则走离线关键词规则基线。
在线路径包含：稳健的代码块清理、单条 SQL 校验、执行失败自动纠错一次、
分类的错误处理，以及失败时回退到离线规则（并如实告知）。
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from . import config, security, semantic_layer

log = logging.getLogger("data_copilot.text_to_sql")

# 执行器：给一条 SQL，返回 DataFrame；失败抛异常（用于校验 + 自动纠错）
Runner = Callable[[str], pd.DataFrame]


@dataclass
class SqlResult:
    question: str
    sql: str
    mode: str  # offline | llm | llm→offline
    df: pd.DataFrame | None = None
    retries: int = 0
    latency_ms: float = 0.0
    api_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    blocked: bool = False
    block_reason: str = ""
    error: str | None = None
    notice: str = ""  # 面向用户的提示（如"已回退到离线模式"）


SYSTEM_PROMPT = """你是把中文问题翻译成 SQLite 查询的助手。严格遵守：
- 只输出一条 SQL，必须是只读的 SELECT 或只读 WITH，不要解释、不要 markdown 代码块。
- 只能用给定的表和字段；方言是 SQLite（日期用 strftime）。
- 销售额/GMV = SUM(oi.quantity*oi.unit_price)，需 join order_items oi。
  毛利 = SUM(oi.quantity*(oi.unit_price - p.cost))，需 join products p。
  按时间分析 join orders o 用 o.order_date；按顾客属性 join customers c。
- 聚合排行请排序并加合理 LIMIT。
- 若问题无法用现有表回答，只返回一行：SELECT '无法回答' AS note。"""


# ============================ 通用清理 / 校验 ============================
def clean_sql(text: str) -> str:
    """从模型回复里稳健地抽出 SQL：去掉 ```代码块```、前缀标签和多余空白。"""
    if not text:
        return ""
    t = text.strip()
    # 取第一个 ``` 代码块的内容（如果有）
    m = re.search(r"```(?:sql)?\s*(.+?)```", t, re.IGNORECASE | re.DOTALL)
    if m:
        t = m.group(1)
    # 去掉零散的反引号和 "SQL:" 之类前缀
    t = t.replace("```", "").strip()
    t = re.sub(r"^\s*sql\s*[:：]\s*", "", t, flags=re.IGNORECASE)
    return t.strip().rstrip(";").strip()


def validate_single_sql(text: str) -> tuple[str | None, str]:
    """清理并确认"恰好一条只读 SQL"。返回 (sql 或 None, 原因)。"""
    sql = clean_sql(text)
    if not sql:
        return None, "模型返回为空"
    ok, why = security.is_safe_sql(sql)
    if not ok:
        return None, why
    return sql, "ok"


# ============================ 离线关键词基线 ============================
def offline_sql(q: str) -> str:
    """无 API Key 时优先走业务语义层，无法编译时再用简单关键词兜底。"""
    plan = semantic_layer.analyze_question(q)
    compiled = semantic_layer.compile_sql(plan)
    if compiled:
        return compiled

    # The legacy public entry point must not silently turn an unsupported request
    # into total sales. The Agent returns needs_input before reaching this path.
    reason = plan.reason or "当前规则不能可靠编译这个分析计划，请拆成单指标查询"
    return "SELECT '需要补充：" + reason.replace("'", "''") + "' AS note"


# ============================ Claude 调用 ============================
def _call_claude(system: str, user: str, max_tokens: int) -> dict:
    """调用一次 Claude。返回 dict：text/error/input_tokens/output_tokens/latency_ms。

    绝不记录或返回 API Key。各类错误被归类为友好文案。
    """
    t0 = time.perf_counter()
    out = {"text": None, "error": None, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0.0}
    if not config.MODEL:
        out["error"] = "未配置MODEL；请填入账号实际可用的模型ID。未发送API请求"
        return out
    try:
        import anthropic

        client = anthropic.Anthropic(timeout=config.API_TIMEOUT_S)
        msg = client.messages.create(
            model=config.MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # 稳健地取文本
        parts = [b.text for b in msg.content if getattr(b, "type", "") == "text"]
        out["text"] = "\n".join(parts).strip()
        if not out["text"]:
            out["error"] = "模型返回为空"
        usage = getattr(msg, "usage", None)
        if usage:
            out["input_tokens"] = getattr(usage, "input_tokens", 0) or 0
            out["output_tokens"] = getattr(usage, "output_tokens", 0) or 0
    except ImportError:
        out["error"] = "未安装 anthropic 库（pip install anthropic）"
    except Exception as e:  # noqa: BLE001 —— 统一归类所有 API 异常
        out["error"] = _classify_error(e)
    out["latency_ms"] = (time.perf_counter() - t0) * 1000
    return out


def _classify_error(e: Exception) -> str:
    name = type(e).__name__
    mapping = {
        "AuthenticationError": "鉴权失败：请检查 ANTHROPIC_API_KEY",
        "PermissionDeniedError": "无权限访问该模型",
        "NotFoundError": f"模型不存在或不可用：{config.MODEL}（用 MODEL 覆盖）",
        "RateLimitError": "触发限流，请稍后重试",
        "APITimeoutError": "API 超时",
        "APIConnectionError": "网络连接失败",
        "BadRequestError": "请求无效（可能是 max_tokens 或模型参数）",
    }
    return mapping.get(name, f"API 调用失败：{name}")


# ============================ 主流程 ============================
def generate(
    question: str,
    schema: str,
    runner: Runner | None = None,
    *,
    plan: semantic_layer.SemanticPlan | None = None,
    use_llm: bool | None = None,
) -> SqlResult:
    """从问题生成并（可选）执行 SQL，带自动纠错与离线回退。

    runner: 给 SQL 返回 DataFrame、失败抛异常。传入即启用"执行 + 失败重试"。
    """
    if use_llm is False or not config.llm_available():
        return _offline_result(question, runner, plan=plan)
    return _llm_result(question, schema, runner, plan=plan)


def _offline_result(
    question: str,
    runner: Runner | None,
    *,
    mode: str = "offline",
    notice: str = "",
    plan: semantic_layer.SemanticPlan | None = None,
) -> SqlResult:
    sql = semantic_layer.compile_sql(plan) if plan is not None else offline_sql(question)
    if not sql:
        return SqlResult(
            question=question,
            sql="",
            mode=mode,
            notice=notice or "需要补充查询条件",
            error="当前分析计划不可执行",
        )
    res = SqlResult(question=question, sql=sql, mode=mode, notice=notice)
    ok, why = security.is_safe_sql(sql)
    if not ok:  # 理论上离线规则都安全，防御性处理
        res.blocked, res.block_reason = True, why
        return res
    if runner is not None:
        try:
            res.df = runner(sql)
        except Exception as e:  # noqa: BLE001
            res.error = str(e)
    return res


def _llm_result(
    question: str,
    schema: str,
    runner: Runner | None,
    *,
    plan: semantic_layer.SemanticPlan | None = None,
) -> SqlResult:
    res = SqlResult(question=question, sql="", mode="llm")
    user = f"数据库结构：\n{schema}\n\n用户问题：{question}\n\n只输出一条 SQL。"
    call = _call_claude(SYSTEM_PROMPT, user, config.MAX_TOKENS_SQL)
    res.api_calls += 1
    res.latency_ms += call["latency_ms"]
    res.input_tokens += call["input_tokens"]
    res.output_tokens += call["output_tokens"]

    if call["error"]:  # API 层失败 -> 回退离线
        log.warning("LLM 调用失败(%s)，回退离线规则", call["error"])
        fb = _offline_result(
            question,
            runner,
            mode="llm→offline",
            notice=f"在线调用失败（{call['error']}），已回退离线规则",
            plan=plan,
        )
        fb.api_calls, fb.latency_ms = res.api_calls, res.latency_ms
        fb.error = call["error"]
        return fb

    sql, why = validate_single_sql(call["text"])
    if sql is None:  # 生成的不是单条只读 SQL -> 拦截
        res.blocked, res.block_reason, res.sql = True, why, clean_sql(call["text"])
        log.info("LLM 输出被拦截：%s", why)
        return res
    res.sql = sql

    if runner is None:  # 只生成不执行
        return res

    # 执行 + 失败自动纠错（最多 MAX_CORRECTION_RETRIES 次）
    try:
        res.df = runner(sql)
        return res
    except Exception as e:  # noqa: BLE001
        last_err = str(e)

    while res.retries < config.MAX_CORRECTION_RETRIES:
        res.retries += 1
        fix_user = (
            f"数据库结构：\n{schema}\n\n问题：{question}\n\n"
            f"你上一版 SQL：\n{res.sql}\n\n执行报错：{last_err}\n"
            f"请修正，只输出一条只读 SELECT。"
        )
        call = _call_claude(SYSTEM_PROMPT, fix_user, config.MAX_TOKENS_SQL)
        res.api_calls += 1
        res.latency_ms += call["latency_ms"]
        res.input_tokens += call["input_tokens"]
        res.output_tokens += call["output_tokens"]
        if call["error"]:
            last_err = call["error"]
            break
        sql2, why2 = validate_single_sql(call["text"])
        if sql2 is None:
            last_err = why2
            continue
        res.sql = sql2
        try:
            res.df = runner(sql2)
            return res
        except Exception as e:  # noqa: BLE001
            last_err = str(e)

    # 纠错仍失败 -> 回退离线
    log.info("自动纠错失败(%s)，回退离线规则", last_err)
    fb = _offline_result(
        question,
        runner,
        mode="llm→offline",
        notice=f"SQL 多次执行失败（{last_err}），已回退离线规则",
        plan=plan,
    )
    fb.api_calls, fb.latency_ms = res.api_calls, res.latency_ms
    fb.retries, fb.error = res.retries, last_err
    fb.input_tokens, fb.output_tokens = res.input_tokens, res.output_tokens
    return fb
