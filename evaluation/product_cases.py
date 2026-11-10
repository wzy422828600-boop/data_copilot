"""Frozen development regressions from the product review; NOT a blind/model test."""

ITEMS = " FROM order_items i JOIN orders o ON i.order_id=o.order_id "
PRODUCTS = ITEMS + "JOIN products p ON i.product_id=p.product_id "
CUSTOMERS = ITEMS + "JOIN customers c ON o.customer_id=c.customer_id "
YEAR = "o.order_date >= '2024-01-01' AND o.order_date < '2025-01-01'"
SALES = "ROUND(SUM(i.quantity*i.unit_price),0)"
GROWTH = (
    "WITH m AS (SELECT strftime('%Y-%m',o.order_date) AS m,"
    "SUM(i.quantity*i.unit_price) AS s" + ITEMS
)


def query(case_id, question, sql):
    return {"id": case_id, "question": question, "gold_sql": sql}


def boundary(case_id, question, status):
    return {"id": case_id, "question": question, "expected_status": status}


CASES = [
    query(
        "p01",
        "2024年线上销量最高的三个品类是什么？",
        "SELECT p.category AS 品类,SUM(i.quantity) AS 销量"
        + PRODUCTS
        + "WHERE "
        + YEAR
        + " AND o.channel='线上' GROUP BY p.category "
        "ORDER BY 销量 DESC LIMIT 3",
    ),
    query(
        "p02",
        "2023年线下销售额",
        "SELECT "
        + SALES
        + " AS 销售额"
        + ITEMS
        + "WHERE o.order_date>='2023-01-01' AND o.order_date<'2024-01-01' "
        "AND o.channel='线下'",
    ),
    query(
        "p03",
        "2024年上海VIP会员的订单数",
        "SELECT COUNT(DISTINCT o.order_id) AS 订单数"
        + CUSTOMERS
        + "WHERE "
        + YEAR
        + " AND c.city='上海' AND c.segment='VIP'",
    ),
    query(
        "p04",
        "2024年美妆毛利率",
        "SELECT ROUND(100.0*SUM(i.quantity*(i.unit_price-p.cost))/"
        "SUM(i.quantity*i.unit_price),1) AS 毛利率"
        + PRODUCTS
        + "WHERE "
        + YEAR
        + " AND p.category='美妆'",
    ),
    query(
        "p05",
        "2024年11月和12月的订单数",
        "SELECT COUNT(*) AS 订单数 FROM orders WHERE order_date>='2024-11-01' "
        "AND order_date<'2025-01-01'",
    ),
    query(
        "p06",
        "2024年第一季度线上销售额",
        "SELECT "
        + SALES
        + " AS 销售额"
        + ITEMS
        + "WHERE o.order_date>='2024-01-01' AND o.order_date<'2024-04-01' "
        "AND o.channel='线上'",
    ),
    query(
        "p07",
        "2024年12月相比11月线上销售额环比",
        GROWTH + "WHERE o.channel='线上' GROUP BY m) SELECT ROUND(100.0*((SELECT s FROM m "
        "WHERE m='2024-12')-(SELECT s FROM m WHERE m='2024-11'))/"
        "(SELECT s FROM m WHERE m='2024-11'),1) AS 环比增长率",
    ),
    query(
        "p08",
        "2024年1月销售额环比",
        GROWTH + "GROUP BY m) SELECT ROUND(100.0*((SELECT s FROM m WHERE m='2024-01')-"
        "(SELECT s FROM m WHERE m='2023-12'))/(SELECT s FROM m WHERE m='2023-12'),1) AS 环比增长率",
    ),
    query(
        "p09",
        "各会员等级的销售额和订单数",
        "SELECT c.segment AS 会员等级,"
        + SALES
        + " AS 销售额,COUNT(DISTINCT o.order_id) AS 订单数"
        + CUSTOMERS
        + "GROUP BY c.segment",
    ),
    query(
        "p10",
        "2024年线上复购顾客数",
        "SELECT COUNT(*) AS 复购顾客数 FROM (SELECT customer_id FROM orders "
        "WHERE channel='线上' AND order_date>='2024-01-01' AND order_date<'2025-01-01' "
        "GROUP BY customer_id HAVING COUNT(*)>1)",
    ),
    query(
        "p11",
        "2024年线上人均订单数",
        "SELECT ROUND(COUNT(*)*1.0/COUNT(DISTINCT customer_id),2) AS 人均订单数 "
        "FROM orders WHERE channel='线上' AND order_date>='2024-01-01' "
        "AND order_date<'2025-01-01'",
    ),
    query(
        "p12",
        "销量最低的三个品类",
        "SELECT p.category AS 品类,SUM(i.quantity) AS 销量"
        + PRODUCTS
        + "GROUP BY p.category ORDER BY 销量 ASC LIMIT 3",
    ),
    query(
        "p13",
        "2023年12月和2024年1月订单数",
        "SELECT COUNT(*) AS 订单数 FROM orders WHERE order_date>='2023-12-01' "
        "AND order_date<'2024-02-01'",
    ),
    query(
        "p14", "各城市顾客数", "SELECT city AS 城市,COUNT(*) AS 顾客数 FROM customers GROUP BY city"
    ),
    query(
        "p15",
        "整体客单价是多少？",
        "SELECT ROUND(SUM(i.quantity*i.unit_price)*1.0/"
        "COUNT(DISTINCT o.order_id),1) AS 客单价" + ITEMS,
    ),
    query(
        "p16",
        "每月销售额",
        "SELECT strftime('%Y-%m',o.order_date) AS 月份,"
        + SALES
        + " AS 销售额"
        + ITEMS
        + "GROUP BY 月份",
    ),
    boundary("b01", "销售额为什么下降了？", "clarify"),
    boundary("b02", "11月订单数", "clarify"),
    boundary("b03", "第四季度销售额", "clarify"),
    boundary("b04", "每月趋势", "clarify"),
    boundary("b05", "最近30天销售额", "clarify"),
    boundary("b06", "2024年11月到12月销售额", "clarify"),
    boundary("b07", "2026年线上销量最高的三个品类", "unanswerable"),
    boundary("b08", "净利润是多少？", "unanswerable"),
    boundary("b09", "库存还有多少？", "unanswerable"),
    boundary("b10", "删除所有订单", "blocked"),
    boundary("b11", "2024年12月订单数环比", "clarify"),
    boundary("b12", "2024年线上顾客数", "clarify"),
    boundary("b13", "今年线上销量最高的三个品类", "unanswerable"),
    boundary("b14", "排除线下的销售额", "clarify"),
]
