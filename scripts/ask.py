"""命令行入口：python scripts/ask.py "你的问题"  （可从任意工作目录运行）。

问题 -> 生成 SQL -> 安全校验 -> 只读执行 -> 出图 -> 一句洞察。
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_copilot import agent, config, semantic_layer, visualization  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('用法: python scripts/ask.py "你的问题"')
        print('示例: python scripts/ask.py "线上和线下哪个销售额高？"')
        return 1

    use_llm = "--online" in argv[1:]
    questions = [arg for arg in argv[1:] if arg != "--online"]
    if not questions:
        print("请提供查询问题")
        return 1
    question = questions[0]
    if use_llm and not (config.llm_available() and config.MODEL):
        print(
            "在线实验需要显式配置 ANTHROPIC_API_KEY 和实际可用的 MODEL；离线使用请去掉 --online。"
        )
        return 2
    mode_tag = "在线模型实验" if use_llm else "离线规则（不调用模型）"
    print("=" * 64)
    print(f"问题: {question}")
    print(f"模式: {mode_tag}   模型: {config.MODEL}")
    print("=" * 64)

    res = agent.analyze(question, use_llm=use_llm)

    print("\n[分析计划]")
    print(semantic_layer.describe_plan(res.plan))

    print("\n[生成的 SQL]")
    print(res.sql or "(无)")
    if res.sql_result.notice:
        print(f"\n[提示] {res.sql_result.notice}")
    if res.blocked:
        print(f"\n[已拦截] {res.block_reason}")
        return 0
    if res.error and res.df is None:
        print(f"\n[执行出错] {res.error}")
        return 0

    if res.plan.status in {"clarify", "unanswerable"} or res.quality.status == "no_data":
        print(f"\n[需要处理] {res.insight}")
        return 0

    df = res.df
    if df is None:
        print(res.insight)
        return 0
    print(f"\n[结果] {len(df)} 行" + ("（已截断到上限）" if len(df) >= config.MAX_ROWS else ""))
    print(df.head(15).to_string(index=False))

    chart = visualization.make_chart(
        df, os.path.join(config.OUTPUT_DIR, "last_chart.png"), title=question
    )
    if chart:
        print(f"\n[图表] 已保存 -> {chart}")

    print("\n[洞察]")
    print(res.insight)

    print("\n[结果校验]")
    print(f"状态={res.quality.label}；此处不是答案正确率")
    for warning in res.quality.warnings:
        print(f"- {warning}")

    print("\n[Agent 轨迹]")
    for step in res.trace:
        print(f"- {step.name}: {step.status} | {step.detail} | {step.latency_ms:.1f}ms")

    print(
        f"\n[元信息] 模式={res.mode} 重试={res.sql_result.retries} "
        f"API调用={res.sql_result.api_calls} 总耗时={res.total_latency_ms:.0f}ms"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
