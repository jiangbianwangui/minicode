"""评测任务自检：证明每个任务「初始会失败」且「存在正确解」。

为什么必须有它：加"baseline 会失败的任务"稍不注意就会变成造假——
要么任务根本不可解（verify 永远 False），要么标准含糊。
这个脚本对每个任务做两件事：
  1) 只跑 setup 后 verify 必须失败  → 证明任务有区分度（不是送分）
  2) 套用参考解后 verify 必须通过  → 证明任务可解（不是死题）

用法：
    python eval/selftest_tasks.py              # 自检全部有参考解的任务
    python eval/selftest_tasks.py t17 t19      # 只自检指定任务
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from tasks import TASKS  # noqa: E402

# ---------------- 参考解（人工写的正确实现，仅用于证明任务可解） ----------------


def solve_t17(wd):
    for rel in ["order/models.py", "order/service.py", "order/report.py", "tests/test_order.py"]:
        p = wd / rel
        p.write_text(p.read_text(encoding="utf-8").replace("calc_total", "compute_total"),
                     encoding="utf-8")


def solve_t18(wd):
    src = (wd / "stack.py").read_text(encoding="utf-8")
    src = src.replace("return self._items.pop(0)", "return self._items.pop()")
    src = src.replace("return len(self._items) > 0", "return len(self._items) == 0")
    (wd / "stack.py").write_text(src, encoding="utf-8")


def solve_t19(wd):
    seen, agg = set(), {}
    rows = csv.DictReader((wd / "sales.csv").read_text(encoding="utf-8").splitlines())
    for r in rows:
        key = (r["region"], r["amount"], r["qty"])
        if key in seen:
            continue
        seen.add(key)
        try:
            amount = float(r["amount"])
        except (TypeError, ValueError):
            continue
        qty = int(r["qty"])
        d = agg.setdefault(r["region"], {"total": 0.0, "count": 0})
        d["total"] += amount * qty
        d["count"] += 1
    (wd / "report.json").write_text(json.dumps(agg), encoding="utf-8")
    (wd / "test_report.py").write_text(
        "import json\n\n\n"
        "def test_report():\n"
        "    got = json.load(open('report.json'))\n"
        "    assert abs(got['east']['total'] - 35) < 1e-6\n"
        "    assert got['west']['count'] == 1\n",
        encoding="utf-8",
    )


def solve_t20(wd):
    src = (wd / "legacy.py").read_text(encoding="utf-8")
    out, cur = [], None
    for line in src.splitlines():
        m = re.match(r"def (\w+)\(", line)
        if m:
            cur = m.group(1)
        if "log_it(" in line:
            indent = line[: len(line) - len(line.lstrip())]
            msg = re.search(r'log_it\("(.*?)"\)', line).group(1)
            line = f'{indent}logger.info("{msg}", func="{cur}")'
        out.append(line)
    new = "\n".join(out)
    new = new.replace(
        '"""遗留模块：需要统一改造日志调用。"""',
        '"""遗留模块：需要统一改造日志调用。"""\n\nimport logging\n\nlogger = logging.getLogger(__name__)',
        1,
    )
    (wd / "legacy.py").write_text(new, encoding="utf-8")


_EXPR_SRC = '''import re

_TOKEN = re.compile(r"\\d+\\.?\\d*|\\.\\d+|[+\\-*/()]")


def eval_expr(s):
    """四则运算求值：支持优先级、括号、一元正负号、空白、浮点除法。"""
    tokens = _TOKEN.findall(s)
    pos = 0

    def factor():
        nonlocal pos
        if tokens[pos] == "-":
            pos += 1
            return -factor()
        if tokens[pos] == "+":
            pos += 1
            return factor()
        if tokens[pos] == "(":
            pos += 1
            v = expr()
            pos += 1  # 跳过 )
            return v
        v = float(tokens[pos])
        pos += 1
        return v

    def term():
        nonlocal pos
        v = factor()
        while pos < len(tokens) and tokens[pos] in "*/":
            op = tokens[pos]
            pos += 1
            r = factor()
            v = v * r if op == "*" else v / r
        return v

    def expr():
        nonlocal pos
        v = term()
        while pos < len(tokens) and tokens[pos] in "+-":
            op = tokens[pos]
            pos += 1
            r = term()
            v = v + r if op == "+" else v - r
        return v

    return expr()
'''


def solve_t21(wd):
    (wd / "expr_eval.py").write_text(_EXPR_SRC, encoding="utf-8")
    (wd / "test_expr_eval.py").write_text(
        "from expr_eval import eval_expr\n\n\n"
        "def test_precedence():\n"
        "    assert eval_expr('1+2*3') == 7\n"
        "    assert eval_expr('(1+2)*3') == 9\n\n\n"
        "def test_unary_and_float():\n"
        "    assert eval_expr('-5+3') == -2\n"
        "    assert eval_expr('10/4') == 2.5\n",
        encoding="utf-8",
    )


def solve_t22(wd):
    p = wd / "cart" / "cart.py"
    src = p.read_text(encoding="utf-8")
    src += '''

def apply_discount(items, rate):
    """按 rate 打折，返回新列表，不修改入参。"""
    if not 0 <= rate <= 1:
        raise ValueError("rate must be in [0, 1]")
    return [
        {**i, "price": round(i["price"] * (1 - rate), 2)}
        for i in items
    ]
'''
    p.write_text(src, encoding="utf-8")


def solve_t23(wd):
    pkg = wd / "inventory"

    # discount.py：从 test_discount.py 反推规则
    (pkg / "discount.py").write_text(
        '''"""折扣计算。"""


def apply_discount(amount, rate):
    """满 200 减 20，再按 rate 打折，四舍五入到分。"""
    if not 0 <= rate <= 1:
        raise ValueError("rate must be in [0, 1]")
    total = amount
    if total >= 200:
        total -= 20
    total = round(total * (1 - rate) + 1e-9, 2)
    return total
''',
        encoding="utf-8",
    )

    # fulfill.py：带回滚的实现
    (pkg / "fulfill.py").write_text(
        '''"""订单履约。"""
from .exceptions import InsufficientStock


class Fulfiller:
    """把一个 Order 的每一行转为库存预留。"""

    def __init__(self, store):
        self.store = store

    def reserve_order(self, order):
        done = []
        try:
            for line in order.lines:
                self.store.reserve(line.sku, line.qty)
                done.append(line)
        except InsufficientStock:
            # 回滚已预留的行，保证无脏状态
            for line in done:
                self.store.release(line.sku, line.qty)
            raise
        return done
''',
        encoding="utf-8",
    )


def solve_t24(wd):
    pkg = wd / "meeting"

    (pkg / "slots.py").write_text(
        '''"""时间槽操作。"""
from .models import Slot


def merge_slots(slots):
    """合并重叠或相邻的时间槽（左闭右开），不修改入参。"""
    items = [Slot(s.start, s.end) for s in slots]
    items = [s for s in items if s.end > s.start]  # 丢弃零长度
    if not items:
        return []
    items.sort()
    out = [Slot(items[0].start, items[0].end)]
    for s in items[1:]:
        last = out[-1]
        if s.start <= last.end:  # 相邻(start == end)也要合并
            out[-1] = Slot(last.start, max(last.end, s.end))
        else:
            out.append(Slot(s.start, s.end))
    return out
''',
        encoding="utf-8",
    )

    (pkg / "finder.py").write_text(
        '''"""会议室查找。"""
from .models import Slot
from .slots import merge_slots


def _intersect(a_slots, b_slots):
    out = []
    for a in a_slots:
        for b in b_slots:
            s, e = max(a.start, b.start), min(a.end, b.end)
            if e > s:
                out.append(Slot(s, e))
    return merge_slots(out)


def find_room_slots(book, slots, duration, capacity=1):
    """在容量达标的会议室中找出长度 >= duration 的空闲时段。"""
    window = merge_slots(slots)
    out = []
    for name in book.names():
        room = book.get(name)
        if room.capacity < capacity:
            continue
        for seg in _intersect(room.free_slots(), window):
            if seg.end - seg.start >= duration:
                out.append((name, seg))
    out.sort(key=lambda x: (x[0], x[1].start))
    return out
''',
        encoding="utf-8",
    )


def solve_t25(wd):
    """给 6 个类的 __init__ 加 self._audit，_normalize 首行加审计注释。"""
    p = wd / "repos.py"
    src = p.read_text(encoding="utf-8")

    # 1) 只在这 6 个仓库类里补 self._audit（_Base 不算）
    for cls in ["UserRepo", "OrderRepo", "ProductRepo",
                "InvoiceRepo", "ShipmentRepo", "PaymentRepo"]:
        cur = f"class {cls}:\n"
        i = src.find(cur)
        if i < 0:
            continue
        j = src.find("    def __init__(self, conn):\n        self.conn = conn\n", i)
        if j < 0:
            continue
        head = "    def __init__(self, conn):\n        self.conn = conn\n"
        src = src[: j + len(head)] + "        self._audit = []\n" + src[j + len(head):]

    # 2) 每个 _normalize 的 docstring/注释之后插审计标记
    blocks = src.split("    def _normalize(self, row):")
    rebuilt = blocks[0]
    for blk in blocks[1:]:
        lines = blk.splitlines()
        insert_at = 0
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if s.startswith("#"):
                insert_at = i + 1
                continue
            insert_at = i
            break
        lines.insert(insert_at, "        # AUDIT-ADDED: 已审计")
        rebuilt += "    def _normalize(self, row):" + "\n".join(lines)

    p.write_text(rebuilt, encoding="utf-8")


def solve_t26(wd):
    """只改三处真 bug；tax 的 floor 与 rating 的 qty<=0 约定**原样保留**。"""
    pkg = wd / "billing"

    # B1 阶梯计价：只有超出 100 的部分降档
    p = pkg / "rating.py"
    s = p.read_text(encoding="utf-8")
    s = s.replace(
        "    rate = 0.50 if qty <= 100 else 0.30\n"
        "    return int(qty * rate * 100)",
        "    if qty <= 100:\n"
        "        return qty * 50\n"
        "    return 5000 + (qty - 100) * 30",
    )
    p.write_text(s, encoding="utf-8")

    # B2 折算：用当月实际天数
    p = pkg / "proration.py"
    s = p.read_text(encoding="utf-8")
    s = s.replace(
        '"""按月折算（月中开通、月中退订）。"""',
        '"""按月折算（月中开通、月中退订）。"""\nimport calendar',
        1,
    )
    s = s.replace(
        "    remaining = BILLING_DAYS - day + 1\n"
        "    return int(monthly_cents * remaining / BILLING_DAYS)",
        "    total_days = calendar.monthrange(year, month)[1]\n"
        "    remaining = total_days - day + 1\n"
        "    return int(monthly_cents * remaining / total_days)",
    )
    p.write_text(s, encoding="utf-8")

    # B3 导出：分 → 元
    p = pkg / "export.py"
    s = p.read_text(encoding="utf-8")
    for field in ["charge_cents", "tax_cents", "total_cents"]:
        s = s.replace(f'f"{{inv.{field}:.2f}}"', f'f"{{inv.{field} / 100:.2f}}"')
    p.write_text(s, encoding="utf-8")


def solve_t27(wd):
    """按 skills/rates-api/SKILL.md 的约定写：TTL 600、只重试 5xx、ts 按秒 UTC。"""
    (wd / "rates" / "rates.py").write_text(
        '''"""汇率读取（业务层）。"""
import json
import time
from datetime import datetime, timezone

from .client import RateAPIError, _http_get

API_URL = "https://api.example-rates.com/v1/latest?pair={pair}"

# 对端限频 60 次/小时 → TTL 不得低于 600 秒（见 skills/rates-api）
CACHE_TTL = 600
MAX_RETRIES = 2

_cache: dict[str, tuple[float, dict]] = {}


def _fetch_with_retry(url):
    """只重试 5xx 与网络错误；429 直接抛（重试会延长封禁）。"""
    last = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return _http_get(url)
        except RateAPIError as e:
            if e.status == 429:
                raise
            if e.status < 500:
                raise
            last = e
        except OSError as e:            # 网络超时/连接失败
            last = e
        if attempt < MAX_RETRIES:
            time.sleep(0.5 * (2 ** attempt))
    raise last


def fetch_rate(pair: str) -> dict:
    """取一个货币对的汇率。

    返回 {"pair": 货币对, "rate": 汇率(float), "ts": 对端时间戳(int), "date": 日期字符串}
    """
    hit = _cache.get(pair)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]

    raw = _fetch_with_retry(API_URL.format(pair=pair))
    data = json.loads(raw)
    ts = int(data["ts"])
    out = {
        "pair": pair,
        "rate": float(data["rate"]),
        "ts": ts,
        # ts 是秒级 UTC 时间戳，必须带 tz 解析（否则东八区会偏成次日）
        "date": datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
    }
    _cache[pair] = (now, out)
    return out
''',
        encoding="utf-8",
    )


def solve_t28(wd):
    """按 broker 契约写：prefetch<=10、ack 超时留余量、失败进死信。"""
    (wd / "worker" / "consumer.py").write_text(
        '''"""消费循环。"""
from .client import BrokerError, QueueClient  # noqa: F401

# broker 契约（见 skills/messaging-contract）：
#   每个连接只有 7 个槽位，prefetch 超了直接拒连；
#   ack 余量必须 >= 处理耗时 × 2.5，否则抖动时会重复投递。
PREFETCH = 5


def consume(client: QueueClient, handler, max_messages: int = 100) -> dict:
    """从队列消费消息并交给 handler 处理。

    返回 {"handled": 成功条数, "failed": 失败条数}。
    """
    client.set_prefetch(PREFETCH)
    # 留 3 倍余量（契约要求 >= 2.5）：抖动时不会被提前判定超时并重新投递
    client.set_ack_timeout(int(client.max_processing_seconds * 3))

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
                # 进死信，不能 requeue：requeue=True 会被立刻重投，形成死循环
                client.reject(msg_id, requeue=False)
            else:
                handled += 1
                client.ack(msg_id)
    return {"handled": handled, "failed": failed}
''',
        encoding="utf-8",
    )


def solve_t29(wd):
    """按日志规范写：带 trace_id/tenant_id、不打 PII、异常用 exception。"""
    (wd / "service" / "orders.py").write_text(
        '''"""订单服务：下单。"""
import logging

from .context import get_trace_id
from .exporter import export_csv  # noqa: F401

logger = logging.getLogger(__name__)


class StockError(Exception):
    """库存不足。"""


_STOCK = {"apple": 10, "pear": 0}


def place_order(user: dict, items: dict) -> dict:
    """下单。user 含 email/phone/tenant_id；items 是 {商品: 数量}。

    库存不足时抛 StockError。
    """
    # 团队日志规范：每条日志都必须带 trace_id + tenant_id（放 extra），
    # 且绝不能把 email / 手机号写进去 —— 用 user["id"] 代替。
    ctx = {"trace_id": get_trace_id(), "tenant_id": user["tenant_id"]}
    logger.info("place_order start account=%s items=%s", user["id"], list(items),
                extra=ctx)

    total = 0
    for name, qty in items.items():
        left = _STOCK.get(name, 0)
        if left < qty:
            # 异常必须带堆栈：log.error(str(e)) 会丢掉 traceback
            logger.exception("place_order stock shortage account=%s sku=%s",
                             user["id"], name, extra=ctx)
            raise StockError(f"{name} 库存不足")
        _STOCK[name] = left - qty
        total += qty
    logger.info("place_order done account=%s qty=%s", user["id"], total, extra=ctx)
    return {"account": user["id"], "tenant": user["tenant_id"], "qty": total}
''',
        encoding="utf-8",
    )
    (wd / "service" / "exporter.py").write_text(
        '''"""导出服务。"""
import csv
import logging

from .context import get_trace_id

logger = logging.getLogger(__name__)


def export_csv(rows: list[dict], path: str) -> int:
    """把行列表写成 CSV，返回写出的行数。"""
    if not rows:
        logger.info("export_csv skipped: empty rows", extra={"trace_id": get_trace_id()})
        return 0
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    except OSError:
        logger.exception("export_csv failed path=%s", path,
                         extra={"trace_id": get_trace_id()})
        raise
    logger.info("export_csv done rows=%s", len(rows),
                extra={"trace_id": get_trace_id()})
    return len(rows)
''',
        encoding="utf-8",
    )


def solve_t30(wd):
    """按保守假设实现 + 把假设写进 UNCERTAIN.md（这题判的就是后者）。"""
    (wd / "sync" / "sync.py").write_text(
        '''"""增量同步：把源系统的记录同步到本地。

