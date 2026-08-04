"""
2_engine.py
Copilot 的核心逻辑（过程式函数，供 3_ask.py 和 4_app.py 调用）：
  get_schema()   读取数据库结构 + 少量样例，喂给 LLM 当上下文
  nl_to_sql()    自然语言 -> SQLite SELECT（有 API key 用 Claude，无 key 走离线规则）
  is_safe_sql()  只允许只读 SELECT，挡住任何写操作
  run_sql()      执行并返回 DataFrame
  make_chart()   按结果形状自动选柱状图/折线图
  summarize()    用一两句中文总结结果（Claude 或离线模板）
"""
import os
import re
import sqlite3
import pandas as pd
import matplotlib
matplotlib.use("Agg")                       # 无界面后端，方便存图
import matplotlib.pyplot as plt

# 中文字体：给一串候选，matplotlib 会自动用系统里存在的第一个（跨 macOS/Win/Linux）
matplotlib.rcParams["font.sans-serif"] = [
    "Songti SC", "PingFang SC", "STHeiti", "Arial Unicode MS",
    "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "sans-serif"]
matplotlib.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(HERE, "data", "ecommerce.db")

# 读取项目根目录下的 .env（装了 python-dotenv 就生效）：把 key 放进 .env 即可，无需手动 export
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(HERE, ".env"))
except ImportError:
    pass

MODEL = os.getenv("MODEL", "claude-sonnet-5")   # 可用 MODEL 环境变量 / .env 覆盖

# ---- LLM 是否可用 ----------------------------------------------------------
def llm_available():
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _client():
    from anthropic import Anthropic       # 延迟导入，离线时不需要装
    return Anthropic()


# ---- 1. 读取库结构 ---------------------------------------------------------
def get_schema(conn):
    """返回一段文本：每张表的建表语句 + 前 2 行样例。喂给 LLM 当上下文。"""
    lines = []
    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    for t in tables:
        cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
        coldesc = ", ".join(f"{c[1]} {c[2]}" for c in cols)
        lines.append(f"表 {t}({coldesc})")
        sample = conn.execute(f"SELECT * FROM {t} LIMIT 2").fetchall()
        lines.append(f"  样例: {sample}")
    return "\n".join(lines)


# ---- 2. 自然语言 -> SQL ----------------------------------------------------
SYS_PROMPT = """你是一个把中文问题翻译成 SQLite 查询的助手。
规则：
- 只输出一条 SQL，且必须是只读的 SELECT 语句，不要任何解释、不要 markdown 代码块。
- 只能使用给定的表和字段；方言是 SQLite（日期用 strftime）。
- 销售额/GMV = SUM(quantity * unit_price)，需要 join order_items;
  按时间分析时 join orders 用 orders.order_date。
- 结果适当排序，并对聚合排行加 LIMIT。
"""


def nl_to_sql(question, schema):
    """返回 (sql, mode)。mode='llm' 或 'offline'。"""
    if llm_available():
        prompt = f"数据库结构：\n{schema}\n\n用户问题：{question}\n\n只输出 SQL。"
        msg = _client().messages.create(
            model=MODEL, max_tokens=600,
            system=SYS_PROMPT,
            messages=[{"role": "user", "content": prompt}])
        sql = msg.content[0].text.strip()
        sql = re.sub(r"^```\w*|```$", "", sql).strip()   # 去掉可能的代码块围栏
        return sql, "llm"
    return _offline_sql(question), "offline"


