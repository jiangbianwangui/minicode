"""t36：接口限流 —— 维度与豁免都是"判据反直觉"

## 缺的是什么知识

`ratelimit/` 只有窗口长度。**配额数字、限流维度、豁免规则**都不在仓库里。

## 三条都和直觉反着来

| 直觉 | 平台规定 | 直觉错在哪 |
| --- | --- | --- |
| 限流按**用户**算（每个用户各自一份配额） | 按**租户**算，租户下所有用户共享一份 | B2B 系统里租户才是计费与配额单位；按用户算等于租户开 500 个账号就白拿 500 倍配额 |
| 所有请求都算进配额 | **内部服务调用不计入** | 内部流量（网关、批处理、监控探针）挤占用户配额，会把真实用户挡在门外 |
| 超限就排队，稍后处理 | **直接拒绝**，返回建议等待秒数 | 排队会雪崩：积压的请求在窗口恢复时同时涌入，比拒绝更糟 |

三条都不会报错。按用户限流，本地测全部正常，只有大租户被打爆时才发现自己的限额是"每人一份"。

## 判卷：4 条行为检查

- **跨用户共享**：同租户 3 个用户轮流打 1200 次 → 只应放行 **900** 次
  （按用户算的话 1200 次会全部放行，正好被这条抓死）
- **租户隔离**：t1 打满后，t2 不受影响
- **内部豁免**：`internal=True` 打 1500 次，之后正常用户请求仍应放行
- **窗口滑动**：打满后等过窗口，应恢复
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""接口限流。"""
'''

_POLICY = '''"""限流窗口配置（平台组维护，不要修改）。

只有窗口长度；配额、限流维度、豁免规则不在本文件内。
"""

WINDOW_SECONDS = 60
'''

_LIMITER = '''"""接口限流器。

⚠️ 还没实现。

配额、按什么维度计数、哪些请求豁免，都是**平台组的限流规定**，
不在本仓库内，也不在任何通用最佳实践里。
"""


