"""
3_ask.py —— 命令行入口
用法:  python 3_ask.py "哪个品类销售额最高？"
流程:  问题 -> SQL -> 安全校验 -> 执行 -> 打印表格 -> 出图 -> 一句洞察
"""
import os
import sys
import importlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # 任意目录都能运行
eng = importlib.import_module("2_engine")   # 文件名以数字开头，用 import_module 加载

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUT, exist_ok=True)


def main():
    if len(sys.argv) < 2:
        print('用法: python 3_ask.py "你的问题"')
        print('示例: python 3_ask.py "线上和线下哪个销售额高？"')
        return

    question = sys.argv[1]
    mode_tag = "Claude 在线" if eng.llm_available() else "离线规则(未检测到 ANTHROPIC_API_KEY)"
    print("=" * 64)
    print(f"问题: {question}")
    print(f"模式: {mode_tag}   模型: {eng.MODEL}")
    print("=" * 64)

    conn = eng.connect()
    schema = eng.get_schema(conn)

    # 1) 生成 SQL
    sql, mode = eng.nl_to_sql(question, schema)
    print("\n[生成的 SQL]")
    print(sql)

    # 2) 安全校验
    ok, why = eng.is_safe_sql(sql)
    if not ok:
        print(f"\n[已拦截] {why}")
        return

    # 3) 执行
    try:
        df = eng.run_sql(conn, sql)
    except Exception as e:
        print(f"\n[执行出错] {e}")
        return
    print(f"\n[结果] {len(df)} 行")
    print(df.head(15).to_string(index=False))

    # 4) 出图
    chart = eng.make_chart(df, os.path.join(OUT, "last_chart.png"))
    if chart:
        print(f"\n[图表] 已保存 -> {chart}")

    # 5) 洞察
    print("\n[洞察]")
    print(eng.summarize(question, df))
    conn.close()


if __name__ == "__main__":
    main()
