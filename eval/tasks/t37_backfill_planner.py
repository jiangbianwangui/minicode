"""t37：数据回刷审批 —— 多步判断链，含"短路"分支

## 为什么造这题

t35 / t36 的共同问题：4–5 条规矩里 base 自己猜对 3 条，
**只有一个数字在区分**。说明"某公司的参数表"这条路线的结构性缺口快用完了。

这题换赛道：**多步判断链**。不是"填几个数字"，而是
"几条规则按固定顺序依次判，先命中的生效"，其中第一条还是**短路**——
命中后后面所有检查都不再执行。

好处有两层：
1. 猜全的概率大幅下降（4 条判定 + 1 个顺序 + 1 个短路 = 要同时对 6 件事）；
2. **判卷能报出它漏了哪一条**，比"全错"更有诊断价值。

## 缺的是什么知识

`backfill/` 只有字段定义，没有任何判定规则。规则是数据治理组定的：

| 步 | 条件 | 返回 | 直觉会怎么做 |
| --- | --- | --- | --- |
| 0 | 小表且非核心 | `run`，**跳过后续全部检查** | 小表也得走流程、也要查重 |
| 1 | 当日已回刷过 | `reject` | 警告 / 排队 / 允许但记日志 |
| 2 | 窗口 > 30 天 | `queue`（离线队列） | 阈值猜 7 天，且多写 `>=` |
| 3 | 有下游活跃订阅 | `wait`（到次日） | 拒绝，或者照样跑 |
| 4 | 其余 | `run` | — |

**第 2 步优先于第 3 步**：同时满足"窗口超 30 天"和"有下游在消费"时，
结果是 `queue` 而不是 `wait`——排进低峰期队列当晚就能跑完，没必要干等到明天。
顺序反了就是另一个答案。

> 注意：另一组顺序（`reject` 优先于 `queue`）**没有单独设检查**。
> 试过一版，但 base 的错误实现里恰好有"窗口超限就拒绝"这条，
> 会碰巧返回 `reject` 而蒙混过关 —— 那条检查对 base **没有区分度**，
> 留着只会让判卷看起来比实际更严（t31 同类错误）。顺序测试改用
> `queue` vs `wait` 这一组，它在 base 上是真阳性。

## 判卷：6 条行为检查

1. 小表直通（同时给它 120 天窗口 + 今日已回刷 3 次 + 2 个订阅者，仍须 `run`）
2. 当日重复 → `reject`
3. 窗口边界 30 → `run`，31 → `queue`
4. 有活跃订阅 → `wait`
5. 顺序：`queue` 优先于 `wait`
6. 核心小表**不**直通（只看行数不看 is_core 会被抓）
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""数据回刷审批。"""
'''

_SCHEMA = '''"""回刷申请与当前状态的数据结构。

字段含义（**不含任何判定规则**）：

request
    table        目标表名
    window_days  要回刷的时间窗口，单位天
    row_count    该窗口内预计涉及的行数
    is_core      是否核心表（由数据治理组标注）

state
    same_day_backfills   该表今天已经回刷过几次
    active_subscribers   当前正在消费该表下游产出的任务数
"""


def new_request(table, window_days, row_count, is_core):
    return {
        "table": table,
        "window_days": window_days,
        "row_count": row_count,
        "is_core": is_core,
    }


def new_state(same_day_backfills=0, active_subscribers=0):
    return {
        "same_day_backfills": same_day_backfills,
        "active_subscribers": active_subscribers,
    }
'''

_PLANNER = '''"""回刷审批器。

⚠️ 还没实现。

什么情况可以直接跑、什么情况排队、什么情况拒绝、什么情况等到次日，
以及**判定顺序**，都是数据治理组的规定，不在本仓库内，
也不是任何通用最佳实践能推出来的。
"""


def plan(request: dict, state: dict) -> str:
    """判断一次回刷申请该怎么处理。

    返回下列之一（字符串字面量）：
      "run"     直接执行
      "queue"   进离线队列，等低峰期跑
      "wait"    等到次日再执行
      "reject"  拒绝

    不得修改传入的 request / state。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状，不涉及任何判定规则。"""
from backfill.planner import plan
from backfill.schema import new_request, new_state


def test_returns_known_verb():
    got = plan(new_request("dwd_x", 1, 1000, False), new_state())
    assert got in {"run", "queue", "wait", "reject"}


def test_does_not_mutate_inputs():
    req = new_request("dwd_x", 1, 1000, False)
    st = new_state()
    snapshot = (dict(req), dict(st))
    plan(req, st)
    assert (dict(req), dict(st)) == snapshot
'''

# ---- 6 条行为检查 ----

_PRELUDE = '''
from backfill.planner import plan


def req(**kw):
    base = {"table": "dwd_order", "window_days": 7,
            "row_count": 5_000_000, "is_core": True}
    base.update(kw)
    return base


def st(**kw):
    base = {"same_day_backfills": 0, "active_subscribers": 0}
    base.update(kw)
    return base
