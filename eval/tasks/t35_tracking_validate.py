"""t35：埋点事件校验 —— 四条"判据反直觉"的数据平台规矩

## 缺的是什么知识

`tracking/` 里有历史埋点样例（能从里面看出**字段名**），但下面四件事都不在仓库里：

1. 一共几个字段是**必填**的（直觉：越多越完整）
2. 值里出现中文该怎么处理（直觉：清洗掉再报）
3. 出现没见过的字段该怎么处理（直觉：报错 / 拒收）
4. 重复上报的窗口有多长

## 为什么每条都和直觉反着来

| 直觉 | 平台规定 | 直觉错在哪 |
| --- | --- | --- |
| 必填字段越多，数据越完整 | **只有 3 个必填**（事件名/用户/时间戳） | 每多一个必填，就多一条"上游漏传 → 整条丢"的链路 |
| 中文文案要清洗后再上报 | **整条事件丢弃** | 清洗后不同版本字段含义不一致，下游对不齐；宁可整条丢也不要半条真半条假 |
| 没见过的字段要拒收（严格） | **静默丢弃字段，事件照常上报，不告警** | 客户端埋点版本比 SDK 快，严格拒收会把新版本的数据全丢 |
| 重复上报无所谓，下游去重 | **5 秒内同人同事件只留第一条** | 下游去重依赖唯一键，而埋点常常没有唯一键 |

四条都不会报错：写错了测试照样绿，只有报表数字对不上、或者某次发版后数据**静默少了 30%** 才发现。

## 判卷：5 条行为检查

关键在第 3 条——它同时排除了"严格拒收"和"透传"两种写法：
未知字段既不能导致整条丢弃，也不能出现在输出里。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""埋点上报。"""
'''

# 历史埋点样例：字段名能从这些看出来，但「哪些必填」不能
_SAMPLES = '''"""历史成功上报的埋点样例（数据平台提供，不要修改）。

这些是线上真实跑通过的样本，字段名以它们为准。
"""

SAMPLES = [
    {
        "event_name": "order_created",
        "user_id": "u_1001",
        "ts": 1699000000000,
        "order_id": "o_9001",
        "amount": 128.5,
    },
    {
        "event_name": "order_paid",
        "user_id": "u_1001",
        "ts": 1699000060000,
        "order_id": "o_9001",
        "pay_channel": "alipay",
    },
    {
        "event_name": "page_viewed",
        "user_id": "u_1002",
        "ts": 1699000120000,
        "page_id": "p_home",
        "referrer": "",
    },
]
'''

_SCHEMA = '''"""埋点字段白名单（数据平台维护，不要修改）。

白名单之外的一律是「未知字段」——**怎么处理未知字段是平台规矩，不在本文件内**。
"""

ALLOWED_FIELDS = frozenset({
    "event_name", "user_id", "ts",
    "order_id", "amount", "pay_channel",
    "page_id", "referrer",
})
'''

_VALIDATOR = '''"""埋点事件校验与清洗。

⚠️ 还没实现。

数据平台的规矩（哪些字段必填、出现异常值怎么处理、去重窗口多长）
**不在本仓库内**，需要按平台文档实现。
"""
from .schema import ALLOWED_FIELDS  # noqa: F401


def normalize(event: dict) -> dict | None:
    """校验并清洗一条埋点，返回可以上报的事件。

    返回 None 表示**整条事件应当丢弃**。
    不得修改传入的 event。
    """
    raise NotImplementedError("尚未实现")


