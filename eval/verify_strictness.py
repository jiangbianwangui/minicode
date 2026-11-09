"""verify 严格性对照：证明 verify 能抓住"看起来对但有细微错"的解。

为什么需要：如果 verify 太宽松，agent 会"假通过"，于是基线虚高、
feature 显得无用。这个脚本对每个任务注入若干**精心构造的错误解**，
要求 verify 必须判定失败。任何一个错误解被放过，说明该任务有漏洞。

与 selftest_tasks.py 互补：
  - selftest 证明「任务可解」（套对解 → 必须过）
  - 本脚本证明「任务不可蒙」（套错解 → 必须不过）

用法：
    python eval/verify_strictness.py            # 全部
    python eval/verify_strictness.py t23 t24    # 指定
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from tasks import TASKS  # noqa: E402

# 每个任务：列表 of (错误解名称, 注入函数)
# 注入函数在 setup 之后被调用，应制造一个"看似合理但实际错误"的状态。


def _wrong_mutates_input(wd):
    """t24：返回值正确，但原地修改了入参（违反契约）。"""
    (wd / "meeting" / "slots.py").write_text(
        '''from .models import Slot


def merge_slots(slots):
    slots.sort()            # 原地排序 —— 修改了入参！
    if not slots:
        return []
    out = [slots[0]]
    for s in slots[1:]:
        last = out[-1]
        if s.start <= last.end:
            out[-1] = Slot(last.start, max(last.end, s.end))
        else:
            out.append(s)
    return out
''',
        encoding="utf-8",
    )
    (wd / "meeting" / "finder.py").write_text(
        '''from .models import Slot
from .slots import merge_slots


def find_room_slots(book, slots, duration, capacity=1):
    window = merge_slots(list(slots))
    out = []
    for name in book.names():
        room = book.get(name)
        if room.capacity < capacity:
            continue
        for a in room.free_slots():
            for b in window:
                s, e = max(a.start, b.start), min(a.end, b.end)
                if e - s >= duration:
                    out.append((name, Slot(s, e)))
    out.sort(key=lambda x: (x[0], x[1].start))
    return out
''',
        encoding="utf-8",
    )


def _wrong_no_rollback(wd):
    """t23：discount 正确，但履约不回滚（留下脏状态）。"""
    (wd / "inventory" / "discount.py").write_text(
        '''def apply_discount(amount, rate):
    if not 0 <= rate <= 1:
        raise ValueError("rate must be in [0, 1]")
    total = amount - 20 if amount >= 200 else amount
    return round(total * (1 - rate) + 1e-9, 2)
''',
        encoding="utf-8",
    )
    (wd / "inventory" / "fulfill.py").write_text(
        '''from .exceptions import InsufficientStock


class Fulfiller:
    def __init__(self, store):
        self.store = store

    def reserve_order(self, order):
        done = []
        for line in order.lines:
            self.store.reserve(line.sku, line.qty)   # 失败时没有回滚！
            done.append(line)
        return done
''',
        encoding="utf-8",
    )


def _wrong_edits_test(wd):
    """t18：直接改测试文件让它通过（必须被抓）。"""
    p = wd / "test_stack.py"
    p.write_text(
        "from stack import Stack\n\n\n"
        "def test_nothing():\n"
        "    assert True\n",
        encoding="utf-8",
    )


def _wrong_partial_unary(wd):
    """t21：只处理数字前的一元负号，不处理括号/嵌套的一元负号。"""
    (wd / "expr_eval.py").write_text(
        '''import re

_TOKEN = re.compile(r"\\d+\\.?\\d*|\\.\\d+|[+\\-*/()]")


def eval_expr(s):
    tokens = _TOKEN.findall(s)
    pos = 0

    def factor():
        nonlocal pos
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            v = expr()
            pos += 1
            return v
        pos += 1
        return float(tok)

    def term():
        nonlocal pos
        v = factor()
        while pos < len(tokens) and tokens[pos] in "*/":
            op = tokens[pos]; pos += 1
            r = factor()
            v = v * r if op == "*" else v / r
        return v

    def expr():
        nonlocal pos
        v = term()
        while pos < len(tokens) and tokens[pos] in "+-":
            op = tokens[pos]; pos += 1
            r = term()
            v = v + r if op == "+" else v - r
        return v

    return expr()
''',
        encoding="utf-8",
    )
    (wd / "test_expr_eval.py").write_text(
        "from expr_eval import eval_expr\n\n\n"
        "def test_basic():\n"
        "    assert eval_expr('1+2') == 3\n",
        encoding="utf-8",
    )


def _wrong_region_typo(wd):
    """t19：聚合基本对，但少算一个 region（典型漏行）。"""
    import csv as _csv
    import json as _json

    seen, agg = set(), {}
    rows = _csv.DictReader((wd / "sales.csv").read_text(encoding="utf-8").splitlines())
    for r in rows:
        key = (r["region"], r["amount"], r["qty"])
        if key in seen:
            continue
        seen.add(key)
        try:
            amount = float(r["amount"])
        except (TypeError, ValueError):
            continue
        if r["region"] == "west":      # 故意漏掉 west
            continue
        qty = int(r["qty"])
        d = agg.setdefault(r["region"], {"total": 0.0, "count": 0})
        d["total"] += amount * qty
        d["count"] += 1
    (wd / "report.json").write_text(_json.dumps(agg), encoding="utf-8")
    (wd / "test_report.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8"
    )


def _wrong_global_replace(wd):
    """t20：全局替换 log_it 为 logger.info，但没加 func= 参数。"""
    import re as _re

    p = wd / "legacy.py"
    src = p.read_text(encoding="utf-8")
    src = src.replace('"""遗留模块：需要统一改造日志调用。"""',
                      '"""遗留模块。"""\n\nimport logging\n\nlogger = logging.getLogger(__name__)')
    src = _re.sub(r'log_it\("(.*?)"\)', r'logger.info("\1")', src)
    p.write_text(src, encoding="utf-8")


def _wrong_rename_partial(wd):
    """t17：只改了 models.py 和 service.py，漏了 report.py 与测试。"""
    for rel in ["order/models.py", "order/service.py"]:
        p = wd / rel
        p.write_text(p.read_text(encoding="utf-8").replace("calc_total", "compute_total"),
                     encoding="utf-8")


def _wrong_copy_not_new(wd):
    """t22：apply_discount 原地改了入参 dict（违反"返回新列表"）。"""
    p = wd / "cart" / "cart.py"
    src = p.read_text(encoding="utf-8")
    src += '''

def apply_discount(items, rate):
    if not 0 <= rate <= 1:
        raise ValueError("bad rate")
    for i in items:
        i["price"] = round(i["price"] * (1 - rate), 2)   # 原地修改！
    return items
'''
    p.write_text(src, encoding="utf-8")


_T27_TMPL = '''"""汇率读取（业务层）。"""
import json
import time
from datetime import datetime, timezone

from .client import RateAPIError, _http_get

API_URL = "https://api.example-rates.com/v1/latest?pair={{pair}}"
CACHE_TTL = {ttl}
_cache = {{}}


def _fetch(url):
{retry}


def fetch_rate(pair: str) -> dict:
    now = time.time()
    hit = _cache.get(pair)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    data = json.loads(_fetch(url=API_URL.format(pair=pair)))
    ts = int(data["ts"])
    out = {{
        "pair": pair,
        "rate": float(data["rate"]),
        "ts": ts,
        "date": {date_expr},
    }}
    _cache[pair] = (now, out)
    return out
'''

_RETRY_ALL = '''    """所有异常都重试 3 次（通用最佳实践 —— 在这题里是错的）。"""
    last = None
    for i in range(3):
        try:
            return _http_get(url)
        except Exception as e:
            last = e
            time.sleep(0.01)
    raise last'''

_RETRY_5XX_ONLY = '''    """只重试 5xx（正确）。"""
    last = None
    for i in range(3):
        try:
            return _http_get(url)
        except RateAPIError as e:
            if e.status < 500:
                raise
            last = e
        except OSError as e:
            last = e
        if i < 2:
            time.sleep(0.01)
    raise last'''

_DATE_OK = 'datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")'


def _write_t27(wd, ttl=600, retry=_RETRY_5XX_ONLY, date_expr=_DATE_OK):
    (wd / "rates" / "rates.py").write_text(
        _T27_TMPL.format(ttl=ttl, retry=retry, date_expr=date_expr),
        encoding="utf-8",
    )


def _wrong_ttl_60(wd):
    """t27：TTL 写 60 秒 —— "更实时"，通用做法，但会把 60 次/小时打满。"""
    _write_t27(wd, ttl=60)


def _wrong_retry_everything(wd):
    """t27：所有异常都重试 —— "更健壮"，但遇到 429 会延长封禁。"""
    _write_t27(wd, retry=_RETRY_ALL)


def _wrong_ts_local(wd):
    """t27：不带 tz 解析 —— 东八区会偏成次日（代码不报错，值错了）。"""
    _write_t27(wd, date_expr='datetime.fromtimestamp(ts).strftime("%Y-%m-%d")')


def _wrong_ts_ms(wd):
    """t27：把秒级 ts 当毫秒 —— 落到 1970。"""
    _write_t27(wd, date_expr='datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")')


_T28_TMPL = '''"""消费循环。"""
from .client import BrokerError, QueueClient  # noqa: F401


def consume(client: QueueClient, handler, max_messages: int = 100) -> dict:
    client.set_prefetch({prefetch})
    client.set_ack_timeout({ack_timeout})
    handled = 0
    failed = 0
    for _ in range(max_messages):
        batch = client.drain()
        if not batch:
            break
        for msg in batch:
            msg_id = msg["id"]
            try:
                handler(msg["payload"])
            except Exception:
                failed += 1
                client.reject(msg_id, requeue={requeue})
            else:
                handled += 1
                {on_success}
    return {{"handled": handled, "failed": failed}}
'''


def _write_t28(wd, prefetch=5, ack_timeout=60, requeue="False", on_success="client.ack(msg_id)"):
    (wd / "worker" / "consumer.py").write_text(
        _T28_TMPL.format(
            prefetch=prefetch, ack_timeout=ack_timeout,
            requeue=requeue, on_success=on_success,
        ),
        encoding="utf-8",
    )


def _wrong_prefetch_10(wd):
    """t28：prefetch=10 —— **最"合理"的猜测值**（base 真的猜了这个），但契约是 7。"""
    _write_t28(wd, prefetch=10, ack_timeout=90)


def _wrong_ack_15x(wd):
    """t28：ack 超时留 1.5 倍余量 —— 通用做法，但契约要求 2.5。"""
    _write_t28(wd, ack_timeout=45)


def _wrong_requeue_true(wd):
    """t28：失败后 requeue=True —— "重试啊"，实际会死循环。"""
    _write_t28(wd, requeue="True")


def _wrong_ack_all(wd):
    """t28：不管成败一律 ack —— 失败消息静默丢失。"""
    _write_t28(wd, on_success="client.ack(msg_id)", requeue="True")


_T29_TMPL = '''"""订单服务：下单。"""
import logging

from .context import get_trace_id
from .exporter import export_csv  # noqa: F401

logger = logging.getLogger(__name__)


class StockError(Exception):
    """库存不足。"""


_STOCK = {{"apple": 10, "pear": 0}}


def place_order(user: dict, items: dict) -> dict:
    """下单。"""
{head}
    total = 0
    for name, qty in items.items():
        left = _STOCK.get(name, 0)
        if left < qty:
{on_error}
            raise StockError(f"{{name}} 库存不足")
        _STOCK[name] = left - qty
        total += qty
{tail}
    return {{"account": user["id"], "tenant": user["tenant_id"], "qty": total}}
'''

_CTX = '    ctx = {"trace_id": get_trace_id(), "tenant_id": user["tenant_id"]}\n'


def _write_t29(wd, head="", on_error="", tail=""):
    (wd / "service" / "orders.py").write_text(
        _T29_TMPL.format(head=head, on_error=on_error, tail=tail),
        encoding="utf-8",
    )


def _wrong_no_trace(wd):
    """t29：只打普通日志，不带 trace_id / tenant_id —— 最常见写法。"""
    _write_t29(
        wd,
        head='    logger.info("place_order start")\n',
        on_error='            logger.error("stock shortage")\n',
        tail='    logger.info("place_order done")\n',
    )


def _wrong_log_pii(wd):
    """t29：把整个 user 打进日志 —— 顺手、可读，但把 PII 泄了。"""
    _write_t29(
        wd,
        head='    ctx = {"trace_id": get_trace_id(), "tenant_id": user["tenant_id"]}\n'
             '    logger.info(f"place_order start user={user}", extra=ctx)\n',
        on_error='            logger.exception("stock shortage", extra=ctx)\n',
        tail='    logger.info("place_order done", extra=ctx)\n',
    )


def _wrong_error_str(wd):
    """t29：异常用 log.error(str(e)) —— 打了日志，但丢掉堆栈。"""
    _write_t29(
        wd,
        head='    ctx = {"trace_id": get_trace_id(), "tenant_id": user["tenant_id"]}\n',
        on_error='            try:\n'
                 '                raise RuntimeError("stock")\n'
                 '            except RuntimeError as e:\n'
                 '                logger.error(str(e), extra=ctx)\n',
        tail='    logger.info("place_order done account=%s", user["id"], extra=ctx)\n',
    )


# ---- t31 重试预算：四个"最标准"的写法 ----

_T31_BASE = '''"""批量同步作业。"""
import time

from .upstream import put, UpstreamError


def push_all(records: list[dict]) -> dict:
    ok = failed = 0
    for r in records:
        try:
            put(r)
            ok += 1
        except UpstreamError:
            failed += 1
    return {{"ok": ok, "failed": failed}}
'''

_T31_RETRY = '''"""批量同步作业。"""
import time

from .upstream import put, UpstreamError


def push_all(records: list[dict]) -> dict:
    ok = failed = 0
    for r in records:
        for attempt in range(1, {attempts} + 1):
            try:
                put(r)
                ok += 1
                break
            except UpstreamError as e:
                if {should_retry}:
                    failed += 1
                    break
                if attempt == {attempts}:
                    failed += 1
                    break
                delay = {delay}
                time.sleep(delay)
    return {{"ok": ok, "failed": failed}}
'''


def _t31(wd, attempts=3, ignore=None, delay="0.5 * 2 ** (attempt - 1)", retry_after=True):
    """生成 t31 的实现。retry_after=False 表示忽略响应头。"""
    if ignore is None:
        ignore = "set()"
    (wd / "job" / "batch.py").write_text(
        f'''"""批量同步作业。"""
import time

from .upstream import put, UpstreamError

_IGNORE = {ignore}
_USE_RA = {retry_after}


def push_all(records: list[dict]) -> dict:
    ok = failed = 0
    for r in records:
        for attempt in range(1, {attempts} + 1):
            try:
                put(r)
                ok += 1
                break
            except UpstreamError as e:
                if e.status in _IGNORE:
                    failed += 1
                    break
                if attempt == {attempts}:
                    failed += 1
                    break
                if _USE_RA and e.status == 429 and e.retry_after is not None:
                    if e.retry_after > 60:
                        failed += 1
                        break
                    time.sleep(e.retry_after)
                    continue
                time.sleep({delay})
    return {{"ok": ok, "failed": failed}}
''',
        encoding="utf-8",
    )


def _wrong_t31_five_attempts(wd):
    """t31：重试 5 次 —— 教科书上最常见的值。"""
    _t31(wd, attempts=5)


def _wrong_t31_cap_30(wd):
    """t31：退避封顶 30 秒 —— 看起来更稳健，实则在抖动窗口里同频重试。"""
    _t31(wd, delay="min(0.5 * 2 ** (attempt - 1), 30)")


def _wrong_t31_retry_all(wd):
    """t31：只要抛异常就重试 —— 会把 422 的坏数据处理 3 遍。"""
    _t31(wd, ignore="set()")


def _wrong_t31_delay_1s(wd):
    """t31：固定 1 秒退避。"""
    _t31(wd, delay="1.0")


def _wrong_t31_ignore_ra(wd):
    """t31：忽略 Retry-After，一律走自己的退避。"""
    _t31(wd, ignore="{422}", retry_after=False)


# ---- t33 SLA 预算：四个"直觉上更合理"的写法 ----

_T33_TMPL = '''"""按 SLA 等级分配超时预算。"""
from .policy import SLA_LEVELS

DEFAULT_LEVEL = __DEFAULT__
_BUDGETS = __BUDGETS__
_RETRIES = __RETRIES__
DEGRADE_RATIO = __RATIO__


def _normalize(level):
    return level if level in SLA_LEVELS else DEFAULT_LEVEL


def budget_ms(level) -> int:
    return _BUDGETS[_normalize(level)]


def max_retries(level) -> int:
    return _RETRIES[_normalize(level)]


class Deadline:
    def __init__(self, level):
        self.level = _normalize(level)
        self._limit = float(_BUDGETS[self.level])
        self._used = 0.0

    @property
    def limit_ms(self) -> float:
        return self._limit

    def charge(self, ms: float) -> None:
        self._used += float(ms)

    def remaining_ms(self) -> float:
        return max(0.0, self._limit - self._used)

    def should_degrade(self) -> bool:
        return self._used >= self._limit * DEGRADE_RATIO

    def expired(self) -> bool:
        return self._used >= self._limit
'''


def _t33(wd, default='"P1"', budgets=None, retries=None, ratio="0.70"):
    src = (_T33_TMPL
           .replace("__DEFAULT__", default)
           .replace("__BUDGETS__", budgets or '{"P0": 800, "P1": 2500, "P2": 8000}')
           .replace("__RETRIES__", retries or '{"P0": 0, "P1": 1, "P2": 2}')
           .replace("__RATIO__", ratio))
    (wd / "sla" / "budget.py").write_text(src, encoding="utf-8")


def _wrong_t33_p0_longest(wd):
    """t33：P0 给 30 秒 —— 「最重要所以等最久」的直觉。"""
    _t33(wd, budgets='{"P0": 30000, "P1": 2500, "P2": 8000}')


def _wrong_t33_ratio_80(wd):
    """t33：80% 才降级 —— 最常见的阈值。"""
    _t33(wd, ratio="0.80")


def _wrong_t33_ratio_50(wd):
    """t33：50% 就降级 —— 过度保守，正常请求会被误降级。"""
    _t33(wd, ratio="0.50")


def _wrong_t33_p0_retry(wd):
    """t33：P0 也允许重试 —— 「重要的更该重试」的直觉。"""
    _t33(wd, retries='{"P0": 2, "P1": 1, "P2": 2}')


def _wrong_t33_default_p2(wd):
    """t33：未知等级给最宽松的 P2 —— 「不知道就宽松点」的直觉。"""
    _t33(wd, default='"P2"')


# ---- t34 灰度门禁：五个"直觉更合理"的判定 ----

_T34_TMPL = '''"""灰度发布门禁。"""

MIN_SAMPLES = 500
DELTA_PP_THRESHOLD = 0.8
BURN_ROLLBACK = __BURN__
HOLD_LIMIT = __HOLD__


def error_rate_delta(baseline_err, canary_err):
    delta = (canary_err - baseline_err) * 100
    return delta if delta > 0 else 0.0


def burn_rate(error_rate, tolerance):
    if tolerance <= 0:
        return float("inf")
    return error_rate / tolerance


def decide(baseline_err, canary_err, canary_samples, tolerance, history=()):
    __MIN_CHECK__
    if error_rate_delta(baseline_err, canary_err) <= DELTA_PP_THRESHOLD:
        return "continue"
    if burn_rate(canary_err, tolerance) > BURN_ROLLBACK:
        return "rollback"
    recent = list(history) + ["hold"]
    tail = recent[-HOLD_LIMIT:]
    if len(tail) == HOLD_LIMIT and all(v == "hold" for v in tail):
        return "rollback"
    __HOLD_RETURN__
'''

_T34_ABS_TMPL = '''"""灰度发布门禁（按 canary 错误率绝对值判定）。"""

MIN_SAMPLES = 500
ABS_THRESHOLD = __ABS__
BURN_ROLLBACK = 3.5


def error_rate_delta(baseline_err, canary_err):
    delta = (canary_err - baseline_err) * 100
    return delta if delta > 0 else 0.0


def burn_rate(error_rate, tolerance):
    if tolerance <= 0:
        return float("inf")
    return error_rate / tolerance


def decide(baseline_err, canary_err, canary_samples, tolerance, history=()):
    if canary_samples < MIN_SAMPLES:
        return "continue"
    if canary_err <= ABS_THRESHOLD:
        return "continue"
    if burn_rate(canary_err, tolerance) > BURN_ROLLBACK:
        return "rollback"
    return "hold"
'''


def _t34(wd, burn=3.5, hold=2, min_check=None, hold_return='return "hold"'):
    src = (_T34_TMPL
           .replace("__BURN__", str(burn))
           .replace("__HOLD__", str(hold))
           .replace("__MIN_CHECK__",
                    min_check if min_check is not None
                    else 'if canary_samples < MIN_SAMPLES:\n        return "continue"')
           .replace("__HOLD_RETURN__", hold_return))
    (wd / "canary" / "gate.py").write_text(src, encoding="utf-8")


def _wrong_t34_absolute(wd):
    """t34：看 canary 错误率绝对值（>1% 就动）—— 最常见写法。"""
    (wd / "canary" / "gate.py").write_text(
        _T34_ABS_TMPL.replace("__ABS__", "0.01"), encoding="utf-8")


def _wrong_t34_no_min_samples(wd):
    """t34：不看样本量，300 个样本也敢判。"""
    _t34(wd, min_check="pass")


def _wrong_t34_rollback_at_once(wd):
    """t34：一劣化立刻回滚，不留 hold 档。"""
    _t34(wd, hold_return='return "rollback"')


def _wrong_t34_hold_three(wd):
    """t34：连续 3 次才升级回滚（更"稳妥"的直觉）。"""
    _t34(wd, hold=3)


def _wrong_t34_burn_15(wd):
    """t34：燃烧率阈值 1.5 —— 太敏感，正常波动就被回滚。"""
    _t34(wd, burn=1.5)


# ---- t35 埋点校验：五个"更严格/更温和"的写法 ----

_T35_TMPL = '''"""埋点事件校验与清洗。"""
from .schema import ALLOWED_FIELDS

REQUIRED_FIELDS = __REQ__
DEDUP_WINDOW_SECONDS = __WINDOW__


def _bad(value):
    return isinstance(value, str) and any(ord(ch) > 127 for ch in value)


def normalize(event):
    out = {}
    for key, value in event.items():
        if key not in ALLOWED_FIELDS:
            __UNKNOWN__
        if _bad(value):
            __CHINESE__
        out[key] = value
    for key in REQUIRED_FIELDS:
        if key not in out:
            return None
    return out


class Dedup:
    def __init__(self, clock):
        self._clock = clock
        self._last = {}

    def accept(self, event):
        key = (event.get("event_name"), event.get("user_id"))
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < DEDUP_WINDOW_SECONDS:
            return False
        self._last[key] = now
        return True
'''


def _t35(wd, req='("event_name", "user_id", "ts")', window=5.0,
         unknown="continue", chinese="return None"):
    src = (_T35_TMPL
           .replace("__REQ__", req)
           .replace("__WINDOW__", str(window))
           .replace("__UNKNOWN__", unknown)
           .replace("__CHINESE__", chinese))
    (wd / "tracking" / "validator.py").write_text(src, encoding="utf-8")


def _wrong_t35_clean_instead_of_drop(wd):
    """t35：含中文只清洗掉那个字段，事件照报 —— 「别丢数据」的直觉。"""
    _t35(wd, chinese="continue")


def _wrong_t35_reject_unknown(wd):
    """t35：出现未知字段就整条拒收 —— 「严格校验」的直觉。"""
    _t35(wd, unknown="return None")


def _wrong_t35_pass_unknown(wd):
    """t35：未知字段原样透传 —— 「别丢信息」的直觉。"""
    _t35(wd, unknown="pass")


def _wrong_t35_required_five(wd):
    """t35：把 order_id / amount 也当必填 —— 「数据越全越好」的直觉。"""
    _t35(wd, req='("event_name", "user_id", "ts", "order_id", "amount")')


def _wrong_t35_window_60(wd):
    """t35：去重窗口 60 秒 —— 「重试间隔通常一分钟」的直觉。"""
    _t35(wd, window=60.0)


# ---- t36 限流：四个"和直觉一致但和规定相反"的写法 ----

_T36_TMPL = '''"""接口限流器。"""
from .policy import WINDOW_SECONDS

QUOTA = __QUOTA__
KEY = __KEY__


class Limiter:
    def __init__(self, clock):
        self._clock = clock
        self._hits = {}
        self._seen = set()

    def check(self, tenant_id, user_id, internal=False, request_id=None):
        __INTERNAL__
        __IDEMPOTENT__
        now = self._clock()
        key = KEY(tenant_id, user_id)
        hits = self._hits.setdefault(key, [])
        cutoff = now - WINDOW_SECONDS
        hits[:] = [t for t in hits if t > cutoff]
        if len(hits) >= QUOTA:
            wait = WINDOW_SECONDS - (now - hits[0])
            return (False, max(wait, 0.001))
        hits.append(now)
        return (True, 0)
'''

_KEY_TENANT = "lambda tenant_id, user_id: tenant_id"
_KEY_USER = "lambda tenant_id, user_id: user_id"
_KEY_GLOBAL = "lambda tenant_id, user_id: 'global'"


_IDEM_OK = """if request_id is not None:
            if request_id in self._seen:
                return (True, 0)
            self._seen.add(request_id)"""

_IDEM_OFF = "pass"


def _t36(wd, quota=900, key=_KEY_TENANT,
         internal="if internal:\n            return (True, 0)",
         idempotent=_IDEM_OK):
    src = (_T36_TMPL
           .replace("__QUOTA__", str(quota))
           .replace("__KEY__", key)
           .replace("__INTERNAL__", internal)
           .replace("__IDEMPOTENT__", idempotent))
    (wd / "ratelimit" / "limiter.py").write_text(src, encoding="utf-8")


def _wrong_t36_by_user(wd):
    """t36：按用户限流 —— 「每人一份配额」的直觉。"""
    _t36(wd, key=_KEY_USER)


def _wrong_t36_internal_counts(wd):
    """t36：内部调用也占配额 —— 「都要算」的直觉。"""
    _t36(wd, internal="pass")


def _wrong_t36_quota_1000(wd):
    """t36：配额给 1000 —— 最常见的那种整数。"""
    _t36(wd, quota=1000)


def _wrong_t36_global(wd):
    """t36：不分租户，全局一个计数器 —— 「网关总限额」的直觉。"""
    _t36(wd, key=_KEY_GLOBAL)


def _wrong_t36_retry_counts(wd):
    """t36：重试重复计费 —— 「每次请求都算一次」的直觉。"""
    _t36(wd, idempotent=_IDEM_OFF)


# ---- t37 回刷审批：5 步判断链，每个变异破坏其中一步 ----

_T37_TMPL = '''"""回刷审批器。"""


def plan(request, state):
    __SHORTCUT__
    __REPEAT__
    __WINDOW__
    __SUBSCRIBER__
    return "run"
'''

# 第 0 步：小表直通（命中后不再看后面任何条件）
_T37_SHORTCUT_OK = '''if request["row_count"] < 1_000_000 and not request["is_core"]:
        return "run"'''
_T37_SHORTCUT_NONE = "pass"
_T37_SHORTCUT_ROW_ONLY = '''if request["row_count"] < 1_000_000:
        return "run"'''

# 第 1 步：当日已回刷 → 拒绝
_T37_REPEAT_REJECT = '''if state["same_day_backfills"] >= 1:
        return "reject"'''
_T37_REPEAT_QUEUE = '''if state["same_day_backfills"] >= 1:
        return "queue"'''

# 第 2 步：窗口 > 30 天 → 离线队列
_T37_WINDOW_GT = '''if request["window_days"] > 30:
        return "queue"'''
_T37_WINDOW_GE = '''if request["window_days"] >= 30:
        return "queue"'''
_T37_WINDOW_7 = '''if request["window_days"] > 7:
        return "queue"'''

# 第 3 步：有下游消费 → 等次日
_T37_SUB_WAIT = '''if state["active_subscribers"] > 0:
        return "wait"'''
_T37_SUB_REJECT = '''if state["active_subscribers"] > 0:
        return "reject"'''


def _t37(wd, shortcut=_T37_SHORTCUT_OK, repeat=_T37_REPEAT_REJECT,
         window=_T37_WINDOW_GT, sub=_T37_SUB_WAIT):
    src = (_T37_TMPL
           .replace("__SHORTCUT__", shortcut)
           .replace("__REPEAT__", repeat)
           .replace("__WINDOW__", window)
           .replace("__SUBSCRIBER__", sub))
    (wd / "backfill" / "planner.py").write_text(src, encoding="utf-8")


def _wrong_t37_no_shortcut(wd):
    """t37：小表也走完整审批 —— 「小表也得守规矩」的直觉。"""
    _t37(wd, shortcut=_T37_SHORTCUT_NONE)


def _wrong_t37_row_only(wd):
    """t37：只看行数不看是否核心表 —— 漏掉半个条件。"""
    _t37(wd, shortcut=_T37_SHORTCUT_ROW_ONLY)


def _wrong_t37_repeat_queue(wd):
    """t37：当日重复改成排队 —— 「既然能跑，排晚点就行」。"""
    _t37(wd, repeat=_T37_REPEAT_QUEUE)


def _wrong_t37_window_ge(wd):
    """t37：30 天也排队 —— 边界写 >= 的经典差一错。"""
    _t37(wd, window=_T37_WINDOW_GE)


def _wrong_t37_window_7(wd):
    """t37：阈值猜成 7 天 —— 最常见的「一周前」直觉。"""
    _t37(wd, window=_T37_WINDOW_7)


def _wrong_t37_sub_reject(wd):
    """t37：有下游在消费就拒绝 —— 比 wait 更保守的直觉。"""
    _t37(wd, sub=_T37_SUB_REJECT)


def _wrong_t37_order(wd):
    """t37：先判"等次日"再判"排队" —— 顺序反了，同命中时给出 wait。"""
    (wd / "backfill" / "planner.py").write_text(
        '''"""回刷审批器（顺序错）。"""


def plan(request, state):
    if request["row_count"] < 1_000_000 and not request["is_core"]:
        return "run"
    if state["same_day_backfills"] >= 1:
        return "reject"
    if state["active_subscribers"] > 0:
        return "wait"
    if request["window_days"] > 30:
        return "queue"
    return "run"
''',
        encoding="utf-8",
    )


# ---- t38 导出审批：5 步判断链，核心是"敏感字段按去向分开处理" ----

_T38_TMPL = '''"""导出审批器。"""

LARGE = __LARGE__


def decide(request):
    same_dept = request["requester_dept"] == request["source_dept"]
    internal = request["target"] == "internal"
    __SHORTCUT__
    __INTERNAL__
    __EXTERNAL__
    __LARGE_CHECK__
    return "allow"
'''

_T38_SHORTCUT_OK = '''if same_dept and internal:
        return "allow"'''
_T38_SHORTCUT_DEPT_ONLY = '''if same_dept:
        return "allow"'''
_T38_SHORTCUT_NONE = "pass"

_T38_INTERNAL_DENY = '''if internal and request["has_sensitive"]:
        return "deny"'''
_T38_INTERNAL_MASK = '''if internal and request["has_sensitive"]:
        return "mask"'''

_T38_EXTERNAL_MASK = '''if not internal and request["has_sensitive"]:
        return "mask"'''
_T38_EXTERNAL_DENY = '''if not internal and request["has_sensitive"]:
        return "deny"'''
_T38_EXTERNAL_ALWAYS_DENY = '''if not internal:
        return "deny"'''

_T38_LARGE_REVIEW = '''if request["row_count"] > LARGE:
        return "review"'''
_T38_LARGE_DENY = '''if request["row_count"] > LARGE:
        return "deny"'''


def _t38(wd, large=500_000, shortcut=_T38_SHORTCUT_OK,
         internal=_T38_INTERNAL_DENY, external=_T38_EXTERNAL_MASK,
         large_check=_T38_LARGE_REVIEW):
    src = (_T38_TMPL
           .replace("__LARGE__", str(large))
           .replace("__SHORTCUT__", shortcut)
           .replace("__INTERNAL__", internal)
           .replace("__EXTERNAL__", external)
           .replace("__LARGE_CHECK__", large_check))
    (wd / "export" / "decider.py").write_text(src, encoding="utf-8")


def _wrong_t38_no_shortcut(wd):
    """t38：本部门也走完整审批 —— 「本部门也要守规矩」的直觉。"""
    _t38(wd, shortcut=_T38_SHORTCUT_NONE)


def _wrong_t38_dept_only(wd):
    """t38：只看部门就直通 —— 漏掉「且内部」（本部门往外发数据也放行）。"""
    _t38(wd, shortcut=_T38_SHORTCUT_DEPT_ONLY)


def _wrong_t38_sensitive_unified_mask(wd):
    """t38：把敏感字段统一脱敏 —— 「敏感就脱敏」的直觉，内部流转也该拒。"""
    _t38(wd, internal=_T38_INTERNAL_MASK)


def _wrong_t38_sensitive_unified_deny(wd):
    """t38：把敏感字段统一拒绝 —— 「敏感就别给」的直觉，对外脱敏就能发。"""
    _t38(wd, external=_T38_EXTERNAL_DENY)


def _wrong_t38_external_always_deny(wd):
    """t38：外部一律拒 —— 最像安全常识，但对外发聚合数据本来就要脱敏后放行。"""
    _t38(wd, external=_T38_EXTERNAL_ALWAYS_DENY)


def _wrong_t38_large_deny(wd):
    """t38：行数超限直接拒绝 —— 「太大了不许导」的直觉。"""
    _t38(wd, large_check=_T38_LARGE_DENY)


def _wrong_t38_large_1m(wd):
    """t38：阈值猜成 100 万行 —— 整数直觉，实际是 50 万。"""
    _t38(wd, large=1_000_000)


def _wrong_t38_order(wd):
    """t38：先判行数再判脱敏 —— 顺序反了，60 万行的对外导出会先撞 review。"""
    (wd / "export" / "decider.py").write_text(
        '''"""导出审批器（顺序错）。"""


def decide(request):
    same_dept = request["requester_dept"] == request["source_dept"]
    internal = request["target"] == "internal"
    if same_dept and internal:
        return "allow"
    if request["row_count"] > 500_000:
        return "review"
    if internal and request["has_sensitive"]:
        return "deny"
    if not internal and request["has_sensitive"]:
        return "mask"
    return "allow"
''',
        encoding="utf-8",
    )


# ---- t39 告警静默：与 export 同构的五步链，内容全不同 ----

_T39_TMPL = '''"""静默审批器。"""

SHORT = __SHORT__
LONG = __LONG__


def approve(rule, ctx):
    __SHORTCUT__
    __GLOBAL__
    __PEAK__
    __LONG_CHECK__
    return "apply"
'''

_T39_SHORTCUT_OK = '''if rule["scope"] == "rule" and rule["minutes"] <= SHORT:
        return "apply"'''
_T39_SHORTCUT_NONE = "pass"
_T39_SHORTCUT_NO_SCOPE = '''if rule["minutes"] <= SHORT:
        return "apply"'''
_T39_SHORTCUT_STRICT = '''if rule["scope"] == "rule" and rule["minutes"] < SHORT:
        return "apply"'''

_T39_GLOBAL_REJECT = '''if rule["scope"] == "global":
        return "reject"'''
_T39_GLOBAL_REVIEW = '''if rule["scope"] == "global":
        return "review"'''

_T39_PEAK_REVIEW = '''if rule["covers_peak"]:
        return "review"'''
_T39_PEAK_REJECT = '''if rule["covers_peak"]:
        return "reject"'''

_T39_LONG_SCHEDULE = '''if rule["minutes"] > LONG:
        return "schedule"'''


def _t39(wd, short=30, long_=1440, shortcut=_T39_SHORTCUT_OK,
         global_=_T39_GLOBAL_REJECT, peak=_T39_PEAK_REVIEW,
         long_check=_T39_LONG_SCHEDULE):
    src = (_T39_TMPL
           .replace("__SHORT__", str(short))
           .replace("__LONG__", str(long_))
           .replace("__SHORTCUT__", shortcut)
           .replace("__GLOBAL__", global_)
           .replace("__PEAK__", peak)
           .replace("__LONG_CHECK__", long_check))
    (wd / "alertmute" / "approver.py").write_text(src, encoding="utf-8")


def _wrong_t39_no_shortcut(wd):
    """t39：静默都要走审批 —— 「静默总得有人批」的直觉。"""
    _t39(wd, shortcut=_T39_SHORTCUT_NONE)


def _wrong_t39_shortcut_no_scope(wd):
    """t39：短路只看时长不看范围 —— 漏掉复合条件的另一半。"""
    _t39(wd, shortcut=_T39_SHORTCUT_NO_SCOPE)


def _wrong_t39_shortcut_strict(wd):
    """t39：短路写成 `< 30` —— 边界差一，正好 30 分钟的请求被卡。"""
    _t39(wd, shortcut=_T39_SHORTCUT_STRICT)


def _wrong_t39_global_review(wd):
    """t39：全局静默转人工 —— 比 reject 温和的直觉。"""
    _t39(wd, global_=_T39_GLOBAL_REVIEW)


def _wrong_t39_peak_reject(wd):
    """t39：覆盖高峰直接拒绝 —— 「高峰绝不能静默」的直觉。"""
    _t39(wd, peak=_T39_PEAK_REJECT)


def _wrong_t39_long_12h(wd):
    """t39：排期阈值猜成 12 小时 —— 半个工作日的直觉，实际是 24 小时。"""
    _t39(wd, long_=720)


def _wrong_t39_order(wd):
    """t39：先判高峰再判全局 —— 顺序反了，全局+高峰会返回 review。"""
    (wd / "alertmute" / "approver.py").write_text(
        '''"""静默审批器（顺序错）。"""


def approve(rule, ctx):
    if rule["scope"] == "rule" and rule["minutes"] <= 30:
        return "apply"
    if rule["covers_peak"]:
        return "review"
    if rule["scope"] == "global":
        return "reject"
    if rule["minutes"] > 1440:
        return "schedule"
    return "apply"
''',
        encoding="utf-8",
    )


MUTANTS = {
    "t17": [("只改部分文件", _wrong_rename_partial)],
    "t18": [("直接改测试文件", _wrong_edits_test)],
    "t19": [("漏算一个 region", _wrong_region_typo)],
    "t20": [("全局替换但缺 func", _wrong_global_replace)],
    "t21": [("一元负号处理不全", _wrong_partial_unary)],
    "t22": [("原地修改入参", _wrong_copy_not_new)],
    "t23": [("履约不回滚", _wrong_no_rollback)],
    "t24": [("原地修改入参", _wrong_mutates_input)],
    "t27": [
        ("TTL 60 秒（通用做法，会打满配额）", _wrong_ttl_60),
        ("所有异常都重试（遇 429 会加重封禁）", _wrong_retry_everything),
        ("ts 按本地时区解析（东八区偏成次日）", _wrong_ts_local),
        ("ts 当毫秒（落到 1970）", _wrong_ts_ms),
    ],
    "t28": [
        ("prefetch=10（最合理的猜测值，契约是 7）", _wrong_prefetch_10),
        ("ack 只留 1.5 倍余量（通用做法，契约要 2.5）", _wrong_ack_15x),
        ("失败 requeue=True（重试，实际死循环）", _wrong_requeue_true),
        ("不分成败一律 ack（失败消息静默丢失）", _wrong_ack_all),
    ],
    "t29": [
        ("不带 trace_id/tenant_id（最常见的写法）", _wrong_no_trace),
        ("把整个 user 打进日志（泄露 PII）", _wrong_log_pii),
        ("异常用 log.error(str(e))（丢掉堆栈）", _wrong_error_str),
    ],
    "t31": [
        ("重试 5 次（教科书值，约定是 3 次）", _wrong_t31_five_attempts),
        ("退避封顶 30 秒（看起来更稳健）", _wrong_t31_cap_30),
        ("422 也重试（坏数据处理 3 遍）", _wrong_t31_retry_all),
        ("固定 1 秒退避", _wrong_t31_delay_1s),
        ("忽略 Retry-After", _wrong_t31_ignore_ra),
    ],
    "t33": [
        ("P0 给 30 秒（最重要所以等最久）", _wrong_t33_p0_longest),
        ("80% 才降级（最常见阈值）", _wrong_t33_ratio_80),
        ("50% 就降级（过度保守）", _wrong_t33_ratio_50),
        ("P0 也重试（重要的更该重试）", _wrong_t33_p0_retry),
        ("未知等级给 P2（不知道就宽松）", _wrong_t33_default_p2),
    ],
    "t34": [
        ("看 canary 绝对值（>1% 就动）", _wrong_t34_absolute),
        ("不看样本量（300 也敢判）", _wrong_t34_no_min_samples),
        ("一劣化就回滚（没有 hold 档）", _wrong_t34_rollback_at_once),
        ("连续 3 次才回滚（更稳妥的直觉）", _wrong_t34_hold_three),
        ("燃烧率阈值 1.5（太敏感）", _wrong_t34_burn_15),
    ],
    "t35": [
        ("含中文只清洗不清整条（别丢数据）", _wrong_t35_clean_instead_of_drop),
        ("未知字段整条拒收（严格校验）", _wrong_t35_reject_unknown),
        ("未知字段原样透传（别丢信息）", _wrong_t35_pass_unknown),
        ("把 order_id/amount 也当必填（数据越全越好）", _wrong_t35_required_five),
        ("去重窗口 60 秒（常见的重试间隔）", _wrong_t35_window_60),
    ],
    "t36": [
        ("按用户限流（每人一份配额）", _wrong_t36_by_user),
        ("内部调用也占配额（都要算）", _wrong_t36_internal_counts),
        ("配额给 1000（最常见的整数）", _wrong_t36_quota_1000),
        ("全局一个计数器（网关总限额）", _wrong_t36_global),
        ("重试重复计费（每次请求都算）", _wrong_t36_retry_counts),
    ],
    "t37": [
        ("小表也走完整审批（小表也得守规矩）", _wrong_t37_no_shortcut),
        ("只看行数不看核心表（漏半个条件）", _wrong_t37_row_only),
        ("当日重复改成排队（晚点跑就行）", _wrong_t37_repeat_queue),
        ("30 天也排队（边界差一）", _wrong_t37_window_ge),
        ("阈值猜成 7 天（一周的直觉）", _wrong_t37_window_7),
        ("有下游消费就拒绝（比 wait 更保守）", _wrong_t37_sub_reject),
        ("先等次日后排队（判定顺序反了）", _wrong_t37_order),
    ],
    "t38": [
        ("本部门也走完整审批（本部门也要守规矩）", _wrong_t38_no_shortcut),
        ("只看部门就直通（漏掉「且内部」）", _wrong_t38_dept_only),
        ("敏感字段统一脱敏（内部流转也该拒）", _wrong_t38_sensitive_unified_mask),
        ("敏感字段统一拒绝（对外脱敏就能发）", _wrong_t38_sensitive_unified_deny),
        ("外部一律拒（最像安全常识）", _wrong_t38_external_always_deny),
        ("行数超限直接拒绝（太大了不许导）", _wrong_t38_large_deny),
        ("阈值猜成 100 万行（整数直觉）", _wrong_t38_large_1m),
        ("先判行数再判脱敏（顺序反了）", _wrong_t38_order),
    ],
    "t39": [
        ("静默都要走审批（静默总得有人批）", _wrong_t39_no_shortcut),
        ("短路只看时长不看范围（漏复合条件）", _wrong_t39_shortcut_no_scope),
        ("短路写成 <30（边界差一）", _wrong_t39_shortcut_strict),
        ("全局静默转人工（比 reject 温和）", _wrong_t39_global_review),
        ("覆盖高峰直接拒绝（高峰绝不能静默）", _wrong_t39_peak_reject),
        ("排期阈值猜 12 小时（半个工作日）", _wrong_t39_long_12h),
        ("先判高峰再判全局（顺序反了）", _wrong_t39_order),
    ],
}


def main() -> None:
    ids = sys.argv[1:] or sorted(MUTANTS)
    print(f"▶ verify 严格性对照：{len(ids)} 个任务\n")
    all_ok = True

    for tid in ids:
        task = next((t for t in TASKS if t["id"] == tid), None)
        if task is None:
            print(f"  {tid}: ❌ 任务不存在")
            all_ok = False
            continue
        for name, inject in MUTANTS.get(tid, []):
            wd = pathlib.Path(tempfile.mkdtemp(prefix=f"mut_{tid}_"))
            # 🔴 关键：把「准备阶段」和「判定阶段」的异常分开。
            #
            # 原实现是一个大 try 包住全部，`except Exception: ✅ 被拦下`。
            # 后果：注入函数自己写崩了（如模板 format 吃了 {pair}）也会报 ✅，
            # 看起来"verify 很严"，实际上**变异根本没注入进去**。
            # 2026-10-27 加 t27 时就踩了这个：4 个变异全报 ✅（KeyError），
            # 而真实原因是我的模板转义写错，一行验证都没执行。
            # 这与「调用了 ≠ 执行了」是同一类错误，只是发生在测试工具自己身上。
            try:
                if task.get("setup"):
                    task["setup"](wd)
                inject(wd)
            except Exception as e:  # noqa: BLE001
                print(f"  {tid}: ❌ 变异「{name}」注入失败（{type(e).__name__}: {e}）"
                      f" —— 变体没写对，这次对照无效")
                all_ok = False
                continue

            try:
                passed, detail = task["verify"](wd)
            except Exception as e:  # noqa: BLE001
                # verify 内部抛异常也算「拦下」，但要把异常打出来供人工判断，
                # 避免"verify 自己坏了"被误读成"verify 很严格"。
                print(f"  {tid}: ⚠️  错误解「{name}」由 verify 异常拦下"
                      f"（{type(e).__name__}: {e}）")
                continue

            if passed:
                print(f"  {tid}: ❌ 错误解「{name}」被放过了 —— verify 有漏洞！")
                all_ok = False
            else:
                print(f"  {tid}: ✅ 错误解「{name}」被拦下")

    print("\n" + ("✅ verify 全部能拦住错误解" if all_ok else "❌ 存在放水的 verify，需修"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
