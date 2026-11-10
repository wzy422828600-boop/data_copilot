"""
test_copilot.py —— 项目自测（pytest）
覆盖：只读安全校验、离线 NL→SQL 能跑通、自动出图（含目录自建）、端到端离线链路。
运行:  pytest -q
"""
import importlib
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
eng = importlib.import_module("2_engine")


@pytest.fixture(scope="session")
def conn():
    """确保演示数据库存在（没有就现生成），供整轮测试复用。"""
    if not os.path.exists(eng.DB_PATH):
        import subprocess
        subprocess.run([sys.executable, os.path.join(ROOT, "1_make_sample_data.py")],
                       check=True)
    c = eng.connect()
    yield c
    c.close()


# ---- 安全校验：放行只读，拦截写操作 --------------------------------------
def test_safe_allows_select():
    assert eng.is_safe_sql("SELECT * FROM orders")[0] is True
    assert eng.is_safe_sql("WITH x AS (SELECT 1) SELECT * FROM x")[0] is True


@pytest.mark.parametrize("bad", [
    "DROP TABLE orders",
    "DELETE FROM orders",
    "UPDATE orders SET channel='x'",
    "INSERT INTO orders VALUES (1)",
    "SELECT 1; DELETE FROM orders",          # 多语句
    "SELECT * FROM orders; DROP TABLE orders",
])
def test_safe_blocks_writes(bad):
    assert eng.is_safe_sql(bad)[0] is False


# ---- schema 反射包含全部表 -----------------------------------------------
def test_schema_has_tables(conn):
    s = eng.get_schema(conn)
    for t in ["customers", "products", "orders", "order_items"]:
        assert t in s


# ---- 离线规则生成的 SQL 都安全且能跑出结果 -------------------------------
@pytest.mark.parametrize("q", [
    "哪个品类销售额最高？",
    "每月销售额趋势",
    "线上和线下哪个销售额高？",
    "哪些城市销售额最高",
    "各会员等级销售额和订单数",
    "整体客单价是多少",
])
def test_offline_sql_runs(conn, q):
    sql = eng._offline_sql(q)
    ok, why = eng.is_safe_sql(sql)
    assert ok, f"{q} -> 不安全: {why}"
    df = eng.run_sql(conn, sql)
    assert len(df) >= 1


# ---- 生成数据的既定事实：线上销售额 > 线下 --------------------------------
def test_online_greater_than_offline(conn):
    df = eng.run_sql(conn, eng._offline_sql("线上线下销售额对比"))
    d = dict(zip(df.iloc[:, 0], df.iloc[:, 1]))
    assert d["线上"] > d["线下"]


# ---- make_chart 会自建目录并出图（对应之前修的 outputs/ 缺失 bug）--------
def test_make_chart_creates_dir_and_file(conn, tmp_path):
    df = eng.run_sql(conn, eng._offline_sql("哪个品类销售额最高？"))
    out = tmp_path / "not_exist_yet" / "chart.png"    # 故意用不存在的子目录
    p = eng.make_chart(df, str(out))
    assert p and os.path.exists(p)


# ---- 单值/非数值结果不出图 ------------------------------------------------
def test_make_chart_skips_single_value(conn, tmp_path):
    df = eng.run_sql(conn, eng._offline_sql("整体客单价是多少"))   # 1 行 1 列
    assert eng.make_chart(df, str(tmp_path / "x.png")) is None


# ---- 离线 summarize 不崩且给出数字 ----------------------------------------
def test_summarize_offline(conn):
    df = eng.run_sql(conn, eng._offline_sql("哪个品类销售额最高？"))
    txt = eng.summarize("哪个品类销售额最高？", df)
    assert "最高" in txt and "离线" in txt


# ---- 端到端离线链路（强制离线，避免测试误调 API）-------------------------
def test_end_to_end_offline(conn, monkeypatch):
    monkeypatch.setattr(eng, "llm_available", lambda: False)
    q = "哪个品类销售额最高？"
    sql, mode = eng.nl_to_sql(q, eng.get_schema(conn))
    assert mode == "offline"
    assert eng.is_safe_sql(sql)[0] is True
    df = eng.run_sql(conn, sql)
    assert "品类" in df.columns and len(df) == 6
