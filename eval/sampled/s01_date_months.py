"""s01 日期区间按自然月切分。

抽样任务（见 eval/SAMPLED.md）：**规格在需求里给全**，不需要任何仓库外知识。
用途是当阴性对照 —— 测 base 在这类任务上的天然通过率。
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tasks._util import run_python  # noqa: E402

_STUB = '''"""把日期区间按自然月切开。"""


def split_by_month(start: str, end: str) -> list:
    """把闭区间 [start, end] 按自然月切分成若干段。

    两个参数都是 "YYYY-MM-DD" 字符串。
    返回 [(段首, 段末), ...]，首段从 start 起、末段到 end 止，
    中间每段都是完整自然月；闭区间，段与段之间不重叠也不留空。

    ⚠️ 还没实现。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只覆盖最基本的两条。"""

from reporting.months import split_by_month


def test_single_month():
    assert split_by_month("2024-05-03", "2024-05-20") == [("2024-05-03", "2024-05-20")]


def test_two_months():
    assert split_by_month("2024-05-20", "2024-06-02") == [
        ("2024-05-20", "2024-05-31"),
        ("2024-06-01", "2024-06-02"),
    ]
'''

_CROSS_Q1 = '''
from reporting.months import split_by_month

got = split_by_month("2024-01-15", "2024-03-10")
want = [("2024-01-15", "2024-01-31"),
        ("2024-02-01", "2024-02-29"),   # 2024 是闰年
        ("2024-03-01", "2024-03-10")]
assert got == want, f"期望 {want}，实际 {got}"
'''

_SINGLE_DAY = '''
from reporting.months import split_by_month

got = split_by_month("2024-07-09", "2024-07-09")
assert got == [("2024-07-09", "2024-07-09")], got
'''

_WHOLE_YEAR = '''
from reporting.months import split_by_month

got = split_by_month("2023-11-01", "2024-02-01")
assert len(got) == 4, f"应切 4 段，实际 {len(got)}：{got}"
assert got[0] == ("2023-11-01", "2023-11-30"), got[0]
assert got[-1] == ("2024-02-01", "2024-02-01"), got[-1]
'''

_REVERSED = '''
from reporting.months import split_by_month

try:
    split_by_month("2024-05-20", "2024-05-03")
except ValueError:
    pass
else:
    raise AssertionError("start 晚于 end 时应抛 ValueError")
'''

_BAD_FORMAT = '''
from reporting.months import split_by_month

for bad in ("2024/05/03", "20240503", "2024-13-01", ""):
    try:
        split_by_month(bad, "2024-06-01")
    except ValueError:
        continue
    raise AssertionError(f"{bad!r} 不是合法的 YYYY-MM-DD，应抛 ValueError")
'''

_CHECKS = [
    ("跨三个月（含闰年 2 月）", _CROSS_Q1),
    ("单日区间", _SINGLE_DAY),
    ("跨年四段", _WHOLE_YEAR),
    ("start 晚于 end 抛 ValueError", _REVERSED),
    ("非法日期格式抛 ValueError", _BAD_FORMAT),
]


def setup(wd):
    (wd / "reporting").mkdir(parents=True, exist_ok=True)
    (wd / "reporting" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "reporting" / "months.py").write_text(_STUB, encoding="utf-8")
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "tests" / "test_months.py").write_text(_TESTS, encoding="utf-8")


def verify(wd):
    f = wd / "tests" / "test_months.py"
    if not f.exists():
        return False, "缺少 tests/test_months.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_months.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试失败 {tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code, timeout=60)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            msg = detail[-1] if detail else ""
            return False, f"未满足：{label} —— {msg}"
    return True, "5 项行为符合需求"


SOLUTION = '''"""把日期区间按自然月切开。"""
import datetime


def _parse(s):
    if not isinstance(s, str) or len(s) != 10:
        raise ValueError("日期必须是 YYYY-MM-DD")
    try:
        d = datetime.date.fromisoformat(s)
    except ValueError:
        raise ValueError("日期必须是 YYYY-MM-DD") from None
    if d.isoformat() != s:
        raise ValueError("日期必须是 YYYY-MM-DD")
    return d


def split_by_month(start, end):
    a, b = _parse(start), _parse(end)
    if a > b:
        raise ValueError("start 不能晚于 end")

    out = []
    cur = a
    while cur <= b:
        if cur.month == 12:
            month_end = datetime.date(cur.year, 12, 31)
            nxt = datetime.date(cur.year + 1, 1, 1)
        else:
            month_end = datetime.date(cur.year, cur.month + 1, 1) - datetime.timedelta(days=1)
            nxt = datetime.date(cur.year, cur.month + 1, 1)
        seg_end = min(month_end, b)
        out.append((cur.isoformat(), seg_end.isoformat()))
        cur = nxt
    return out
'''

_MUT_LAST_SEGMENT = SOLUTION.replace(
    "seg_end = min(month_end, b)", "seg_end = month_end"
)

_MUT_NO_REVERSE_CHECK = SOLUTION.replace(
    '''    if a > b:
        raise ValueError("start 不能晚于 end")
''',
    '''    if a > b:
        return []
''',
)

MUTANTS = {
    "末段不截到 end（多算整月）": _MUT_LAST_SEGMENT,
    "start>end 返回空列表（不报错）": _MUT_NO_REVERSE_CHECK,
}

TASK = {
    "id": "s01",
    "name": "日期区间按自然月切分",
    "prompt": (
        "在 reporting/months.py 里实现 split_by_month(start, end)。\n"
        "需求如下（规格已给全，不需要查任何外部文档）：\n"
        "- 两个参数都是 \"YYYY-MM-DD\" 字符串，闭区间。\n"
        "- 返回 [(段首, 段末), ...]：首段从 start 起，末段到 end 止，"
        "中间每段都是完整自然月；段与段之间不重叠也不留空。\n"
        "- 段首/段末都是 \"YYYY-MM-DD\" 字符串。\n"
        "- start 晚于 end → 抛 ValueError。\n"
        "- 任何不是严格 \"YYYY-MM-DD\" 的输入（如 2024/05/03、20240503、空串）→ 抛 ValueError。\n"
        "既有测试在 tests/test_months.py，必须保持原样并通过。\n"
        "改完自己跑一遍 `python -m pytest tests -q` 确认。"
    ),
    "setup": setup,
    "verify": verify,
}
