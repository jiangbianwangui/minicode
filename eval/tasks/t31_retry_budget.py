"""t31：失败重试的预算 —— 通用做法在这里全是错的

## 这题考什么

写重试逻辑人人都会，但"几次、等多久、哪些错误不重试"这些数字是**团队约定**：
仓库不可见、代码不可推、日志里也没有。不给 skill 时 agent 会写出"最标准"的版本：
5 次尝试、封顶 30 秒、只要抛异常就重试——三条全和约定相反。

## 为什么这些错误是"静默"的

- 重试 4xx：数据不会被修好，只是把坏数据处理多遍（如果下游是写操作，就是重复写）。
- 退避封顶：本地测试永远测试不到——测试里只跑 1 次失败，看不出上限被砍。
- 次数太多：跑得通，只是慢。上线后批作业超时，主因往往是这个。

## 判卷：注入"会抖的上游"看真实调用行为

全部用假 upstream + 假时钟，检查：
1. 连续失败时总尝试次数 = 3（不是 5、不是无限）；
2. 退避序列 = [0.5, 1.0]（按 attempt 递增，且**超过 30 秒也不封顶**）；
3. 422 只调 1 次；429 尊重 Retry-After；Retry-After > 60 时放弃该条。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""批量作业。"""
'''

_UPSTREAM = '''"""上游服务（不可修改）。

`put(record)` 失败时抛 UpstreamError(status=...)。
测试通过 set_transport() 注入假传输层，不联网。
"""


class UpstreamError(Exception):
    """上游返回错误。status 是 HTTP 状态码；retry_after 是响应头里的秒数（可能为 None）。"""

    def __init__(self, status: int, retry_after=None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}")


_transport = None


def set_transport(fn):
    global _transport
    _transport = fn


def put(record: dict) -> bool:
    """把一条记录写到上游。失败抛 UpstreamError。"""
    if _transport is None:
        raise RuntimeError("transport 未注入")
    return _transport(record)
'''

_BATCH = '''"""批量同步作业。"""
from .upstream import put  # noqa: F401


def push_all(records: list[dict]) -> dict:
    """把 records 逐条推到上游，带重试。

    返回 {"ok": 成功条数, "failed": 最终仍失败的条数}。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：只验证接口形状。"""
from job.batch import push_all
from job.upstream import set_transport


def test_all_ok():
    set_transport(lambda r: True)
    r = push_all([{"id": 1}, {"id": 2}])
    assert r == {"ok": 2, "failed": 0}
'''

# ---- 判卷用例：每个都在独立子进程里跑，互不污染 ----

_ATTEMPTS = '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all

calls = []
def t(r):
    calls.append(1)
    raise UpstreamError(503)
set_transport(t)
r = push_all([{"id": 1}])
assert len(calls) == 3, f"尝试次数应为 3（首次+2次重试），实际 {len(calls)}"
assert r == {"ok": 0, "failed": 1}, r
'''

# 🔴 判卷必须**用自己的时钟**。
#
# 第一版直接 `time.sleep = 记录`，结果把"参数化注入 sleep"的正确解判成失败
# —— 它默认 sleep=time.sleep 在**函数定义时**就绑定好了，之后再改模块属性无效。
# 这不是 agent 的错，是我的判法只认一种合法写法。
# 但这说明**只重绑 time.sleep 覆盖不全**，所以要给假时钟三条路径都试一遍
# （模块属性 / 每次调用时查 time.sleep / 可见名 time.sleep），
# 三条都不记录才判定"没有退避"。
_SPY = '''
import sys
import time

WAITS = []


class _Clock:
    """记录所有 sleep 请求的假时钟。"""

    def __call__(self, s=0):
        WAITS.append(s)

    def sleep(self, s=0):
        WAITS.append(s)


_clock = _Clock()

# 路径 1：换个模块名指向同一个 time 模块（函数运行时绑定 `time` 的话会命中）
sys.modules.setdefault("_time_alias_t31", time)
time.sleep = _clock

# 路径 2：直接把 sys.modules 的名字绑到幽灵模块，覆盖 "import time 后再 time.sleep" 的写法
import types
_shadow = types.ModuleType("time")
_shadow.sleep = _clock
_shadow.monotonic = time.monotonic
_shadow.time = time.time
sys.modules["time"] = _shadow
'''

