"""t33：SLA 分级与超时预算 —— 四条"反直觉"的平台约定

## 缺的是什么知识

`.sla_policy` 里只有等级名（P0/P1/P2），**所有数字都不在仓库里**：

1. 每个等级的超时预算；
2. 未知/缺失等级该归到哪一档；
3. 用到总预算的百分之几该走降级；
4. 每个等级允许重试几次。

## 为什么这四条必须靠查文档（而不是推理）

每一条都是**反直觉**的，而且方向都是"越重要的反而越克制"：

| 直觉 | 平台约定 | 为什么直觉是错的 |
| --- | --- | --- |
| P0 最重要 → 预算给最长 | P0 只有 **800ms**（最短） | P0 面向前台，宁可快速失败降级，也不让用户干等 |
| P0 最重要 → 多给重试 | P0 **不允许重试** | 重试只会把用户等待翻倍，P0 要的是立刻降级 |
| 未知等级 → 按最宽松的 P2 | 未知 → 按 **P1** | 未知流量不能享受最宽松待遇，也不该直接拒绝 |
| 留一点余量 → 80%/90% 降级 | **70%** 就降级 | 剩下 30% 要留给"降级路径本身"的耗时 |

**这四条都无法从代码/测试/注释推出**，而且都**不会报错**：
预算给长了，测试照样过，只是线上 P0 请求在用户面前转 8 秒；重试给多了，
只是同一笔支付被重试两次。

## 判卷：四条独立的行为检查

- 预算表：`budget_ms("P0"/"P1"/"P2")` 三个精确值
- 未知等级：`None` / `"P9"` 都要落到 P1 的预算
- 降级阈值：`charge` 累计到 68.75% 不降级，到 71.25% 必须降级（把 70% 夹在中间）
- 重试次数：`max_retries` 三档分别是 0 / 1 / 2

不读源码常量，只调函数看返回值 —— 常量名换掉、写成 dict、写成 if-else 都能过。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""SLA 超时预算。"""
'''

_POLICY = '''"""SLA 等级定义（平台组维护，不要修改）。"""

SLA_LEVELS = ("P0", "P1", "P2")


class UnknownLevel(Exception):
    """等级不在 SLA_LEVELS 内。"""
'''

_BUDGET = '''"""按 SLA 等级分配超时预算。

⚠️ 还没实现。

平台组只在这里放了等级名；**具体数值由 SLA 文档规定，不在仓库里**：
每个等级的预算、未知等级的归属、降级阈值、各等级允许的重试次数。
"""


def budget_ms(level) -> int:
    """返回该等级一个请求的总预算（毫秒）。level 可能是 None 或未知值。"""
    raise NotImplementedError("尚未实现")


def max_retries(level) -> int:
    """返回该等级允许的重试次数。"""
    raise NotImplementedError("尚未实现")


