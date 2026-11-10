"""Streamlit 界面：streamlit run app.py

只有点击「分析」才会调用模型（首次打开/示例按钮都不会触发 API 调用）。
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile

import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from data_copilot import (  # noqa: E402
    agent,
    business_analysis,
    config,
    database,
    semantic_layer,
    visualization,
)
from data_copilot.finance_catalog import ACCOUNTS, REPORT_NAMES  # noqa: E402

st.set_page_config(page_title="零售BI助手", page_icon="📊", layout="wide")

if getattr(visualization, "PRESENTATION_REVISION", 0) < 2:
    importlib.reload(visualization)

# A running 0.4 Streamlit process can retain old imported package objects even
# after app.py updates. Refresh this one protocol transition without stopping
# the user's terminal service; do not repeatedly reload modules on widget use.
if (
    "analysis" not in agent.AgentResult.__dataclass_fields__
    or getattr(business_analysis, "ANALYSIS_VERSION", None) != "0.5.0"
    or getattr(business_analysis, "ANALYSIS_REVISION", 0) < 2
):
    from data_copilot import periods

    for module in (periods, semantic_layer, business_analysis, agent):
        importlib.reload(module)
    st.session_state.pop("analysis_result", None)
if "analysis_result" in st.session_state and not hasattr(
    st.session_state["analysis_result"], "analysis"
):
    st.session_state.pop("analysis_result", None)

EXAMPLES = [
    "2026年10月净利润是多少？",
    "销售额为什么下降了？",
    "今年营业收入和净利润分别是多少？",
    "2026年10月资产负债率和流动比率",
    "为什么2026年10月净利润与经营现金流不一致？",
    "最近三个月净利润趋势",
    "2026年10月利润表",
    "2024年线上销量最高的三个品类是什么？",
]

if "question" not in st.session_state:
    st.session_state["question"] = EXAMPLES[0]


def _schema() -> str:
    return database.schema_text()


def run_analysis(question: str, *, use_llm: bool = False, result=None) -> None:
    """执行一次分析并渲染。任何异常都转成友好提示，不让页面崩溃。"""
    try:
        if result is None:
            with st.spinner("正在确认期间、读取数据并计算…"):
                res = agent.analyze(question, schema=_schema(), use_llm=use_llm)
            st.session_state["analysis_result"] = res
            st.session_state["analysis_question"] = question
        else:
            res = result
    except Exception as e:  # noqa: BLE001 —— 兜底，绝不崩
        st.error(f"分析失败：{e}")
        return

    # Keep the result state readable; technical execution details stay in trace.
    mode_label = {
        "offline": "离线规则",
        "llm": "在线实验",
        "semantic": "数据边界",
        "policy": "请求判断",
        "analysis": "本地经营分析",
    }.get(res.mode, res.mode)
    st.caption(f"{mode_label} · {res.quality.label} · 耗时 {res.total_latency_ms:.0f} ms")
    if res.sql_result.notice and res.plan.status == "ready":
        st.warning(res.sql_result.notice)

    if res.plan.status == "ready" and not res.analysis:
        st.write(semantic_layer.describe_plan(res.plan))
    with st.expander("查看处理步骤", expanded=False):
        st.dataframe(
            [step.as_dict() for step in res.trace],
            use_container_width=True,
            hide_index=True,
        )

    if res.blocked:
        st.error(f"已拦截：{res.block_reason}")
        return
    if res.error and res.df is None:
        st.error(f"执行出错：{res.error}")
        return

    if res.plan.status in {"clarify", "unanswerable"}:
        st.info(res.insight)
        st.caption(res.quality.limitations)
        return

    if res.analysis:
        st.subheader("分析结论")
        st.write(res.insight)
        for note in res.analysis["notes"][:2]:
            st.caption(note)
        tables = res.analysis["tables"]
        table_tabs = st.tabs([table["title"] for table in tables])
        for table_index, (tab, table) in enumerate(zip(table_tabs, tables)):
            with tab:
                frame = table["df"]
                st.dataframe(
                    frame.style.format(precision=2, thousands=",", na_rep="—"),
                    hide_index=True,
                    use_container_width=True,
                    height=visualization.table_height(frame),
                )
                if table.get("chart") == "bar":
                    st.bar_chart(frame.set_index(table["x"])[[table["y"]]])
                elif table.get("chart") == "line":
                    trend_spec = visualization.choose_chart(frame)
                    if len(frame) < 2:
                        st.caption("当前只有一个月份，暂不绘制趋势线；请扩大查询期间。")
                    elif trend_spec.get("metrics"):
                        metric = st.selectbox(
                            "趋势指标（不同单位分别展示）", trend_spec["metrics"],
                            key=f"trend_metric_{table_index}",
                        )
                        st.line_chart(frame.set_index("月份")[[metric]])
                    elif trend_spec["kind"] != "none":
                        st.line_chart(frame.set_index("月份"))
        with st.expander("计算口径与取数SQL"):
            for note in res.analysis["notes"]:
                st.write("• " + note)
            st.caption("以下为只读取数语句，报表聚合与贡献拆解在本地按明确公式完成。")
            st.code(res.sql, language="sql")
        return

    with st.expander("取数 SQL 与计算口径", expanded=False):
        st.code(res.sql or "(无)", language="sql")
        for key in res.plan.metrics:
            metric = semantic_layer.METRICS.get(key)
            if metric:
                st.caption(f"{metric.label}：{metric.definition}；单位：{metric.unit}")

    df = res.df
    if res.quality.status == "no_data":
        st.info(res.insight)
        st.caption(res.quality.limitations)
        return
    if df is None:
        st.info(res.insight)
        return

    if res.quality.warnings:
        st.warning("；".join(res.quality.warnings))
    if res.quality.status != "passed":
        st.subheader("待复核的原始结果")
        st.dataframe(df, use_container_width=True)
        st.caption(res.quality.limitations)
        return
    # Recompute from the current renderer, including results cached before upgrade.
    spec = visualization.choose_chart(df)
    st.subheader("查询结果")
    if "margin" in res.plan.metrics or "margin_rate" in res.plan.metrics:
        st.caption("订单毛利口径：商品成交额减商品成本，未扣模拟退款和期间费用。财报口径请查询“财务毛利”或利润表。")
    if res.truncated:
        st.caption(f"（结果较大，已截断到 {config.MAX_ROWS} 行）")
    if spec["kind"] == "kpi":
        labels = [f"{c}：{df.iloc[0][c]}" for c in df.columns if c not in spec["metrics"]]
        if labels:
            st.caption("；".join(labels))
        for start in range(0, len(spec["metrics"]), 3):
            metrics = spec["metrics"][start:start + 3]
            for col, metric in zip(st.columns(len(metrics)), metrics):
                col.metric(
                    str(metric), visualization.format_metric(df.iloc[0][metric], str(metric))
                )
        st.caption("单条汇总结果以指标卡展示；趋势或对比图需要多个期间或类别。")
        with st.expander("查看结果明细"):
            st.dataframe(visualization.format_table(df), hide_index=True, use_container_width=True,
                         height=visualization.table_height(df))
    else:
        st.dataframe(visualization.format_table(df),
                     hide_index=True, use_container_width=True,
                     height=visualization.table_height(df))
        chart_df = df
        if spec.get("metrics"):
            metric = st.selectbox("图表指标（不同单位分别展示）", spec["metrics"])
            dimensions = [c for c in df.columns if c not in spec["metrics"]]
            chart_df = df[dimensions + [metric]]
        with tempfile.TemporaryDirectory(prefix="retail_chart_") as chart_dir:
            path = visualization.make_chart(
                chart_df, os.path.join(chart_dir, "chart.png"), title=question
            )
            if path:
                with open(path, "rb") as chart_file:
                    st.image(chart_file.read(), use_container_width=True)
            else:
                st.caption(f"未生成图表：{spec.get('reason', '缺少可绘制数据')}。")

    st.subheader("结果解读")
    st.success(res.insight)
    st.caption(res.quality.limitations)


# ============================ 布局 ============================
with st.sidebar:
    st.header("零售经营与财务助手")
    st.caption("查报表 · 看变化 · 核对口径")
    st.info("默认使用离线规则，无模型调用费用。支持范围有限；未明确的条件会请求补充。")
    use_online = st.checkbox(
        "使用在线模型实验（可能产生费用，尚未实测）",
        value=False,
        disabled=not (config.llm_available() and config.MODEL),
    )
    if use_online:
        st.caption(f"模型：{config.MODEL}；当前仍受同一语义计划范围约束。")
    st.markdown("**演示数据**")
    date_start, date_end = database.order_date_range()
    st.caption(
        f"合成订单记录：{date_start} 至 {date_end}。今年/去年按北京时间解释，不改成数据年份。"
    )
    st.caption("财报覆盖2023-01至2026-10；新增64个报表及补充分析项目，金额按整数分存储。")
    with st.expander("查看数据库结构"):
        st.code(_schema(), language="text")
    st.markdown("**示例问题**（点击填入，再按「分析」）")
    for i, ex in enumerate(EXAMPLES):
        if st.button(ex, key=f"ex_{i}", use_container_width=True):
            st.session_state["question"] = ex
            st.session_state.pop("analysis_result", None)
            st.rerun()

st.title("零售经营与财务分析助手")
st.write("基于虚构零售企业的月度财报与销售明细，查询经营指标、拆解变化并核对报表关系。")
question_tab, statement_tab, definition_tab = st.tabs(
    ["问数据与分析", "模拟财务报表", "数据与指标口径"]
)
with question_tab:
    with st.form("ask_form"):
        st.text_input("你的问题：", key="question")
        submitted = st.form_submit_button("分析", type="primary")
    if submitted:
        run_analysis(st.session_state["question"], use_llm=use_online)
    elif "analysis_result" in st.session_state:
        run_analysis(
            st.session_state["analysis_question"], result=st.session_state["analysis_result"]
        )
    else:
        st.info(
            "输入或选择一个问题，然后点击「分析」。未写期间的财务查询默认使用最新完整模拟月，并明确显示。"
        )
with statement_tab:
    meta = business_analysis.metadata()
    if meta:
        with database.readonly_connection() as conn:
            months = [
                r[0]
                for r in conn.execute(
                    "SELECT month FROM financial_values WHERE code='revenue' ORDER BY month DESC"
                )
            ]
        month = st.selectbox("报告月份", months)
        st.caption(f"编制单位：{meta['company']}　报告期间：{month}　单位：人民币元")
        report_tabs = st.tabs(list(REPORT_NAMES.values()))
        for tab, (code, name) in zip(report_tabs, REPORT_NAMES.items()):
            with tab:
                report = business_analysis.statement_preview(month, code)
                if report.df is None:
                    st.warning(report.insight)
                else:
                    st.dataframe(
                        report.df.style.format(precision=2, thousands=",", na_rep="—"),
                        hide_index=True,
                        use_container_width=True,
                        height=600,
                    )
                    st.download_button(
                        "导出此报表 CSV",
                        report.df.to_csv(index=False, float_format="%.2f").encode("utf-8-sig"),
                        file_name=f"青禾零售_模拟_{name}_{month}_元.csv",
                        mime="text/csv",
                        key=f"download_{code}",
                    )
        st.caption("缩进项目为费用明细，不应与其小计重复相加。权益表为简化的未分配利润变动展示。")
    else:
        st.info("请用桌面的离线启动文件更新模拟数据库后，再查看财报。")
with definition_tab:
    st.markdown("**数据来源：全部模拟，不对应任何真实企业。**")
    st.write(
        "订单销售额为退款前成交额；财报营业收入已扣模拟退款。余额项目取期末，流量按期间累计，比率由分子和分母重新计算。"
    )
    st.write(
        "可尝试：上个月净利润、今年管理费用、最近三个月应收账款趋势、2025年和2026年营业收入对比、净利润与经营现金流为什么不同。"
    )
    st.dataframe(
        [
            {
                "报表": REPORT_NAMES[a.report],
                "项目": a.label,
                "期间口径": {"flow": "期间累计", "stock": "期末余额", "opening": "期初余额"}[
                    a.kind
                ],
                "所属小计": a.parent,
            }
            for a in ACCOUNTS
        ],
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("模拟假设与适用边界"):
        for note in business_analysis.assumptions():
            st.write("• " + note)
        st.write(
            "按公开报表结构组织的简化教学数据，不代表完整准则合规、真实企业业绩或真实经营因果。"
        )
        st.write(
            "全公司费用未分摊到渠道/品类，因此不输出虚构的分渠道净利润。库存SKU数量和浏览转化率尚无数据。"
        )

st.caption(
    "个人演示原型，数据为合成数据。结果结构检查不是答案正确率。"
    "在线实验仅向模型发送问题、结构和口径用于生成SQL；结论由本地模板汇总。默认离线不发送。"
)
