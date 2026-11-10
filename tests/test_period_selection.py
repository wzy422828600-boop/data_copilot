from datetime import date

import pytest

from data_copilot.periods import InputNeeded, resolve
from scripts.upgrade_demo_data import months_between

AVAILABLE = tuple(months_between("2023-01", "2026-10"))
REF = date(2026, 11, 5)


@pytest.mark.parametrize(
    "question,current,previous",
    [
        ("为什么销售额下降", ("2026-10",), ("2026-09",)),
        ("上个月净利润同比", ("2026-10",), ("2025-10",)),
        ("2026年9月净利润比10月", ("2026-09",), ("2026-10",)),
        ("2026年9月和10月对比", ("2026-10",), ("2026-09",)),
        ("2026年九月比十月净利润", ("2026-09",), ("2026-10",)),
        ("2026-10比2025-10", ("2026-10",), ("2025-10",)),
        (
            "2026年第三季度同比",
            ("2026-07", "2026-08", "2026-09"),
            ("2025-07", "2025-08", "2025-09"),
        ),
    ],
)
def test_ordering_relative_dates_and_comparison(question, current, previous):
    p = resolve(question, AVAILABLE, reference_date=REF, compare=True)
    assert p.current == current and p.previous == previous


def test_month_range_and_recent_months():
    assert resolve("2026年8-10月净利润", AVAILABLE).current == ("2026-08", "2026-09", "2026-10")
    assert resolve("最近三个月净利润", AVAILABLE).current == ("2026-08", "2026-09", "2026-10")


def test_year_comparison_aligns_months_when_current_year_is_partial():
    p = resolve("今年和去年营业收入对比", AVAILABLE, reference_date=REF, compare=True)
    assert len(p.current) == len(p.previous) == 10
    assert p.current[-1] == "2026-10" and p.previous[-1] == "2025-10"


def test_bare_month_and_missing_comparison_period_are_not_zero():
    for q in ("11月净利润", "2026年11月净利润", "2023年1月同比"):
        with pytest.raises(InputNeeded):
            resolve(q, AVAILABLE, reference_date=REF, compare=True)
