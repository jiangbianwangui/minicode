"""t34：灰度发布的门禁判定 —— 四条"和直觉反向"的放量规矩

## 缺的是什么知识

`canary/gate.py` 只有三个空函数。**所有阈值都不在仓库里**：

1. 多少算"劣化"（看变化量还是看绝对值？）
2. canary 样本多少才够下结论
3. 什么条件直接回滚、什么条件只是暂停放量
4. 暂停几次才升级成回滚

## 为什么每一条都反直觉

| 直觉 | 平台规定 | 直觉错在哪 |
| --- | --- | --- |
| canary 错误率 2.5% 太高了（超过 1%） | 看**差值**：baseline 2.0% → canary 2.5% 只差 0.5pp，**不算劣化** | 绝对值受业务波动影响，大促期间 baseline 本身就 2% |
| 样本少 → 更容易出事 → 更该回滚 | 样本 < **500** → **不下结论**，继续观察 | 样本少时 1 个错误就能把错误率打到 8%，那是噪声 |
| 一旦劣化立刻回滚 | 先 **hold**（停止放量、保留现场），burn rate > **3.5** 才直接回滚 | 立即回滚会把现场销毁，排查时什么都不剩 |
| 连续 3 次异常才回滚 | **2 次** | 灰度的成本是每个受影响用户，等第三次太贵 |

四条都无法从代码推出，而且**都不会报错**：判据写错，测试照样全绿，
只有线上把一次正常发布掐死、或者放过一次真实故障时才知道。

## 判卷：6 个场景，全部是行为调用

不看源码常量，只调 `decide()` 看返回字符串。
每个场景都配了"直觉写法会给出什么答案"，确保直觉一定被拦。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""灰度发布门禁。"""
'''

_TOLERANCE = '''"""SLO 容忍错误率（平台组维护，不要修改）。"""

#: 各服务线的容忍错误率。canary 的容忍值与主链路一致。
TOLERANCE = {
    "checkout": 0.005,
    "search": 0.02,
    "report": 0.05,
}
'''

_GATE = '''"""灰度发布门禁。

⚠️ 还没实现。

门禁的判定阈值（多少算劣化、样本多少才够、什么条件回滚）是**发布平台的规定**，
不在本仓库内。

`decide()` 返回以下三种之一：
  - "continue"：继续按计划放量
  - "hold"：停止放量，保持当前比例（保留现场）
  - "rollback"：回滚到灰度前的版本
"""


def error_rate_delta(baseline_err: float, canary_err: float) -> float:
    """canary 相对 baseline 的劣化幅度，单位是**百分点**。

    例：baseline 0.020、canary 0.025 → 差 0.5 个百分点，返回 0.5。
    canary 更好（错误率更低）时返回 0.0，不返回负数。
    """
    raise NotImplementedError("尚未实现")


def burn_rate(error_rate: float, tolerance: float) -> float:
    """SLO 燃烧率 = 实际错误率 / 容忍错误率。"""
    raise NotImplementedError("尚未实现")


def decide(baseline_err: float, canary_err: float, canary_samples: int,
           tolerance: float, history=()) -> str:
    """给出一轮灰度的判定。

    参数：
      baseline_err    对照组错误率（0~1）
      canary_err      灰度组错误率（0~1）
      canary_samples  灰度组到目前为止的请求数
      tolerance       SLO 容忍错误率（见 sla 侧配置）
      history         之前几轮的判定，按时间顺序，元素是 decide() 的返回值

    返回："continue" / "hold" / "rollback"
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状与取值域，不涉及任何阈值。"""
import pytest

from canary.gate import burn_rate, decide, error_rate_delta
from canary.tolerance import TOLERANCE

VERDICTS = {"continue", "hold", "rollback"}


def test_verdict_is_one_of_three():
    for history in ((), ("continue",), ("hold",), ("hold", "hold")):
        got = decide(0.01, 0.02, 5000, TOLERANCE["checkout"], history)
        assert got in VERDICTS, f"返回值 {got!r} 不在约定集合内"


def test_delta_is_non_negative_pp():
    assert error_rate_delta(0.01, 0.03) > 0
    assert error_rate_delta(0.03, 0.01) == 0.0
    assert error_rate_delta(0.02, 0.02) == 0.0


def test_delta_unit_is_percentage_point():
    # 0.020 与 0.025 相差 0.5 个百分点（不是 0.005）
    assert error_rate_delta(0.020, 0.025) == pytest.approx(0.5, abs=1e-6)


def test_burn_rate_ratio():
    assert burn_rate(0.01, 0.005) == pytest.approx(2.0)
    assert burn_rate(0.02, 0.005) == pytest.approx(4.0)


def test_improvement_is_continue():
    # 灰度比对照组更好，任何情况下都不该 hold / rollback
    assert decide(0.05, 0.01, 10000, TOLERANCE["checkout"]) == "continue"
