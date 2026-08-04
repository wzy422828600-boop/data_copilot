"""
1_make_sample_data.py
生成一个用于演示的合成电商数据库 (SQLite)。
一年的销售数据：顾客 / 产品 / 订单 / 订单明细。
数据是随机但固定种子的，任何人克隆仓库后都能得到同一份 demo 数据。
"""
import os
import sqlite3
import random
from datetime import date, timedelta

random.seed(42)
HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "ecommerce.db")
os.makedirs(os.path.join(HERE, "data"), exist_ok=True)

# ---- 一些取值池 ------------------------------------------------------------
CITIES = ["北京", "上海", "广州", "深圳", "杭州", "成都", "武汉", "西安"]
SEGMENTS = ["新客", "普通会员", "高级会员", "VIP"]
CATEGORIES = {
    "女装": (79, 399), "男装": (99, 499), "鞋靴": (129, 699),
    "配饰": (29, 199), "美妆": (49, 329), "运动户外": (89, 899),
}
CHANNELS = ["线上", "线下"]

print("=" * 60)
print("生成合成电商数据 (SQLite)")
print("=" * 60)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# ---- 重建表 ----------------------------------------------------------------
cur.executescript("""
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY,
    name        TEXT,
    city        TEXT,
    segment     TEXT,      -- 会员等级
    signup_date TEXT
);
CREATE TABLE products (
    product_id  INTEGER PRIMARY KEY,
    name        TEXT,
    category    TEXT,
    price       REAL,      -- 标准售价
    cost        REAL       -- 成本
);
CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER,
    order_date  TEXT,
    channel     TEXT,      -- 线上 / 线下
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);
CREATE TABLE order_items (
    order_id    INTEGER,
    product_id  INTEGER,
    quantity    INTEGER,
    unit_price  REAL,      -- 实际成交单价（可能有折扣）
    FOREIGN KEY (order_id)   REFERENCES orders(order_id),
    FOREIGN KEY (product_id) REFERENCES products(product_id)
);
""")

# ---- 顾客 ------------------------------------------------------------------
N_CUST = 400
start = date(2023, 1, 1)
customers = []
for cid in range(1, N_CUST + 1):
    signup = start + timedelta(days=random.randint(0, 700))
    customers.append((cid, f"顾客{cid:04d}", random.choice(CITIES),
                      random.choices(SEGMENTS, weights=[4, 3, 2, 1])[0],
                      signup.isoformat()))
cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", customers)
print(f"顾客: {len(customers)} 行")

# ---- 产品 ------------------------------------------------------------------
products = []
pid = 1
for cat, (lo, hi) in CATEGORIES.items():
    for k in range(6):                       # 每个品类 6 个 SKU
        price = round(random.uniform(lo, hi), 0)
        cost = round(price * random.uniform(0.45, 0.7), 0)
        products.append((pid, f"{cat}-{k+1:02d}", cat, price, cost))
        pid += 1
cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)
print(f"产品: {len(products)} 行, {len(CATEGORIES)} 个品类")

# ---- 订单 + 明细 -----------------------------------------------------------
N_ORDERS = 4000
orders, items = [], []
day0 = date(2024, 1, 1)
for oid in range(1, N_ORDERS + 1):
    cid = random.randint(1, N_CUST)
    # 订单日期：2024 全年，年末稍旺（双11/双12）
    d = random.randint(0, 364)
    if random.random() < 0.18:               # 制造一点季节性
        d = random.randint(300, 364)
    odate = (day0 + timedelta(days=d)).isoformat()
    channel = random.choices(CHANNELS, weights=[6, 4])[0]  # 线上略多
    orders.append((oid, cid, odate, channel))

    for _ in range(random.randint(1, 4)):    # 每单 1-4 件
        p = random.choice(products)
        qty = random.randint(1, 3)
        # 线上折扣更狠一点，体现"线上更价格敏感"的业务事实
        disc = random.uniform(0.75, 1.0) if channel == "线上" else random.uniform(0.9, 1.0)
        unit = round(p[3] * disc, 0)
        items.append((oid, p[0], qty, unit))

cur.executemany("INSERT INTO orders VALUES (?,?,?,?)", orders)
cur.executemany("INSERT INTO order_items VALUES (?,?,?,?)", items)
print(f"订单: {len(orders)} 行 | 订单明细: {len(items)} 行")

conn.commit()

# ---- 快速自检 --------------------------------------------------------------
rev = cur.execute(
    "SELECT ROUND(SUM(quantity*unit_price),0) FROM order_items").fetchone()[0]
print(f"总销售额(GMV): {rev:,.0f} 元")
print(f"数据库已写入: {DB_PATH}")
conn.close()
print("完成。下一步可运行:  python 3_ask.py \"哪个品类销售额最高？\"")
