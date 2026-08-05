"""命令行入口：python scripts/ask.py "你的问题"  （可从任意工作目录运行）。

问题 -> 生成 SQL -> 安全校验 -> 只读执行 -> 出图 -> 一句洞察。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_copilot import config, database, insights, text_to_sql, visualization  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('用法: python scripts/ask.py "你的问题"')
        print('示例: python scripts/ask.py "线上和线下哪个销售额高？"')
        return 1

    question = argv[1]
    mode_tag = "Claude 在线" if config.llm_available() else "离线规则(无 ANTHROPIC_API_KEY)"
    print("=" * 64)
    print(f"问题: {question}")
    print(f"模式: {mode_tag}   模型: {config.MODEL}")
    print("=" * 64)

    def runner(sql: str):
        df, _ = database.run_query(sql)
        return df

    schema = database.schema_text()
    res = text_to_sql.generate(question, schema, runner=runner)

    print("\n[生成的 SQL]")
    print(res.sql or "(无)")
    if res.notice:
        print(f"\n[提示] {res.notice}")
    if res.blocked:
        print(f"\n[已拦截] {res.block_reason}")
        return 0
    if res.error and res.df is None:
        print(f"\n[执行出错] {res.error}")
        return 0

    df = res.df
    print(f"\n[结果] {len(df)} 行"
          + ("（已截断到上限）" if len(df) >= config.MAX_ROWS else ""))
    print(df.head(15).to_string(index=False))

    chart = visualization.make_chart(
        df, os.path.join(config.OUTPUT_DIR, "last_chart.png"), title=question)
    if chart:
        print(f"\n[图表] 已保存 -> {chart}")

    print("\n[洞察]")
    print(insights.summarize(question, df))

    print(f"\n[元信息] 模式={res.mode} 重试={res.retries} "
          f"API调用={res.api_calls} 耗时={res.latency_ms:.0f}ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
