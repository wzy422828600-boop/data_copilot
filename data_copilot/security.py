"""多层只读安全护栏（不是"生产级绝对安全"，见下方边界说明）。

三层：
  1) 关键词/结构预检 is_safe_sql()：单条语句、必须 SELECT/WITH、挡写关键字。
  2) SQLite authorizer：在解析层拒绝一切写/DDL/ATTACH/PRAGMA 等操作。
  3) 只读连接 + PRAGMA query_only=ON（见 database.py）：执行层再兜一层。

边界：本模块降低而非消除风险。它面向"LLM 误生成破坏性 SQL"这一威胁，
不是防御可信主机上的任意攻击者；也不做 SQL 注入意义上的输入消毒
（这里不存在拼接用户输入到查询的场景）。
"""
from __future__ import annotations

import re
import sqlite3

# 预检：命中任一写/DDL 关键字即拒绝
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|"
    r"attach|detach|reindex|vacuum|pragma|grant|revoke)\b",
    re.IGNORECASE,
)
_STARTS_OK = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)


def is_safe_sql(sql: str) -> tuple[bool, str]:
    """第一层预检。返回 (是否安全, 原因)。"""
    if not sql or not sql.strip():
        return False, "空 SQL"
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        return False, "只允许单条语句"
    if not _STARTS_OK.match(s):
        return False, "只允许只读 SELECT / WITH 查询"
    if _FORBIDDEN.search(s):
        return False, "检测到写操作或 DDL 关键字，已拒绝"
    return True, "ok"


# authorizer 允许的动作：只读相关
_ALLOWED_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_READ,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
}


def readonly_authorizer(action: int, arg1, arg2, dbname, source) -> int:
    """SQLite authorizer 回调：放行只读动作，拒绝其余（写/DDL/ATTACH/PRAGMA…）。

    即使预检被绕过，解析阶段也会因返回 SQLITE_DENY 而报错，查询无法执行。
    """
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY
