"""t40：临时提权审批 —— 留出任务（held-out），**故意不写配套 skill**

## 为什么要有第二道留出任务

t39 的结论是「skill 能迁移结构、不能迁移参数，净效果为零」，但那只有 **n=1**。
一次观察可能是这题恰好如此，也可能是我事后给这个结果编了个解释。

第二道留出任务的作用是**让它有可能被推翻**：
- 若 B 组（现有库）在这里分数比 A 组高 → 「净零」不成立，边界要改写
- 若 B 组又一次总分相同但内部挪了位置 → 结论从「一次观察」变成「两次复现」
- 若 B 组这次连结构都没迁过去 → 说明 t39 那次是巧合，结论要撤回

## 与 t39 的关系：同族但不同构

t39 与 `export-approval-policy` 同构（审批 + 分级 + 短路）。
t40 换成与 `backfill-approval-policy` 同构的骨架：

```
第 0 步  短路直通        （对应 backfill 的「小表直通」）
第 1 步  某类一票否决    （对应「当日重复 → reject」）
第 2 步  超长 → 降级处理 （对应「窗口 > 30 天 → queue」）
第 3 步  环境规则 → 转人工（对应「有下游在消费 → wait」）
第 4 步  兜底放行        （对应「其余 → run」）
```

业务对象换成**权限系统**、参数全部不同（4 小时 / 12 小时，不是 30 分钟 / 24 小时），
判定方向也逐条换过。所以它测的仍然是「结构能不能迁移」。

## 题目设计：五步，四条方向与直觉相反

| 步 | 条件 | 返回 | 直觉会怎么做 |
| --- | --- | --- | --- |
| 0 | 只读 **且** ≤ 4 小时 | `grant` | 生产环境一律要审 |
| 1 | 角色是 admin | `deny` | 高危 → 转人工复核 |
| 2 | 时长 > 12 小时 | `grant_staged` | 太长 → 转人工 |
| 3 | 生产 **且** 无第二审批人 | `review` | —（这条方向正常） |
| 4 | 其余 | `grant` | 保守起见转人工 |

第 0 步是**复合短路**（两个条件都要满足），且要压过第 3 步；
第 1 步要压过第 2 步。这两处顺序错了，判定全错但测试照样绿。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""临时提权审批。"""
'''

_SCHEMA = '''"""提权申请的数据结构。

字段含义（**不含任何判定规则**）：

req
    role              申请提权后的角色：'read'（可查生产数据）/ 'write'（可改数据）/
                      'admin'（可改权限本身）
    hours             申请时长，单位小时
    second_approver   第二个审批人是否已签署（四眼原则中"另一双眼睛"）

ctx
    env               环境：'prod'（生产）/ 'staging'（预发）
"""

ROLES = ("read", "write", "admin")
ENVS = ("prod", "staging")


def new_request(role, hours, second_approver=False):
    return {"role": role, "hours": hours, "second_approver": second_approver}


def new_ctx(env="staging"):
    return {"env": env}
'''

_ELEVATOR = '''"""提权审批器。

⚠️ 还没实现。

什么角色可以直接给、多久算短、多长时间要走另一条路、
生产环境缺第二审批人怎么处理，以及**几条判定的先后顺序**，
都由权限治理组的《临时提权管理办法》规定，
不在本仓库内，也不是通用安全常识能推出来的。
"""


def decide(req: dict, ctx: dict) -> str:
    """判断一次提权申请该怎么处理。

    返回下列之一（字符串字面量）：
      "grant"         直接授予，立即生效
      "grant_staged"  先授予前一段，到期再评估
      "review"        转人工审批
      "deny"          拒绝

    不得修改传入的 req / ctx。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状，不涉及任何判定规则。"""
from authz.elevator import decide
from authz.schema import new_request, new_ctx


def test_returns_known_verdict():
    got = decide(new_request("read", 1), new_ctx())
    assert got in {"grant", "grant_staged", "review", "deny"}


def test_does_not_mutate_inputs():
    req = new_request("write", 2, second_approver=True)
    ctx = new_ctx("prod")
    snapshot = (dict(req), dict(ctx))
    decide(req, ctx)
    assert (dict(req), dict(ctx)) == snapshot
'''

# ---- 6 条行为检查 ----

_PRELUDE = '''
from authz.elevator import decide
from authz.schema import new_request, new_ctx
'''

_READ_SHORTCUT = _PRELUDE + '''
# 只读 + 正好 4 小时：即使在生产、即使没有第二审批人，也应直接授予。
# 第 0 步命中后，不再执行后面任何检查。
got = decide(new_request("read", 4, second_approver=False), new_ctx("prod"))
assert got == "grant", (
    f"只读提权 4 小时（正好到上限）、生产环境、无第二审批人，应直接授予，实际 {got!r}。"
    f"权限治理组的规定是：只读且**4 小时以内**（含 4）属于排障的常规操作，"
    f"不必走审批——只读改不了数据，看错了也留不下痕迹。"
    f"（这题的规则有先后顺序，先命中的生效）")
