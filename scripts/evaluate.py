"""Text-to-SQL 评测：跑题库、算指标、写 evaluation/results/、打印汇总表。

默认只运行离线开发回归；在线实验必须显式 --online。
比较忽略行序和列名，但保留列位置及一行内各值的对应关系，数值保留6位小数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from numbers import Number

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_copilot import agent, config, database  # noqa: E402
from evaluation.questions import load  # noqa: E402

SENTINEL = "无法回答"


def signature(df) -> list | None:
    """忽略行序和列名；禁止行内排序，以免混淆维度与指标的对应关系。"""
    if df is None:
        return None
    rows = []
    for _, row in df.iterrows():
        vals = []
        for v in row:
            if pd.isna(v):
                vals.append("<NULL>")
            elif isinstance(v, bool):
                vals.append(str(v))
            elif isinstance(v, Number):
                vals.append(str(round(float(v), 6)))
            else:
                vals.append(str(v))
        rows.append(vals)
    return sorted(rows)


def gold_signature(sql: str):
    df, _ = database.run_query(sql)
    return signature(df)


def looks_unanswerable(res) -> bool:
    # Errors, empty data and excessive blocking are not a correct missing-field refusal.
    return res.plan.status == "unanswerable" and res.quality.status == "unanswerable"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    if args.online and not (config.llm_available() and config.MODEL):
        parser.error("在线实验需配置实际可用的 MODEL 和 ANTHROPIC_API_KEY")
    mode = "llm" if args.online else "offline"
    questions = load()
    schema = database.schema_text(include_samples=False)
    before_counts = database.row_counts()
    db_hash_before = hashlib.sha256(config.DB_PATH.read_bytes()).hexdigest()

    per_cat = defaultdict(lambda: [0, 0])  # category -> [correct, total]
    per_diff = defaultdict(lambda: [0, 0])
    records = []
    n_answerable = n_exec_ok = n_acc = 0
    n_safe = n_safe_pass = 0
    n_reject = n_blocked = n_db_safe = 0
    total_latency = total_calls = total_in = total_out = 0
    n_quality_pass = 0

    for q in questions:
        counts_before_q = database.row_counts()
        res = agent.analyze(q["question"], schema=schema, use_llm=args.online)
        sql_res = res.sql_result
        total_latency += res.total_latency_ms
        total_calls += sql_res.api_calls
        total_in += sql_res.input_tokens
        total_out += sql_res.output_tokens
        n_quality_pass += int(res.quality.status in {"passed", "unanswerable", "blocked"})

        correct = False
        if q.get("should_reject"):
            n_reject += 1
            if res.blocked:
                n_blocked += 1
            # 只读执行下数据库不会被改；此处记录"是否只读安全"
            db_safe = counts_before_q == database.row_counts()
            if db_safe:
                n_db_safe += 1
            correct = res.blocked and db_safe
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
                except Exception:  # noqa: BLE001
                    correct = False
            if correct:
                n_acc += 1

        per_cat[q["category"]][1] += 1
        per_cat[q["category"]][0] += int(correct)
        per_diff[q["difficulty"]][1] += 1
        per_diff[q["difficulty"]][0] += int(correct)
        records.append(
            dict(
                id=q["id"],
                category=q["category"],
                difficulty=q["difficulty"],
                mode=res.mode,
                blocked=bool(res.blocked),
                correct=bool(correct),
                quality_status=res.quality.status,
                semantic_plan=res.plan.as_dict(),
                sql=res.sql[:200],
            )
        )

    after_counts = database.row_counts()
    db_hash_after = hashlib.sha256(config.DB_PATH.read_bytes()).hexdigest()
    db_unchanged = before_counts == after_counts and db_hash_before == db_hash_after

    estimated_cost = config.estimate_cost_usd(total_in, total_out)
    summary = {
        "mode": mode,
        "set_kind": "原35题开发回归；16/17/25题已补全年份，不是盲测",
        "model": config.MODEL if mode == "llm" else None,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "n_questions": len(questions),
        "sql_exec_success_rate": _rate(n_exec_ok, n_answerable),
        "execution_accuracy": _rate(n_acc, n_answerable),
        "safe_request_pass_rate": _rate(n_safe_pass, n_safe),
        "dangerous_handled_rate": _rate(n_db_safe, n_reject),
        "dangerous_explicit_block_rate": _rate(n_blocked, n_reject),
        "db_unchanged_after_eval": db_unchanged,
        "quality_check_pass_rate": _rate(n_quality_pass, len(questions)),
        "missing_field_correct_refusals": sum(
            int(r["correct"]) for r in records if r["category"] == "unanswerable"
        ),
        "db_sha256_before": db_hash_before,
        "db_sha256_after": db_hash_after,
        "avg_latency_ms": round(total_latency / len(questions), 1),
        "total_api_calls": total_calls,
        "est_cost_usd": round(estimated_cost, 4) if estimated_cost is not None else None,
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
    print(f"结果校验通过率    : {s['quality_check_pass_rate']}%")
    print("结构检查不是答案正确率；此结果仅覆盖当前开发回归题。")
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
