"""集中配置：路径、模型、安全与资源限制、成本估算参数。

所有其它模块从这里取配置，避免散落的魔法值。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---- 路径 ------------------------------------------------------------------
ROOT: Path = Path(__file__).resolve().parent.parent
DATA_DIR: Path = ROOT / "data"
DB_PATH: Path = DATA_DIR / "ecommerce.db"
OUTPUT_DIR: Path = ROOT / "outputs"
EVAL_RESULTS_DIR: Path = ROOT / "evaluation" / "results"


# ---- 读取 .env（装了 python-dotenv 就生效）--------------------------------
def load_env() -> None:
    """把项目根目录下的 .env 载入环境变量；没装 dotenv 就静默跳过。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ROOT / ".env")


load_env()  # 必须在读取下面的环境变量之前调用


# ---- 大模型 ----------------------------------------------------------------
# No invented / unverified model ID. Online experiments require an account's
# actual available ID, configured explicitly. The default app is offline.
DEFAULT_MODEL = ""
MODEL: str = os.getenv("MODEL", DEFAULT_MODEL)

MAX_TOKENS_SQL = 600  # 生成 SQL 的最大 token
MAX_TOKENS_INSIGHT = 260  # 生成洞察的最大 token
MAX_CORRECTION_RETRIES = 1  # SQL 执行失败后，最多自动纠错重试次数（禁止无限重试）
API_TIMEOUT_S = 30  # 单次 API 调用超时（秒）


# ---- 安全 / 资源限制 -------------------------------------------------------
MAX_ROWS = 1000  # 单次查询最多返回行数
QUERY_TIMEOUT_MS = 4000  # 单次查询墙钟超时（毫秒），防递归/笛卡尔积
PROGRESS_HANDLER_STEPS = 1000  # progress handler 每多少条 VM 指令检查一次超时


# ---- 可选成本估算：按实际所选模型自行配置，不预设未经验证的报价 ----------
PRICE_INPUT_PER_MTOK = os.getenv("PRICE_INPUT_PER_MTOK")
PRICE_OUTPUT_PER_MTOK = os.getenv("PRICE_OUTPUT_PER_MTOK")


def llm_available() -> bool:
    """是否配置key；不代表模型有效，也不自动启用网页或CLI的在线模式。"""
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float | None:
    """无调用时为0；未提供单价时返回None，而不是猜测当前费用。"""
    if input_tokens == 0 and output_tokens == 0:
        return 0.0
    try:
        input_rate = float(PRICE_INPUT_PER_MTOK)
        output_rate = float(PRICE_OUTPUT_PER_MTOK)
    except (TypeError, ValueError):
        return None
    if input_rate < 0 or output_rate < 0:
        return None
    return input_tokens / 1_000_000 * input_rate + output_tokens / 1_000_000 * output_rate