class Limiter:
    """按窗口限流。"""

    def __init__(self, clock):
        """clock 是无参可调用对象，返回当前时间（秒，float）。"""
        raise NotImplementedError("尚未实现")

    def check(self, tenant_id: str, user_id: str, internal: bool = False,
              request_id: str | None = None):
        """检查一次请求是否放行。

        返回 (allowed, retry_after)：
          allowed      是否放行
          retry_after  放行时为 0；被拒时是建议等待的秒数

        request_id 是调用方的请求标识；客户端重试时会带同一个 id。
        """
        raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状与基本放行，不涉及配额和维度。"""
from ratelimit.limiter import Limiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_returns_tuple():
    lim = Limiter(Clock())
    got = lim.check("t1", "u1")
    assert isinstance(got, tuple) and len(got) == 2
    assert isinstance(got[0], bool)


def test_first_request_allowed():
    lim = Limiter(Clock())
    allowed, retry_after = lim.check("t1", "u1")
    assert allowed is True
    assert retry_after == 0


def test_internal_kwarg_accepted():
    lim = Limiter(Clock())
    got = lim.check("t1", "u1", internal=True)
    assert isinstance(got, tuple)


def test_reject_returns_positive_wait():
    clock = Clock()
    lim = Limiter(clock)
    allowed = True
    for _ in range(5000):
        allowed, retry_after = lim.check("busy", "u1")
        if not allowed:
            assert retry_after > 0, "被拒时应给出正的等待秒数"
            break
    else:
        raise AssertionError("打了 5000 次都没被限流，说明没有任何配额")
'''

# ---- 4 条行为检查 ----

_PRELUDE = '''
from ratelimit.limiter import Limiter


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now
'''

_SHARED_QUOTA = _PRELUDE + '''
clock = Clock()
lim = Limiter(clock)

allowed = 0
for _ in range(400):
    for user in ("u1", "u2", "u3"):
        if lim.check("t1", user)[0]:
            allowed += 1

assert allowed == 900, (
    f"同一租户下 3 个用户共打 1200 次，实际放行 {allowed} 次。"
    f"配额是**按租户**的 900 次/分钟，不是每个用户各 900 次——"
    f"按用户算等于租户开 500 个账号就白拿 500 倍配额")
'''

_TENANT_ISOLATION = _PRELUDE + '''
clock = Clock()
lim = Limiter(clock)

for _ in range(900):
    lim.check("t1", "u1")

assert lim.check("t1", "u1")[0] is False, "t1 打满 900 次后应被拒"
assert lim.check("t2", "u1")[0] is True, (
    "t2 的请求被拒了——不同租户的配额必须互相独立")
'''

_INTERNAL_EXEMPT = _PRELUDE + '''
clock = Clock()
lim = Limiter(clock)

for _ in range(1500):
    lim.check("t1", "svc-gateway", internal=True)

assert lim.check("t1", "u1")[0] is True, (
    "内部服务调用把配额吃光了。平台规定内部调用（internal=True）"
    "不占用租户配额——否则网关/批处理的流量会把真实用户挡在门外")
'''

_WINDOW_RECOVERY = _PRELUDE + '''
clock = Clock()
lim = Limiter(clock)

for _ in range(900):
    lim.check("t1", "u1")
assert lim.check("t1", "u1")[0] is False, "打满后应被拒"

clock.now += 61.0
assert lim.check("t1", "u1")[0] is True, (
    "窗口（60 秒）已过，配额应恢复")

allowed, retry_after = 0, 0
for _ in range(2000):
    ok, ra = lim.check("t2", "u1")
    if not ok:
        retry_after = ra
        break
assert 0 < retry_after <= 60, (
    f"被拒时建议等待秒数应在 (0, 60] 内，实际 {retry_after}")
'''

_IDEMPOTENT_RETRY = _PRELUDE + '''
clock = Clock()
lim = Limiter(clock)

for _ in range(2000):
    lim.check("t1", "u1", request_id="req-1")

assert lim.check("t1", "u2")[0] is True, (
    "同一个 request_id 打了 2000 次就把配额吃光了。客户端重试**不重复计费**——"
    "否则超时重试的请求会把自己的配额打满，越重试越被拒，形成死循环")
'''

_CHECKS = [
    ("配额按租户共享", _SHARED_QUOTA),
    ("租户之间互相隔离", _TENANT_ISOLATION),
    ("内部调用不占配额", _INTERNAL_EXEMPT),
    ("同一 request_id 只计一次", _IDEMPOTENT_RETRY),
    ("窗口过后恢复", _WINDOW_RECOVERY),
]


def _setup(wd):
    pkg = wd / "ratelimit"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "policy.py").write_text(_POLICY, encoding="utf-8")
    (pkg / "limiter.py").write_text(_LIMITER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_limiter.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_limiter.py"
    if not f.exists():
        return False, "缺少 tests/test_limiter.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_limiter.py 必须保持原样）"

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

    return True, "配额维度 / 租户隔离 / 内部豁免 / 窗口恢复 四项符合平台规定"


TASK = {
    "id": "t36",
    "name": "接口限流(需查知识库)",
    "prompt": (
        "`ratelimit/` 是接口网关的限流模块。`ratelimit/limiter.py` 里的 "
        "`Limiter` 还没实现，请补全。\n\n"
        "约束：\n"
        "- `ratelimit/policy.py` 只有窗口长度，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- `check()` 返回 `(allowed, retry_after)`，放行时 `retry_after` 为 0；\n"
        "- 时间只能从构造时传入的 `clock` 取，不要直接调 `time.time()`。\n\n"
        "⚠️ **平台组的限流规定**：一分钟给多少配额、按什么维度计数、哪些请求不占配额，"
        "这些**不在代码里、不在注释里，仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最合理的方式判断。\n"
        "注意：猜错不会报错——既有测试只验接口形状，配额和维度全错它也是绿的，"
        "只有某个大租户被打爆、或者内部流量把用户挡在门外时才暴露。"
    ),
    "setup": _setup,
    "verify": _verify,
}
