"""Explicit month selection for statements and comparisons; no silent date loss."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo


class InputNeeded(ValueError):
    pass


@dataclass(frozen=True)
class Periods:
    current: tuple[str, ...]
    previous: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


def shift(month, delta):
    year, mon = map(int, month.split("-"))
    number = year * 12 + mon - 1 + delta
    return f"{number // 12:04d}-{number % 12 + 1:02d}"


def label(months):
    if len(months) == 1:
        return months[0]
    ordered = sorted(months)
    if all(shift(a, 1) == b for a, b in zip(ordered, ordered[1:])):
        return f"{ordered[0]}至{ordered[-1]}"
    return "、".join(ordered)


def resolve(question, available, *, reference_date: date | None = None, compare=False):
    ref = reference_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    available = tuple(sorted(available))
    if not available:
        raise InputNeeded("没有可用的月度数据，请先更新模拟数据库。")
    latest = available[-1]
    q = question.replace("上个月", "上月").replace("这个月", "本月").replace("这月", "本月")
    q = q.replace("去年同期", "同比").replace("上年同期", "同比")
    q = q.replace("本年", "今年").replace("上年", "去年")
    q = q.replace("上半年", "1月至6月").replace("下半年", "7月至12月")
    q = re.sub(r"过去(?=[一二三四五六七八九十0-9]+个?月)", "最近", q)
    q = re.sub(r"(?<!\d)((?:19|20)\d{2})(?!\d|年|-|/)", r"\1年", q)
    numbers = {
        "一": 1,
        "二": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "十": 10,
        "十一": 11,
        "十二": 12,
    }
    q = re.sub(r"(十一|十二|[一二三四五六七八九十])月", lambda m: f"{numbers[m[1]]}月", q)
    notes = []
    if re.search(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d日|\d号|本周|上周|最近\d+天", q):
        raise InputNeeded("财报按月编制，暂不支持日级或周级财务口径，请指定年月。")
    for token, year in (("今年", ref.year), ("去年", ref.year - 1), ("前年", ref.year - 2)):
        if token in q:
            q = q.replace(token, f"{year}年")
            notes.append(f"{token}按参考日{ref}解释为{year}年")
    for token, month in (("本月", f"{ref:%Y-%m}"), ("上月", shift(f"{ref:%Y-%m}", -1))):
        if token in q:
            year, mon = month.split("-")
            q = q.replace(token, f"{year}年{int(mon)}月")
            notes.append(f"{token}按参考日{ref}解释为{month}")
    q = re.sub(r"(?<!\d)(20\d{2})[-/](\d{1,2})(?!\d)", r"\1年\2月", q)
    q = re.sub(r"(\d{1,2})\s*[-至到]\s*(\d{1,2})月", r"\1月至\2月", q)
    years = list(dict.fromkeys(re.findall(r"((?:19|20)\d{2})年", q)))
    recent = re.search(r"(?:最近|近)([1-9]\d*|[一二三四五六七八九十])个?月", q)
    quarters = list(re.finditer(r"第?([一二三四1-4])季度|[qQ]([1-4])", q))
    quarter = quarters[0] if quarters else None
    groups = []
    annual = False
    if recent:
        raw = recent[1]
        n = int(raw) if raw.isdigit() else numbers[raw]
        if n > 24:
            raise InputNeeded("最近月份范围请限制在24个月内。")
        current = tuple(shift(latest, i) for i in range(-n + 1, 1))
        notes.append(f"最近{n}个月以最新完整模拟月{latest}为终点")
    elif quarter:
        if len(years) != 1:
            raise InputNeeded("请为季度补充一个年份，例如2026年第三季度。")
        if len(quarters) > 2 or (len(quarters) == 2 and not compare):
            raise InputNeeded("请明确要对比的两个季度，或改用明确的月份区间。")
        quarter_groups = []
        for selected in quarters:
            raw = selected[1] or selected[2]
            n = int(raw) if raw.isdigit() else numbers[raw]
            quarter_groups.append(tuple(f"{years[0]}-{m:02d}" for m in range(n * 3 - 2, n * 3 + 1)))
        current = quarter_groups[0]
        if len(quarter_groups) == 2:
            marker = re.search(r"相比|相较|(?<![同环对])比(?!率|例)", q)
            groups = (
                quarter_groups
                if marker and quarters[0].end() <= marker.start() < quarters[1].start()
                else sorted(quarter_groups, reverse=True)
            )
    else:
        matches = list(re.finditer(r"(?:(20\d{2})年\s*)?(\d{1,2})月", q))
        months = []
        active_year = years[0] if len(years) == 1 else None
        for match in matches:
            year, mon = match[1] or active_year, int(match[2])
            if not year:
                raise InputNeeded("月份缺少年份，请补充例如2026年10月。")
            if not 1 <= mon <= 12:
                raise InputNeeded("月份必须在1至12月之间。")
            active_year = year
            months.append(f"{year}-{mon:02d}")
        if months:
            if len(months) == 2 and re.search(r"月\s*(?:到|至|-)\s*(?:20\d{2}年)?\d{1,2}月", q):
                first, last = months
                if first > last:
                    raise InputNeeded("月份区间起点晚于终点，请确认年份。")
                current = tuple(shift(first, i) for i in range(25) if shift(first, i) <= last)
                if current[-1] != last:
                    raise InputNeeded("月区间请限制在24个月内。")
            else:
                months = list(dict.fromkeys(months))
                if compare and len(months) == 2:
                    # Before 比 is current; 和/与对比 defaults to chronological order.
                    marker = re.search(r"相比|相较|(?<![同环对])比(?!率|例)", q)
                    if marker and matches[0].end() <= marker.start() < matches[1].start():
                        groups = [(months[0],), (months[1],)]
                    else:
                        groups = [(max(months),), (min(months),)]
                current = tuple(sorted(months))
        elif years:
            annual = True
            year_groups = []
            for year in years:
                group = tuple(m for m in available if m.startswith(year + "-"))
                if not group:
                    raise InputNeeded(f"记录范围为{available[0]}至{latest}，没有{year}年的数据。")
                if len(group) < 12:
                    if "全年" in q or "完整年度" in q:
                        raise InputNeeded(f"{year}年只有截至{group[-1]}的数据，不能当成完整全年。")
                    notes.append(f"{year}年当前仅覆盖{group[0]}至{group[-1]}，并非完整全年")
                year_groups.append(group)
            if len(year_groups) > 1:
                if len(year_groups) != 2 or not compare:
                    raise InputNeeded("多个年份请明确要对比的两个期间，或使用按月趋势。")
                year_positions = list(re.finditer(r"(?:19|20)\d{2}年", q))
                marker = re.search(r"相比|相较|(?<![同环对])比(?!率|例)", q)
                directed = (
                    marker and year_positions[0].end() <= marker.start() < year_positions[1].start()
                )
                current_group, previous_group = (
                    year_groups if directed else sorted(year_groups, reverse=True)
                )
                common = {m[-2:] for m in current_group} & {m[-2:] for m in previous_group}
                groups = [
                    tuple(m for m in current_group if m[-2:] in common),
                    tuple(m for m in previous_group if m[-2:] in common),
                ]
                if len(common) < 12:
                    notes.append("跨年比较已对齐相同月份范围，避免全年与年中累计直接比较")
            current = year_groups[0]
        else:
            if any(token in q for token in ("季度", "上半年", "下半年", "年初", "年末")):
                raise InputNeeded("请补充明确年份、年月或季度。")
            current = (latest,)
            notes.append(f"未指定期间，使用最新完整模拟月{latest}")
    previous = ()
    if groups:
        current, previous = groups
    elif compare:
        if "同比" in q:
            previous = tuple(shift(m, -12) for m in current)
        elif annual:
            previous = tuple(shift(m, -12) for m in current)
            notes.append("年度累计比较采用上年相同月份")
        else:
            if len(current) > 1 and not all(shift(a, 1) == b for a, b in zip(current, current[1:])):
                raise InputNeeded("非连续月份的比较请分别指定本期和基期。")
            previous = tuple(shift(m, -len(current)) for m in current)
            notes.append("比较期使用紧邻之前的同等月份跨度")
    missing = sorted(set(current + previous) - set(available))
    if missing:
        raise InputNeeded(
            f"记录范围为{available[0]}至{latest}，缺少{'、'.join(missing)}，不能按0处理。"
        )
    if current == previous:
        raise InputNeeded("本期和比较期相同，请指定不同期间。")
    return Periods(tuple(current), tuple(previous), tuple(notes))
