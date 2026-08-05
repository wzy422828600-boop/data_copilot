"""Streamlit 界面：streamlit run app.py

只有点击「分析」才会调用模型（首次打开/示例按钮都不会触发 API 调用）。
"""
from __future__ import annotations

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_copilot import config, database, insights, text_to_sql, visualization  # noqa: E402

st.set_page_config(page_title="Text-to-SQL Analytics Assistant",
                   page_icon="📊", layout="wide")

EXAMPLES = [
    "哪个品类销售额最高？",
    "每月销售额的趋势是怎样的？",
    "线上和线下哪个销售额高？",
    "各会员等级的销售额和订单数？",
    "哪些城市贡献了最多销售额？",
    "各品类的毛利率是多少？",
    "整体客单价是多少？",
]

if "question" not in st.session_state:
    st.session_state["question"] = EXAMPLES[0]


@st.cache_data(show_spinner=False)
def _schema() -> str:
    return database.schema_text()


def run_analysis(question: str) -> None:
    """执行一次分析并渲染。任何异常都转成友好提示，不让页面崩溃。"""
    try:
        with st.spinner("正在生成 SQL 并查询…"):
            def runner(sql: str):
                df, _ = database.run_query(sql)
                return df
            res = text_to_sql.generate(question, _schema(), runner=runner)
    except Exception as e:  # noqa: BLE001 —— 兜底，绝不崩
        st.error(f"分析失败：{e}")
        return

    # 状态条：模式 / 耗时 / 重试 / 安全
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("模式", res.mode)
    c2.metric("耗时", f"{res.latency_ms:.0f} ms")
    c3.metric("自动纠错", res.retries)
    c4.metric("安全校验", "已拦截" if res.blocked else "通过（只读）")
    if res.notice:
        st.warning(res.notice)

    st.subheader("① 生成的 SQL")
    st.code(res.sql or "(无)", language="sql")

    if res.blocked:
        st.error(f"已拦截：{res.block_reason}")
        return
    if res.error and res.df is None:
        st.error(f"执行出错：{res.error}")
        return

    df = res.df
    spec = visualization.choose_chart(df)
    left, right = st.columns([1, 1])
    with left:
        st.subheader("② 查询结果")
        if len(df) >= config.MAX_ROWS:
            st.caption(f"（结果较大，已截断到 {config.MAX_ROWS} 行）")
        st.dataframe(df, use_container_width=True, height=340)
    with right:
        st.subheader("③ 可视化")
        if spec["kind"] == "kpi":
            cols = st.columns(len(spec["metrics"]))
            for col, m in zip(cols, spec["metrics"]):
                col.metric(str(m), visualization.format_number(
                    df.iloc[0][m], visualization._is_pct(m)))
        else:
            path = visualization.make_chart(
                df, os.path.join(config.OUTPUT_DIR, "app_chart.png"),
                title=question)
            if path:
                st.image(path, use_container_width=True)
            else:
                st.info(f"该结果不适合出图（{spec.get('reason', '')}）。")

    st.subheader("④ 洞察")
    st.success(insights.summarize(question, df))


# ============================ 布局 ============================
with st.sidebar:
    st.header("📊 Text-to-SQL Assistant")
    st.caption("自然语言 → SQL → 图表 → 洞察")
    if config.llm_available():
        st.success(f"Claude 在线（{config.MODEL}）")
    else:
        st.warning("离线规则模式：设 ANTHROPIC_API_KEY 可用 Claude 处理更自由的提问")
    st.markdown("**数据库结构**")
    st.code(_schema(), language="text")
    st.markdown("**示例问题**（点击填入，再按「分析」）")
    for i, ex in enumerate(EXAMPLES):
        if st.button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state["question"] = ex
            st.rerun()

st.title("对话式零售 BI 助手")
st.write("用中文问一个关于销售数据的问题；只有点击「分析」才会调用模型。")

with st.form("ask_form"):
    st.text_input("你的问题：", key="question")
    submitted = st.form_submit_button("分析", type="primary")

if submitted:                     # 仅在明确提交这一次运行，首屏不触发
    run_analysis(st.session_state["question"])
else:
    st.info("输入或选择一个问题，然后点击「分析」。")

st.caption("演示数据为合成数据。SQL 经只读安全校验并在只读连接上执行，拒绝任何写操作。")
