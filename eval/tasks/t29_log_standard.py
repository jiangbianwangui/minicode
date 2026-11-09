"""t29：团队日志规范 —— 工具在仓库里，但「怎么用」的规矩不在

## 与 t27/t28 的差别

t27 缺的是「对端限额」，t28 缺的是「broker 语义」，这题缺的是**团队自己的约定**——
它甚至不是外部的，就是"我们组这么定的"。

仓库里有 `get_trace_id()` 这个工具（能拿到链路 id），
但**没有任何地方告诉你"每条日志都必须带上它"**，也没说不能打 PII、异常要用 exception。

这类知识最容易被低估：它不是技术约束，是**组织约定**，而组织约定永远不在代码里。

## 三条判据（都会静默失败）

1. **每条日志必须带 `trace_id` + `tenant_id`**（放在 `extra=` 里）。
   不打不会报错，只是出事时**没法按链路捞日志**——排查时才发现，已经晚了。
2. **禁止把 PII 打进日志**：email / 手机号 / 身份证。
   这是合规问题，不是 bug；代码跑得好好的，出事是罚款。
3. **异常必须 `log.exception(...)`**，不能 `log.error(str(e))`。
   后者丢掉堆栈，线上只能看到"boom"，看不到在哪炸的。

## 判卷：拦截 logging 的真实调用

用 `patch.object(logging.Logger, "_log")` 截获每一条日志记录，检查：
- `extra` 里有没有 trace_id / tenant_id
- 格式化后的消息里有没有 email / 手机号
- 至少一条记录带 `exc_info`（异常路径）

不看源码文本 —— `extra={"trace_id": x}` 换个写法（`extra=ctx`）就匹配不到了，
必须看运行时真实记录。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""订单服务。"""
'''

_CONTEXT = '''"""请求上下文：链路追踪 id 的存取。

这是仓库里**已有**的工具，直接用即可。
"""
import uuid

_trace_id: str = ""


def set_trace_id(tid: str | None = None) -> str:
    """设置当前请求的 trace id（不传则生成一个）。"""
    global _trace_id
    _trace_id = tid or uuid.uuid4().hex[:16]
    return _trace_id


def get_trace_id() -> str:
    """取当前请求的 trace id。"""
    return _trace_id
'''

_ORDERS = '''"""订单服务：下单。"""
from .context import get_trace_id  # noqa: F401
from .exporter import export_csv  # noqa: F401


class StockError(Exception):
    """库存不足。"""


_STOCK = {"apple": 10, "pear": 0}


def place_order(user: dict, items: dict) -> dict:
    """下单。user 含 email/phone/tenant_id；items 是 {商品: 数量}。

    库存不足时抛 StockError。
    """
    total = 0
    for name, qty in items.items():
        left = _STOCK.get(name, 0)
        if left < qty:
            raise StockError(f"{name} 库存不足")
        _STOCK[name] = left - qty
        total += qty
    return {"account": user["id"], "tenant": user["tenant_id"], "qty": total}
'''

_EXPORTER = '''"""导出服务。"""
import csv


def export_csv(rows: list[dict], path: str) -> int:
    """把行列表写成 CSV，返回写出的行数。"""
    if not rows:
        return 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return len(rows)
'''

_TESTS = '''"""既有测试：覆盖当前稳定行为，不得修改。"""
import pytest

from service.context import set_trace_id
from service.exporter import export_csv
from service.orders import StockError, place_order


@pytest.fixture(autouse=True)
def _tid():
    set_trace_id("trace-fixed")
    yield


def test_place_order_ok():
    r = place_order({"id": "u1", "email": "a@b.com", "phone": "13800138000",
                     "tenant_id": "t1"}, {"apple": 2})
    assert r["qty"] == 2
    assert r["tenant"] == "t1"


def test_place_order_out_of_stock():
    with pytest.raises(StockError):
        place_order({"id": "u1", "email": "a@b.com", "phone": "13800138000",
                     "tenant_id": "t1"}, {"pear": 1})


def test_export_csv(tmp_path):
    p = tmp_path / "out.csv"
    n = export_csv([{"a": 1, "b": 2}, {"a": 3, "b": 4}], str(p))
    assert n == 2
    assert p.read_text(encoding="utf-8").splitlines()[0] == "a,b"
'''


def _setup(wd):
    pkg = wd / "service"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "context.py").write_text(_CONTEXT, encoding="utf-8")
    (pkg / "orders.py").write_text(_ORDERS, encoding="utf-8")
    (pkg / "exporter.py").write_text(_EXPORTER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_service.py").write_text(_TESTS, encoding="utf-8")


# ---- 3 条行为检查 ----

_PRELUDE = '''
import logging
from unittest.mock import patch

from service.context import set_trace_id