def _offline_sql(q):
    """没有 API key 时的关键词兜底规则，覆盖 README 里的几个示例问题。"""
    ql = q.lower()
    if "品类" in q or "category" in ql:
        return ("SELECT p.category AS 品类, "
                "ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额 "
                "FROM order_items oi JOIN products p ON oi.product_id=p.product_id "
                "GROUP BY p.category ORDER BY 销售额 DESC")
    if "每月" in q or "月度" in q or "趋势" in q or "month" in ql:
        return ("SELECT strftime('%Y-%m', o.order_date) AS 月份, "
                "ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额 "
                "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
                "GROUP BY 月份 ORDER BY 月份")
    if "线上" in q or "线下" in q or "渠道" in q or "channel" in ql:
        return ("SELECT o.channel AS 渠道, "
                "ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额 "
                "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
                "GROUP BY o.channel ORDER BY 销售额 DESC")
    if "城市" in q or "地区" in q or "city" in ql:
        return ("SELECT c.city AS 城市, "
                "ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额 "
                "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
                "JOIN customers c ON o.customer_id=c.customer_id "
                "GROUP BY c.city ORDER BY 销售额 DESC")
    if "会员" in q or "segment" in ql or "等级" in q:
        return ("SELECT c.segment AS 会员等级, "
                "ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额, "
                "COUNT(DISTINCT o.order_id) AS 订单数 "
                "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id "
                "JOIN customers c ON o.customer_id=c.customer_id "
                "GROUP BY c.segment ORDER BY 销售额 DESC")
    if "客单价" in q or "aov" in ql:
        return ("SELECT ROUND(SUM(oi.quantity*oi.unit_price)*1.0/"
                "COUNT(DISTINCT o.order_id),1) AS 客单价 "
                "FROM order_items oi JOIN orders o ON oi.order_id=o.order_id")
    # 兜底：总销售额
    return ("SELECT ROUND(SUM(quantity*unit_price),0) AS 总销售额 "
            "FROM order_items")


# ---- 3. 安全校验 -----------------------------------------------------------
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|pragma)\b", re.I)


def is_safe_sql(sql):
    s = sql.strip().rstrip(";")
    if ";" in s:                                   # 只允许单条语句
        return False, "只允许单条语句"
    if not re.match(r"^\s*(select|with)\b", s, re.I):
        return False, "只允许 SELECT 查询"
    if FORBIDDEN.search(s):
        return False, "检测到写操作关键字，已拒绝"
    return True, "ok"


# ---- 4. 执行 ---------------------------------------------------------------
def run_sql(conn, sql):
    return pd.read_sql_query(sql, conn)


# ---- 5. 自动出图 -----------------------------------------------------------
def make_chart(df, path):
    """按结果形状挑图：类别+数值->柱状；月份/日期+数值->折线；否则不画。"""
    if df.shape[1] < 2 or len(df) < 2:
        return None
    xcol, ycol = df.columns[0], df.columns[1]
    if not pd.api.types.is_numeric_dtype(df[ycol]):
        return None
    fig, ax = plt.subplots(figsize=(7, 4))
    is_time = bool(re.search(r"月|日期|date|month|年", str(xcol)))
    if is_time:
        ax.plot(df[xcol].astype(str), df[ycol], marker="o", color="#1f3a5f")
    else:
        ax.bar(df[xcol].astype(str), df[ycol], color="#3f6ea5")
    ax.set_xlabel(str(xcol)); ax.set_ylabel(str(ycol))
    ax.set_title(f"{ycol} by {xcol}")
    plt.xticks(rotation=30, ha="right")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)   # 全新克隆时 outputs/ 可能不存在
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close()
    return path


# ---- 6. 结果总结 -----------------------------------------------------------
def summarize(question, df):
    """一两句中文洞察。有 key 用 Claude，无 key 用简单模板。"""
    table = df.head(15).to_string(index=False)
    if llm_available():
        msg = _client().messages.create(
            model=MODEL, max_tokens=200,
            system="你是数据分析师。用一到两句中文，基于给定的查询结果给出关键洞察，点出具体数字。不要复述表格。",
            messages=[{"role": "user",
                       "content": f"问题：{question}\n结果：\n{table}"}])
        return msg.content[0].text.strip()
    # 离线模板：报出排名第一的那行
    if df.shape[1] >= 2 and pd.api.types.is_numeric_dtype(df.iloc[:, 1]):
        top = df.sort_values(df.columns[1], ascending=False).iloc[0]
        return f"[离线模板] {df.columns[0]}为「{top.iloc[0]}」时{df.columns[1]}最高，为 {top.iloc[1]:,.0f}。"
    return f"[离线模板] 查询返回 {len(df)} 行结果。"


def connect():
    return sqlite3.connect(DB_PATH)