class Dedup:
    """上报去重。"""

    def __init__(self, clock):
        """clock 是一个无参可调用对象，返回当前时间（秒，float）。"""
        raise NotImplementedError("尚未实现")

    def accept(self, event: dict) -> bool:
        """True = 该上报；False = 窗口内重复，丢弃。

        窗口从**上一次被接受**的上报开始算；被丢弃的重复请求**不刷新**窗口
        （否则一直重试的事件会永远被拦）。
        """
        raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状与样例可上报，不涉及任何平台规矩。"""
import pytest

from tracking.samples import SAMPLES
from tracking.validator import Dedup, normalize

FULL = {
    "event_name": "order_created",
    "user_id": "u_1",
    "ts": 1699000000000,
    "order_id": "o_1",
}


def test_samples_are_accepted():
    for s in SAMPLES:
        assert normalize(dict(s)) is not None, f"样例 {s['event_name']} 被判为丢弃"


def test_returns_new_dict():
    event = dict(FULL)
    out = normalize(event)
    assert out is not None
    assert out is not event, "不应返回传入的同一个对象"
    assert event == FULL, "不得修改传入的 event"


def test_none_or_dict():
    out = normalize(dict(FULL))
    assert out is None or isinstance(out, dict)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_dedup_accepts_first():
    d = Dedup(_Clock())
    assert d.accept(dict(FULL)) is True


def test_dedup_rejects_immediate_repeat():
    c = _Clock()
    d = Dedup(c)
    d.accept(dict(FULL))
    assert d.accept(dict(FULL)) is False
'''


def _setup(wd):
    pkg = wd / "tracking"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "samples.py").write_text(_SAMPLES, encoding="utf-8")
    (pkg / "schema.py").write_text(_SCHEMA, encoding="utf-8")
    (pkg / "validator.py").write_text(_VALIDATOR, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_validator.py").write_text(_TESTS, encoding="utf-8")


# ---- 5 条行为检查 ----

_PRELUDE = '''
from tracking.validator import Dedup, normalize

BASE = {"event_name": "order_created", "user_id": "u_1", "ts": 1699000000000}


def ev(**kw):
    d = dict(BASE)
    d.update(kw)
    return d
'''

_REQUIRED_PRESENT = _PRELUDE + '''
out = normalize(ev())
assert out is not None, (
    "只有 event_name / user_id / ts 三个字段的事件被丢弃了。"
    "平台的必填字段就是这三个，多一个都不要——"
    "每多一个必填，就多一条「上游漏传就把整条数据丢掉」的链路")
assert set(out) >= {"event_name", "user_id", "ts"}, out
'''

_MISSING_REQUIRED = _PRELUDE + '''
for miss in ("event_name", "user_id", "ts"):
    d = ev()
    d.pop(miss)
    assert normalize(d) is None, (
        f"缺 {miss} 的事件应整条丢弃，实际被接受了")
'''

_CHINESE_VALUE = _PRELUDE + '''
got = normalize(ev(order_id="订单-9001"))
assert got is None, (
    f"事件里出现了中文文案（order_id=订单-9001），应**整条丢弃**，实际返回了 {got}。"
    f"不能只把这个字段删掉再上报——半条真半条假的数据比没有更糟："
    f"下游会以为这个事件本来就没有这个字段")
'''

_UNKNOWN_FIELD = _PRELUDE + '''
got = normalize(ev(utm_source="douyin", _debug=True))
assert got is not None, (
    f"出现没见过的字段就整条丢弃了。平台要求静默忽略未知字段、事件照常上报——"
    f"客户端埋点版本迭代比 SDK 快，严格拒收会把新版本的数据全部丢掉")
assert "utm_source" not in got and "_debug" not in got, (
    f"未知字段被透传了：{got}。未知字段要丢掉，不能原样传给下游")
assert got.get("event_name") == "order_created", f"事件本身被改坏了：{got}"
'''

_DEDUP_WINDOW = _PRELUDE + '''
class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


c = Clock()
d = Dedup(c)
assert d.accept(ev()) is True, "第一条应放行"

c.now += 4.0
assert d.accept(ev()) is False, "4 秒内同一人同一事件重复，应被去重"

c.now += 2.0
assert d.accept(ev()) is True, (
    "距第一次已 6 秒（超过 5 秒窗口），应重新放行")

other = ev(user_id="u_2")
c.now += 1.0
assert d.accept(other) is True, "不同用户的事件不应被去重"
'''

_CHECKS = [
    ("只要三个必填字段", _REQUIRED_PRESENT),
    ("缺必填整条丢", _MISSING_REQUIRED),
    ("含中文文案整条丢", _CHINESE_VALUE),
    ("未知字段静默忽略", _UNKNOWN_FIELD),
    ("5 秒去重窗口", _DEDUP_WINDOW),
]


def _verify(wd):
    f = wd / "tests" / "test_validator.py"
    if not f.exists():
        return False, "缺少 tests/test_validator.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_validator.py 必须保持原样）"

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

    return True, "必填集合 / 异常值处理 / 未知字段 / 去重窗口 五项符合数据平台规矩"


TASK = {
    "id": "t35",
    "name": "埋点校验(需查知识库)",
    "prompt": (
        "`tracking/` 是埋点上报前的校验层。`tracking/validator.py` 里 "
        "`normalize()` 和 `Dedup` 都是 `NotImplementedError`，请实现。\n\n"
        "约束：\n"
        "- `tracking/samples.py` 是历史成功上报的样例，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- `normalize()` 返回 `None` 表示整条事件丢弃，返回 dict 表示可以上报；\n"
        "- 不得修改传入的 event。\n\n"
        "⚠️ **数据平台的规矩**：哪些字段是必填的、值里出现异常内容时该丢弃还是清洗、"
        "遇到没见过的字段该怎么办、多久之内的重复上报要拦下来。\n"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：猜错不会报错——既有测试只验接口形状，规矩全错它也是绿的，"
        "只有数据报表对不上号、或者某次发版后数据静默变少时才暴露。"
    ),
    "setup": _setup,
    "verify": _verify,
}
