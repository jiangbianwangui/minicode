"""t27：外部系统约定 —— 考「知道去哪里查知识」，不考「会不会写代码」

## 与 t01–t26 的本质差别

前面 26 个任务的**答案都在仓库里**：读代码、读测试、读注释、跑 pytest，总能推出来。
这题不行——限频、重试规则、时间戳单位这三条是**对端服务方的契约**，
代码里没有、注释里没有、测试也不覆盖，`Grep` 怎么搜都搜不到。

所以它只有一个正确答案来源：`skills/rates-api/SKILL.md`（在沙箱外面，
只能通过 LoadSkill 工具拿到）。

## 为什么这是 Agent 的真实短板

写代码这部分是简单的（TTL 缓存 + 指数退避，谁都写得出来）。
难的是**意识到自己缺知识**：

- 不给它 skill → 它会按"通用最佳实践"写：TTL 60 秒（看起来更实时）、
  所有异常都重试 3 次（看起来更健壮）、ts 直接 `datetime.fromtimestamp()`。
  **这三条在别的项目里都对，在这个项目里全是错的。**
- 而且**猜错不报错**：代码跑得通、既有测试全绿、返回值类型也对，
  只有把 date 打出来看才会发现是 1970 年。这正是「沉默失败」的典型。

## 判卷的 4 条：全部是行为测试，不看实现

1. **缓存 TTL ≥ 600 秒**：300 秒后不得重新请求，700 秒后必须重新请求。
   不看源码里的常量（换个常量名就失效），直接控时钟看请求次数。
2. **429 不重试**：对端返回 429 时必须只请求 1 次就抛错。
3. **5xx 要重试**：503 一次后恢复，必须能成功返回。
4. **date 是 UTC 日期**：ts=1700000000（秒级）→ 2023-11-14。
   当毫秒处理会落到 1970；用本地时区会偏移成 11-15（东八区）。

第 2 条和第 3 条是一对：只测"会不会重试"不够，必须同时测"该不重试的时候别重试"，
否则"全都重试 3 次"这种最危险的写法反而能过。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""汇率取数模块。"""
'''

_CLIENT = '''"""对端 API 的底层 HTTP 访问。

不要改这个文件：它负责真正的网络与错误封装，上层（rates.py）只做业务逻辑。
测试通过 set_transport() 注入假传输层，因此这里不联网也能跑。
"""


class RateAPIError(Exception):
    """对端返回非 2xx 响应。status 是 HTTP 状态码。"""

    def __init__(self, status: int):
        self.status = status
        super().__init__(f"HTTP {status}")


_transport = None


def set_transport(fn):
    """测试注入假传输层：fn(url, timeout) -> str。"""
    global _transport
    _transport = fn


def _http_get(url, timeout=10):
    """GET 一个 URL 并返回响应体字符串。非 2xx 抛 RateAPIError。"""
    if _transport is not None:
        return _transport(url, timeout)
    raise RateAPIError(0)
'''

_RATES_BODY = '''"""汇率读取（业务层）。"""
import json

from .client import _http_get

API_URL = "https://api.example-rates.com/v1/latest?pair={pair}"


def fetch_rate(pair: str) -> dict:
    """取一个货币对的汇率。

    返回 {"pair": 货币对, "rate": 汇率(float), "ts": 对端时间戳(int), "date": 日期字符串}
    """
    raw = _http_get(API_URL.format(pair=pair))
    data = json.loads(raw)
    return {
        "pair": pair,
        "rate": float(data["rate"]),
        "ts": int(data["ts"]),
    }
'''

_TESTS = '''"""既有测试：覆盖当前稳定行为，不得修改。"""
import json

import pytest

from rates import client
from rates.rates import fetch_rate


@pytest.fixture(autouse=True)
def _fake(monkeypatch):
    def ok(url, timeout=10):
        return json.dumps({"pair": "USD/CNY", "rate": 7.2413, "ts": 1700000000})

    client.set_transport(ok)
    yield
    client.set_transport(None)


def test_returns_float_rate():
    r = fetch_rate("USD/CNY")
    assert r["rate"] == 7.2413
    assert isinstance(r["rate"], float)


def test_keeps_pair_and_ts():
    r = fetch_rate("USD/CNY")
    assert r["pair"] == "USD/CNY"
    assert r["ts"] == 1700000000
'''


def _setup(wd):
    pkg = wd / "rates"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "client.py").write_text(_CLIENT, encoding="utf-8")
    (pkg / "rates.py").write_text(_RATES_BODY, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_rates.py").write_text(_TESTS, encoding="utf-8")


# ---- 4 条行为检查，脚本在子进程里跑（方便 patch time） ----

_PRELUDE = '''
import json
from unittest.mock import patch
from rates import client
from rates.rates import fetch_rate

calls = {"n": 0}

def _payload():
    return json.dumps({"pair": "USD/CNY", "rate": 7.2413, "ts": 1700000000})

class _Clock:
    """可推进的假时钟，同时顶替 time.time / time.monotonic / datetime.now。"""
    def __init__(self):
        self.t = 1_000_000.0
    def __call__(self):
        return self.t
'''

_CACHE_CHECK = _PRELUDE + '''
clock = _Clock()

def ok(url, timeout=10):
    calls["n"] += 1
    return _payload()

