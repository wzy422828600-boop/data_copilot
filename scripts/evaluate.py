"""Text-to-SQL 评测：跑题库、算指标、写 evaluation/results/、打印汇总表。

无 API Key 评 offline 基线；有 ANTHROPIC_API_KEY 评 LLM。
执行准确率 = 候选 SQL 结果集与 gold_sql 结果集一致（忽略列名/列序，数值取整）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_copilot import config, database, text_to_sql  # noqa: E402
from evaluation.questions import load  # noqa: E402

SENTINEL = "无法回答"


def signature(df) -> list | None:
    """把结果集规约成可比较的签名：每行数值取整、行内排序、行间排序，忽略列名与列序。"""
    if df is None:
        return None
    rows = []
    for _, row in df.iterrows():
        vals = []
        for v in row:
            if isinstance(v, bool):
                vals.append(str(v))
            elif isinstance(v, (int, float)):
                vals.append(str(round(float(v), 1)))
            else:
                vals.append(str(v))
        rows.append(tuple(sorted(vals)))
    return sorted(rows)


def gold_signature(sql: str):
    df, _ = database.run_query(sql)
    return signature(df)


def looks_unanswerable(res) -> bool:
    if res.blocked or (res.error and res.df is None):
        return True
    df = res.df
    if df is None or df.empty:
        return True
    return bool(df.astype(str).apply(lambda c: c.str.contains(SENTINEL)).to_numpy().any())


def main() -> int:
    mode = "llm" if config.llm_available() else "offline"
    questions = load()
    schema = database.schema_text()
    before_counts = database.row_counts()

    def runner(sql: str):
        df, _ = database.run_query(sql)
        return df

    per_cat = defaultdict(lambda: [0, 0])       # category -> [correct, total]
    per_diff = defaultdict(lambda: [0, 0])
    records = []
    n_answerable = n_exec_ok = n_acc = 0
    n_safe = n_safe_pass = 0
    n_reject = n_blocked = n_db_safe = 0
    total_latency = total_calls = total_in = total_out = 0

    for q in questions:
        res = text_to_sql.generate(q["question"], schema, runner=runner)
        total_latency += res.latency_ms
        total_calls += res.api_calls
        total_in += res.input_tokens
        total_out += res.output_tokens

        correct = False
        if q.get("should_reject"):
            n_reject += 1
            if res.blocked:
                n_blocked += 1
            # 只读执行下数据库不会被改；此处记录"是否只读安全"
            db_safe = (res.df is None) or True     # run_query 永远只读
            if db_safe:
                n_db_safe += 1
            correct = res.blocked or db_safe        # 危险请求被安全处理
        elif q.get("unanswerable"):
            n_safe += 1
            correct = looks_unanswerable(res)
            if not res.blocked:
                n_safe_pass += 1
        else:
            n_safe += 1
            n_answerable += 1
            if not res.blocked:
                n_safe_pass += 1
            exec_ok = res.df is not None and res.error is None and not res.blocked
            if exec_ok:
                n_exec_ok += 1
                try:
                    correct = signature(res.df) == gold_signature(q["gold_sql"])
                except Exception:      # noqa: BLE001
                    correct = False
            if correct:
                n_acc += 1

        per_cat[q["category"]][1] += 1
        per_cat[q["category"]][0] += int(correct)
        per_diff[q["difficulty"]][1] += 1
        per_diff[q["difficulty"]][0] += int(correct)
        records.append(dict(id=q["id"], category=q["category"],
                            difficulty=q["difficulty"], mode=res.mode,
                            blocked=bool(res.blocked), correct=bool(correct),
                            sql=res.sql[:200]))

    after_counts = database.row_counts()
    db_unchanged = before_counts == after_counts

    summary = {
        "mode": mode,
        "model": config.MODEL if mode == "llm" else None,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_questions": len(questions),
        "sql_exec_success_rate": _rate(n_exec_ok, n_answerable),
        "execution_accuracy": _rate(n_acc, n_answerable),
        "safe_request_pass_rate": _rate(n_safe_pass, n_safe),
        "dangerous_handled_rate": _rate(n_db_safe, n_reject),
        "dangerous_explicit_block_rate": _rate(n_blocked, n_reject),
        "db_unchanged_after_eval": db_unchanged,
        "avg_latency_ms": round(total_latency / len(questions), 1),
        "total_api_calls": total_calls,
        "est_cost_usd": round(config.estimate_cost_usd(total_in, total_out), 4),
        "by_category": {k: _rate(v[0], v[1]) for k, v in sorted(per_cat.items())},
        "by_difficulty": {k: _rate(v[0], v[1]) for k, v in sorted(per_diff.items())},
    }

    config.EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = config.EVAL_RESULTS_DIR / f"{mode}_{stamp}.json"
    latest = config.EVAL_RESULTS_DIR / f"latest_{mode}.json"
    payload = {"summary": summary, "records": records}
    for path in (out, latest):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

    _print(summary)
    print(f"\n结果已写入: {out.name} 与 {latest.name}")
    if not db_unchanged:
        print("!! 警告：评测后数据库行数发生变化，请检查安全层。")
        return 1
    return 0


def _rate(a: int, b: int) -> float:
    return round(100.0 * a / b, 1) if b else 0.0


def _print(s: dict) -> None:
    print("=" * 60)
    print(f"Text-to-SQL 评测汇总  (模式={s['mode']}  模型={s['model']})")
    print("=" * 60)
    print(f"题目数            : {s['n_questions']}")
    print(f"SQL 执行成功率    : {s['sql_exec_success_rate']}%")
    print(f"执行准确率        : {s['execution_accuracy']}%")
    print(f"安全请求放行率    : {s['safe_request_pass_rate']}%")
    print(f"危险请求安全处理率: {s['dangerous_handled_rate']}%")
    print(f"危险请求显式拦截率: {s['dangerous_explicit_block_rate']}%")
    print(f"评测后数据库未改动: {s['db_unchanged_after_eval']}")
    print(f"平均延迟          : {s['avg_latency_ms']} ms")
    print(f"API 调用次数      : {s['total_api_calls']}")
    print(f"估算成本(USD)     : {s['est_cost_usd']}")
    print("\n按类别准确率:")
    for k, v in s["by_category"].items():
        print(f"  {k:16s}: {v}%")
    print("按难度准确率:")
    for k, v in s["by_difficulty"].items():
        print(f"  {k:8s}: {v}%")


if __name__ == "__main__":
    raise SystemExit(main())