# 判卷必须把级别开到最低：默认 root 是 WARNING，INFO 日志根本不会进 _log，
# 会被误判成"一条日志都没打"（第一版就踩了这个，参考解都被判失败）。
logging.getLogger().setLevel(logging.DEBUG)
logging.getLogger("service").setLevel(logging.DEBUG)

records = []


def _spy(self, level, msg, args, exc_info=None, extra=None,
         stack_info=False, stacklevel=1, **kw):
    try:
        text = msg % args if args else str(msg)
    except Exception:
        text = str(msg)
    records.append({
        "level": level, "msg": text, "exc_info": exc_info,
        "extra": dict(extra or {}),
    })


def capture(fn):
    records.clear()
    with patch.object(logging.Logger, "_log", _spy):
        try:
            fn()
        except Exception:
            pass
    return list(records)


USER = {"id": "u1", "email": "alice.wong@example.com",
        "phone": "13800138000", "tenant_id": "t1"}
'''

_TRACE_FIELDS = _PRELUDE + '''
from service.orders import place_order

set_trace_id("trace-abc123")
recs = capture(lambda: place_order(dict(USER), {"apple": 2}))
assert recs, "place_order 一条日志都没打 —— 无法按链路排查问题"
missing = [r for r in recs if not r["extra"].get("trace_id")
           or not r["extra"].get("tenant_id")]
assert not missing, (
    f"{len(missing)}/{len(recs)} 条日志缺 trace_id 或 tenant_id。"
    "团队规范要求每条日志都带这两个字段（放在 extra= 里），"
    "否则出事时没法按链路/租户捞日志")
'''

_NO_PII = _PRELUDE + '''
import re
from service.orders import place_order

set_trace_id("trace-abc123")
recs = capture(lambda: place_order(dict(USER), {"apple": 2}))
EMAIL = re.compile(r"[\\w.+-]+@[\\w-]+\\.[\\w.]+")
PHONE = re.compile(r"\\b1[3-9]\\d{9}\\b")
bad = []
for r in recs:
    blob = r["msg"] + " " + " ".join(str(v) for v in r["extra"].values())
    if EMAIL.search(blob):
        bad.append(("email", r["msg"][:60]))
    if PHONE.search(blob):
        bad.append(("phone", r["msg"][:60]))
assert not bad, (
    f"日志里出现了用户隐私数据：{bad}。"
    "团队规范禁止把 email / 手机号 / 身份证写进日志（合规要求，不是 bug）——"
    "要记录用户就用 id 或手机号掩码")
'''

_EXC_INFO = _PRELUDE + '''
from service.orders import place_order

set_trace_id("trace-abc123")


def boom():
    place_order(dict(USER), {"pear": 1})   # 库存为 0，必然抛 StockError


recs = capture(boom)
assert recs, "异常路径一条日志都没打"
with_tb = [r for r in recs if r["exc_info"]]
assert with_tb, (
    "异常路径没有带堆栈的日志。规范要求异常用 log.exception(...)（或 exc_info=True），"
    "log.error(str(e)) 会把堆栈丢掉，线上只能看到一条消息、看不到在哪炸的")
'''

_CHECKS = [
    ("每条日志带 trace_id / tenant_id", _TRACE_FIELDS),
    ("日志不含用户隐私数据", _NO_PII),
    ("异常路径记录堆栈", _EXC_INFO),
]


def _verify(wd):
    f = wd / "tests" / "test_service.py"
    if not f.exists():
        return False, "缺少 tests/test_service.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_service.py 必须保持原样）"

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

    return True, "链路字段 / 隐私 / 堆栈三项符合日志规范"


TASK = {
    "id": "t29",
    "name": "日志规范(需查知识库)",
    "prompt": (
        "`service/` 是一个订单服务，`place_order()` 和 `export_csv()` 目前**一条日志都不打**。"
        "上周线上排查一个用户投诉时，翻遍日志都拼不出一次请求的完整链路，"
        "最后只能靠猜——这次要把观测补上。\n\n"
        "请给这两个函数加上日志。\n\n"
        "约束：\n"
        "- `service/context.py` 里有现成的 `get_trace_id()`，直接用；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- 不要改变任何函数的返回值和异常行为（只加日志）。\n\n"
        "⚠️ **这个团队对日志有硬性规范**（要带哪些字段、什么数据绝对不能写进去、"
        "异常该怎么记），规范**不在代码里，也不在注释里——仓库里搜不到**，"
        "它是组里约定俗成的规矩。\n"
        "不要试图在文件系统里搜索它们；如果你手上有检索技能文档的工具，先查一下；"
        "**没有就按你认为最稳妥的方式把日志加上——规范查不到不是停工的理由**，"
        "别把预算花在找文档上。\n"
        "注意：违反规范不会报错——测试全绿、功能正常，代价在排查和合规上。"
    ),
    "setup": _setup,
    "verify": _verify,
}
