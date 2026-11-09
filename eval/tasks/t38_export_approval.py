"""t38：数据导出审批 —— 多步判断链（第二题），核心难点是"同一字段、不同去向、相反处理"

## 为什么要改造（第一版失败）

第一版规则是：外部一律拒 / 敏感就脱敏 / 行数超限转人工。
base 三次里猜对了 **4 条**，只有"本部门直通"没猜到 —— 跟 t35 一个水平，
区分度只来自 1 个检查点。

原因：那三条都是**通用安全常识**（外发要拦、敏感要脱敏、大批量要审批），
模型本来就知道。**结构再复杂，内容全是常识也没用。**

## 改造后：让同一个字段在不同去向下有相反的结果

| 步 | 条件 | 返回 | 直觉会怎么做 |
| --- | --- | --- | --- |
| 0 | **本部门 且 内部流转** | `allow`，跳过后续全部 | 本部门也要查敏感和行数 |
| 1 | **内部** 且 含敏感字段 | **`deny`** | 脱敏后放行 |
| 2 | **外部** 且 含敏感字段 | **`mask`** | 外部一律拒 |
| 3 | 行数 > **50 万** | `review` | 阈值猜 10 万 |
| 4 | 其余 | `allow` | — |

第 1 步和第 2 步是这题的核心：**同样"含敏感字段"，内部直接拒、对外脱敏放行。**
模型几乎一定会把"敏感字段"当同一个维度统一处理（要么都 mask、要么都 deny），
一旦统一，两条检查点同时失败。

业务理由（自洽）：部门之间应该走数据接口，导出成文件就脱离了权限体系——
文件落地之后没人管得住；而对外发的是聚合结果，脱敏之后本来就该给。

## 判卷：6 条行为检查

1. 本部门 + 内部 + 敏感 + 100 万行 → 仍 `allow`（证明短路跳过敏感和行数）
2. 本部门但**发往外部** + 敏感 → `mask`；无敏感 → `allow`（证明直通要求"且内部"）
3. 跨部门 + **内部** + 敏感 → `deny`（不是 mask）
4. 跨部门 + **外部** + 敏感 → `mask`（不是 deny）
5. 跨部门 + 内部 + 无敏感 + 60 万行 → `review`（不是 deny）
6. 外部 + 敏感 + 60 万行 → `mask`（脱敏优先于行数）
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""数据导出审批。"""
'''

_SCHEMA = '''"""导出申请的数据结构。

字段含义（**不含任何判定规则**）：

request
    source_dept     数据所属部门
    requester_dept  申请人所属部门
    target          数据去向：'internal'（内部系统）或 'external'（外部邮箱/第三方）
    has_sensitive   是否包含敏感字段（手机号、身份证、银行卡等）
    row_count       导出记录数
"""

TARGETS = ("internal", "external")


def new_request(source_dept, requester_dept, target, has_sensitive, row_count):
    return {
        "source_dept": source_dept,
        "requester_dept": requester_dept,
        "target": target,
        "has_sensitive": has_sensitive,
        "row_count": row_count,
    }
'''

_DECIDER = '''"""导出审批器。

⚠️ 还没实现。

什么情况直接放行、什么情况脱敏、什么情况转人工、什么情况拒绝，
以及**判定顺序**，都是安全合规组的《数据导出管理办法》规定的，
不在本仓库内，也不是通用最佳实践能推出来的。
"""


def decide(request: dict) -> str:
    """判断一次导出申请该怎么处理。

    返回下列之一（字符串字面量）：
      "allow"    直接导出
      "mask"     脱敏后导出（敏感字段替换为掩码）
      "review"   转人工审批
      "deny"     拒绝

    不得修改传入的 request。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状，不涉及任何判定规则。"""
from export.decider import decide
from export.schema import new_request


def test_returns_known_verdict():
    got = decide(new_request("d1", "d1", "internal", False, 10))
    assert got in {"allow", "mask", "review", "deny"}


def test_does_not_mutate_input():
    req = new_request("d1", "d2", "external", True, 100)
    snapshot = dict(req)
    decide(req)
    assert req == snapshot
'''

# ---- 6 条行为检查 ----

_PRELUDE = '''
from export.decider import decide
from export.schema import new_request
'''

_SAME_DEPT_SHORTCUT = _PRELUDE + '''
# 本部门 + 内部：即使含敏感字段、即使 100 万行，也应直接放行。
# 这条命中了就不再执行后面任何检查。
got = decide(new_request("d1", "d1", "internal", True, 1_000_000))
assert got == "allow", (
    f"本部门内部流转，应直接放行，实际 {got!r}。"
    f"安全合规组的规定是：数据不出部门就不设卡——本部门的人本来就有权限看，"
    f"导出只是换个地方看。（这题的规则有先后顺序，先命中的生效）")
