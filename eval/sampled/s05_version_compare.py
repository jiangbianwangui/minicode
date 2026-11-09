"""s05 版本号比较（规则全给）。

抽样任务（见 eval/SAMPLED.md）：规格全给，不需要仓库外知识。
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tasks._util import run_python  # noqa: E402

_STUB = '''"""比较两个版本号。"""


def compare(a: str, b: str) -> int:
    """返回 -1 / 0 / 1（a 小于 / 等于 / 大于 b）。

    格式：1~3 段十进制数字，用 . 分隔，可选后缀 -alpha / -beta / -rc。
    缺失的段按 0 处理（"1.2" 等价于 "1.2.0"）。
    同一版本号下，预发布标记的顺序是 alpha < beta < rc < 无后缀（正式版）。

    ⚠️ 还没实现。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：基本大小关系。"""

from versioning.version import compare


def test_basic_order():
    assert compare("1.0.0", "2.0.0") == -1
    assert compare("2.0.0", "1.0.0") == 1
    assert compare("1.2.3", "1.2.3") == 0
'''

_NUMERIC = '''
from versioning.version import compare

assert compare("1.10.0", "1.9.0") == 1, "按数值比较：1.10 > 1.9（不是按字符串）"
assert compare("1.9.0", "1.10.0") == -1
assert compare("10.0.0", "9.9.9") == 1
'''

_PADDING = '''
from versioning.version import compare

assert compare("1.2", "1.2.0") == 0, "'1.2' 等价于 '1.2.0'"
assert compare("1", "1.0.0") == 0, "'1' 等价于 '1.0.0'"
assert compare("1.2", "1.2.1") == -1
'''

_PRERELEASE = '''
from versioning.version import compare

seq = ["1.0.0-alpha", "1.0.0-beta", "1.0.0-rc", "1.0.0"]
for lo, hi in zip(seq, seq[1:]):
    assert compare(lo, hi) == -1, f"{lo} 应小于 {hi}，实际 {compare(lo, hi)}"
assert compare("1.0.0-alpha", "1.0.1-alpha") == -1, "先比版本号再比预发布"
assert compare("1.0.0-rc", "1.0.1-alpha") == -1
'''

_ANTISYM = '''
from versioning.version import compare

pairs = [("1.2.3", "1.3"), ("2.0.0-beta", "2.0.0"), ("1.0.0-rc", "1.0.0-rc")]
for x, y in pairs:
    assert compare(x, y) == -compare(y, x), f"{x} 与 {y} 不满足对称性"
'''

_BAD = '''
from versioning.version import compare

bads = ["1.2.3.4", "abc", "", "1..2", "v1.2", "1.2.3-gamma", "1.2.3-", "1.-2"]
for bad in bads:
    try:
        compare(bad, "1.0.0")
    except ValueError:
        continue
    raise AssertionError(f"{bad!r} 不是合法版本号，应抛 ValueError")
'''

_CHECKS = [
    ("按数值比较（1.10 > 1.9）", _NUMERIC),
    ("缺失段补 0", _PADDING),
    ("预发布顺序 alpha<beta<rc<正式", _PRERELEASE),
    ("对称性", _ANTISYM),
    ("非法格式抛 ValueError", _BAD),
]


def setup(wd):
    (wd / "versioning").mkdir(parents=True, exist_ok=True)
    (wd / "versioning" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "versioning" / "version.py").write_text(_STUB, encoding="utf-8")
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "tests" / "test_version.py").write_text(_TESTS, encoding="utf-8")


def verify(wd):
    f = wd / "tests" / "test_version.py"
    if not f.exists():
        return False, "缺少 tests/test_version.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_version.py 必须保持原样）"

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


SOLUTION = '''"""比较两个版本号。"""

_PRE_RANK = {"alpha": 0, "beta": 1, "rc": 2, None: 3}


def _parse(v):
    if not isinstance(v, str) or not v or v.endswith("-"):
        raise ValueError(f"非法版本号: {v!r}")

    body, sep, pre = v.partition("-")
    pre = pre if sep else None
    if pre is not None and pre not in ("alpha", "beta", "rc"):
        raise ValueError(f"非法预发布标记: {v!r}")

    parts = body.split(".")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"非法版本号: {v!r}")
    nums = []
    for p in parts:
        if not p.isdigit():
            raise ValueError(f"非法版本号: {v!r}")
        nums.append(int(p))
    while len(nums) < 3:
        nums.append(0)
    return nums, _PRE_RANK[pre]


def compare(a, b):
    na, pa = _parse(a)
    nb, pb = _parse(b)
    if na != nb:
        return -1 if na < nb else 1
    if pa != pb:
        return -1 if pa < pb else 1
    return 0
'''

_MUT_STRING = SOLUTION.replace(
    """    if na != nb:
        return -1 if na < nb else 1""",
    """    sa = ".".join(str(x) for x in na)
    sb = ".".join(str(x) for x in nb)
    if sa != sb:
        return -1 if sa < sb else 1""",
)

_MUT_NO_PRE = SOLUTION.replace(
    """    if pa != pb:
        return -1 if pa < pb else 1
""",
    "",
)

MUTANTS = {
    "按字符串比较（1.9 > 1.10）": _MUT_STRING,
    "忽略预发布标记（1.0.0-rc == 1.0.0）": _MUT_NO_PRE,
}

TASK = {
    "id": "s05",
    "name": "版本号比较",
    "prompt": (
        "在 versioning/version.py 里实现 compare(a, b)，返回 -1 / 0 / 1。\n"
        "需求如下（规格已给全，不需要查任何外部文档）：\n"
        "- 版本号是 1~3 段十进制数字，用 . 分隔；段数不足时按 0 补齐"
        "（\"1.2\" 等价于 \"1.2.0\"，\"1\" 等价于 \"1.0.0\"）。\n"
        "- 数字段按数值比较，不是按字符串（1.10 > 1.9）。\n"
        "- 可选后缀 -alpha / -beta / -rc；同一版本号下顺序是"
        " alpha < beta < rc < 无后缀（正式版）。先比版本号，再比后缀。\n"
        "- 非法输入抛 ValueError：段数超过 3、段里不是纯数字、空串、"
        "空段、后缀不是这三个之一、以 - 结尾。\n"
        "既有测试在 tests/test_version.py，必须保持原样并通过。\n"
        "改完自己跑一遍 `python -m pytest tests -q` 确认。"
    ),
    "setup": setup,
    "verify": verify,
}
