# 数据分析 Copilot · Conversational Data Analysis Assistant

> 用自然语言问业务问题，助手自动写 SQL、查询数据库、画图并给出一句话洞察。
> Ask a business question in plain language — the assistant writes the SQL, queries the database, charts the result, and explains it.

面向非技术业务同学的「对话式 BI」：把 **自然语言 → SQL → 图表 → 洞察** 串成一条链路，让不会写 SQL 的人也能自助取数。

---

## 🎯 解决什么问题

在大多数团队里，运营 / 产品 / 市场想看个数，得排队等数据同学写 SQL。本项目把这条链路自动化：

| 传统方式 | 用 Copilot |
| --- | --- |
| 提需求 → 排期 → 等 SQL → 拿数 | 直接打字问，几秒出结果 |
| 只有会 SQL 的人能自助 | 任何人都能问 |
| 结果是一张干巴巴的表 | 表 + 图 + 一句话洞察 |

## ✨ 功能

- **自然语言转 SQL**：基于 Claude，把中文问题翻译成 SQLite 查询（附库结构做上下文）。
- **只读安全校验**：只放行单条 `SELECT`，任何 `INSERT/UPDATE/DROP/...` 一律拦截。
- **自动可视化**：按结果形状自动选柱状图 / 折线图。
- **自动洞察**：用一到两句话点出关键结论和数字。
- **离线可跑**：没有 API key 也能用内置规则跑通全流程 demo（方便试用与截图）。
- **两种入口**：命令行 `3_ask.py` 和 Streamlit 网页 `4_app.py`。

## 🏗️ 架构

```
用户提问(中文)
     │
     ▼
┌─────────────┐   库结构+样例    ┌──────────────┐
│  get_schema │ ───────────────▶ │  nl_to_sql   │  Claude / 离线规则
└─────────────┘                  └──────┬───────┘
                                        │  SQL
                                        ▼
                                 ┌──────────────┐
                                 │ is_safe_sql  │  只读校验(挡写操作)
                                 └──────┬───────┘
                                        ▼
                    ┌───────────┬───────────────┬─────────────┐
                    │  run_sql  │   make_chart   │  summarize  │
                    │ (DataFrame)│  (柱状/折线)   │ (一句洞察)  │
                    └───────────┴───────────────┴─────────────┘
```

## 🖼️ 演示

示例问题：`哪个品类销售额最高？`

![示例图表](docs/demo_chart.png)

```
[生成的 SQL]
SELECT p.category AS 品类, ROUND(SUM(oi.quantity*oi.unit_price),0) AS 销售额
FROM order_items oi JOIN products p ON oi.product_id=p.product_id
GROUP BY p.category ORDER BY 销售额 DESC

[洞察]
运动户外品类销售额最高，为 1,456,658 元，其次是鞋靴（1,359,505 元）。
```

### Web 界面（`streamlit run 4_app.py`）

左侧栏展示数据库结构与示例问题（一键填入），主区把「① 生成的 SQL → ② 查询结果 → ③ 自动图表 → ④ 洞察」四步依次呈现。
> 首次运行后可自行截图保存为 `docs/demo_app.png` 放进 README。

## 🧰 技术栈

Python · Claude API (Anthropic) · SQLite · pandas · matplotlib · Streamlit

## 🚀 快速开始

```bash
# 1. 创建环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. 生成演示数据库
python 1_make_sample_data.py

# 3a. 命令行提问
python 3_ask.py "线上和线下哪个销售额高？"

# 3b. 或启动网页界面
streamlit run 4_app.py
```

**解锁完整自然语言能力**（可选）：复制 `.env.example` 为 `.env` 并填入 `ANTHROPIC_API_KEY`。
不填也能运行——会自动进入离线规则模式演示。

## ✅ 测试

```bash
pytest -q          # 19 个用例：安全校验 / 离线 SQL / 自动出图 / 端到端离线链路
```

测试若发现数据库不存在会自动生成，端到端用例会强制离线以避免误调 API。

## 🔒 安全

生成的 SQL 在执行前都会经过 `is_safe_sql()`：只允许单条 `SELECT/WITH`，命中任何写操作关键字（`INSERT/UPDATE/DELETE/DROP/ALTER/...`）直接拒绝，防止 LLM 误生成破坏性语句。

## 📁 数据说明

`1_make_sample_data.py` 生成一份**合成**电商数据（固定随机种子）：顾客 / 产品 / 订单 / 订单明细，一年销售记录。不含任何真实数据。

## 🗺️ 局限与后续

- 复杂多轮追问、跨库 join 暂未支持；
- 目前是单库单轮问答，后续可加对话记忆、结果缓存、更多图表类型、以及对生成 SQL 的自动校错重试。

## 👤 作者

王振烨 · MSc Statistics with Data Science, University of Edinburgh
