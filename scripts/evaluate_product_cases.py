"""Same-data before/after regression comparison. Offline only; no API clients.

The cases are development regressions, not an independent user or model eval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from data_copilot import config, database
from evaluation.product_cases import CASES
from scripts.evaluate import signature

CHILD = """
import inspect, json, sys
from datetime import date
sys.path.insert(0, sys.argv[1])
from data_copilot import agent
from scripts.evaluate import signature
cases=json.load(open(sys.argv[3]))
records=[]
for case in cases:
    kwargs=dict(db_path=sys.argv[2])
    if "reference_date" in inspect.signature(agent.analyze).parameters:
        kwargs["reference_date"]=date(2026,11,5)
    try:
        r=agent.analyze(case["question"], **kwargs)
        records.append(dict(id=case["id"], status=r.plan.status,
            quality=r.quality.status, signature=signature(r.df),
            sql=r.sql, api_calls=r.sql_result.api_calls))
    except Exception as exc:
        records.append(dict(id=case["id"], status="error", error=str(exc), api_calls=0))
print(json.dumps(records,ensure_ascii=False))
"""


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_source(source_root, case_path):
    env = dict(os.environ, ANTHROPIC_API_KEY="", MODEL="", PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-c", CHILD, str(source_root), str(config.DB_PATH), str(case_path)],
        cwd=source_root,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-archive", type=Path, required=True)
    args = parser.parse_args()
    before_db_hash = digest(config.DB_PATH)
    with tempfile.TemporaryDirectory(prefix="bi-product-regression-") as tmp:
        tmp = Path(tmp)
        baseline_root = tmp / "baseline"
        (baseline_root / "data_copilot").mkdir(parents=True)
        (baseline_root / "scripts").mkdir()
        for path in (ROOT / "data_copilot").glob("*.py"):
            shutil.copyfile(path, baseline_root / "data_copilot" / path.name)
        # Only source modules from the pre-edit archive; no .env, no execution of
        # setup hooks, and no extraction of arbitrary archive paths.
        with zipfile.ZipFile(args.baseline_archive) as archive:
            for name in archive.namelist():
                if (
                    name.startswith("data_copilot/")
                    and name.endswith(".py")
                    and len(Path(name).parts) == 2
                ):
                    (baseline_root / name).write_bytes(archive.read(name))
        # Both versions use the fixed comparator, preserving column association.
        shutil.copyfile(ROOT / "scripts/evaluate.py", baseline_root / "scripts/evaluate.py")
        (baseline_root / "evaluation").mkdir()
        shutil.copyfile(ROOT / "evaluation/questions.py", baseline_root / "evaluation/questions.py")
        case_path = tmp / "cases.json"
        case_path.write_text(json.dumps(CASES, ensure_ascii=False))
        old = run_source(baseline_root, case_path)
        new = run_source(ROOT, case_path)

    expected = {
        case["id"]: signature(database.run_query(case["gold_sql"])[0])
        for case in CASES
        if "gold_sql" in case
    }
    records = []
    for case, previous, current in zip(CASES, old, new, strict=True):

        def passed(record):
            if "expected_status" in case:
                return record["status"] == case["expected_status"]
            return (
                record["status"] == "ready"
                and record["quality"] == "passed"
                and record["signature"] == expected[case["id"]]
            )

        records.append(
            dict(
                case=case,
                before=previous,
                after=current,
                before_pass=passed(previous),
                after_pass=passed(current),
            )
        )
    summary = dict(
        set_kind="新增开发回归集，不是盲测，不是在线模型准确率",
        reference_date="2026-11-05 Asia/Shanghai",
        cases=len(CASES),
        query_cases=len(expected),
        boundary_cases=len(CASES) - len(expected),
        before_pass=sum(r["before_pass"] for r in records),
        after_pass=sum(r["after_pass"] for r in records),
        api_calls=sum(r.get("api_calls", 0) for r in old + new),
        db_sha256_before=before_db_hash,
        db_sha256_after=digest(config.DB_PATH),
        baseline_archive_sha256=digest(args.baseline_archive),
        cases_sha256=digest(ROOT / "evaluation/product_cases.py"),
    )
    payload = dict(summary=summary, records=records)
    out = config.EVAL_RESULTS_DIR / "product_regression_20261105.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"结果：{out}")
    return (
        0
        if (
            summary["after_pass"] == len(CASES)
            and summary["db_sha256_before"] == summary["db_sha256_after"]
            and summary["api_calls"] == 0
        )
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
