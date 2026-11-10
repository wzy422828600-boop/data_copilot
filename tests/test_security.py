"""安全护栏：预检、authorizer 执行层拦截、数据库不可改、资源上限。"""

from __future__ import annotations

import pytest

from data_copilot import config, database
from data_copilot.database import QueryError, run_query
from data_copilot.security import is_safe_sql


# ---- 第一层：关键词/结构预检 --------------------------------------------
def test_allows_plain_select():
    assert is_safe_sql("SELECT * FROM orders")[0] is True


def test_allows_readonly_with():
    assert is_safe_sql("WITH x AS (SELECT 1) SELECT * FROM x")[0] is True


@pytest.mark.parametrize(
    "bad",
    [
        "INSERT INTO orders VALUES (1)",
        "UPDATE orders SET channel='x'",
        "DELETE FROM orders",
        "DROP TABLE orders",
        "ALTER TABLE orders ADD COLUMN x INT",
        "CREATE TABLE t(x)",
        "ATTACH DATABASE '/etc/passwd' AS p",
        "DETACH DATABASE p",
        "PRAGMA table_info(orders)",
        "SELECT 1; DELETE FROM orders",  # 多语句
        "WITH t AS (SELECT 1) DELETE FROM orders",  # 写操作藏在 WITH 后
        "WITH t AS (SELECT 1) INSERT INTO orders VALUES(1)",
    ],
)
def test_precheck_blocks(bad):
    assert is_safe_sql(bad)[0] is False


# ---- 第二/三层：即使绕过预检，执行层也拦截，且数据库不被改动 ----------
@pytest.mark.parametrize(
    "payload",
    [
        "DELETE FROM orders",
        "UPDATE products SET price=0",
        "DROP TABLE customers",
        "INSERT INTO orders VALUES (999999, 1, '2024-01-01', '线上')",
        "CREATE TABLE hack(x)",
        "ATTACH DATABASE '/tmp/x.db' AS p",
        "PRAGMA writable_schema=ON",
    ],
)
def test_execution_layer_blocks_and_db_unchanged(payload):
    before = database.row_counts()
    with pytest.raises(QueryError):
        run_query(payload)  # 直接打执行层（不经预检）
    after = database.row_counts()
    assert before == after  # 数据库行数分毫未变


def test_row_cap_bounds_runaway_query():
    # 无界递归 CTE：靠行数上限截断，不会挂起
    sql = "WITH RECURSIVE r(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM r) SELECT x FROM r"
    df, truncated = run_query(sql)
    assert truncated is True
    assert len(df) == config.MAX_ROWS


def test_timeout_aborts_expensive_query():
    # 巨大笛卡尔积聚合无法被行数上限截断（只返回一行），必须靠超时中止
    heavy = "SELECT COUNT(*) FROM orders a, order_items b, order_items c"
    with pytest.raises(QueryError):
        run_query(heavy, timeout_ms=5)


def test_full_dataset_unchanged_after_all_reads():
    counts = database.row_counts()
    assert counts["orders"] == 11557
    assert counts["order_items"] > 0
