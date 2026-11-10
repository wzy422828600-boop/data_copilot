"""Idempotently extend the synthetic retail demo and generate linked statements.

Only explicit demo-data preparation writes. All application analysis is read-only.
No balancing plug: financing cash movements explicitly explain additional debt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from calendar import monthrange
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data_copilot import config, database
from data_copilot.finance_catalog import ACCOUNTS

VERSION = "linked_statements_v1_20261105"


def months_between(start, end):
    year, month = map(int, start.split("-"))
    while f"{year:04d}-{month:02d}" <= end:
        yield f"{year:04d}-{month:02d}"
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def upgrade(db_path=None):
    path = Path(db_path or config.DB_PATH)
    with database.writable_connection(path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "simulation_metadata" in tables:
            version = conn.execute(
                "SELECT value FROM simulation_metadata WHERE key='version'"
            ).fetchone()
            if version and version[0] == VERSION:
                return {"version": VERSION, "already_current": True}
        if not {"orders", "products", "customers", "order_items"}.issubset(tables):
            raise ValueError("请先创建合成零售样例库。")
        if conn.execute("SELECT COUNT(*) FROM customers WHERE name NOT LIKE '顾客%'").fetchone()[0]:
            raise ValueError("只允许更新本项目的合成顾客样例，拒绝改动未知业务数据。")
        conn.execute("BEGIN IMMEDIATE")
        _extend_orders(conn)
        _create_schema(conn)
        _settlements(conn)
        summary = _statements(conn)
        summary["version"] = VERSION
        summary["checks"] = validate(conn)
        metadata = {
            "version": VERSION,
            "company": "青禾零售（虚构教学企业）",
            "currency": "CNY",
            "unit": "元（底层按整数分保存）",
            "period_start": "2023-01",
            "period_end": "2026-10",
            "assumptions": json.dumps(
                [
                    "全部为固定种子的模拟数据，非任何真实企业的经营业绩。",
                    "模拟单体零售企业，金额不含增值税；未模拟合并、递延所得税、外币和租赁会计。",
                    "退款按订单所属月份归集，并假设对应商品成本全部转回；属于教学简化。",
                    "所得税按正的税前利润的25%模拟，不代表实际适用税率；亏损不模拟递延税或结转。",
                    "现金和现金等价物在此模型中等同于货币资金；不含受限资金。",
                    "销售/管理费用含明细及小计；明细与小计不可再次相加。",
                    "毛利、费用明细和管理比率是补充分析项目，并非声明完整符合全部法定列报要求。",
                    "财务费用为借款利息，现金流分类为筹资活动；折旧为非现金管理费用。",
                ],
                ensure_ascii=False,
            ),
            "format_reference": "https://kjs.mof.gov.cn/zhengcefabu/201905/t20190510_3254992.htm",
        }
        conn.executemany("INSERT INTO simulation_metadata VALUES (?,?)", metadata.items())
    return summary


def _create_schema(conn):
    # No executescript here: keep the upgrade inside one atomic transaction.
    ddl = [
        "CREATE TABLE financial_accounts(code TEXT PRIMARY KEY,label TEXT NOT NULL,"
        "report TEXT NOT NULL,kind TEXT NOT NULL,parent TEXT NOT NULL,"
        "display_order INTEGER NOT NULL)",
        "CREATE TABLE financial_values(month TEXT NOT NULL,code TEXT NOT NULL REFERENCES "
        "financial_accounts(code),amount_cents INTEGER NOT NULL,PRIMARY KEY(month,code))",
        "CREATE TABLE order_finance(order_id INTEGER PRIMARY KEY REFERENCES orders(order_id),"
        "sales_cents INTEGER NOT NULL,refund_cents INTEGER NOT NULL CHECK(refund_cents>=0),"
        "cogs_cents INTEGER NOT NULL,returned_cost_cents INTEGER NOT NULL,"
        "fulfillment_cents INTEGER NOT NULL,payment_fee_cents INTEGER NOT NULL,"
        "CHECK(refund_cents<=sales_cents),CHECK(returned_cost_cents<=cogs_cents))",
        "CREATE TABLE simulation_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
        "CREATE INDEX idx_financial_values_code_month ON financial_values(code,month)",
    ]
    for sql in ddl:
        conn.execute(sql)
    conn.executemany(
        "INSERT INTO financial_accounts VALUES (?,?,?,?,?,?)",
        [(a.code, a.label, a.report, a.kind, a.parent, i) for i, a in enumerate(ACCOUNTS)],
    )


def _extend_orders(conn):
    rng = random.Random(20261105)
    products = conn.execute("SELECT product_id,price FROM products ORDER BY product_id").fetchall()
    customers = [
        r[0] for r in conn.execute("SELECT customer_id FROM customers ORDER BY customer_id")
    ]
    oid = conn.execute("SELECT MAX(order_id) FROM orders").fetchone()[0] or 0
    # The old 2023-24 history is preserved exactly; append only new periods.
    for k, month in enumerate(months_between("2025-01", "2026-10")):
        if conn.execute(
            "SELECT 1 FROM orders WHERE order_date LIKE ? LIMIT 1", (month + "%",)
        ).fetchone():
            raise ValueError(f"{month}已有未知数据，停止追加以避免重复。")
        year, mon = map(int, month.split("-"))
        counts = {"线上": 160 + k * 4, "线下": 100 + k * 3}
        if mon == 11:
            counts = {c: int(n * 1.4) for c, n in counts.items()}
        if month == "2026-09":
            counts = {"线上": 340, "线下": 160}
        elif month == "2026-10":
            counts = {"线上": 225, "线下": 170}
        for channel, count in counts.items():
            for _ in range(count):
                oid += 1
                day = rng.randint(1, monthrange(year, mon)[1])
                conn.execute(
                    "INSERT INTO orders VALUES (?,?,?,?)",
                    (oid, rng.choice(customers), f"{month}-{day:02d}", channel),
                )
                for pid, price in rng.sample(products, rng.randint(1, 4)):
                    discount = (
                        rng.uniform(0.78, 0.96) if channel == "线上" else rng.uniform(0.91, 1)
                    )
                    if month == "2026-10" and channel == "线上":
                        discount *= 0.93
                    conn.execute(
                        "INSERT INTO order_items VALUES (?,?,?,?)",
                        (oid, pid, rng.randint(1, 3), round(price * discount, 2)),
                    )


def _settlements(conn):
    rows = conn.execute(
        "SELECT o.order_id,o.channel,SUM(i.quantity*i.unit_price),SUM(i.quantity*p.cost) "
        "FROM orders o JOIN order_items i ON o.order_id=i.order_id JOIN products p "
        "ON i.product_id=p.product_id GROUP BY o.order_id ORDER BY o.order_id"
    ).fetchall()
    for oid, channel, sales, cogs in rows:
        rng = random.Random(88000 + oid)
        sales, cogs = round(sales * 100), round(cogs * 100)
        ratio = rng.choice([0.25, 0.5, 1.0]) if rng.random() < 0.065 else 0
        refund, returned = round(sales * ratio), round(cogs * ratio)
        shipping = rng.randint(650, 1900) if channel == "线上" else rng.randint(150, 600)
        fee = round((sales - refund) * (0.008 if channel == "线上" else 0.004))
        conn.execute(
            "INSERT INTO order_finance VALUES (?,?,?,?,?,?,?)",
            (oid, sales, refund, cogs, returned, shipping, fee),
        )


def _statements(conn):
    old = {
        "cash": 100000000,
        "receivables": 12000000,
        "inventory": 24000000,
        "prepaid": 2000000,
        "fixed_assets": 30000000,
        "payables": 16000000,
        "taxes_payable": 0,
        "short_debt": 20000000,
        "long_debt": 30000000,
        "capital": 102000000,
        "retained_earnings": 0,
    }
    _balance_totals(old)
    conn.executemany(
        "INSERT INTO financial_values VALUES (?,?,?)",
        [("2022-12", a.code, old[a.code]) for a in ACCOUNTS if a.report == "balance"],
    )
    for month in months_between("2023-01", "2026-10"):
        rev, cogs, shipping, fee = conn.execute(
            "SELECT SUM(f.sales_cents-f.refund_cents),SUM(f.cogs_cents-f.returned_cost_cents),"
            "SUM(f.fulfillment_cents),SUM(f.payment_fee_cents) FROM order_finance f "
            "JOIN orders o ON f.order_id=o.order_id WHERE o.order_date LIKE ?",
            (month + "%",),
        ).fetchone()
        rev, cogs, shipping, fee = [int(x or 0) for x in (rev, cogs, shipping, fee)]
        v = {a.code: 0 for a in ACCOUNTS}
        v.update(
            revenue=rev, cogs=cogs, gross_profit=rev - cogs, fulfillment=shipping, payment_fee=fee
        )
        v["marketing"] = round(rev * (0.16 if month == "2026-09" else 0.085)) + 500000
        v["selling_payroll"] = 850000
        v["selling_expense"] = sum(
            v[c] for c in ("marketing", "fulfillment", "payment_fee", "selling_payroll")
        )
        v.update(admin_payroll=1100000, rent=450000, admin_other=120000, rd_expense=250000)
        v["depreciation"] = min(350000, old["fixed_assets"])
        v["admin_expense"] = sum(
            v[c] for c in ("admin_payroll", "rent", "admin_other", "depreciation")
        )
        v["finance_expense"] = round((old["short_debt"] + old["long_debt"]) * 0.045 / 12)
        v["surcharges"] = round(rev * 0.003)
        v["operating_profit"] = (
            v["gross_profit"]
            - v["selling_expense"]
            - v["admin_expense"]
            - v["rd_expense"]
            - v["finance_expense"]
            - v["surcharges"]
        )
        v["pretax_profit"] = v["operating_profit"]
        v["income_tax"] = round(max(0, v["pretax_profit"]) * 0.25)
        v["net_profit"] = v["pretax_profit"] - v["income_tax"]
        v["receivables"] = round(rev * 0.15)
        v["inventory"] = max(round(cogs * 1.3) + 3500000, old["inventory"] - cogs)
        purchases = cogs + v["inventory"] - old["inventory"]
        v["payables"] = round(purchases * 0.35)
        v["taxes_payable"] = round((v["income_tax"] + v["surcharges"]) * 0.4)
        v["customer_receipts"] = rev + old["receivables"] - v["receivables"]
        v["supplier_payments"] = purchases + old["payables"] - v["payables"]
        v["employee_payments"] = v["selling_payroll"] + v["admin_payroll"] + v["rd_expense"]
        v["tax_payments"] = (
            v["income_tax"] + v["surcharges"] + old["taxes_payable"] - v["taxes_payable"]
        )
        v["other_operating_payments"] = sum(
            v[c] for c in ("marketing", "fulfillment", "payment_fee", "rent", "admin_other")
        )
        v["cfo"] = v["customer_receipts"] - sum(
            v[c]
            for c in (
                "supplier_payments",
                "employee_payments",
                "tax_payments",
                "other_operating_payments",
            )
        )
        mon = int(month[-2:])
        v["capex"] = 600000 if mon % 3 == 0 else 0
        v["cfi"] = -v["capex"]
        v["interest_paid"] = v["finance_expense"]
        v["dividends"] = min(max(old["retained_earnings"], 0) // 10, 1000000) if mon == 12 else 0
        v["debt_repayments"] = min(500000, old["short_debt"]) if mon % 6 == 0 else 0
        cash_without_new_debt = (
            old["cash"]
            + v["cfo"]
            + v["cfi"]
            - v["interest_paid"]
            - v["dividends"]
            - v["debt_repayments"]
        )
        v["borrowing_received"] = max(0, 10000000 - cash_without_new_debt)
        v["cff"] = (
            v["borrowing_received"] - v["debt_repayments"] - v["dividends"] - v["interest_paid"]
        )
        v["cash_change"] = v["cfo"] + v["cfi"] + v["cff"]
        v["opening_cash"] = old["cash"]
        v["cash"] = v["closing_cash"] = v["opening_cash"] + v["cash_change"]
        v["short_debt"] = old["short_debt"] + v["borrowing_received"] - v["debt_repayments"]
        v["long_debt"], v["capital"], v["prepaid"] = (
            old["long_debt"],
            old["capital"],
            old["prepaid"],
        )
        v["fixed_assets"] = old["fixed_assets"] + v["capex"] - v["depreciation"]
        v["retained_earnings"] = old["retained_earnings"] + v["net_profit"] - v["dividends"]
        v["opening_retained"], v["equity_net_profit"] = old["retained_earnings"], v["net_profit"]
        v["equity_dividends"], v["closing_retained"] = v["dividends"], v["retained_earnings"]
        _balance_totals(v)
        conn.executemany(
            "INSERT INTO financial_values VALUES (?,?,?)",
            [(month, a.code, v[a.code]) for a in ACCOUNTS],
        )
        old = v
    return {
        "months": 46,
        "through": "2026-10",
        "accounts": len(ACCOUNTS),
        "orders": conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
    }


def _balance_totals(v):
    v["current_assets"] = sum(v[c] for c in ("cash", "receivables", "inventory", "prepaid"))
    v["noncurrent_assets"] = v["fixed_assets"]
    v["total_assets"] = v["current_assets"] + v["noncurrent_assets"]
    v["current_liabilities"] = sum(v[c] for c in ("payables", "taxes_payable", "short_debt"))
    v["noncurrent_liabilities"] = v["long_debt"]
    v["total_liabilities"] = v["current_liabilities"] + v["noncurrent_liabilities"]
    v["total_equity"] = v["capital"] + v["retained_earnings"]
    v["liabilities_equity"] = v["total_liabilities"] + v["total_equity"]


def validate(conn):
    all_values = {}
    for month, code, value in conn.execute("SELECT month,code,amount_cents FROM financial_values"):
        all_values.setdefault(month, {})[code] = value
    checks = {
        "balance_sheet": 0,
        "cash_rollforward": 0,
        "cash_flow_sum": 0,
        "net_profit": 0,
        "equity_rollforward": 0,
        "cash_profit_reconciliation": 0,
        "revenue_reconciliation": 0,
        "nonnegative_payments": 0,
    }
    old = all_values["2022-12"]
    for month in sorted(m for m in all_values if m > "2022-12"):
        v = all_values[month]
        checks["balance_sheet"] += int(v["total_assets"] != v["liabilities_equity"])
        checks["cash_rollforward"] += int(v["cash"] != old["cash"] + v["cash_change"])
        checks["cash_flow_sum"] += int(v["cash_change"] != v["cfo"] + v["cfi"] + v["cff"])
        checks["net_profit"] += int(v["net_profit"] != v["pretax_profit"] - v["income_tax"])
        checks["equity_rollforward"] += int(
            v["retained_earnings"] != old["retained_earnings"] + v["net_profit"] - v["dividends"]
        )
        indirect_cfo = (
            v["net_profit"]
            + v["depreciation"]
            + v["finance_expense"]
            - v["receivables"]
            + old["receivables"]
            - v["inventory"]
            + old["inventory"]
            + v["payables"]
            - old["payables"]
            + v["taxes_payable"]
            - old["taxes_payable"]
        )
        checks["cash_profit_reconciliation"] += int(v["cfo"] != indirect_cfo)
        revenue = conn.execute(
            "SELECT SUM(f.sales_cents-f.refund_cents) FROM order_finance f "
            "JOIN orders o ON f.order_id=o.order_id WHERE o.order_date LIKE ?",
            (month + "%",),
        ).fetchone()[0]
        checks["revenue_reconciliation"] += int(v["revenue"] != revenue)
        checks["nonnegative_payments"] += int(
            any(
                v[c] < 0
                for c in (
                    "supplier_payments",
                    "customer_receipts",
                    "tax_payments",
                    "cash",
                    "inventory",
                )
            )
        )
        old = v
    if any(checks.values()):
        raise ValueError(f"模拟财报勾稽失败，事务将回滚：{checks}")
    if conn.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("外键检查失败")
    return checks


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=config.DB_PATH)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    result = upgrade(args.db)
    result["db_sha256"] = hashlib.sha256(args.db.read_bytes()).hexdigest()
    if args.db == config.DB_PATH and not result.get("already_current"):
        config.EVAL_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        (config.EVAL_RESULTS_DIR / "finance_data_validation.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
    print(
        "模拟经营与财报数据库已准备好。"
        if args.quiet
        else json.dumps(result, ensure_ascii=False, indent=2)
    )


if __name__ == "__main__":
    main()