class Deadline:
    """一次请求的预算账本。

    用法：每完成一段工作就调用 charge(耗时毫秒)，
    编排器在开始下一段前检查 should_degrade() / expired()。
    """

    def __init__(self, level):
        """level 可能是 None 或未知值。"""
        raise NotImplementedError("尚未实现")

    @property
    def limit_ms(self) -> float:
        """本次请求的总预算。"""
        raise NotImplementedError("尚未实现")

    def charge(self, ms: float) -> None:
        """记一段已消耗的耗时（毫秒）。"""
        raise NotImplementedError("尚未实现")

    def remaining_ms(self) -> float:
        """剩余可用预算（不小于 0）。"""
        raise NotImplementedError("尚未实现")

    def should_degrade(self) -> bool:
        """是否应放弃正常路径、转走降级路径。"""
        raise NotImplementedError("尚未实现")

    def expired(self) -> bool:
        """预算是否已经耗尽。"""
        raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状，不涉及任何具体数值。"""
from sla.budget import Deadline, budget_ms, max_retries
from sla.policy import SLA_LEVELS


def test_budget_is_positive_int():
    for level in SLA_LEVELS:
        value = budget_ms(level)
        assert isinstance(value, int)
        assert value > 0


def test_retries_is_non_negative_int():
    for level in SLA_LEVELS:
        value = max_retries(level)
        assert isinstance(value, int)
        assert value >= 0


def test_deadline_starts_fresh():
    d = Deadline("P1")
    assert d.limit_ms > 0
    assert d.remaining_ms() > 0
    assert d.should_degrade() is False
    assert d.expired() is False


def test_charge_reduces_remaining():
    d = Deadline("P2")
    before = d.remaining_ms()
    d.charge(100)
    assert d.remaining_ms() < before


def test_unknown_level_does_not_raise():
    assert budget_ms(None) > 0
    assert Deadline(None).limit_ms > 0
'''

# ---- 四条行为检查 ----

_BUDGET_TABLE = '''
from sla.budget import budget_ms

p0 = budget_ms("P0")
p1 = budget_ms("P1")
p2 = budget_ms("P2")

assert p0 == 800, (
    f"P0 预算应为 800ms，实际 {p0}。注意方向：P0 不是「给最长」的那一档——"
    f"它面向前台用户，宁可快速失败也不要让人干等")
assert p1 == 2500, f"P1 预算应为 2500ms，实际 {p1}"
assert p2 == 8000, f"P2 预算应为 8000ms，实际 {p2}"
assert p0 < p1 < p2, f"预算应随等级放宽，实际 P0={p0} P1={p1} P2={p2}"
'''

_UNKNOWN_LEVEL = '''
from sla.budget import budget_ms

for bad in (None, "", "P9", "unknown"):
    got = budget_ms(bad)
    assert got == 2500, (
        f"等级为 {bad!r} 时应按 P1 的 2500ms 处理，实际 {got}。"
        f"未知流量既不该享受最宽松档，也不该被直接拒绝")
'''

_DEGRADE_THRESHOLD = '''
from sla.budget import Deadline

d = Deadline("P0")
assert d.limit_ms == 800, f"P0 总预算应为 800ms，实际 {d.limit_ms}"
assert d.should_degrade() is False, "刚创建就要降级？"

d.charge(550)
assert d.should_degrade() is False, (
    f"P0 已用 550/800（68.75%）就降级，阈值比约定更早")

d.charge(20)
assert d.should_degrade() is True, (
    f"P0 已用 570/800（71.25%）仍未降级。"
    f"降级阈值是总预算的 70%——不是 80% 也不是 90%，"
    f"剩下的 30% 要留给降级路径本身")
assert d.expired() is False, "570/800 还没耗尽预算，expired 不该为 True"

d.charge(300)
assert d.expired() is True, "870/800 已超预算，expired 应为 True"
assert d.remaining_ms() == 0, f"超预算后剩余应为 0（不是负数），实际 {d.remaining_ms()}"
'''

_RETRY_TABLE = '''
from sla.budget import max_retries

r0 = max_retries("P0")
assert r0 == 0, (
    f"P0 不应重试，实际 {r0}。方向反了：越重要的请求越不能重试——"
    f"重试会让用户等待翻倍，P0 要做的是一到阈值就降级")
assert max_retries("P1") == 1, f"P1 允许重试 1 次，实际 {max_retries('P1')}"
assert max_retries("P2") == 2, f"P2 允许重试 2 次，实际 {max_retries('P2')}"
assert max_retries(None) == 1, f"未知等级按 P1，重试次数应为 1，实际 {max_retries(None)}"
'''

_CHECKS = [
    ("分级预算表", _BUDGET_TABLE),
    ("未知等级归到 P1", _UNKNOWN_LEVEL),
    ("降级阈值 70%", _DEGRADE_THRESHOLD),
    ("分级重试次数", _RETRY_TABLE),
]


def _setup(wd):
    pkg = wd / "sla"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "policy.py").write_text(_POLICY, encoding="utf-8")
    (pkg / "budget.py").write_text(_BUDGET, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_budget.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_budget.py"
    if not f.exists():
        return False, "缺少 tests/test_budget.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_budget.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"既有测试失败（引入了回归）{tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code, timeout=90)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            detail = detail[-1] if detail else "未知"
            return False, f"{label} 不满足 → {detail}"

    return True, "预算表 / 未知等级 / 降级阈值 / 重试次数四项符合 SLA 规定"


TASK = {
    "id": "t33",
    "name": "SLA超时预算(需查知识库)",
    "prompt": (
        "`sla/` 是网关的超时预算模块。`sla/budget.py` 里 `budget_ms`、`max_retries` 和 "
        "`Deadline` 全部是 `NotImplementedError`，请实现它们。\n\n"
        "约束：\n"
        "- `sla/policy.py` 只有等级名，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿。\n\n"
        "⚠️ **所有具体数值都是平台组的 SLA 规定**：每个等级给多少预算、"
        "等级缺失或未知时归到哪一档、用到多少算该降级、各等级允许重试几次。\n"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：猜错不会报错——既有测试只验接口形状，"
        "数值全错它也是绿的，只有线上按等级跑起来才暴露。"
    ),
    "setup": _setup,
    "verify": _verify,
}