'''

# ---- 6 个场景（每条都写着"直觉会给什么答案"）----

_ABSOLUTE_VS_DELTA = '''
from canary.gate import decide

# baseline 2.0%，canary 2.5%：绝对值 2.5% 看着高，但只差 0.5 个百分点。
# 直觉（看绝对值 / 看 1% 这条线）会说 rollback，约定要求 continue。
got = decide(0.020, 0.025, 2000, 0.005)
assert got == "continue", (
    f"baseline 2.0% → canary 2.5% 只差 0.5 个百分点，未达劣化阈值，应为 continue，实际 {got!r}。"
    f"注意判据是**两组之差**，不是 canary 的绝对值——大促期间 baseline 本身就 2%")
'''

_SAMPLES = '''
from canary.gate import decide

# 300 个样本里 15 个失败 → 错误率 5%，差值 4.5 个百分点。
# 直觉会说「这么严重还不回滚」，约定要求先不下结论。
got = decide(0.005, 0.05, 300, 0.005)
assert got == "continue", (
    f"canary 只有 300 个样本，不足以判定，应为 continue，实际 {got!r}。"
    f"样本少时几个错误就能把错误率打到两位数，那是噪声不是劣化")
'''

_HOLD_NOT_ROLLBACK = '''
from canary.gate import decide

# 明确劣化（0.2% → 1.5%，差 1.3 个百分点），但燃烧率只有 3.0，未到直接回滚的线。
got = decide(0.002, 0.015, 2000, 0.005)
assert got == "hold", (
    f"已劣化但燃烧率未达直接回滚阈值，应为 hold（停止放量、保留现场），实际 {got!r}。"
    f"立刻回滚会把现场销毁，排查时什么都不剩")
'''

_ROLLBACK_WHEN_SEVERE = '''
from canary.gate import decide

# 0.2% → 2.0%，差 1.8 个百分点，燃烧率 2.0/0.5 = 4.0，超过直接回滚的线。
got = decide(0.002, 0.020, 2000, 0.005)
assert got == "rollback", (
    f"燃烧率 4.0 已超过直接回滚阈值，应为 rollback（不必等第二轮），实际 {got!r}")
'''

_TWO_HOLDS = '''
from canary.gate import decide

# 上一轮已经 hold，这一轮仍劣化（同样的条件）→ 该升级成回滚。
got = decide(0.002, 0.015, 2000, 0.005, history=["hold"])
assert got == "rollback", (
    f"连续第二轮仍然劣化，应升级为 rollback，实际 {got!r}。"
    f"灰度的成本是每个受影响用户，不能等更多轮")
'''

_ONE_HOLD_NOT_ENOUGH = '''
from canary.gate import decide

# history 里是 continue（不是 hold），这一轮才第一次劣化 → 只能是 hold。
got = decide(0.002, 0.015, 2000, 0.005, history=["continue"])
assert got == "hold", (
    f"只连续了 1 轮（前一轮是 continue），应为 hold，实际 {got!r}")
'''

_CHECKS = [
    ("看差值不看绝对值", _ABSOLUTE_VS_DELTA),
    ("样本不足不下结论", _SAMPLES),
    ("劣化先 hold 不直接回滚", _HOLD_NOT_ROLLBACK),
    ("燃烧率超标才直接回滚", _ROLLBACK_WHEN_SEVERE),
    ("连续两次 hold 升级回滚", _TWO_HOLDS),
    ("只连续一次不升级", _ONE_HOLD_NOT_ENOUGH),
]


def _setup(wd):
    pkg = wd / "canary"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "tolerance.py").write_text(_TOLERANCE, encoding="utf-8")
    (pkg / "gate.py").write_text(_GATE, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_gate.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_gate.py"
    if not f.exists():
        return False, "缺少 tests/test_gate.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_gate.py 必须保持原样）"

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

    return True, "差值/样本量/hold 与回滚/升级条件 六项符合发布平台规定"


TASK = {
    "id": "t34",
    "name": "灰度门禁判定(需查知识库)",
    "prompt": (
        "`canary/` 是灰度发布的自动门禁。`canary/gate.py` 里 "
        "`error_rate_delta`、`burn_rate`、`decide` 三个函数都是 `NotImplementedError`，请实现。\n\n"
        "约束：\n"
        "- `canary/tolerance.py` 是 SLO 配置，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- `decide()` 只能返回 `\"continue\"` / `\"hold\"` / `\"rollback\"`。\n\n"
        "⚠️ **判定的阈值全部是发布平台的规定**：差多少算劣化、灰度组要跑够多少请求才能下结论、"
        "什么情况停止放量、什么情况直接回滚、连续几轮异常才升级。\n"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：猜错不会报错——既有测试只验接口形状和取值域，阈值全错它也是绿的，"
        "只有线上真的按它放量时才暴露。"
    ),
    "setup": _setup,
    "verify": _verify,
}
