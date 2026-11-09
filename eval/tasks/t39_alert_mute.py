"""t39：告警静默审批 —— 留出任务（held-out），**故意不写配套 skill**

## 这题不是用来"加一道题"的，是用来做三组对照的

现有 10 题的共同问题：**每篇 skill 都是题做完才写的** —— 等于开卷考试。
所以"skill 有效"只能说明"答案放在它能拿到的地方时，它抄得对"，
不能说明 skill 有**可迁移的知识价值**。

三组对照：

| 组 | 给什么 | 预期 |
| --- | --- | --- |
| A | 什么都不给 | 基线 |
| B | **现有 12 篇 skill 库**（不给新 skill） | 关键组 |
| C | 专门为这题写的 skill | 上限 |

三种结果，三种结论：
- **B > A** → skill 有方法论迁移价值（学到了"复合短路要放最前"，不只是抄答案）
- **B ≈ A** → skill 只是**精确答案的索引**，换个场景就废
- **B ≈ C** → 现有库的泛化能力比想象中强

## 题目设计：与 export-approval-policy 同构、内容全不同

| 步 | 条件 | 返回 | 直觉会怎么做 |
| --- | --- | --- | --- |
| 0 | 范围是**单条规则** 且 ≤ **30 分钟** | `apply`，跳过后续全部 | 静默总要走审批 |
| 1 | 范围是**全局** | `reject` | 短时全局应当可以 |
| 2 | **覆盖业务高峰** | `review` | 高峰绝不能静默 |
| 3 | 时长 > **24 小时** | `schedule` 排到变更窗口 | 太长该人工审 |
| 4 | 其余 | `apply` | — |

四条判定方向全部与直觉相反，且第 0 步是**复合短路**（两个条件都要满足）。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""告警静默审批。"""
'''

_SCHEMA = '''"""静默申请的数据结构。

字段含义（**不含任何判定规则**）：

rule
    scope         静默范围：'rule'（单条规则）/ 'service'（整个服务）/ 'global'（全局）
    minutes       静默时长，单位分钟
    covers_peak   该时段是否覆盖业务高峰（由排班系统算好）

ctx
    in_change_window  当前是否处于变更窗口
"""

SCOPES = ("rule", "service", "global")


def new_rule(scope, minutes, covers_peak):
    return {"scope": scope, "minutes": minutes, "covers_peak": covers_peak}


def new_ctx(in_change_window=False):
    return {"in_change_window": in_change_window}
'''

_APPROVER = '''"""静默审批器。

⚠️ 还没实现。

什么范围可以直接生效、多久算短、覆盖高峰怎么处理、超过多久要排期，
以及**判定先后顺序**，都是告警治理组的《告警静默管理办法》规定的，
不在本仓库内，也不是通用运维常识能推出来的。
"""


def approve(rule: dict, ctx: dict) -> str:
    """判断一次静默申请该怎么处理。

    返回下列之一（字符串字面量）：
      "apply"     立即生效
      "schedule"  排到下一个变更窗口
      "review"    转人工审批
      "reject"    拒绝

    不得修改传入的 rule / ctx。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状，不涉及任何判定规则。"""
from alertmute.approver import approve
from alertmute.schema import new_rule, new_ctx


def test_returns_known_verdict():
    got = approve(new_rule("rule", 10, False), new_ctx())
    assert got in {"apply", "schedule", "review", "reject"}


def test_does_not_mutate_inputs():
    rule = new_rule("rule", 10, False)
    ctx = new_ctx()
    snapshot = (dict(rule), dict(ctx))
    approve(rule, ctx)
    assert (dict(rule), dict(ctx)) == snapshot
'''

# ---- 6 条行为检查 ----

_PRELUDE = '''
from alertmute.approver import approve
from alertmute.schema import new_rule, new_ctx
'''

_SHORT_SINGLE_SHORTCUT = _PRELUDE + '''
# 单条规则 + 正好 30 分钟：即使覆盖业务高峰，也应立即生效。
# 第 0 步命中后，不再执行后面任何检查。
got = approve(new_rule("rule", 30, True), new_ctx())
assert got == "apply", (
    f"单条规则静默 30 分钟（正好到上限），应直接生效，实际 {got!r}。"
    f"告警治理组的规定是：单条规则、**30 分钟以内**（含 30）属于值班同学的日常操作，"
    f"不必走审批——哪怕它盖住了高峰时段。"
    f"（这题的规则有先后顺序，先命中的生效）")