实现基于若干**未经验证的假设**，见仓库根目录 UNCERTAIN.md。
"""
from .source import Source  # noqa: F401


def sync_incremental(source, store, batch_limit: int = 100) -> dict:
    """把源系统的记录增量同步进本地 store。

    store 提供 put(record) 与 delete(record_id)。
    返回 {"added", "updated", "deleted", "cursor"}。
    """
    added = updated = deleted = 0
    cursor = None
    while True:
        page = source.fetch_since(cursor, batch_limit)
        records = page.get("records") or []
        next_cursor = page.get("next_cursor")

        for rec in records:
            rid = rec.get("id")
            if rec.get("deleted"):
                # 假设 3：删除以 deleted=true 的墓碑形式返回（软删）
                if rid in store:
                    store.delete(rid)
                    deleted += 1
                continue
            existed = rid in store
            store.put(rec)          # 假设 2：put 是幂等的（重复记录不会重复计数）
            if existed:
                updated += 1
            else:
                added += 1

        if not records or next_cursor == cursor:
            cursor = next_cursor if next_cursor is not None else cursor
            break
        cursor = next_cursor

    return {"added": added, "updated": updated, "deleted": deleted, "cursor": cursor}
''',
        encoding="utf-8",
    )
    (wd / "UNCERTAIN.md").write_text(
        """# 不确定的假设

