"""兼容垫片（保留旧接口，内部转调新包 data_copilot）。

新代码请用 data_copilot.* 与 scripts/、app.py。此文件仅为让旧脚本/旧测试继续可用。
"""
from __future__ import annotations

import sqlite3

import pandas as pd

from data_copilot import config, security, text_to_sql
from data_copilot.database import get_schema  # noqa: F401  (旧接口)
from data_copilot.insights import summarize  # noqa: F401
from data_copilot.visualization import make_chart  # noqa: F401

DB_PATH = str(config.DB_PATH)
MODEL = config.MODEL

is_safe_sql = security.is_safe_sql
_offline_sql = text_to_sql.offline_sql


def llm_available() -> bool:
    return config.llm_available()


def connect() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def run_sql(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn)


def nl_to_sql(question: str, schema: str) -> tuple[str, str]:
    """旧接口：返回 (sql, mode)。离线走关键词规则，在线走 Claude。"""
    if llm_available():
        res = text_to_sql.generate(question, schema, runner=None)
        return res.sql, "llm"
    return _offline_sql(question), "offline"