'''

_LONGER_SINGLE_HITS_PEAK = _PRELUDE + '''
# 单条规则但 31 分钟：短路不再成立，会撞上"覆盖高峰"那一条
got = approve(new_rule("rule", 31, True), new_ctx())
assert got == "review", (
    f"单条规则、31 分钟、覆盖高峰，应转人工，实际 {got!r}。"
    f"30 分钟的豁免刚好过期——超过半小时就不再是随手操作，"
    f"而盖住高峰的静默必须有人确认")
'''

_GLOBAL_REJECT = _PRELUDE + '''
got = approve(new_rule("global", 5, False), new_ctx())
assert got == "reject", (
    f"全局静默哪怕只有 5 分钟也应拒绝，实际 {got!r}。"
    f"全局静默等于关掉整个告警系统——那 5 分钟里发生的真实故障没人知道")
'''

_LONG_SCHEDULE = _PRELUDE + '''
got = approve(new_rule("rule", 3000, False), new_ctx())
assert got == "schedule", (
    f"单条规则静默 3000 分钟（约 50 小时）、不覆盖高峰，应排到变更窗口，实际 {got!r}。"
    f"不是转人工——时长本身不构成风险，问题是它跨越了那么长的时间，"
    f"应该挑一个有人值守的窗口执行")
'''

_ORDER_GLOBAL_BEFORE_PEAK = _PRELUDE + '''
# 全局 + 覆盖高峰 + 3000 分钟：三条都命中，全局判断在最前
got = approve(new_rule("global", 3000, True), new_ctx())
assert got == "reject", (
    f"全局静默同时覆盖高峰、时长 3000 分钟，应拒绝，实际 {got!r}。"
    f"判定顺序是固定的：先判范围（全局一票否决），再判高峰，最后判时长")
'''

_BOUNDARY_24H = _PRELUDE + '''
ok = approve(new_rule("service", 1440, False), new_ctx())
assert ok == "apply", (
    f"整 24 小时（1440 分钟）、不覆盖高峰，应直接生效，实际 {ok!r}。"
    f"阈值是**超过** 24 小时才排期，正好 24 小时不算超")

over = approve(new_rule("service", 1441, False), new_ctx())
assert over == "schedule", (
    f"1441 分钟（超过 24 小时）、不覆盖高峰，应排到变更窗口，实际 {over!r}")
'''

_CHECKS = [
    ("单规则短时直通（跳过后续全部检查）", _SHORT_SINGLE_SHORTCUT),
    ("31 分钟不再豁免 → 转人工", _LONGER_SINGLE_HITS_PEAK),
    ("全局静默 → 拒绝", _GLOBAL_REJECT),
    ("超 24 小时 → 排期", _LONG_SCHEDULE),
    ("判定顺序：全局优先于高峰", _ORDER_GLOBAL_BEFORE_PEAK),
    ("24 小时边界（>1440 才排期）", _BOUNDARY_24H),
]


def _setup(wd):
    pkg = wd / "alertmute"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA, encoding="utf-8")
    (pkg / "approver.py").write_text(_APPROVER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_approver.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_approver.py"
    if not f.exists():
        return False, "缺少 tests/test_approver.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_approver.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"既有测试失败（引入了回归）{tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code, timeout=60)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            detail = detail[-1] if detail else "未知"
            return False, f"{label} 不满足 → {detail}"

    return True, "直通/范围/高峰/时长/顺序 五项符合告警治理组规定"


TASK = {
    "id": "t39",
    "name": "告警静默审批(留出任务)",
    "prompt": (
        "`alertmute/` 是告警平台的静默审批器。`alertmute/approver.py` 里的 "
        "`approve()` 还没实现，请补全。\n\n"
        "它接收一次静默申请和当前上下文，返回处理方式之一：`apply`（立即生效）/ "
        "`schedule`（排到下一个变更窗口）/ `review`（转人工审批）/ `reject`（拒绝）。"
        "字段含义见 `alertmute/schema.py`。\n\n"
        "约束：\n"
        "- `alertmute/schema.py` 只定义字段，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- 不得修改传入的 `rule` / `ctx`。\n\n"
        "⚠️ **告警治理组的静默规定**：什么范围可以直接生效、多久算短、"
        "覆盖业务高峰怎么处理、超过多久要排期，以及**几条规则的判定先后顺序**——"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：这些规则有**固定的先后顺序，先命中的那条生效**；"
        "猜错不会报错——既有测试只验接口形状，判定全错它也是绿的。"
    ),
    "setup": _setup,
    "verify": _verify,
}
