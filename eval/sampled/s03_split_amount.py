"""s03 金额按权重分摊（最大余数法）。

抽样任务（见 eval/SAMPLED.md）：规格全给，不需要仓库外知识。
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tasks._util import run_python  # noqa: E402

_STUB = '''"""把一笔钱按权重分摊成整数分。"""


def split_amount(total_cents: int, weights: list) -> list:
    """按权重把 total_cents 分摊给各项，返回每项分到的整数分。

    规则：
    - 每项先拿 floor(total_cents * w_i / sum(weights))；
    - 剩下的分（一定少于项数）按「小数部分从大到小」依次各补 1 分；
      小数部分相同时，索引小的先补；
    - 返回值之和必须严格等于 total_cents。

    ⚠️ 还没实现。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：等分与不等分各一条。"""

from billing.split import split_amount


def test_equal_three_ways():
    assert split_amount(10000, [1, 1, 1]) == [3334, 3333, 3333]


def test_sums_to_total():
    got = split_amount(7, [2, 3, 5])
    assert sum(got) == 7
'''

_EQUAL = '''
from billing.split import split_amount

got = split_amount(10000, [1, 1, 1])
assert got == [3334, 3333, 3333], (
    f"期望 [3334, 3333, 3333]（余下的 1 分给小数部分最大的，三者相同则给索引 0），实际 {got}")
'''

_LARGEST_REMAINDER = '''
from billing.split import split_amount

# 精确值 16.67 / 33.33 / 50.00 → floor 16/33/50 = 99，差 1 分
# 小数部分 0.67 / 0.33 / 0.00 → 补给索引 0
got = split_amount(100, [1, 2, 3])
assert got == [17, 33, 50], f"期望 [17, 33, 50]，实际 {got}"

# 精确值 1.4 / 2.1 / 3.5 → floor 1/2/3 = 6，差 1 分；小数 0.4/0.1/0.5 → 补给索引 2
got2 = split_amount(7, [2, 3, 5])
assert got2 == [1, 2, 4], f"期望 [1, 2, 4]，实际 {got2}"
'''

_CONSERVED = '''
from billing.split import split_amount

cases = [
    (10000, [1, 1, 1]),
    (999, [3, 1]),
    (12345, [7, 11, 13, 17]),
    (1, [1, 1, 1, 1]),
    (1000000, [1, 2]),
]
for total, ws in cases:
    got = split_amount(total, ws)
    assert len(got) == len(ws), f"{total}/{ws} 返回长度不对：{got}"
    assert sum(got) == total, f"{total}/{ws} 总和 {sum(got)} != {total}：{got}"
    assert all(isinstance(x, int) and x >= 0 for x in got), f"必须是非负整数分：{got}"
'''

_BAD_WEIGHTS = '''
from billing.split import split_amount

for ws in ([1, 0, 2], [1, -3], [0]):
    try:
        split_amount(100, ws)
    except ValueError:
        continue
    raise AssertionError(f"权重含 0 或负数（{ws}）时应抛 ValueError")
'''

_BAD_TOTAL = '''
from billing.split import split_amount

for total, ws in ((0, [1]), (-5, [1, 2]), (100, [])):
    try:
        split_amount(total, ws)
    except ValueError:
        continue
    raise AssertionError(f"total={total} weights={ws} 非法，应抛 ValueError")
'''

_NO_MUTATE = '''
from billing.split import split_amount

ws = [1, 2, 3]
split_amount(100, ws)
assert ws == [1, 2, 3], f"不得修改传入的 weights，实际 {ws}"
'''

_CHECKS = [
    ("三等价（余数给索引 0）", _EQUAL),
    ("最大余数法（小数部分大的先补）", _LARGEST_REMAINDER),
    ("总和守恒", _CONSERVED),
    ("权重含 0/负数抛 ValueError", _BAD_WEIGHTS),
    ("总额非法/权重为空抛 ValueError", _BAD_TOTAL),
    ("不修改入参", _NO_MUTATE),
]


def setup(wd):
    (wd / "billing").mkdir(parents=True, exist_ok=True)
    (wd / "billing" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "billing" / "split.py").write_text(_STUB, encoding="utf-8")
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "tests" / "test_split.py").write_text(_TESTS, encoding="utf-8")


def verify(wd):
    f = wd / "tests" / "test_split.py"
    if not f.exists():
        return False, "缺少 tests/test_split.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_split.py 必须保持原样）"

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
    return True, "6 项行为符合需求"


SOLUTION = '''"""把一笔钱按权重分摊成整数分。"""


def split_amount(total_cents, weights):
    if not weights:
        raise ValueError("weights 不能为空")
    if total_cents <= 0:
        raise ValueError("total_cents 必须为正")
    if any(w <= 0 for w in weights):
        raise ValueError("权重必须都是正数")

    total_w = sum(weights)
    parts = []
    remainders = []
    for i, w in enumerate(weights):
        q, r = divmod(total_cents * w, total_w)
        parts.append(q)
        remainders.append((r, i))

    left = total_cents - sum(parts)
    remainders.sort(key=lambda t: (-t[0], t[1]))
    for _, i in remainders[:left]:
        parts[i] += 1
    return parts
'''

_MUT_TRUNCATE = SOLUTION.replace(
    "    left = total_cents - sum(parts)\n"
    "    remainders.sort(key=lambda t: (-t[0], t[1]))\n"
    "    for _, i in remainders[:left]:\n"
    "        parts[i] += 1\n"
    "    return parts\n",
    "    return parts\n",
)

_MUT_TIE_LAST = SOLUTION.replace(
    "key=lambda t: (-t[0], t[1])", "key=lambda t: (-t[0], -t[1])"
)

MUTANTS = {
    "只向下取整不补余数（总和对不上）": _MUT_TRUNCATE,
    "余数相同时给索引大的（顺序反了）": _MUT_TIE_LAST,
}

TASK = {
    "id": "s03",
    "name": "金额按权重分摊",
    "prompt": (
        "在 billing/split.py 里实现 split_amount(total_cents, weights)。\n"
        "需求如下（规格已给全，不需要查任何外部文档）：\n"
        "- 每项先拿 floor(total_cents * w_i / sum(weights))（整数分）。\n"
        "- 剩下的分（一定少于项数）按「小数部分从大到小」依次各补 1 分；"
        "小数部分相同时，索引小的先补。\n"
        "- 返回值之和必须严格等于 total_cents。\n"
        "- weights 为空、total_cents <= 0、权重含 0 或负数 → 抛 ValueError。\n"
        "- 不得修改传入的 weights。\n"
        "既有测试在 tests/test_split.py，必须保持原样并通过。\n"
        "改完自己跑一遍 `python -m pytest tests -q` 确认。"
    ),
    "setup": setup,
    "verify": verify,
}