_BACKOFF = _SPY + '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all

calls = []


def t(r):
    calls.append(1)
    raise UpstreamError(503)


set_transport(t)
push_all([{"id": 1}])

assert len(calls) == 3, f"503 连续失败时应尝试 3 次，实际 {len(calls)}"
assert len(WAITS) == 2, (
    f"应退避 2 次，实际记录到 {WAITS}。"
    f"（若你确实调用了 sleep 但这里没记录到，说明退避走的是别的时钟——"
    f"请用 time.sleep 的默认实现）")
assert abs(WAITS[0] - 0.5) < 1e-6, f"第 1 次退避应为 0.5s，实际 {WAITS[0]}"
assert abs(WAITS[1] - 1.0) < 1e-6, f"第 2 次退避应为 1.0s，实际 {WAITS[1]}"
'''

_NO_CAP = _SPY + '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all


def t(r):
    raise UpstreamError(503)


set_transport(t)
push_all([{"id": 1}])

assert WAITS == [0.5, 1.0], (
    f"退避应严格按 0.5→1.0 递增（不封顶、不加抖动），实际 {WAITS}")
'''

_NO_RETRY_422 = _SPY + '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all

calls = []


def t(r):
    calls.append(1)
    raise UpstreamError(422)


set_transport(t)
r = push_all([{"id": 1}])
assert len(calls) == 1, f"422 不应重试，实际调用 {len(calls)} 次"
assert r == {"ok": 0, "failed": 1}, r
'''

_RETRY_AFTER = _SPY + '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all

calls = []


def t(r):
    calls.append(1)
    if len(calls) == 1:
        raise UpstreamError(429, retry_after=7)
    return True


set_transport(t)
r = push_all([{"id": 1}])
assert WAITS and abs(WAITS[0] - 7) < 1e-6, (
    f"429 带 Retry-After=7 时应等 7 秒（而不是自己的退避），实际 {WAITS}")
assert r == {"ok": 1, "failed": 0}, r
'''

_GIVE_UP_LONG_WAIT = _SPY + '''
from job.upstream import set_transport, UpstreamError
from job.batch import push_all


def t(r):
    raise UpstreamError(429, retry_after=600)


set_transport(t)
r = push_all([{"id": 1}])
assert not any(w > 60 for w in WAITS), (
    f"Retry-After=600 超过 60 秒上限，应放弃该条而不是干等，实际等了 {WAITS}")
assert r == {"ok": 0, "failed": 1}, r
'''

_CHECKS = [
    ("最多 3 次尝试", _ATTEMPTS),
    ("退避 0.5/1.0", _BACKOFF),
    ("退避不封顶", _NO_CAP),
    ("422 不重试", _NO_RETRY_422),
    ("尊重 Retry-After", _RETRY_AFTER),
    ("Retry-After>60 放弃该条", _GIVE_UP_LONG_WAIT),
]


def _setup(wd):
    pkg = wd / "job"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "upstream.py").write_text(_UPSTREAM, encoding="utf-8")
    (pkg / "batch.py").write_text(_BATCH, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_batch.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_batch.py"
    if not f.exists():
        return False, "缺少 tests/test_batch.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_batch.py 必须保持原样）"

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
    return True, "次数/退避/错误分类/Retry-After 六项全部符合约定"


TASK = {
    "id": "t31",
    "name": "重试预算(需查知识库)",
    "prompt": (
        "`job/batch.py` 里的 `push_all()` 还没实现。请实现它：逐条把记录推到上游"
        "`job/upstream.py::put()`，失败要重试，返回 `{\"ok\", \"failed\"}`。\n\n"
        "约束：\n"
        "- `job/upstream.py` 是上游提供的，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿。\n\n"
        "⚠️ 重试的**次数、退避节奏、哪些错误该重试、Retry-After 怎么处理**"
        "是我们组的统一约定，**不在代码里、仓库里搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最稳妥的方式判断。\n"
        "注意：猜错不会报错——代码跑得通、既有测试全绿，只会在批作业整体超时、"
        "或者坏数据被重复处理时才显现。"
    ),
    "setup": _setup,
    "verify": _verify,
}
