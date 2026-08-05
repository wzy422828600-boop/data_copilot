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
# 默认用一个正式的公有 API model ID；可用 MODEL 环境变量 / .env 覆盖。
# claude-sonnet-5 在成本和 Text-to-SQL 能力间较平衡；换成你账号可用的模型即可。
# 注意：本项目构建时无 API Key，未对该 ID 做过在线校验。
DEFAULT_MODEL = "claude-sonnet-5"
MODEL: str = os.getenv("MODEL", DEFAULT_MODEL)

MAX_TOKENS_SQL = 600          # 生成 SQL 的最大 token
MAX_TOKENS_INSIGHT = 260      # 生成洞察的最大 token
MAX_CORRECTION_RETRIES = 1    # SQL 执行失败后，最多自动纠错重试次数（禁止无限重试）
API_TIMEOUT_S = 30            # 单次 API 调用超时（秒）


# ---- 安全 / 资源限制 -------------------------------------------------------
MAX_ROWS = 1000               # 单次查询最多返回行数
QUERY_TIMEOUT_MS = 4000       # 单次查询墙钟超时（毫秒），防递归/笛卡尔积
PROGRESS_HANDLER_STEPS = 1000  # progress handler 每多少条 VM 指令检查一次超时


# ---- 成本估算（仅粗略参考，USD / 每百万 token）----------------------------
# 真实价格以 Anthropic 官网为准；这里只为 evaluate.py 给一个数量级估算。
PRICE_INPUT_PER_MTOK = 3.0
PRICE_OUTPUT_PER_MTOK = 15.0


def llm_available() -> bool:
    """是否配置了 ANTHROPIC_API_KEY（决定走在线 Claude 还是离线规则）。"""
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def estimate_cost_usd(input_tokens: int, output_tokens: int) -> float:
    """按上面的粗略单价估算一次/多次调用的美元成本。"""
    return (input_tokens / 1_000_000 * PRICE_INPUT_PER_MTOK
            + output_tokens / 1_000_000 * PRICE_OUTPUT_PER_MTOK)
