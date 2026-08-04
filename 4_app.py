"""
4_app.py —— Streamlit 网页界面（作品集演示用）
运行:  streamlit run 4_app.py
左侧显示数据库结构和示例问题；主区输入问题，返回 SQL + 表格 + 图表 + 洞察。
"""
import os
import sys
import importlib
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 任意目录都能 run
eng = importlib.import_module("2_engine")

st.set_page_config(page_title="数据分析 Copilot", page_icon="📊", layout="wide")

# ---- 侧边栏：结构 + 状态 + 示例 -------------------------------------------
conn = eng.connect()
schema = eng.get_schema(conn)

with st.sidebar:
    st.header("📊 数据分析 Copilot")
    st.caption("自然语言 → SQL → 图表 → 洞察")
    online = eng.llm_available()
    if online:
        st.success(f"Claude 在线（{eng.MODEL}）")
    else:
        st.warning("离线规则模式：设置 ANTHROPIC_API_KEY 解锁任意自然语言提问")
    st.markdown("**数据库结构**")
    st.code(schema, language="text")
    st.markdown("**试试这些问题**")
    examples = [
        "哪个品类销售额最高？",
        "每月销售额的趋势是怎样的？",
        "线上和线下哪个销售额高？",
        "各会员等级的销售额和订单数？",
        "哪些城市贡献了最多销售额？",
        "整体客单价是多少？",
    ]
    for ex in examples:
        if st.button(ex, use_container_width=True):
            st.session_state["q"] = ex

# ---- 主区 -----------------------------------------------------------------
st.title("对话式数据分析助手")
st.write("用中文问一个关于销售数据的问题，助手会自动写 SQL、查询、画图并给出洞察。")

q = st.text_input("你的问题：", value=st.session_state.get("q", "哪个品类销售额最高？"))

if st.button("分析", type="primary") or q:
    sql, mode = eng.nl_to_sql(q, schema)
    ok, why = eng.is_safe_sql(sql)

    st.subheader("① 生成的 SQL")
    st.code(sql, language="sql")

    if not ok:
        st.error(f"已拦截：{why}")
    else:
        try:
            df = eng.run_sql(conn, sql)
            c1, c2 = st.columns([1, 1])
            with c1:
                st.subheader("② 查询结果")
                st.dataframe(df, use_container_width=True, height=340)
            with c2:
                st.subheader("③ 自动图表")
                path = eng.make_chart(df, os.path.join(
                    os.path.dirname(__file__), "outputs", "app_chart.png"))
                if path:
                    st.image(path, use_container_width=True)
                else:
                    st.info("该结果不适合出图（单值或非数值）。")
            st.subheader("④ 洞察")
            st.success(eng.summarize(q, df))
        except Exception as e:
            st.error(f"执行出错：{e}")

st.caption("演示数据为合成数据，仅供展示。SQL 经过只读安全校验，拒绝任何写操作。")
