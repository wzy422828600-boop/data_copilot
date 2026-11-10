"""数据库访问层：写连接（仅供数据生成）与只读连接（供分析），及查询执行。

分析路径默认只读，且叠加 authorizer + query_only + 超时 + 行数上限；
无论安全检查是否被绕过，分析连接都无法修改数据库。
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pandas as pd

from . import config
from .security import readonly_authorizer


class QueryError(RuntimeError):
    """查询执行失败（语法错误、被 authorizer 拒绝、超时等）。"""


def writable_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """可写连接，仅供 scripts/make_sample_data.py 生成数据时使用。"""
    path = Path(db_path) if db_path else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def readonly_connection(
    db_path: Path | str | None = None,
    timeout_ms: int = config.QUERY_TIMEOUT_MS,
    guarded: bool = True,
) -> Iterator[sqlite3.Connection]:
    """只读连接上下文管理器：mode=ro + query_only（+ 可选 authorizer + 墙钟超时）。

    guarded=True 用于执行不可信的 LLM SQL（叠加 authorizer 与超时看门狗）；
    guarded=False 用于我们自己可信的结构读取（需要 PRAGMA table_info）。
    两种情况下连接都是只读的，且在成功/异常/被拒绝路径下都会正确关闭。
    """
    path = Path(db_path) if db_path else config.DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"找不到数据库 {path}；请先运行 python scripts/make_sample_data.py"
        )
    # mode=ro：操作系统/SQLite 层面只读，任何写入直接失败
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        # 必须在设 authorizer 之前执行（之后 PRAGMA 会被 authorizer 拒绝）
        conn.execute("PRAGMA query_only=ON")
        if guarded:
            deadline = time.monotonic() + timeout_ms / 1000.0

            def _watchdog() -> int:
                # progress handler：超时返回非 0 让 SQLite 中止查询
                return 1 if time.monotonic() > deadline else 0

            conn.set_progress_handler(_watchdog, config.PROGRESS_HANDLER_STEPS)
            conn.set_authorizer(readonly_authorizer)
        yield conn
    finally:
        conn.set_authorizer(None)
        conn.close()


def get_schema(conn: sqlite3.Connection, *, include_samples: bool = False) -> str:
    """返回库结构文本；默认不包含样例行，降低发送真实数据的隐私风险。"""
    lines: list[str] = []
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
        coldesc = ", ".join(f"{c[1]} {c[2]}" for c in cols)
        lines.append(f"表 {t}({coldesc})")
        if include_samples:
            sample = conn.execute(f"SELECT * FROM {t} LIMIT 2").fetchall()
            lines.append(f"  样例: {sample}")
    return "\n".join(lines)


def schema_text(
    db_path: Path | str | None = None,
    *,
    include_samples: bool = False,
) -> str:
    """便捷函数：自己开只读连接读结构（可信读取，不加 authorizer 以便用 PRAGMA）。"""
    with readonly_connection(db_path, guarded=False) as conn:
        return get_schema(conn, include_samples=include_samples)


def order_date_range(db_path: Path | str | None = None) -> tuple[str | None, str | None]:
    """Observed order-date bounds, not a claim that every day is complete."""
    with readonly_connection(db_path) as conn:
        row = conn.execute("SELECT MIN(order_date), MAX(order_date) FROM orders").fetchone()
        return row[0], row[1]


def run_query(
    sql: str,
    db_path: Path | str | None = None,
    max_rows: int = config.MAX_ROWS,
    timeout_ms: int = config.QUERY_TIMEOUT_MS,
) -> tuple[pd.DataFrame, bool]:
    """在只读连接上执行 SQL，返回 (DataFrame, 是否被行数上限截断)。

    最多取 max_rows 行；超时或被 authorizer 拒绝会抛 QueryError。
    """
    try:
        with readonly_connection(db_path, timeout_ms=timeout_ms) as conn:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1)
            truncated = len(rows) > max_rows
            df = pd.DataFrame(rows[:max_rows], columns=cols)
            return df, truncated
    except sqlite3.Error as e:
        raise QueryError(str(e)) from e


def row_counts(db_path: Path | str | None = None) -> dict[str, int]:
    """各表行数（用于完整性检查：确认查询后数据未被改动）。"""
    with readonly_connection(db_path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                for t in tables}
