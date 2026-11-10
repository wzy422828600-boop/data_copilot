"""轻量业务语义层：把业务问法映射为统一指标、维度、筛选与 SQL。

它有两个作用：
1. 在线模式把明确的指标口径作为上下文交给 LLM，降低“同问不同算”的风险；
2. 离线模式把常见经营问题编译为可执行 SQL，作为可审计、零成本的可靠兜底。

这不是通用自然语言解析器。它只覆盖本项目声明过的零售分析领域，无法确认时会
降低置信度或明确返回“无法回答”，而不是伪造字段。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    definition: str
    unit: str


@dataclass(frozen=True)
class DimensionDefinition:
    key: str
    label: str
    aliases: tuple[str, ...]
    column: str


@dataclass(frozen=True)
class SemanticPlan:
    status: str
    intent: str
    metrics: tuple[str, ...] = ()
    dimensions: tuple[str, ...] = ()
    filters: tuple[str, ...] = ()
    time_grain: str | None = None
    top_n: int | None = None
    confidence: float = 1.0
    reason: str = ""
    assumptions: tuple[str, ...] = ()
    sort_order: str = "DESC"

    def as_dict(self) -> dict:
        return asdict(self)


METRICS: dict[str, MetricDefinition] = {
    "revenue": MetricDefinition(
        "revenue",
        "销售额",
        ("销售额", "gmv", "成交额", "营收"),
        "SUM(order_items.quantity * order_items.unit_price)",
        "元",
    ),
    "order_count": MetricDefinition(
        "order_count",
        "订单数",
        ("订单数", "订单量", "多少笔订单", "多少单"),
        "COUNT(DISTINCT orders.order_id)",
        "笔",
    ),
    "customer_count": MetricDefinition(
        "customer_count",
        "顾客数",
        ("顾客数", "客户数", "用户数", "多少个顾客"),
        "COUNT(DISTINCT customers.customer_id)",
        "人",
    ),
    "quantity": MetricDefinition(
        "quantity",
        "销量",
        ("销量", "销售量", "卖出数量"),
        "SUM(order_items.quantity)",
        "件",
    ),
    "aov": MetricDefinition(
        "aov",
        "客单价",
        ("客单价", "aov"),
        "销售额 / 去重订单数",
        "元/笔",
    ),
    "margin": MetricDefinition(
        "margin",
        "毛利",
        ("毛利", "利润", "margin"),
        "SUM(quantity * (unit_price - product.cost))",
        "元",
    ),
    "margin_rate": MetricDefinition(
        "margin_rate",
        "毛利率",
        ("毛利率", "margin rate"),
        "毛利 / 销售额 * 100%",
        "%",
    ),
    "growth_rate": MetricDefinition(
        "growth_rate",
        "环比增长率",
        ("环比", "增长率"),
        "(本期销售额 - 上期销售额) / 上期销售额 * 100%",
        "%",
    ),
    "repurchase_customers": MetricDefinition(
        "repurchase_customers",
        "复购顾客数",
        ("复购", "不止一单"),
        "下单次数大于 1 的去重顾客数",
        "人",
    ),
    "orders_per_customer": MetricDefinition(
        "orders_per_customer",
        "人均订单数",
        ("平均每个顾客", "人均订单数"),
        "订单数 / 去重下单顾客数",
        "笔/人",
    ),
}


DIMENSIONS: dict[str, DimensionDefinition] = {
    "category": DimensionDefinition(
        "category", "品类", ("品类", "类目", "category"), "products.category"
    ),
    "product": DimensionDefinition("product", "产品", ("产品", "商品", "sku"), "products.name"),
    "channel": DimensionDefinition(
        "channel", "渠道", ("渠道", "线上", "线下", "channel"), "orders.channel"
    ),
    "city": DimensionDefinition("city", "城市", ("城市", "地区", "区域", "city"), "customers.city"),
    "segment": DimensionDefinition(
        "segment", "会员等级", ("会员等级", "会员", "segment"), "customers.segment"
    ),
    "month": DimensionDefinition(
        "month", "月份", ("每月", "每个月", "月度", "按月"), "orders.order_date"
    ),
}


_DANGEROUS_NL = re.compile(
    r"删除|清空|删库|修改|改成|设为|更新.*(?:为|成)|写入|插入|drop\s+table|"
    r"delete\s+from|update\s+\w+\s+set|insert\s+into|attach\s+database",
    re.IGNORECASE,
)
_UNAVAILABLE_FIELDS = {
    "手机号": "数据库没有手机号字段",
    "电话": "数据库没有联系电话字段",
    "邮箱": "数据库没有邮箱字段",
    "库存": "数据库没有库存字段",
    "退货": "数据库没有退货记录",
    "退款": "数据库没有退款记录",
    "转化率": "数据库没有曝光、浏览或转化漏斗数据",
}
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "十": 10}


def analyze_question(question: str, *, reference_date: date | None = None) -> SemanticPlan:
    """把问题解析为可展示、可审计的业务分析计划。"""
    q = question.strip()
    ql = q.lower()
    if not q:
        return SemanticPlan("clarify", "unknown", confidence=0.0, reason="问题为空")
    if _DANGEROUS_NL.search(q):
        return SemanticPlan(
            "blocked",
            "write_request",
            confidence=1.0,
            reason="检测到修改或删除数据的意图；分析助手仅允许只读查询",
        )
    for field, reason in _UNAVAILABLE_FIELDS.items():
        if field in q:
            return SemanticPlan(
                "unanswerable",
                "missing_field",
                confidence=1.0,
                reason=reason,
            )

    if re.search(r"为什么|原因|归因|导致|因果|怎么办|怎么提升|如何提升|预测|预计", q):
        return SemanticPlan(
            "clarify",
            "diagnosis",
            confidence=0.0,
            reason="当前数据只能描述销售表现，不能直接确定原因或预测。"
            "请先明确要比较的时间、指标和维度，例如：2024年各月销售额。",
        )
    if "利润" in q and "毛利" not in q:
        return SemanticPlan(
            "clarify",
            "metric_ambiguity",
            confidence=0.0,
            reason="请明确毛利、营业利润还是净利润，它们使用不同口径；例如：2026年10月净利润。",
        )

    metrics = _detect_metrics(q, ql)
    dimensions = _detect_dimensions(q, ql)
    filters, time_error, assumptions = _detect_filters(q, reference_date=reference_date)
    if time_error:
        return SemanticPlan("clarify", "unsupported_condition", confidence=0.0, reason=time_error)
    top_n = _detect_top_n(q)
    time_grain = "month" if "month" in dimensions else None

    if "环比" in q or ("相比" in q and "增长" in q):
        if dimensions or any(m not in {"revenue"} for m in metrics):
            return SemanticPlan(
                "clarify",
                "unsupported_combination",
                reason="当前环比仅支持整体或筛选后的销售额，请单独查询，不混用其他指标或分组。",
            )
        intent = "period_growth"
        metrics = ("growth_rate",)
    elif "高于平均" in q and "品类" in q:
        if metrics != ("revenue",):
            return SemanticPlan(
                "clarify",
                "unsupported_combination",
                reason="当前高于平均分析仅支持品类销售额，请明确此指标。",
            )
        intent = "above_average"
        metrics = ("revenue",)
        dimensions = ("category",)
    elif top_n or any(k in q for k in ("最高", "最多", "最低", "最少", "排名", "top", "Top")):
        intent = "ranking"
    elif dimensions:
        intent = "breakdown"
    else:
        intent = "kpi"

    if intent == "period_growth":
        months = [f.split("=", 1)[1] for f in filters if f.startswith("月份=")]
        if not months or len(months) > 2:
            return SemanticPlan(
                "clarify",
                "period_growth",
                confidence=0.0,
                reason="请提供单月环比的明确年月，例如2024年12月销售额环比。"
                "当前不自动猜测比较期，也不支持整段月度环比序列。",
            )
        if len(months) == 2 and months[0] == months[1]:
            return SemanticPlan("clarify", "period_growth", reason="比较月份相同，请指定不同月份。")
    special = {"repurchase_customers", "orders_per_customer"}
    if special.intersection(metrics) and (dimensions or len(metrics) > 1):
        return SemanticPlan(
            "clarify",
            "unsupported_combination",
            confidence=0.0,
            reason="当前复购顾客数和人均订单数只支持整体或筛选后统计，请单独查询，不与分组或其他指标混用。",
        )
    if "growth_rate" in metrics and intent != "period_growth":
        return SemanticPlan("clarify", "period_growth", reason="请指定环比的明确年月。")
    if "customer_count" in metrics and (
        len(metrics) > 1
        or any(f.startswith("品类=") for f in filters)
        or any(f.startswith(("年份=", "月份=", "季度=", "渠道=")) for f in filters)
        or any(d in dimensions for d in ("month", "channel", "category", "product"))
    ):
        return SemanticPlan(
            "clarify",
            "customer_scope",
            reason="顾客数默认指顾客表中的人数；"
            "交易时间或渠道下的下单顾客数是另一口径，当前请先查询订单数，不能混用。",
        )

    if not metrics:
        if not dimensions:
            return SemanticPlan(
                "clarify",
                "unknown",
                confidence=0.2,
                reason="未识别到可用的零售指标或分析维度，请补充想分析的指标",
            )
        return SemanticPlan(
            "clarify",
            "missing_metric",
            confidence=0.0,
            reason="已识别分析维度，但未确认指标。请明确销售额、销量、订单数或毛利率，系统不默认选择。",
        )
    else:
        confidence = 0.95
        reason = "已匹配受治理的业务指标与维度"

    return SemanticPlan(
        "ready",
        intent,
        metrics=metrics,
        dimensions=dimensions,
        filters=filters,
        time_grain=time_grain,
        top_n=top_n,
        confidence=confidence,
        reason=reason,
        assumptions=assumptions,
        sort_order="ASC" if re.search(r"最低|最少|倒数", q) else "DESC",
    )


def _detect_metrics(q: str, ql: str) -> tuple[str, ...]:
    if "复购" in q or "不止一单" in q:
        return ("repurchase_customers",)
    if "平均每个顾客" in q or "人均订单数" in q:
        return ("orders_per_customer",)
    found = []
    # Consume longer aliases before shorter ones; 毛利率 must not also request 毛利.
    remaining = ql
    for key in (
        "margin_rate",
        "aov",
        "quantity",
        "order_count",
        "customer_count",
        "revenue",
        "margin",
    ):
        aliases = sorted(METRICS[key].aliases, key=len, reverse=True)
        matched = [a for a in aliases if a.lower() in remaining]
        if matched:
            found.append(key)
            for alias in matched:
                remaining = remaining.replace(alias.lower(), "")
    # Keep revenue first for the conventional sales / orders combination.
    if "revenue" in found:
        found.remove("revenue")
        found.insert(0, "revenue")
    return tuple(found)


def _detect_dimensions(q: str, ql: str) -> tuple[str, ...]:
    found: list[str] = []
    for key, dim in DIMENSIONS.items():
        if any(alias.lower() in ql for alias in dim.aliases):
            found.append(key)

    # “VIP会员的总销售额”是筛选，不是按会员等级分组。
    if "vip" in ql and "各会员" not in q and "会员等级" not in q:
        found = [d for d in found if d != "segment"]
    # “女装品类里最高的产品”按产品分组，品类是筛选。
    if any(c in q for c in _CATEGORIES) and not re.search(r"各品类|按品类|分品类", q):
        found = [d for d in found if d != "category"]
    if ("线上" in q) != ("线下" in q):
        found = [d for d in found if d != "channel"]
    if any(c in q for c in _CITIES) and not re.search(r"各城市|按城市|分城市", q):
        found = [d for d in found if d != "city"]
    return tuple(dict.fromkeys(found))


_CATEGORIES = ("女装", "男装", "鞋靴", "运动户外", "配饰", "美妆")
_CITIES = ("北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安")


def _detect_filters(q: str, *, reference_date: date | None = None):
    filters: list[str] = []
    assumptions: list[str] = []
    if "vip" in q.lower():
        filters.append("会员等级=VIP")
    if any(s in q for s in ("普通会员", "金卡", "银卡")):
        return (), "当前单一会员等级筛选只支持VIP，请改用各会员等级分组查看，避免忽略会员条件。", ()
    for category in _CATEGORIES:
        if category in q:
            filters.append(f"品类={category}")
    for city in _CITIES:
        if city in q:
            filters.append(f"城市={city}")
    online, offline = "线上" in q, "线下" in q
    if re.search(r"排除|不含|不要|除了|以外", q):
        return (), "当前不能可靠解析排除条件，请改为明确的包含条件，例如：仅线上销售额。", ()
    if online != offline:
        filters.append("渠道=" + ("线上" if online else "线下"))

    ref = reference_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    if re.search(r"(?:19|20)\d{2}[-/]\d{1,2}[-/]\d{1,2}", q):
        return (), "当前不支持日级区间，请改为明确年月或季度。", ()
    if any(c in q for c in ("数码", "家居")):
        return (
            (),
            "演示数据没有该品类，请确认品类名称；支持女装、男装、鞋靴、配饰、美妆、运动户外。",
            (),
        )
    if re.search(r"同比|最近|近\d+|上季度|本季度|昨天|今天|本周|上周|上半年|下半年|\d日|\d号", q):
        return (), "当前不支持此时间表达，请改为明确年份、年月或季度；系统不会忽略该条件。", ()
    rewritten = re.sub(r"(?<!\d)((?:19|20)\d{2})[-/](\d{1,2})(?![\d/-])", r"\1年\2月", q)
    relative_year = None
    for token, year in (("今年", ref.year), ("去年", ref.year - 1)):
        if token in q:
            if relative_year is not None:
                return (), "存在多个相对年份，请改为明确年月分别查询。", ()
            relative_year = year
            rewritten = rewritten.replace(token, f"{year}年")
            assumptions.append(f"{token}按北京时间参考日{ref.isoformat()}解析为{year}年")
    for token, delta in (("本月", 0), ("上月", -1)):
        if token in q:
            first = ref.replace(day=1)
            if delta:
                from datetime import timedelta

                first = (first - timedelta(days=1)).replace(day=1)
            rewritten = rewritten.replace(token, f"{first.year}年{first.month}月")
            assumptions.append(f"{token}按参考日{ref.isoformat()}解析为{first:%Y-%m}")

    years = list(dict.fromkeys(int(y) for y in re.findall(r"(?<!\d)((?:19|20)\d{2})年", rewritten)))
    if re.search(r"\d{4}年?\s*(?:到|至|-)\s*\d{4}", rewritten):
        return (), "当前不支持年份区间，请分别查询明确年份，避免漏掉中间年份。", ()
    quarter = re.search(r"第?([一二三四1-4])季度", q)
    if quarter:
        if len(years) != 1:
            return (), "季度必须指定一个年份，例如：2024年第四季度销售额。", ()
        raw = quarter.group(1)
        num = int(raw) if raw.isdigit() else _CN_NUMBERS[raw]
        filters.append(f"年份={years[0]}")
        filters.append(f"季度=Q{num}")
    else:
        explicit = re.findall(r"((?:19|20)\d{2})年\s*(\d{1,2})月", rewritten)
        bare_months = re.findall(r"(?<!\d)(\d{1,2})月", rewritten)
        if any(not 1 <= int(m) <= 12 for m in bare_months):
            return (), "月份无效，请使用1至12月。", ()
        if re.search(r"\d\s*[-到至]\s*\d{1,2}月|\d月\s*(?:到|至|-)\s*\d", rewritten):
            return (), "当前月区间请拆成单月查询，不能忽略区间中间月份。", ()
        if bare_months:
            if not years:
                return (), "月份缺少年份，请补充例如2024年11月；不会默认使用演示数据年份。", ()
            if len(years) > 1 and len(explicit) != len(bare_months):
                return (), "跨年查询请为每个月补全年份。", ()
            if len(years) == 1:
                filters.extend(f"月份={years[0]}-{int(m):02d}" for m in bare_months)
            else:
                filters.extend(f"月份={int(y)}-{int(m):02d}" for y, m in explicit)
        elif years:
            filters.extend(f"年份={y}" for y in years)
    return tuple(dict.fromkeys(filters)), "", tuple(assumptions)


def _detect_top_n(q: str) -> int | None:
    m = re.search(r"(?:最高|最多|最低|最少|前|top\s*)的?\s*([1-9]\d*)\s*个", q, re.IGNORECASE)
    if not m:
        m = re.search(r"(?:最高|最多|最低|最少|排名|前)的?\s*([一二三四五十])\s*个", q)
    if m:
        raw = m.group(1)
        return int(raw) if raw.isdigit() else _CN_NUMBERS.get(raw)
    if "最高" in q and any(k in q.lower() for k in ("产品", "商品", "sku")):
        return 1
    return None


def prompt_context(plan: SemanticPlan) -> str:
    """只返回与当前问题相关的指标口径，减少 prompt 噪声和口径漂移。"""
    if plan.status != "ready":
        return f"语义层状态：{plan.status}；原因：{plan.reason}"
    metric_lines = [
        f"- {METRICS[key].label}：{METRICS[key].definition}；单位={METRICS[key].unit}"
        for key in plan.metrics
    ]
    dims = "、".join(DIMENSIONS[d].label for d in plan.dimensions) or "无（整体指标）"
    filters = "、".join(plan.filters) or "无"
    return (
        "\n业务语义层（必须遵守以下指标口径）：\n"
        + "\n".join(metric_lines)
        + f"\n分析维度：{dims}\n筛选条件：{filters}\n分析意图：{plan.intent}"
    )


def compile_sql(plan: SemanticPlan) -> str | None:
    """把受支持的语义计划编译为 SQLite SQL；无法编译时返回 None。"""
    if plan.status == "unanswerable":
        reason = plan.reason.replace("'", "''")
        return f"SELECT '无法回答：{reason}' AS note"
    if plan.status != "ready":
        return None

    if plan.intent == "period_growth":
        months = [f.split("=")[1] for f in plan.filters if f.startswith("月份=")]
        if len(months) >= 2:
            old, new = months[1], months[0]
        else:
            new = months[0]
            year, month = map(int, new.split("-"))
            old = f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
        # Apply non-time restrictions to both comparison periods. Never compare
        # online current-month sales with all-channel previous-month sales.
        base_filters = tuple(
            f for f in plan.filters if not f.startswith(("月份=", "年份=", "季度="))
        )
        parts = ["order_items oi", "JOIN orders o ON oi.order_id=o.order_id"]
        if any(f.startswith("品类=") for f in base_filters):
            parts.append("JOIN products p ON oi.product_id=p.product_id")
        if any(f.startswith(("城市=", "会员等级=")) for f in base_filters):
            parts.append("JOIN customers c ON o.customer_id=c.customer_id")
        where = _where_clauses(base_filters)
        where.append(f"strftime('%Y-%m',o.order_date) IN ('{old}','{new}')")
        return (
            "WITH m AS (SELECT strftime('%Y-%m',o.order_date) AS 月份, "
            "SUM(oi.quantity*oi.unit_price) AS 销售额 FROM "
            + " ".join(parts)
            + " WHERE "
            + " AND ".join(where)
            + " GROUP BY 月份) "
            f"SELECT ROUND(100.0*((SELECT 销售额 FROM m WHERE 月份='{new}')-"
            f"(SELECT 销售额 FROM m WHERE 月份='{old}'))/"
            f"NULLIF((SELECT 销售额 FROM m WHERE 月份='{old}'),0),1) AS 环比增长率"
        )
    if plan.intent == "above_average":
        joins = ["JOIN products p ON oi.product_id=p.product_id"]
        if any(
            f.startswith(("月份=", "年份=", "季度=", "渠道=", "城市=", "会员等级="))
            for f in plan.filters
        ):
            joins.append("JOIN orders o ON oi.order_id=o.order_id")
        if any(f.startswith(("城市=", "会员等级=")) for f in plan.filters):
            joins.append("JOIN customers c ON o.customer_id=c.customer_id")
        predicates = _where_clauses(plan.filters)
        where = " WHERE " + " AND ".join(predicates) if predicates else ""
        return (
            "WITH cat AS (SELECT p.category AS 品类, "
            "SUM(oi.quantity*oi.unit_price) AS 销售额 FROM order_items oi "
            + " ".join(joins)
            + where
            + " GROUP BY p.category) "
            "SELECT 品类, ROUND(销售额,0) AS 销售额 FROM cat "
            "WHERE 销售额>(SELECT AVG(销售额) FROM cat) ORDER BY 销售额 DESC"
        )

    metric = plan.metrics[0]
    if metric in {"repurchase_customers", "orders_per_customer"}:
        joins = []
        if any(f.startswith(("城市=", "会员等级=")) for f in plan.filters):
            joins.append("JOIN customers c ON o.customer_id=c.customer_id")
        if any(f.startswith("品类=") for f in plan.filters):
            joins.extend(
                [
                    "JOIN order_items oi ON o.order_id=oi.order_id",
                    "JOIN products p ON oi.product_id=p.product_id",
                ]
            )
        predicates = _where_clauses(plan.filters)
        tail = " FROM orders o " + " ".join(joins)
        tail += " WHERE " + " AND ".join(predicates) if predicates else ""
        if metric == "repurchase_customers":
            return (
                "SELECT COUNT(*) AS 复购顾客数 FROM (SELECT o.customer_id"
                + tail
                + " GROUP BY o.customer_id HAVING COUNT(DISTINCT o.order_id)>1)"
            )
        return (
            "SELECT ROUND(COUNT(DISTINCT o.order_id)*1.0/"
            "NULLIF(COUNT(DISTINCT o.customer_id),0),2) AS 人均订单数" + tail
        )
    if metric == "customer_count" and not plan.dimensions:
        where = _where_clauses(plan.filters)
        if plan.filters and any(f.startswith("品类=") for f in plan.filters):
            return None
        return "SELECT COUNT(*) AS 顾客数 FROM customers c" + (
            " WHERE " + " AND ".join(where) if where else ""
        )

    return _compile_generic(plan, metric)


def _compile_generic(plan: SemanticPlan, metric: str) -> str:
    dims = plan.dimensions
    needs_product = any(d in dims for d in ("category", "product"))
    needs_customer = any(d in dims for d in ("city", "segment"))
    needs_orders = any(d in dims for d in ("channel", "month", "city", "segment"))
    needs_product |= any(m in {"margin", "margin_rate"} for m in plan.metrics)
    needs_orders |= any(m in {"aov", "order_count"} for m in plan.metrics)
    needs_product |= any(f.startswith("品类=") for f in plan.filters)
    needs_customer |= any(f.startswith(("会员等级=", "城市=")) for f in plan.filters)
    needs_orders |= needs_customer or any(
        f.startswith(("月份=", "季度=", "年份=", "渠道=")) for f in plan.filters
    )

    if metric == "customer_count":
        base, joins = "customers c", []
        measure = "COUNT(DISTINCT c.customer_id)"
    elif metric == "order_count" and len(plan.metrics) == 1 and not needs_product:
        base = "orders o"
        joins = ["JOIN customers c ON o.customer_id=c.customer_id"] if needs_customer else []
        measure = "COUNT(DISTINCT o.order_id)"
    else:
        base = "order_items oi"
        joins = []
        if needs_product:
            joins.append("JOIN products p ON oi.product_id=p.product_id")
        if needs_orders:
            joins.append("JOIN orders o ON oi.order_id=o.order_id")
        if needs_customer:
            joins.append("JOIN customers c ON o.customer_id=c.customer_id")
        measure = _metric_expression(metric)

    dim_exprs = [_dimension_expression(d) for d in dims]
    select_parts = [f"{expr} AS {DIMENSIONS[d].label}" for d, expr in zip(dims, dim_exprs)]
    label = METRICS[metric].label
    if len(plan.metrics) == 1:
        select_parts.append(f"{measure} AS {label}")
    else:
        select_parts.extend(f"{_metric_expression(m)} AS {METRICS[m].label}" for m in plan.metrics)
    sql = f"SELECT {', '.join(select_parts)} FROM {base}"
    if joins:
        sql += " " + " ".join(joins)

    where = _where_clauses(plan.filters)
    if where:
        sql += " WHERE " + " AND ".join(where)
    if dim_exprs:
        sql += " GROUP BY " + ", ".join(dim_exprs)
        if "month" in dims:
            sql += " ORDER BY 月份"
        else:
            sql += f" ORDER BY {label} {plan.sort_order}"
    if plan.top_n:
        sql += f" LIMIT {plan.top_n}"
    return sql


def _metric_expression(metric: str) -> str:
    expressions = {
        "revenue": "ROUND(SUM(oi.quantity*oi.unit_price),0)",
        "quantity": "SUM(oi.quantity)",
        "customer_count": "COUNT(DISTINCT c.customer_id)",
        "order_count": "COUNT(DISTINCT o.order_id)",
        "aov": ("ROUND(SUM(oi.quantity*oi.unit_price)*1.0/COUNT(DISTINCT o.order_id),1)"),
        "margin": "ROUND(SUM(oi.quantity*(oi.unit_price-p.cost)),0)",
        "margin_rate": (
            "ROUND(100.0*SUM(oi.quantity*(oi.unit_price-p.cost))/SUM(oi.quantity*oi.unit_price),1)"
        ),
    }
    return expressions[metric]


def _dimension_expression(dimension: str) -> str:
    expressions = {
        "category": "p.category",
        "product": "p.name",
        "channel": "o.channel",
        "city": "c.city",
        "segment": "c.segment",
        "month": "strftime('%Y-%m',o.order_date)",
    }
    return expressions[dimension]


def _where_clauses(filters: tuple[str, ...]) -> list[str]:
    groups: dict[str, list[str]] = {}
    for item in filters:
        key, value = item.split("=", 1)
        groups.setdefault(key, []).append(value)
    columns = {
        "会员等级": "c.segment",
        "品类": "p.category",
        "城市": "c.city",
        "渠道": "o.channel",
        "月份": "strftime('%Y-%m',o.order_date)",
        "年份": "strftime('%Y',o.order_date)",
    }
    clauses = []
    for key, values in groups.items():
        if key == "季度":
            for value in values:
                quarter = int(value[-1])
                start = (quarter - 1) * 3 + 1
                clauses.append(
                    f"CAST(strftime('%m',o.order_date) AS INTEGER) BETWEEN {start} AND {start + 2}"
                )
        elif key in columns:
            escaped = ["'" + v.replace("'", "''") + "'" for v in values]
            clauses.append(f"{columns[key]} IN ({','.join(escaped)})")
        else:
            raise ValueError(f"不支持的筛选条件：{key}")
    return clauses


def describe_plan(plan: SemanticPlan) -> str:
    """面向 UI / CLI 的简短中文计划说明。"""
    if plan.status != "ready":
        return plan.reason
    if not plan.metrics:
        return plan.reason + ("；" + "；".join(plan.assumptions) if plan.assumptions else "")
    metrics = "、".join(METRICS[m].label for m in plan.metrics)
    dims = "、".join(DIMENSIONS[d].label for d in plan.dimensions) or "整体"
    filters = "；筛选=" + "、".join(plan.filters) if plan.filters else ""
    top_n = f"；Top {plan.top_n}" if plan.top_n else ""
    notes = "；" + "；".join(plan.assumptions) if plan.assumptions else ""
    return f"指标={metrics}；维度={dims}{filters}{top_n}{notes}"
