"""合成数据的质量断言（对应需求二：时间逻辑、约束、去重）。

在只读连接上运行，只做校验、不改动数据。
"""

from __future__ import annotations

from data_copilot import database


def _q(sql: str):
    with database.readonly_connection(guarded=False) as conn:
        return conn.execute(sql).fetchone()[0]


def test_no_order_before_signup():
    """核心断言：不存在订单日期早于对应顾客注册日期的记录。"""
    bad = _q(
        "SELECT COUNT(*) FROM orders o JOIN customers c "
        "ON o.customer_id=c.customer_id WHERE o.order_date < c.signup_date"
    )
    assert bad == 0, f"发现 {bad} 笔'注册前下单'，数据生成的时间逻辑有误"


def test_no_orphan_orders_or_items():
    orphan_orders = _q(
        "SELECT COUNT(*) FROM orders o LEFT JOIN customers c "
        "ON o.customer_id=c.customer_id WHERE c.customer_id IS NULL"
    )
    orphan_items = _q(
        "SELECT COUNT(*) FROM order_items i LEFT JOIN orders o "
        "ON i.order_id=o.order_id WHERE o.order_id IS NULL"
    )
    assert orphan_orders == 0 and orphan_items == 0


def test_no_duplicate_item_within_order():
    """(order_id, product_id) 是复合主键 —— 同单同品不应重复。"""
    dup = _q(
        "SELECT COUNT(*) FROM (SELECT order_id, product_id "
        "FROM order_items GROUP BY order_id, product_id HAVING COUNT(*)>1)"
    )
    assert dup == 0


def test_positive_quantity_and_price():
    bad = _q("SELECT COUNT(*) FROM order_items WHERE quantity<=0 OR unit_price<=0")
    assert bad == 0


def test_cost_below_price_for_positive_margin():
    """成本按 price 的 45%~70% 生成，毛利应为正，便于毛利指标有意义。"""
    bad = _q("SELECT COUNT(*) FROM products WHERE cost >= price")
    assert bad == 0


def test_row_counts_reasonable():
    assert _q("SELECT COUNT(*) FROM customers") == 400
    assert _q("SELECT COUNT(*) FROM orders WHERE order_date<'2025-01-01'") == 4000
    assert _q("SELECT COUNT(*) FROM orders") == 11557
    assert _q("SELECT COUNT(*) FROM order_items") > 4000
    assert _q("SELECT COUNT(DISTINCT category) FROM products") == 6