client.set_transport(ok)
with patch("time.time", clock), patch("time.monotonic", clock), \\
     patch("time.sleep", lambda s: None):
    r1 = fetch_rate("USD/CNY")
    assert calls["n"] == 1, f"首次调用应只请求 1 次，实际 {calls['n']}"

    clock.t += 300
    r2 = fetch_rate("USD/CNY")
    assert calls["n"] == 1, (
        f"缓存 TTL 太短：仅 300 秒后就重新请求了（{calls['n']} 次）。"
        f"该 API 限频 60 次/小时，TTL 必须 >= 600 秒")

    clock.t += 400
    r3 = fetch_rate("USD/CNY")
    assert calls["n"] == 2, (
        f"缓存永不过期：700 秒后仍是 {calls['n']} 次请求，应该已过期重取")
'''

_NO_RETRY_429 = _PRELUDE + '''
def always_429(url, timeout=10):
    calls["n"] += 1
    raise client.RateAPIError(429)

client.set_transport(always_429)
with patch("time.sleep", lambda s: None):
    try:
        fetch_rate("USD/CNY")
        raise AssertionError("遇到 429 应当抛错，实际正常返回了")
    except client.RateAPIError as e:
        assert e.status == 429
    except Exception as e:
        raise AssertionError(f"应抛 RateAPIError，实际抛 {type(e).__name__}: {e}")

assert calls["n"] == 1, (
    f"429 不该重试，实际请求了 {calls['n']} 次。"
    f"429 表示已超限，重试只会延长封禁窗口")
'''

_RETRY_5XX = _PRELUDE + '''
def flaky_503(url, timeout=10):
    calls["n"] += 1
    if calls["n"] == 1:
        raise client.RateAPIError(503)
    return _payload()

client.set_transport(flaky_503)
with patch("time.sleep", lambda s: None):
    r = fetch_rate("USD/CNY")

assert calls["n"] == 2, f"5xx 应重试一次后成功，实际请求 {calls['n']} 次"
assert r["rate"] == 7.2413
'''

_DATE_UTC = _PRELUDE + '''
def ok(url, timeout=10):
    calls["n"] += 1
    return _payload()

client.set_transport(ok)
with patch("time.sleep", lambda s: None):
    r = fetch_rate("USD/CNY")

assert "date" in r, "返回值缺少 date 字段"
d = r["date"]
assert isinstance(d, str), f"date 应为字符串，实际 {type(d).__name__}"
assert "2023-11-14" in d, (
    f"date={d!r} 不正确。ts=1700000000 是**秒级** UTC 时间戳，"
    f"对应 2023-11-14（当毫秒会落到 1970，用本地时区会偏到 11-15）")
'''

_CHECKS = [
    ("缓存 TTL >= 600 秒", _CACHE_CHECK),
    ("429 不重试", _NO_RETRY_429),
    ("5xx 要重试", _RETRY_5XX),
    ("date 按秒级 UTC 解析", _DATE_UTC),
]


def _verify(wd):
    f = wd / "tests" / "test_rates.py"
    if not f.exists():
        return False, "缺少 tests/test_rates.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_rates.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"既有测试失败（引入了回归）{tail}"

    for label, code in _CHECKS:
        # 用 -c 直接传源码，不落临时文件：
        # 落文件再 unlink 会让"删除操作"快速累积，撞上沙箱的批量删除阈值
        # （实测跑到第 4 个任务就被 SAFE_DELETE_BULK_CONFIRM_REQUIRED 拦了）。
        rc, out, err = run_python(wd, code=code, timeout=90)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            detail = detail[-1] if detail else "未知"
            return False, f"{label} 不满足 → {detail}"

    return True, "缓存/重试/占位符四项全部符合外部约定"


TASK = {
    "id": "t27",
    "name": "外部API约定(需查知识库)",
    "prompt": (
        "`rates/` 是一个汇率取数模块。现在 `fetch_rate()` 每次调用都直连对端 API，"
        "在批量场景下会把配额打满。请改造它，做三件事：\n\n"
        "1. **加缓存**：同一个货币对在缓存有效期内不得重复请求对端；\n"
        "2. **加重试**：对端偶发故障时要能自动重试（但**不是所有错误都该重试**）；\n"
        "3. **返回值增加 `date` 字段**：把 `ts` 转成可读日期字符串（形如 `\"2023-11-14\"`）。\n\n"
        "约束：\n"
        "- 必须复用 `rates/client.py` 的 `_http_get`，不要自己发 HTTP；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿。\n\n"
        "⚠️ **这个 API 有使用约束**（限频配额、哪些错误该重试、时间戳的单位与精度），"
        "但这些约定**不在代码里，也不在注释里——仓库里搜不到**，"
        "它们是服务方给的外部契约。\n"
        "**不要试图在文件系统里搜索它们**（搜不到，只会浪费时间）；"
        "如果你手上有检索技能文档的工具，先查一下；没有就按你认为最稳妥的方式判断。\n"
        "注意：**猜错不会报错，只会在线上炸**——代码跑得通、既有测试全绿，"
        "都不能说明这三件事做对了。"
    ),
    "setup": _setup,
    "verify": _verify,
    # 无 agent 可见的完成判据命令（判分全靠隐藏用例），审计无需豁免
}