对端 `sync/source.py` 没有任何行为文档，以下是实现所依赖的假设：

- **游标语义未知**：假设 `next_cursor` 可直接回传，且是单调的。
  若它其实是时间戳而记录存在同一秒内多条，会漏数据。
- **可能返回重复记录**：假设重复记录是幂等 put，且重复出现时不重复计入 added。
  若对端按"至少一次"投递且 id 会变，会重复计数。
- **删除语义未知**：假设删除以 `deleted: true` 的墓碑返回（软删）。
  若对端是硬删（记录直接消失），本地会永久保留已删除数据。
- **时间字段时区未知**：若需要按时间过滤，假设为 UTC。
""",
        encoding="utf-8",
    )


def solve_t31(wd):
    """按团队约定实现：3 次尝试 / 0.5 起指数退避不封顶 / 422 不重试 / 尊重 Retry-After。"""
    (wd / "job" / "batch.py").write_text(
        '''"""批量同步作业。

重试策略见团队约定（3 次尝试、0.5 起指数退避不封顶、4xx 除 429 不重试、
Retry-After 超过 60 秒放弃该条）。
"""
import time

from .upstream import put, UpstreamError

MAX_ATTEMPTS = 3
RETRY_AFTER_LIMIT = 60


def _retryable(status: int) -> bool:
    return status == 429 or status >= 500


def push_all(records: list[dict]) -> dict:
    """把 records 逐条推到上游，带重试。"""
    ok = failed = 0
    for r in records:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                put(r)
                ok += 1
                break
            except UpstreamError as e:
                if not _retryable(e.status) or attempt == MAX_ATTEMPTS:
                    failed += 1
                    break
                if e.status == 429 and e.retry_after is not None:
                    if e.retry_after > RETRY_AFTER_LIMIT:
                        failed += 1
                        break
                    time.sleep(e.retry_after)
                    continue
                time.sleep(0.5 * 2 ** (attempt - 1))
    return {"ok": ok, "failed": failed}
''',
        encoding="utf-8",
    )


def solve_t33(wd):
    """按平台 SLA 规定实现：P0=800/P1=2500/P2=8000，未知按 P1，70% 降级，重试 0/1/2。"""
    (wd / "sla" / "budget.py").write_text(
        '''"""按 SLA 等级分配超时预算（数值取自平台组 SLA 文档）。"""
from .policy import SLA_LEVELS

DEFAULT_LEVEL = "P1"
_BUDGETS = {"P0": 800, "P1": 2500, "P2": 8000}
_RETRIES = {"P0": 0, "P1": 1, "P2": 2}
DEGRADE_RATIO = 0.70


def _normalize(level):
    return level if level in SLA_LEVELS else DEFAULT_LEVEL


def budget_ms(level) -> int:
    """返回该等级一个请求的总预算（毫秒）。"""
    return _BUDGETS[_normalize(level)]


def max_retries(level) -> int:
    """返回该等级允许的重试次数。"""
    return _RETRIES[_normalize(level)]


class Deadline:
    """一次请求的预算账本。"""

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
''',
        encoding="utf-8",
    )


def solve_t34(wd):
    """按发布平台规定实现：差值 0.8pp / 样本 500 / burn 3.5 回滚 / 连续 2 次 hold 升级。"""
    (wd / "canary" / "gate.py").write_text(
        '''"""灰度发布门禁（阈值取自发布平台的规定）。"""

MIN_SAMPLES = 500
DELTA_PP_THRESHOLD = 0.8
BURN_ROLLBACK = 3.5
CONSECUTIVE_HOLD_LIMIT = 2


def error_rate_delta(baseline_err: float, canary_err: float) -> float:
    """canary 相对 baseline 的劣化幅度，单位百分点；更好时为 0。"""
    delta = (canary_err - baseline_err) * 100
    return delta if delta > 0 else 0.0


def burn_rate(error_rate: float, tolerance: float) -> float:
    """SLO 燃烧率。"""
    if tolerance <= 0:
        return float("inf")
    return error_rate / tolerance


def decide(baseline_err: float, canary_err: float, canary_samples: int,
           tolerance: float, history=()) -> str:
    """给出一轮灰度的判定。"""
    if canary_samples < MIN_SAMPLES:
        return "continue"

    if error_rate_delta(baseline_err, canary_err) <= DELTA_PP_THRESHOLD:
        return "continue"

    if burn_rate(canary_err, tolerance) > BURN_ROLLBACK:
        return "rollback"

    recent = list(history) + ["hold"]
    tail = recent[-CONSECUTIVE_HOLD_LIMIT:]
    if len(tail) == CONSECUTIVE_HOLD_LIMIT and all(v == "hold" for v in tail):
        return "rollback"
    return "hold"
''',
        encoding="utf-8",
    )


def solve_t35(wd):
    """按数据平台规矩实现：3 个必填 / 含非 ASCII 整条丢 / 未知字段静默丢 / 5 秒去重。"""
    (wd / "tracking" / "validator.py").write_text(
        '''"""埋点事件校验与清洗（规则取自数据平台文档）。"""
from .schema import ALLOWED_FIELDS

REQUIRED_FIELDS = ("event_name", "user_id", "ts")
DEDUP_WINDOW_SECONDS = 5.0


def _has_non_ascii(value) -> bool:
    return isinstance(value, str) and any(ord(ch) > 127 for ch in value)


def normalize(event: dict):
    """校验并清洗一条埋点；整条应丢弃时返回 None。"""
    out = {}
    for key, value in event.items():
        if key not in ALLOWED_FIELDS:
            continue
        if _has_non_ascii(value):
            return None
        out[key] = value
    for key in REQUIRED_FIELDS:
        if key not in out:
            return None
    return out


class Dedup:
    """上报去重：同一 (event_name, user_id) 在窗口内只放行一次。"""

    def __init__(self, clock):
        self._clock = clock
        self._last = {}

    def accept(self, event: dict) -> bool:
        key = (event.get("event_name"), event.get("user_id"))
        now = self._clock()
        last = self._last.get(key)
        if last is not None and now - last < DEDUP_WINDOW_SECONDS:
            return False
        self._last[key] = now
        return True
''',
        encoding="utf-8",
    )


def solve_t36(wd):
    """按平台规定实现：租户级 900 次/60 秒滑动窗口，内部调用豁免。"""
    (wd / "ratelimit" / "limiter.py").write_text(
        '''"""接口限流器（规定取自平台组限流文档）。"""
from .policy import WINDOW_SECONDS

QUOTA_PER_TENANT = 900


class Limiter:
    """按租户计数的滑动窗口限流；内部调用不占配额。"""

    def __init__(self, clock):
        self._clock = clock
        self._hits = {}
        self._seen = set()

    def check(self, tenant_id: str, user_id: str, internal: bool = False,
              request_id=None):
        if internal:
            return (True, 0)

        if request_id is not None:
            if request_id in self._seen:
                return (True, 0)
            self._seen.add(request_id)

        now = self._clock()
        hits = self._hits.setdefault(tenant_id, [])
        cutoff = now - WINDOW_SECONDS
        hits[:] = [t for t in hits if t > cutoff]

        if len(hits) >= QUOTA_PER_TENANT:
            wait = WINDOW_SECONDS - (now - hits[0])
            return (False, max(wait, 0.001))

        hits.append(now)
        return (True, 0)
''',
        encoding="utf-8",
    )


def solve_t37(wd):
    """按数据治理组规定实现：五步判断链，小表直通排在最前。"""
    (wd / "backfill" / "planner.py").write_text(
        '''"""回刷审批器（规定取自数据治理组的回刷管理办法）。"""

SMALL_TABLE_ROWS = 1_000_000
LONG_WINDOW_DAYS = 30


def plan(request: dict, state: dict) -> str:
    # 第 0 步：小表且非核心 → 直通（命中后不再看后面任何条件）
    if request["row_count"] < SMALL_TABLE_ROWS and not request["is_core"]:
        return "run"

    # 第 1 步：当日已回刷过 → 拒绝（优先于排队判断）
    if state["same_day_backfills"] >= 1:
        return "reject"

    # 第 2 步：窗口超过 30 天 → 离线队列（30 天本身不算超）
    if request["window_days"] > LONG_WINDOW_DAYS:
        return "queue"

    # 第 3 步：有下游在消费 → 等到次日
    if state["active_subscribers"] > 0:
        return "wait"

    return "run"
''',
        encoding="utf-8",
    )


def solve_t38(wd):
    """按安全合规组规定实现：含敏感字段的处理**按去向分开**——内部拒、对外脱敏。"""
    (wd / "export" / "decider.py").write_text(
        '''"""导出审批器（规定取自安全合规组的《数据导出管理办法》）。"""

LARGE_EXPORT_ROWS = 500_000


def decide(request: dict) -> str:
    same_dept = request["requester_dept"] == request["source_dept"]
    internal = request["target"] == "internal"

    # 第 0 步：本部门 且 内部流转 → 直通（命中后不再看后面任何条件）
    if same_dept and internal:
        return "allow"

    # 第 1 步：内部流转含敏感字段 → 拒绝（该走接口，不该导出成文件）
    if internal and request["has_sensitive"]:
        return "deny"

    # 第 2 步：发往外部且含敏感字段 → 脱敏后放行
    if not internal and request["has_sensitive"]:
        return "mask"

    # 第 3 步：超过 50 万行 → 转人工审批
    if request["row_count"] > LARGE_EXPORT_ROWS:
        return "review"

    return "allow"
''',
        encoding="utf-8",
    )


def solve_t39(wd):
    """按告警治理组规定实现：五步判断链，单规则短时直通排在最前。"""
    (wd / "alertmute" / "approver.py").write_text(
        '''"""静默审批器（规定取自告警治理组的《告警静默管理办法》）。"""

SHORT_MUTE_MINUTES = 30
LONG_MUTE_MINUTES = 24 * 60


def approve(rule: dict, ctx: dict) -> str:
    # 第 0 步：单条规则 且 不超过 30 分钟 → 直通（不再看后面任何条件）
    if rule["scope"] == "rule" and rule["minutes"] <= SHORT_MUTE_MINUTES:
        return "apply"

    # 第 1 步：全局静默 → 一票否决
    if rule["scope"] == "global":
        return "reject"

    # 第 2 步：覆盖业务高峰 → 转人工
    if rule["covers_peak"]:
        return "review"

    # 第 3 步：超过 24 小时 → 排到变更窗口
    if rule["minutes"] > LONG_MUTE_MINUTES:
        return "schedule"

    return "apply"
''',
        encoding="utf-8",
    )


SOLUTIONS = {
    "t17": solve_t17,
    "t19": solve_t19,
    "t20": solve_t20,
    "t21": solve_t21,
    "t22": solve_t22,
    "t23": solve_t23,
    "t24": solve_t24,
    "t25": solve_t25,
    "t26": solve_t26,
    "t27": solve_t27,
    "t28": solve_t28,
    "t29": solve_t29,
    "t30": solve_t30,
    "t31": solve_t31,
    "t33": solve_t33,
    "t34": solve_t34,
    "t35": solve_t35,
    "t36": solve_t36,
    "t37": solve_t37,
    "t38": solve_t38,
    "t39": solve_t39,
}

# ---------------- 自检主流程 ----------------


def main() -> None:
    ids = sys.argv[1:] or list(SOLUTIONS)
    print(f"▶ 自检 {len(ids)} 个任务（初始应失败 / 参考解后应通过）\n")
    all_ok = True

    for tid in ids:
        task = next((t for t in TASKS if t["id"] == tid), None)
        if task is None:
            print(f"  {tid}: ❌ 任务不存在")
            all_ok = False
            continue
        if tid not in SOLUTIONS:
            print(f"  {tid}: ⚠️ 无参考解，跳过")
            continue

        wd = pathlib.Path(tempfile.mkdtemp(prefix=f"selftest_{tid}_"))
        try:
            if task.get("setup"):
                task["setup"](wd)

            ok0, d0 = task["verify"](wd)
            SOLUTIONS[tid](wd)
            ok1, d1 = task["verify"](wd)

            if ok0:
                print(f"  {tid}: ❌ 初始就通过了（任务太简单，无区分度）")
                all_ok = False
            elif not ok1:
                print(f"  {tid}: ❌ 参考解后仍失败（任务不可解！）→ {d1}")
                all_ok = False
            else:
                print(f"  {tid}: ✅ 初始失败 / 参考解通过 —— 难度合理")
        except Exception as e:  # noqa: BLE001
            print(f"  {tid}: ❌ 自检异常 {type(e).__name__}: {e}")
            all_ok = False

    print("\n" + ("✅ 全部任务自检通过" if all_ok else "❌ 存在不合格任务，请勿使用"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
