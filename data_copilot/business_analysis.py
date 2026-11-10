"""Local statement querying and descriptive business decomposition.

No model calls; aliases choose data dictionary entries, not prewritten answers.
Statement flows sum, balances use period-end, ratios recompute from components.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from . import database, semantic_layer
from .finance_catalog import ACCOUNTS, BY_CODE, RATIOS, REPORT_NAMES
from .periods import InputNeeded, label, resolve, shift
from .semantic_layer import SemanticPlan

ANALYSIS_VERSION = "0.5.0"
ANALYSIS_REVISION = 2


@dataclass
class BusinessAnswer:
    plan: SemanticPlan
    df: pd.DataFrame | None = None
    sql: str = ""
    insight: str = ""
    tables: list = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


COMPARISON = (
    r"同比|环比|同期|对比|比较|相比|相较|比\s*(?:20\d{2}年)?\d|变化|增长|下降|提升|上涨|下跌|"
    r"增加|减少|为什么|为何"
)
DIAGNOSIS = r"为什么|为何|原因|归因|分析|拆解"
SALES_DIAGNOSIS = r"销售额|成交额|GMV|gmv"
DERIVED = {
    "period_expenses": (
        "期间费用",
        ("selling_expense", "admin_expense", "rd_expense", "finance_expense"),
    ),
    "operating_total_cost": (
        "营业总成本",
        ("cogs", "surcharges", "selling_expense", "admin_expense", "rd_expense", "finance_expense"),
    ),
}
EXTRA = {
    "refunds": ("模拟退款金额", ("退款金额", "退款额", "退货金额")),
    "gross_sales": ("退款前成交额", ("销售总额",)),
}


def try_answer(question, *, db_path=None, reference_date=None):
    sales_comparison = "环比" not in question and re.search(
        r"同比|相比|相较|对比|比较|月.*比.*月", question
    )
    is_sales = bool(
        re.search(SALES_DIAGNOSIS, question)
        and (re.search(DIAGNOSIS, question) or sales_comparison)
        and "营业收入" not in question
    )
    # Plain order gross margin retains the legacy order metric, not the statement
    # after-refund margin. Explicit financial context selects the latter.
    finance_triggers = [a.label for a in ACCOUNTS if a.code not in {"gross_profit"}]
    finance_triggers += [
        alias for a in ACCOUNTS for alias in a.aliases if a.code not in {"gross_profit", "revenue"}
    ]
    finance_triggers += [v[0] for k, v in RATIOS.items() if k != "gross_margin"]
    finance_triggers += [alias for k, v in RATIOS.items() for alias in v[4] if k != "gross_margin"]
    finance_triggers += [name for name, _ in EXTRA.values()]
    finance_triggers += [alias for _, aliases in EXTRA.values() for alias in aliases]
    finance_triggers += [
        "期间费用",
        "收入",
        "营收",
        "库存金额",
        "库存价值",
        "营业总成本",
        "营业总收入",
        "总收入",
        "财报",
        "财务",
        "利润表",
        "损益表",
        "资产负债表",
        "现金流量表",
        "权益变动表",
        "经营情况",
        "费用结构",
        "利润",
    ]
    # 毛利 alone is an established order metric. Do not change its scope silently.
    is_finance = any(t in question for t in finance_triggers)
    if "毛利" in question and not any(t in question for t in finance_triggers if t != "利润"):
        is_finance = False
    if not (is_sales or is_finance):
        return None
    intent = "sales_diagnosis" if is_sales else "financial_analysis"
    try:
        if re.search(r"预测|预计|明年|未来|假如|如果", question):
            raise InputNeeded("当前支持已记录期间的取数和变化拆解，尚不做预测或假设推演。")
        with database.readonly_connection(db_path) as conn:
            if is_sales:
                return _sales_answer(conn, question, reference_date)
            return _finance_answer(conn, question, reference_date)
    except (InputNeeded, database.QueryError) as exc:
        return BusinessAnswer(SemanticPlan("clarify", intent, reason=str(exc)), insight=str(exc))
    except sqlite3.Error:
        reason = "读取模拟账务数据失败，请检查数据库结构或重新运行数据准备脚本；未生成财务结论。"
        return BusinessAnswer(SemanticPlan("clarify", intent, reason=reason), insight=reason)


def metadata(db_path=None):
    with database.readonly_connection(db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "simulation_metadata" not in tables:
            return {}
        return dict(conn.execute("SELECT key,value FROM simulation_metadata"))


def _values(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "financial_values" not in tables:
        raise InputNeeded("当前库尚无月度财报，请先运行 scripts/upgrade_demo_data.py。")
    sql = "SELECT month,code,amount_cents FROM financial_values ORDER BY month,code"
    rows = conn.execute(sql).fetchmany(10001)
    if len(rows) > 10000:
        raise InputNeeded("财报明细超过本地原型的读取上限，请缩小数据集。")
    values = {}
    for month, code, cents in rows:
        values.setdefault(month, {})[code] = cents
    settlement_sql = (
        "SELECT substr(o.order_date,1,7) AS month,SUM(f.sales_cents),SUM(f.refund_cents) "
        "FROM order_finance f JOIN orders o ON f.order_id=o.order_id GROUP BY month"
    )
    for month, gross, refunds in conn.execute(settlement_sql):
        values[month]["gross_sales"], values[month]["refunds"] = gross, refunds
    sql += ";\n-- 订单结算口径对照\n" + settlement_sql
    available = tuple(m for m, v in values.items() if "revenue" in v)
    for month in available:
        v = values[month]
        if set(BY_CODE) - set(v):
            raise InputNeeded(f"{month}缺少财报项目，不能用0替代缺失值。")
        if (
            v["total_assets"] != v["liabilities_equity"]
            or v["net_profit"] != v["pretax_profit"] - v["income_tax"]
            or v["cash"] != v["closing_cash"]
            or v["closing_cash"] != v["opening_cash"] + v["cash_change"]
            or v["cash_change"] != v["cfo"] + v["cfi"] + v["cff"]
            or v["current_assets"]
            != sum(v[c] for c in ("cash", "receivables", "inventory", "prepaid"))
            or v["current_liabilities"]
            != sum(v[c] for c in ("payables", "taxes_payable", "short_debt"))
            or v["selling_expense"]
            != sum(v[c] for c in ("marketing", "fulfillment", "selling_payroll", "payment_fee"))
            or v["admin_expense"]
            != sum(v[c] for c in ("admin_payroll", "rent", "depreciation", "admin_other"))
            or v["revenue"] != v.get("gross_sales", 0) - v.get("refunds", 0)
        ):
            raise InputNeeded(f"{month}报表勾稽未通过，暂停输出财务结论。")
    return values, available, sql


def amount(values, code, months):
    if code in EXTRA:
        return sum(values[m][code] for m in months) / 100
    if code in DERIVED:
        return sum(amount(values, child, months) for child in DERIVED[code][1])
    if code == "quick_assets":
        return amount(values, "cash", months) + amount(values, "receivables", months)
    if code in RATIOS:
        _, numerator, denominator, unit, _ = RATIOS[code]
        n, d = amount(values, numerator, months), amount(values, denominator, months)
        return None if d == 0 else n / d * (100 if unit == "%" else 1)
    account = BY_CODE[code]
    if account.kind == "stock":
        return values[max(months)][code] / 100
    if account.kind == "opening":
        return values[min(months)][code] / 100
    return sum(values[m][code] for m in months) / 100


def _recognize(q):
    candidates = []
    q = q.replace("营业总收入", "营业收入").replace("总收入", "营业收入")
    q = q.replace("库存金额", "存货").replace("库存价值", "存货")
    for key, (name, _) in DERIVED.items():
        candidates.append((name, key, name, "元"))
    for key, (name, aliases) in EXTRA.items():
        for alias in (name,) + aliases:
            candidates.append((alias, key, name, "元"))
    for key, (name, _, _, unit, aliases) in RATIOS.items():
        for alias in (name,) + aliases:
            candidates.append((alias, key, name, unit))
    for a in ACCOUNTS:
        for alias in (a.label,) + a.aliases:
            candidates.append((alias, a.code, a.label, "元"))
    remaining, found = q, []
    for alias, code, name, unit in sorted(candidates, key=lambda x: len(x[0]), reverse=True):
        if alias in remaining:
            remaining = remaining.replace(alias, "")
            if code not in [r[0] for r in found]:
                found.append((code, name, unit))
    return found


def _finance_answer(conn, q, reference_date):
    values, available, sql = _values(conn)
    if re.search(r"周转|收益率|ROE|ROA|保障倍数|利息覆盖", q, re.IGNORECASE):
        raise InputNeeded(
            "该复合指标尚未定义计算口径，不能用余额金额替代。当前支持毛利率、净利率、"
            "费用率、资产负债率、流动比率和速动比率；周转类指标需要另行定义平均余额和年化口径。"
        )
    if any(
        t in q
        for t in (
            "线上",
            "线下",
            "渠道",
            "品类",
            "会员",
            "城市",
            "区域",
            "地区",
            "华东",
            "华南",
            "各客户",
        )
    ) or any(city in q for city in semantic_layer._CITIES):
        raise InputNeeded(
            "财报是全公司口径，未将管理费等分摊给渠道、城市或品类，"
            "不能据此计算分组净利润。销售额分组请直接问渠道或品类销售额。"
        )
    if re.search(r"排除|不含|除了|以外", q):
        raise InputNeeded("尚不支持财务项目的自然语言排除式组合，请明确要查询的项目。")
    if re.search(r"存货.*(?:多少件|数量|几件)|(?:多少件|几件).*存货", q):
        raise InputNeeded("财报存货是账面金额，不是库存件数；当前没有SKU库存数量，请查询存货金额。")
    cash_profit = (
        "净利润" in q
        and ("现金流" in q or "经营现金" in q)
        and re.search(r"为什么|为何|差|不同|一致|关系|对比|比较", q)
    )
    overview = any(t in q for t in ("财务概况", "经营情况", "财务分析", "经营分析"))
    multiple_months = len(re.findall(r"\d{1,2}月|20\d{2}[-/]\d{1,2}", q)) > 1
    multiple_years = len(re.findall(r"20\d{2}年|今年|去年|前年", q)) > 1 and "月" not in q
    separate = bool(
        re.search(r"分别|各多少|各是多少|各为", q) and (multiple_months or multiple_years)
    )
    revenue_bridge = (
        "营业收入" in q
        and ("销售额" in q or "成交额" in q)
        and re.search(r"为什么|为何|差|不等|不同|一致|关系", q)
    )
    compare = bool(re.search(COMPARISON, q) or separate) and not cash_profit and not revenue_bridge
    if overview:
        compare = True
    trend = bool(re.search(r"每月|按月|趋势|逐月", q))
    selection_q = q
    if trend and not re.search(r"20\d{2}|今年|去年|本月|上月|最近|近\d", q):
        selection_q += "最近12个月"
    p = resolve(
        selection_q, available, reference_date=reference_date, compare=compare and not trend
    )
    notes = list(p.notes)
    notes.append(
        f"全公司口径；本期{label(p.current)}"
        + (f"，比较期{label(p.previous)}" if p.previous else "")
    )
    notes.append("财报营业收入已扣模拟退款；订单取数的销售额为退款前成交额，两者口径不同。")
    scale, currency = (10000, "万元") if "万元" in q else (1, "元")
    if "亿元" in q:
        scale, currency = 100000000, "亿元"
    requested_report = next((key for key, name in REPORT_NAMES.items() if name in q), None)
    if "损益表" in q:
        requested_report = "income"
    if requested_report or "财务报表" in q or "三大报表" in q:
        reports = [requested_report] if requested_report else list(REPORT_NAMES)
        tables = []
        for report in reports:
            rows = []
            for a in ACCOUNTS:
                if a.report != report:
                    continue
                row = {"项目": ("    其中：" if a.parent else "") + a.label}
                if report == "balance":
                    opening = f"{p.current[-1][:4]}-01"
                    opening = shift(opening, -1)
                    row["期末余额"] = amount(values, a.code, p.current) / scale
                    row["年初余额"] = values[opening][a.code] / 100 / scale
                    if p.previous:
                        row["比较期末余额"] = amount(values, a.code, p.previous) / scale
                else:
                    row["本期金额"] = amount(values, a.code, p.current) / scale
                    ytd = tuple(
                        m for m in available if m[:4] == p.current[-1][:4] and m <= p.current[-1]
                    )
                    row["本年累计/期末值"] = amount(values, a.code, ytd) / scale
                    prior = tuple(shift(m, -12) for m in p.current)
                    row["上年同期金额"] = (
                        amount(values, a.code, prior) / scale
                        if all(m in available for m in prior)
                        else None
                    )
                    if p.previous:
                        row["比较期金额"] = amount(values, a.code, p.previous) / scale
                rows.append(row)
            tables.append(
                {
                    "title": REPORT_NAMES[report] + f"（{currency}）",
                    "df": pd.DataFrame(rows).round(2),
                }
            )
        notes.append(
            "余额项目取期末、期初项目取期初，流量项目按期间累计；缩进明细与小计不可重复相加。"
        )
        insight = f"已列出{label(p.current)}的" + "、".join(REPORT_NAMES[r] for r in reports) + "。"
        return _ready("financial_statements", tables, notes, sql, insight)
    if cash_profit:
        return _cash_profit_bridge(values, p, notes, sql)
    if revenue_bridge:
        gross = amount(values, "gross_sales", p.current)
        refunds = amount(values, "refunds", p.current)
        revenue = amount(values, "revenue", p.current)
        frame = pd.DataFrame(
            [
                {"项目": "退款前成交额", "金额（元）": gross},
                {"项目": "减：模拟退款金额", "金额（元）": refunds},
                {"项目": "等于：财报营业收入", "金额（元）": revenue},
            ]
        )
        return _ready(
            "revenue_reconciliation",
            [{"title": "成交额与营业收入衔接", "df": frame}],
            notes,
            sql,
            "本模型的订单销售额为退款前成交额，财报营业收入扣除了模拟退款；"
            f"{gross:,.2f} - {refunds:,.2f} = {revenue:,.2f}元。",
        )
    metrics = _recognize(q)
    if overview:
        metrics = [
            (c, n, u)
            for c, n, u in [
                ("revenue", "营业收入", "元"),
                ("net_profit", "净利润", "元"),
                ("cfo", "经营现金流", "元"),
                ("debt_ratio", "资产负债率", "%"),
                ("current_ratio", "流动比率", "倍"),
            ]
        ]
    if "费用结构" in q:
        metrics = [
            (c, BY_CODE[c].label, "元")
            for c in ("selling_expense", "admin_expense", "rd_expense", "finance_expense")
        ]
        notes.append("期间费用结构仅含销售、管理、研发、财务费用，不含营业成本及所得税。")
    if not metrics:
        raise InputNeeded(
            "请明确财务项目，如净利润、营业收入、应收账款、存货金额、经营现金流、"
            "资产负债率，或直接查询利润表/资产负债表/现金流量表。"
        )
    if len(metrics) > 8:
        raise InputNeeded("单次请查询不超过8个指标；更多项目请直接查看完整报表。")
    if trend:
        rows = []
        for month in p.current:
            row = {"月份": month}
            for code, name, unit in metrics:
                value = amount(values, code, (month,))
                row[name + f"（{currency if unit == '元' else unit}）"] = (
                    value / scale if unit == "元" and value is not None else value
                )
            rows.append(row)
        tables = [{"title": "月度趋势", "df": pd.DataFrame(rows).round(2), "chart": "line"}]
        return _ready(
            "financial_trend",
            tables,
            notes,
            sql,
            f"展示{label(p.current)}的月度记录；余额不跨月相加，比率逐月重新计算。",
        )
    rows, headlines = [], []
    for code, name, unit in metrics:
        current = amount(values, code, p.current)
        display_unit = currency if unit == "元" else unit
        factor = scale if unit == "元" else 1
        row = {
            "指标": name,
            "单位": display_unit,
            "本期值": current / factor if current is not None else None,
        }
        if p.previous:
            previous = amount(values, code, p.previous)
            change = current - previous if current is not None and previous is not None else None
            row["比较期值"] = previous / factor if previous is not None else None
            row["变化值"] = change / factor if change is not None else None
            row["变化单位"] = "百分点" if unit == "%" else display_unit
            row["变动比例（%）"] = (
                change / previous * 100
                if unit == "元" and previous is not None and previous > 0
                else None
            )
            if change is not None:
                direction = "增加" if change > 0 else "减少" if change < 0 else "不变"
                headlines.append(f"{name}{direction}{abs(change) / factor:,.2f}{row['变化单位']}")
        else:
            headlines.append(
                f"{name}为{current / factor:,.2f}{display_unit}"
                if current is not None
                else f"{name}分母为0，不能计算"
            )
        rows.append(row)
    tables = [
        {"title": "财务指标" + ("对比" if p.previous else ""), "df": pd.DataFrame(rows).round(2)}
    ]
    if p.previous and re.search(DIAGNOSIS, q):
        if any(code == "net_profit" for code, _, _ in metrics):
            bridge = _profit_bridge(values, p)
            tables.append(
                {
                    "title": "净利润变化贡献（元）",
                    "df": bridge,
                    "chart": "bar",
                    "x": "项目",
                    "y": "对净利润变化的贡献",
                }
            )
            largest = bridge.iloc[bridge["对净利润变化的贡献"].abs().argmax()]
            headlines.append(f"净利润变化中，{largest['项目']}的数值贡献绝对值最大")
        else:
            notes.append("当前展示对应项目的期间变化；更多经营原因需要销售明细或外部证据。")
    if any(code in {"current_ratio", "quick_ratio"} for code, _, _ in metrics):
        notes.append("速动比率口径=(货币资金+应收账款)/流动负债；流动比率=流动资产/流动负债。")
    notes.append("比例的分母为0时留空；比较期金额非正时不展示普通增长百分比。")
    return _ready("financial_analysis", tables, notes, sql, "；".join(headlines) + "。")


def _profit_bridge(values, p):
    signs = {
        "revenue": 1,
        "cogs": -1,
        "selling_expense": -1,
        "admin_expense": -1,
        "rd_expense": -1,
        "finance_expense": -1,
        "surcharges": -1,
        "other_income": 1,
        "investment_income": 1,
        "impairment": -1,
        "nonoperating_income": 1,
        "nonoperating_expense": -1,
        "income_tax": -1,
    }
    rows = [
        {
            "项目": BY_CODE[c].label,
            "本期金额": amount(values, c, p.current),
            "比较期金额": amount(values, c, p.previous),
            "对净利润变化的贡献": round(
                s * (amount(values, c, p.current) - amount(values, c, p.previous)), 2
            ),
        }
        for c, s in signs.items()
    ]
    df = pd.DataFrame(rows)
    expected = amount(values, "net_profit", p.current) - amount(values, "net_profit", p.previous)
    if abs(df["对净利润变化的贡献"].sum() - expected) > 0.011:
        raise InputNeeded("净利润变化贡献未能对齐总变化，暂停分析。")
    return df.round(2)


def _cash_profit_bridge(values, p, notes, sql):
    opening_month = shift(min(p.current), -1)
    if not all(shift(a, 1) == b for a, b in zip(p.current, p.current[1:])):
        raise InputNeeded("利润与现金流衔接需要连续月份。")
    if opening_month not in values:
        raise InputNeeded("缺少期初资产负债记录，不能计算现金流衔接。")
    current, opening = values[max(p.current)], values[opening_month]
    rows = [
        {"调整项目": "净利润", "金额（元）": amount(values, "net_profit", p.current)},
        {"调整项目": "加：非现金折旧", "金额（元）": amount(values, "depreciation", p.current)},
        {
            "调整项目": "加：列入筹资现金流的利息",
            "金额（元）": amount(values, "finance_expense", p.current),
        },
    ]
    for code, sign, name in [
        ("receivables", -1, "应收账款变动"),
        ("inventory", -1, "存货变动"),
        ("payables", 1, "应付账款变动"),
        ("taxes_payable", 1, "应交税费变动"),
    ]:
        rows.append({"调整项目": name, "金额（元）": sign * (current[code] - opening[code]) / 100})
    cfo = amount(values, "cfo", p.current)
    if abs(sum(r["金额（元）"] for r in rows) - cfo) > 0.011:
        raise InputNeeded("利润与现金流衔接不一致，暂停输出。")
    rows.append({"调整项目": "等于：经营活动现金流量净额", "金额（元）": cfo})
    notes.append("此桥接采用模拟企业的简化科目，金额正负表示对经营现金流的增加或减少。")
    return _ready(
        "cash_profit_bridge",
        [{"title": "净利润到经营现金流的衔接", "df": pd.DataFrame(rows)}],
        notes,
        sql,
        "净利润与经营现金流不同，表中用非现金费用和营运资金变化逐项对齐；"
        "应收账款增加、存货增加会在本模型中占用现金。",
    )


def _sales_answer(conn, q, reference_date):
    available = [
        r[0] for r in conn.execute("SELECT DISTINCT substr(order_date,1,7) FROM orders ORDER BY 1")
    ]
    p = resolve(q, available, reference_date=reference_date, compare=True)
    if re.search(r"排除|不含|除了|以外|库存|净利润|转化率", q):
        raise InputNeeded("当前销售变化拆解支持明确的包含条件，不混用库存、净利润或转化率口径。")
    conditions, parameters, notes = [], [], list(p.notes)
    if ("线上" in q) != ("线下" in q):
        channel = "线上" if "线上" in q else "线下"
        conditions.append("o.channel=?")
        parameters.append(channel)
        notes.append("渠道=" + channel)
    for phrase, column, allowed in [
        ("城市", "c.city", semantic_layer._CITIES),
        ("品类", "p.category", semantic_layer._CATEGORIES),
    ]:
        selected = [x for x in allowed if x in q]
        if selected:
            conditions.append(column + " IN (" + ",".join("?" for _ in selected) + ")")
            parameters.extend(selected)
            notes.append(phrase + "=" + "、".join(selected))
    if "vip" in q.lower():
        conditions.append("c.segment='VIP'")
        notes.append("会员=VIP")

    def query(months, dimension=None):
        dims = {"渠道": "o.channel", "品类": "p.category"}
        prefix = (dims[dimension] + " AS 分组,") if dimension else ""
        sql = (
            "SELECT "
            + prefix
            + "SUM(CAST(ROUND(i.quantity*i.unit_price*100) AS INTEGER)) AS sales,"
            "COUNT(DISTINCT o.order_id) AS orders,SUM(i.quantity) AS units FROM orders o "
            "JOIN order_items i ON o.order_id=i.order_id "
            "JOIN products p ON i.product_id=p.product_id "
            "JOIN customers c ON o.customer_id=c.customer_id WHERE substr(o.order_date,1,7) IN ("
            + ",".join("?" for _ in months)
            + ")"
        )
        if conditions:
            sql += " AND " + " AND ".join(conditions)
        if dimension:
            sql += " GROUP BY " + dims[dimension]
        params = list(months) + parameters
        cursor = conn.execute(sql, params)
        df = pd.DataFrame(cursor.fetchall(), columns=[x[0] for x in cursor.description])
        display_sql = sql
        for value in params:
            display_sql = display_sql.replace("?", "'" + value.replace("'", "''") + "'", 1)
        return df, display_sql

    current, sql = query(p.current)
    previous, old_sql = query(p.previous)
    summaries = []
    for frame, months in ((previous, p.previous), (current, p.current)):
        row = frame.iloc[0]
        if pd.isna(row["sales"]) or not row["orders"]:
            raise InputNeeded(f"{label(months)}所选范围没有订单，无法稳定拆解客单价与订单数。")
        summaries.append(
            {
                "期间": label(months),
                "销售额（元）": row["sales"] / 100,
                "订单数": int(row["orders"]),
                "客单价（元）": row["sales"] / 100 / row["orders"],
                "销量": int(row["units"]),
            }
        )
    base, now = summaries
    delta = now["销售额（元）"] - base["销售额（元）"]
    order_effect = (
        (now["订单数"] - base["订单数"]) * (now["客单价（元）"] + base["客单价（元）"]) / 2
    )
    basket_effect = (
        (now["客单价（元）"] - base["客单价（元）"]) * (now["订单数"] + base["订单数"]) / 2
    )
    decomposition = pd.DataFrame(
        [
            {"因素": "订单数变化", "对销售额变化的贡献（元）": round(order_effect, 2)},
            {"因素": "客单价变化", "对销售额变化的贡献（元）": round(basket_effect, 2)},
            {
                "因素": "舍入差额",
                "对销售额变化的贡献（元）": round(
                    delta - round(order_effect, 2) - round(basket_effect, 2), 2
                ),
            },
        ]
    )
    tables = [
        {"title": "两期经营指标", "df": pd.DataFrame(summaries).round(2)},
        {"title": "订单数与客单价拆解", "df": decomposition},
    ]
    group_headlines = []
    for dimension in ("渠道", "品类"):
        a, _ = query(p.current, dimension)
        b, _ = query(p.previous, dimension)
        amounts_a = dict(zip(a["分组"], a["sales"]))
        amounts_b = dict(zip(b["分组"], b["sales"]))
        rows = []
        for group in sorted(set(amounts_a) | set(amounts_b)):
            difference = (amounts_a.get(group, 0) - amounts_b.get(group, 0)) / 100
            rows.append(
                {
                    dimension: group,
                    "本期销售额": amounts_a.get(group, 0) / 100,
                    "比较期销售额": amounts_b.get(group, 0) / 100,
                    "销售额变化（元）": difference,
                    "占总变化（%）": difference / delta * 100 if abs(delta) > 0.001 else None,
                }
            )
        frame = pd.DataFrame(rows).round(2)
        if abs(frame["销售额变化（元）"].sum() - delta) > 0.011:
            raise InputNeeded("分组变化与总体变化未对齐，暂停输出。")
        tables.append(
            {
                "title": dimension + "变化贡献",
                "df": frame,
                "chart": "bar",
                "x": dimension,
                "y": "销售额变化（元）",
            }
        )
        group_headlines.append(str(frame.iloc[frame["销售额变化（元）"].abs().argmax()][dimension]))
    direction = "上升" if delta > 0 else "下降" if delta < 0 else "持平"
    insight = (
        f"{label(p.current)}较{label(p.previous)}销售额{direction}"
        f"{abs(delta):,.2f}元（{delta / base['销售额（元）'] * 100:+.2f}%）。"
        f"订单数从{base['订单数']}变为{now['订单数']}，客单价从{base['客单价（元）']:,.2f}"
        f"变为{now['客单价（元）']:,.2f}元。渠道中{group_headlines[0]}、品类中"
        f"{group_headlines[1]}的变化贡献绝对值最大。"
    )
    notes += [
        f"本期{label(p.current)}；比较期{label(p.previous)}；销售额为退款前成交额。",
        "订单数/客单价采用对称分解，两项贡献与总变化相加对齐；不证明促销或竞品等真实原因。",
        "渠道和品类是两种切面，各自对齐总体变化，不能把两张表的贡献再相加。",
        "相反方向的抵消可能让贡献占比为负或超过100%；总变化为0时占比留空。",
    ]
    return _ready(
        "sales_diagnosis",
        tables,
        notes,
        sql + ";\n-- 比较期只读查询\n" + old_sql,
        insight,
        filters=tuple(conditions),
    )


def _ready(intent, tables, notes, sql, insight, filters=()):
    plan = SemanticPlan(
        "ready",
        intent,
        filters=filters,
        reason="按结构化数据与明确期间计算",
        assumptions=tuple(notes),
    )
    return BusinessAnswer(plan, tables[0]["df"], sql, insight, tables, notes)


def statement_preview(month, report, db_path=None):
    """Use the same financial query as natural-language questions, not a second set of formulas."""
    year, mon = month.split("-")
    return try_answer(f"{year}年{int(mon)}月{REPORT_NAMES[report]}", db_path=db_path)


def assumptions(db_path=None):
    return json.loads(metadata(db_path).get("assumptions", "[]"))
