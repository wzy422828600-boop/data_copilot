"""生成合成电商数据库 (SQLite)。固定随机种子，任何人克隆后结果一致。

要点：
- 每笔订单的 order_date >= 该顾客的 signup_date（构造时保证，末尾断言校验）。
- 主键 / 非空 / 外键 / 唯一约束 + 常用索引。
- 同一订单内商品不重复（无放回抽样），(order_id, product_id) 作为复合主键。
- 生成后跑数据质量检查，任一失败即报错退出。
"""

from __future__ import annotations

import os
import random
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_copilot import config  # noqa: E402
from data_copilot.database import writable_connection  # noqa: E402

random.seed(42)

CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安"]
SEGMENTS = ["新客", "普通会员", "高级会员", "VIP"]
CATEGORIES = {
    "女装": (79, 399),
    "男装": (99, 499),
    "鞋靴": (129, 699),
    "配饰": (29, 199),
    "美妆": (49, 329),
    "运动户外": (89, 899),
}
CHANNELS = ["线上", "线下"]

SIGNUP_START = date(2023, 1, 1)
SIGNUP_SPAN = 729  # 2023-01-01 .. 2024-12-30
ORDER_END = date(2024, 12, 31)
SEASON_START = date(2024, 11, 1)  # 年末大促


def main() -> None:
    if config.DB_PATH.exists():
        with writable_connection() as existing:
            has_statements = existing.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='simulation_metadata'"
            ).fetchone()
        if has_statements:
            print("已有扩展模拟库，保留现有数据。")
            return
    print("=" * 60)
    print("生成合成电商数据 (SQLite)")
    print("=" * 60)
    conn = writable_connection()
    cur = conn.cursor()

    cur.executescript("""
    DROP TABLE IF EXISTS order_items;
    DROP TABLE IF EXISTS orders;
    DROP TABLE IF EXISTS products;
    DROP TABLE IF EXISTS customers;

    CREATE TABLE customers (
        customer_id INTEGER PRIMARY KEY,
        name        TEXT NOT NULL,
        city        TEXT NOT NULL,
        segment     TEXT NOT NULL,
        signup_date TEXT NOT NULL
    );
    CREATE TABLE products (
        product_id  INTEGER PRIMARY KEY,
        name        TEXT NOT NULL UNIQUE,
        category    TEXT NOT NULL,
        price       REAL NOT NULL CHECK(price > 0),
        cost        REAL NOT NULL CHECK(cost  > 0)
    );
    CREATE TABLE orders (
        order_id    INTEGER PRIMARY KEY,
        customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
        order_date  TEXT NOT NULL,
        channel     TEXT NOT NULL
    );
    CREATE TABLE order_items (
        order_id    INTEGER NOT NULL REFERENCES orders(order_id),
        product_id  INTEGER NOT NULL REFERENCES products(product_id),
        quantity    INTEGER NOT NULL CHECK(quantity > 0),
        unit_price  REAL    NOT NULL CHECK(unit_price > 0),
        PRIMARY KEY (order_id, product_id)      -- 同单同品唯一，天然去重
    );
    CREATE INDEX idx_orders_customer ON orders(customer_id);
    CREATE INDEX idx_orders_date     ON orders(order_date);
    CREATE INDEX idx_items_product   ON order_items(product_id);
    """)

    # ---- 顾客 --------------------------------------------------------------
    n_cust = 400
    signups: dict[int, date] = {}
    customers = []
    for cid in range(1, n_cust + 1):
        signup = SIGNUP_START + timedelta(days=random.randint(0, SIGNUP_SPAN))
        signups[cid] = signup
        customers.append(
            (
                cid,
                f"顾客{cid:04d}",
                random.choice(CITIES),
                random.choices(SEGMENTS, weights=[4, 3, 2, 1])[0],
                signup.isoformat(),
            )
        )
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
    print(f"顾客: {len(customers)} 行")

    # ---- 产品 --------------------------------------------------------------
    products = []
    pid = 1
    for cat, (lo, hi) in CATEGORIES.items():
        for k in range(6):
            price = round(random.uniform(lo, hi), 0)
            cost = round(price * random.uniform(0.45, 0.7), 0)
            products.append((pid, f"{cat}-{k + 1:02d}", cat, price, cost))
            pid += 1
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
    print(f"产品: {len(products)} 行, {len(CATEGORIES)} 个品类")

    # ---- 订单 + 明细 -------------------------------------------------------
    n_orders = 4000
    orders, items = [], []
    for oid in range(1, n_orders + 1):
        cid = random.randint(1, n_cust)
        signup = signups[cid]
        # 订单日期在 [signup, 2024-12-31] 内均匀取；保证 >= 注册日
        max_off = (ORDER_END - signup).days
        if max_off <= 0:
            odate = signup
        elif (
            random.random() < 0.18
            and (ORDER_END - SEASON_START).days > 0
            and signup <= SEASON_START
        ):
            lo = (SEASON_START - signup).days
            odate = signup + timedelta(days=random.randint(lo, max_off))
        else:
            odate = signup + timedelta(days=random.randint(0, max_off))
        channel = random.choices(CHANNELS, weights=[6, 4])[0]
        orders.append((oid, cid, odate.isoformat(), channel))

        k = random.randint(1, 4)
        for p in random.sample(products, k):  # 无放回，避免同单重复
            qty = random.randint(1, 3)
            disc = random.uniform(0.75, 1.0) if channel == "线上" else random.uniform(0.9, 1.0)
            unit = round(p[3] * disc, 0)
            items.append((oid, p[0], qty, max(unit, 1.0)))
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
    cur.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
    conn.commit()
    print(f"订单: {len(orders)} 行 | 订单明细: {len(items)} 行")

    # ---- 数据质量检查（任一失败即报错）------------------------------------
    checks = {
        "订单早于注册日": cur.execute(
            "SELECT COUNT(*) FROM orders o JOIN customers c "
            "ON o.customer_id=c.customer_id WHERE o.order_date < c.signup_date"
        ).fetchone()[0],
        "孤儿订单(无对应顾客)": cur.execute(
            "SELECT COUNT(*) FROM orders o LEFT JOIN customers c "
            "ON o.customer_id=c.customer_id WHERE c.customer_id IS NULL"
        ).fetchone()[0],
        "孤儿明细(无对应订单)": cur.execute(
            "SELECT COUNT(*) FROM order_items i LEFT JOIN orders o "
            "ON i.order_id=o.order_id WHERE o.order_id IS NULL"
        ).fetchone()[0],
        "非正数量或单价": cur.execute(
            "SELECT COUNT(*) FROM order_items WHERE quantity<=0 OR unit_price<=0"
        ).fetchone()[0],
    }
    print("\n数据质量检查:")
    bad = False
    for name, cnt in checks.items():
        flag = "OK" if cnt == 0 else "FAIL"
        if cnt != 0:
            bad = True
        print(f"  [{flag}] {name}: {cnt}")
    if bad:
        conn.close()
        raise SystemExit("数据质量检查未通过，已终止。")

    rev = cur.execute("SELECT ROUND(SUM(quantity*unit_price),0) FROM order_items").fetchone()[0]
    print(f"\n总销售额(GMV): {rev:,.0f} 元")
    print(f"数据库已写入: {config.DB_PATH}")
    conn.close()
    from scripts.upgrade_demo_data import upgrade

    print("补充月度财报与后续模拟订单：", upgrade())
    print('完成。试试:  python scripts/ask.py "哪个品类销售额最高？"')


if __name__ == "__main__":
    main()