'''

_SAME_DEPT_BUT_EXTERNAL = _PRELUDE + '''
# 本部门，但发往外部：直通条件不满足（要求「且内部」），要走后面的判断。
assert decide(new_request("d1", "d1", "external", True, 10)) == "mask", (
    "本部门发往外部且含敏感字段，应脱敏后放行。"
    "「本部门直通」的条件是**本部门且内部**，不是只看部门")
assert decide(new_request("d1", "d1", "external", False, 10)) == "allow", (
    "本部门发往外部、不含敏感字段，应直接放行")
'''

_INTERNAL_SENSITIVE_DENY = _PRELUDE + '''
got = decide(new_request("d3", "d1", "internal", True, 100))
assert got == "deny", (
    f"跨部门、**内部**流转、含敏感字段，应拒绝，实际 {got!r}。"
    f"不是脱敏——部门之间应该走数据接口，导出成文件就脱离了权限体系"
    f"（文件一旦落地，谁拷走了都不知道）")
'''

_EXTERNAL_SENSITIVE_MASK = _PRELUDE + '''
got = decide(new_request("d4", "d1", "external", True, 100))
assert got == "mask", (
    f"跨部门、**发往外部**、含敏感字段，应脱敏后放行，实际 {got!r}。"
    f"不是拒绝——对外发的本来就是聚合结果，脱敏之后可以给。"
    f"注意这跟内部流转的结论**相反**：同一个「含敏感字段」，"
    f"在内部是拒绝，在外部是脱敏")
'''

_LARGE_REVIEW = _PRELUDE + '''
got = decide(new_request("d5", "d1", "internal", False, 600_000))
assert got == "review", (
    f"跨部门、内部、无敏感、60 万行，应转人工审批，实际 {got!r}。"
    f"不是拒绝——大批量导出是允许的，只是要有人看一眼；"
    f"阈值是**超过 50 万行**")
'''

_ORDER_MASK_BEFORE_SIZE = _PRELUDE + '''
# 发往外部、含敏感字段、60 万行：脱敏判断在行数之前，结果应为 mask
got = decide(new_request("d6", "d1", "external", True, 600_000))
assert got == "mask", (
    f"发往外部、含敏感、60 万行，应脱敏后放行，实际 {got!r}。"
    f"判定顺序是固定的：先判要不要脱敏，再判要不要转人工——"
    f"脱敏之后行数多也不构成风险，只是量大")
'''

_CHECKS = [
    ("本部门内部直通（跳过后续全部检查）", _SAME_DEPT_SHORTCUT),
    ("本部门但发往外部 → 不走直通", _SAME_DEPT_BUT_EXTERNAL),
    ("内部 + 敏感 → 拒绝", _INTERNAL_SENSITIVE_DENY),
    ("外部 + 敏感 → 脱敏", _EXTERNAL_SENSITIVE_MASK),
    ("超过 50 万行 → 转人工", _LARGE_REVIEW),
    ("判定顺序：脱敏优先于行数", _ORDER_MASK_BEFORE_SIZE),
]


def _setup(wd):
    pkg = wd / "export"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA, encoding="utf-8")
    (pkg / "decider.py").write_text(_DECIDER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_decider.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_decider.py"
    if not f.exists():
        return False, "缺少 tests/test_decider.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_decider.py 必须保持原样）"

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

    return True, "直通/去向×敏感/行数/顺序 五项符合安全合规组规定"


TASK = {
    "id": "t38",
    "name": "导出审批(多步判断链第二题)",
    "prompt": (
        "`export/` 是数据平台的导出审批器。`export/decider.py` 里的 `decide()` "
        "还没实现，请补全。\n\n"
        "它接收一次导出申请，返回处理方式之一：`allow`（直接导出）/ "
        "`mask`（脱敏后导出）/ `review`（转人工审批）/ `deny`（拒绝）。"
        "字段含义见 `export/schema.py`。\n\n"
        "约束：\n"
        "- `export/schema.py` 只定义字段，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- 不得修改传入的 `request`。\n\n"
        "⚠️ **安全合规组的导出规定**：什么情况可以直接放行、什么情况脱敏、"
        "什么情况转人工、什么情况拒绝，以及**几条规则的判定先后顺序**——"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：这些规则有**固定的先后顺序，先命中的那条生效**；"
        "猜错不会报错——既有测试只验接口形状，判定全错它也是绿的。"
    ),
    "setup": _setup,
    "verify": _verify,
}