'''

_READ_EXPIRED = _PRELUDE + '''
# 只读但 5 小时：短路不再成立，会撞上"生产环境缺第二审批人"那一条
got = decide(new_request("read", 5, second_approver=False), new_ctx("prod"))
assert got == "review", (
    f"只读提权 5 小时、生产环境、无第二审批人，应转人工，实际 {got!r}。"
    f"4 小时的豁免刚好过期——超过 4 小时就不再是随手排障，"
    f"生产环境上长时间的读权限也有人要看一眼")
'''

_ADMIN_DENY = _PRELUDE + '''
got = decide(new_request("admin", 1, second_approver=True), new_ctx("staging"))
assert got == "deny", (
    f"申请 admin 角色、只 1 小时、预发环境、有第二审批人，应拒绝，实际 {got!r}。"
    f"提权到管理员**根本不走这条审批流**——它有单独的流程和更重的签署，"
    f"短时长和双人签署都不能让它在这里被放行")
'''

_ORDER_ADMIN_BEFORE_STAGED = _PRELUDE + '''
# admin + 20 小时：第 1 步和第 2 步都命中，admin 判断在前
got = decide(new_request("admin", 20, second_approver=True), new_ctx("staging"))
assert got == "deny", (
    f"申请 admin、20 小时，应拒绝，实际 {got!r}。"
    f"判定顺序是固定的：先判角色（admin 一票否决），再判时长")
'''

_LONG_STAGED = _PRELUDE + '''
# 写权限 20 小时 + 生产 + 无第二审批人：第 2 步（超长）与第 3 步（生产缺签署）都命中
got = decide(new_request("write", 20, second_approver=False), new_ctx("prod"))
assert got == "grant_staged", (
    f"写权限 20 小时、生产环境、无第二审批人，应先授予前一段再评估，实际 {got!r}。"
    f"不是转人工——时长本身不是风险，问题是没人盯着那么久；"
    f"先给 4 小时、到期再判一次，比让人干等审批更安全。"
    f"超长判断在生产环境检查之前，所以这里不是 review")
'''

_BOUNDARY_12H = _PRELUDE + '''
ok = decide(new_request("write", 12, second_approver=True), new_ctx("staging"))
assert ok == "grant", (
    f"写权限整 12 小时、预发、有第二审批人，应直接授予，实际 {ok!r}。"
    f"阈值是**超过** 12 小时才分段，正好 12 小时不算超")

over = decide(new_request("write", 13, second_approver=True), new_ctx("staging"))
assert over == "grant_staged", (
    f"13 小时（超过 12 小时）、预发、有第二审批人，应分段授予，实际 {over!r}")
'''

_CHECKS = [
    ("只读短时直通（跳过后续全部检查）", _READ_SHORTCUT),
    ("只读 5 小时不再豁免 → 转人工", _READ_EXPIRED),
    ("admin 申请 → 拒绝（不因短时或双签放行）", _ADMIN_DENY),
    ("判定顺序：admin 优先于超长分段", _ORDER_ADMIN_BEFORE_STAGED),
    ("判定顺序：超长分段优先于生产环境检查", _LONG_STAGED),
    ("12 小时边界（>12 才分段）", _BOUNDARY_12H),
]


def _setup(wd):
    pkg = wd / "authz"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA, encoding="utf-8")
    (pkg / "elevator.py").write_text(_ELEVATOR, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_elevator.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_elevator.py"
    if not f.exists():
        return False, "缺少 tests/test_elevator.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_elevator.py 必须保持原样）"

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

    return True, "直通/角色/时长/环境/顺序 五项符合权限治理组规定"


TASK = {
    "id": "t40",
    "name": "临时提权审批(留出任务)",
    "prompt": (
        "`authz/` 是权限系统的临时提权审批器。`authz/elevator.py` 里的 "
        "`decide()` 还没实现，请补全。\n\n"
        "它接收一次提权申请和当前环境，返回处理方式之一：`grant`（直接授予）/ "
        "`grant_staged`（先授予前一段，到期再评估）/ `review`（转人工审批）/ `deny`（拒绝）。"
        "字段含义见 `authz/schema.py`。\n\n"
        "约束：\n"
        "- `authz/schema.py` 只定义字段，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- 不得修改传入的 `req` / `ctx`。\n\n"
        "⚠️ **权限治理组的提权规定**：什么角色可以直接授予、多久算短、"
        "多长时间要走分段授予、生产环境缺第二审批人怎么处理，以及**几条判定的先后顺序**——"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：这些规则有**固定的先后顺序，先命中的那条生效**；"
        "猜错不会报错——既有测试只验接口形状，判定全错它也是绿的。"
    ),
    "setup": _setup,
    "verify": _verify,
}
