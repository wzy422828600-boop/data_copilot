"""Teaching-company statement dictionary. Amounts are integer RMB cents.

Leaf lines and subtotals must never be summed together. This is a simplified
management demo, not a claim of compliance with every reporting standard.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Account:
    code: str
    label: str
    report: str
    kind: str = "flow"
    parent: str = ""
    aliases: tuple[str, ...] = ()


ACCOUNTS = [
    Account("revenue", "营业收入", "income", aliases=("收入", "营收")),
    Account("cogs", "营业成本", "income", aliases=("销售成本", "商品成本")),
    Account("gross_profit", "毛利", "income", aliases=("毛利润",)),
    Account("surcharges", "税金及附加", "income"),
    Account("selling_expense", "销售费用", "income"),
    Account(
        "marketing",
        "广告推广费",
        "income",
        parent="selling_expense",
        aliases=("营销费", "广告费", "推广费"),
    ),
    Account(
        "fulfillment",
        "仓储物流费",
        "income",
        parent="selling_expense",
        aliases=("物流费", "履约费", "运费"),
    ),
    Account("selling_payroll", "销售人员薪酬", "income", parent="selling_expense"),
    Account("payment_fee", "支付平台手续费", "income", parent="selling_expense"),
    Account("admin_expense", "管理费用", "income"),
    Account("admin_payroll", "管理人员薪酬", "income", parent="admin_expense"),
    Account("rent", "办公租金", "income", parent="admin_expense", aliases=("租金",)),
    Account("depreciation", "折旧费", "income", parent="admin_expense", aliases=("折旧",)),
    Account("admin_other", "其他管理费用", "income", parent="admin_expense"),
    Account("rd_expense", "研发费用", "income"),
    Account("finance_expense", "财务费用", "income", aliases=("利息费用",)),
    Account("other_income", "其他收益", "income"),
    Account("investment_income", "投资收益", "income"),
    Account("impairment", "资产减值损失", "income"),
    Account("operating_profit", "营业利润", "income"),
    Account("nonoperating_income", "营业外收入", "income"),
    Account("nonoperating_expense", "营业外支出", "income"),
    Account("pretax_profit", "利润总额", "income", aliases=("税前利润",)),
    Account("income_tax", "所得税费用", "income", aliases=("所得税",)),
    Account("net_profit", "净利润", "income", aliases=("税后利润",)),
    Account("cash", "货币资金", "balance", "stock", aliases=("现金余额",)),
    Account("receivables", "应收账款", "balance", "stock"),
    Account("inventory", "存货", "balance", "stock", aliases=("存货金额", "账面库存金额")),
    Account("prepaid", "预付款项", "balance", "stock"),
    Account("current_assets", "流动资产合计", "balance", "stock", aliases=("流动资产",)),
    Account("fixed_assets", "固定资产净额", "balance", "stock", aliases=("固定资产",)),
    Account("noncurrent_assets", "非流动资产合计", "balance", "stock"),
    Account("total_assets", "资产总计", "balance", "stock", aliases=("总资产", "资产总额")),
    Account("payables", "应付账款", "balance", "stock"),
    Account("taxes_payable", "应交税费", "balance", "stock"),
    Account("short_debt", "短期借款", "balance", "stock"),
    Account("current_liabilities", "流动负债合计", "balance", "stock", aliases=("流动负债",)),
    Account("long_debt", "长期借款", "balance", "stock"),
    Account("noncurrent_liabilities", "非流动负债合计", "balance", "stock"),
    Account("total_liabilities", "负债合计", "balance", "stock", aliases=("总负债",)),
    Account("capital", "实收资本", "balance", "stock"),
    Account("retained_earnings", "未分配利润", "balance", "stock"),
    Account("total_equity", "所有者权益合计", "balance", "stock", aliases=("净资产", "所有者权益")),
    Account("liabilities_equity", "负债和所有者权益总计", "balance", "stock"),
    Account(
        "customer_receipts", "销售商品收到的现金", "cashflow", aliases=("销售回款", "客户回款")
    ),
    Account("supplier_payments", "购买商品支付的现金", "cashflow"),
    Account("employee_payments", "支付给职工的现金", "cashflow"),
    Account("tax_payments", "支付的各项税费", "cashflow"),
    Account("other_operating_payments", "支付其他与经营活动有关的现金", "cashflow"),
    Account(
        "cfo",
        "经营活动现金流量净额",
        "cashflow",
        aliases=("经营现金流", "经营性现金流", "经营活动现金流", "经营净现金流"),
    ),
    Account("capex", "购建固定资产支付的现金", "cashflow", aliases=("资本开支",)),
    Account("cfi", "投资活动现金流量净额", "cashflow", aliases=("投资现金流",)),
    Account("borrowing_received", "取得借款收到的现金", "cashflow"),
    Account("debt_repayments", "偿还债务支付的现金", "cashflow"),
    Account("dividends", "分配股利支付的现金", "cashflow", aliases=("分红",)),
    Account("interest_paid", "偿付利息支付的现金", "cashflow"),
    Account("cff", "筹资活动现金流量净额", "cashflow", aliases=("筹资现金流",)),
    Account(
        "cash_change",
        "现金及现金等价物净增加额",
        "cashflow",
        aliases=("现金净增加额", "现金净变动"),
    ),
    Account("opening_cash", "期初现金及现金等价物余额", "cashflow", "opening"),
    Account("closing_cash", "期末现金及现金等价物余额", "cashflow", "stock", aliases=("期末现金",)),
    Account("opening_retained", "期初未分配利润", "equity", "opening"),
    Account("equity_net_profit", "本期净利润", "equity"),
    Account("equity_dividends", "本期分红", "equity"),
    Account("closing_retained", "期末未分配利润", "equity", "stock"),
]
BY_CODE = {a.code: a for a in ACCOUNTS}
REPORT_NAMES = {
    "income": "利润表",
    "balance": "资产负债表",
    "cashflow": "现金流量表",
    "equity": "权益变动表",
}
RATIOS = {
    "gross_margin": ("毛利率", "gross_profit", "revenue", "%", ()),
    "net_margin": ("净利率", "net_profit", "revenue", "%", ("净利润率",)),
    "selling_ratio": ("销售费用率", "selling_expense", "revenue", "%", ()),
    "admin_ratio": ("管理费用率", "admin_expense", "revenue", "%", ()),
    "debt_ratio": ("资产负债率", "total_liabilities", "total_assets", "%", ("负债率",)),
    "current_ratio": ("流动比率", "current_assets", "current_liabilities", "倍", ()),
    "quick_ratio": ("速动比率", "quick_assets", "current_liabilities", "倍", ()),
}