'''

_SMALL_TABLE_SHORTCUT = _PRELUDE + '''
# 小表 + 非核心：即使窗口 120 天、今天已经回刷过 3 次、还有 2 个下游在消费，
# 也应当直接跑 —— 这条命中了就**不再看后面任何条件**。
got = plan(req(row_count=800_000, is_core=False, window_days=120),
           st(same_day_backfills=3, active_subscribers=2))
assert got == "run", (
    f"小表（80 万行）且非核心表，应直接执行，实际 {got!r}。"
    f"数据治理组的规定是：这类回刷量小、可秒级完成，**不走审批链**——"
    f"包括不查当日重复、不看窗口长度、不等下游。（这题的规则有先后顺序，先命中的生效）")
'''

_SAME_DAY_REJECT = _PRELUDE + '''
got = plan(req(window_days=7), st(same_day_backfills=1))
assert got == "reject", (
    f"该表今天已经回刷过一次，应拒绝，实际 {got!r}。"
    f"不是警告、不是排队——同一天对同一张表重复回刷会让下游看到"
    f"两批互相冲突的数据，而下游无法分辨哪批是后来的")
'''

_WINDOW_BOUNDARY = _PRELUDE + '''
ok30 = plan(req(window_days=30), st())
assert ok30 == "run", (
    f"窗口正好 30 天，应直接执行，实际 {ok30!r}。"
    f"阈值是**超过** 30 天才进离线队列，30 天本身不算超")

got31 = plan(req(window_days=31), st())
assert got31 == "queue", (
    f"窗口 31 天，应进离线队列，实际 {got31!r}。"
    f"窗口超过 30 天时必须走低峰期队列，不能占用在线资源")
'''

_ACTIVE_SUBSCRIBER_WAIT = _PRELUDE + '''
got = plan(req(window_days=7), st(active_subscribers=2))
assert got == "wait", (
    f"有 2 个下游任务正在消费这张表，应等到次日，实际 {got!r}。"
    f"不是拒绝（回刷本身是允许的），也不是直接跑——下游正在读的"
    f"分区被改写会让它读到一半新一半旧的数据")
'''

_ORDER_QUEUE_BEFORE_WAIT = _PRELUDE + '''
# 窗口 60 天（该排队）同时有 3 个下游在消费（该等次日）：
# 队列判断在前——先进队列，等低峰期跑，不需要等到明天。
got = plan(req(window_days=60), st(active_subscribers=3))
assert got == "queue", (
    f"同时命中排队和次日两条规则时应返回 queue，实际 {got!r}。"
    f"判定顺序是固定的：先判怎么排（队列），再判要不要等（次日）——"
    f"排进低峰期队列当晚就能跑完，没必要干等到明天")
'''

_CORE_SMALL_NOT_SHORTCUT = _PRELUDE + '''
got = plan(req(row_count=500_000, is_core=True, window_days=60), st())
assert got == "queue", (
    f"核心表即使只有 50 万行也不能直通，应进离线队列，实际 {got!r}。"
    f"直通的条件是**小表且非核心**，两个都要满足——核心表出错的代价"
    f"比多等几个小时大得多")
'''

_CHECKS = [
    ("小表直通（跳过后续全部检查）", _SMALL_TABLE_SHORTCUT),
    ("当日已回刷过 → 拒绝", _SAME_DAY_REJECT),
    ("窗口边界 30 天（>30 才排队）", _WINDOW_BOUNDARY),
    ("有下游消费 → 等次日", _ACTIVE_SUBSCRIBER_WAIT),
    ("判定顺序：排队优先于等次日", _ORDER_QUEUE_BEFORE_WAIT),
    ("核心表不适用直通", _CORE_SMALL_NOT_SHORTCUT),
]


def _setup(wd):
    pkg = wd / "backfill"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA, encoding="utf-8")
    (pkg / "planner.py").write_text(_PLANNER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_planner.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_planner.py"
    if not f.exists():
        return False, "缺少 tests/test_planner.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_planner.py 必须保持原样）"

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

    return True, "直通/重复/窗口/订阅/顺序 五项符合数据治理组规定"


TASK = {
    "id": "t37",
    "name": "回刷审批(多步判断链)",
    "prompt": (
        "`backfill/` 是数据平台的回刷审批器。`backfill/planner.py` 里的 `plan()` "
        "还没实现，请补全。\n\n"
        "它接收一次回刷申请（`request`）和这张表的当前状态（`state`），"
        "返回处理方式之一：`run`（直接执行）/ `queue`（进离线队列，低峰期跑）/ "
        "`wait`（等到次日）/ `reject`（拒绝）。字段含义见 `backfill/schema.py`。\n\n"
        "约束：\n"
        "- `backfill/schema.py` 只定义字段，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- 不得修改传入的 `request` / `state`。\n\n"
        "⚠️ **数据治理组的回刷规定**：什么情况下可以直接跑、什么情况要排队、"
        "什么情况拒绝、什么情况等到次日，以及**几条规则的判定先后顺序**——"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：这些规则有**固定的先后顺序，先命中的那条生效**；"
        "猜错不会报错——既有测试只验接口形状，判定全错它也是绿的。"
    ),
    "setup": _setup,
    "verify": _verify,
}
