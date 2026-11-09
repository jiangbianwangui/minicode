"""t23：在既有项目中实现跨模块功能（隐式耦合，规模 8 文件）

难度来源（这是与前 22 个任务的本质差别）：
1) **规模**：项目有 8 个文件、约 600 行，「读完再动手」本身就要多轮，靠猜必错。
2) **隐式耦合**：没有 README、没有文档说明，唯一的事实来源是既有测试与代码本身。
   需求里给的 API 形状必须靠读 `test_existing.py` 才能推断出来。
3) **必须跑测试才能发现**：仓库里预置的测试会覆盖新功能的部分边界，
   不跑就不知道自己的实现漏了什么。
4) **不能破坏既有行为**：改了 inventory.py 的既有逻辑，老测试立刻红。
"""

import subprocess
import sys

from ._util import run_python

# ---- 既有项目：8 个文件 ----

_INIT = '''"""仓储库存系统。"""
'''

_EXCEPTIONS = '''"""领域异常。"""


class StockError(Exception):
    """库存相关错误基类。"""


class InsufficientStock(StockError):
    """库存不足。"""

    def __init__(self, sku, requested, available):
        self.sku = sku
        self.requested = requested
        self.available = available
        super().__init__(f"{sku}: 请求 {requested}，可用 {available}")


class UnknownSku(StockError):
    """未知 SKU。"""
'''

_MODELS = '''"""领域模型。"""
from dataclasses import dataclass, field


@dataclass
class Product:
    sku: str
    name: str
    price: float


@dataclass
class StockEntry:
    sku: str
    qty: int
    reserved: int = 0

    @property
    def available(self) -> int:
        return self.qty - self.reserved


@dataclass
class OrderLine:
    sku: str
    qty: int


@dataclass
class Order:
    order_id: str
    lines: list = field(default_factory=list)

    def add(self, sku, qty):
        self.lines.append(OrderLine(sku=sku, qty=qty))
        return self
'''

_STORE = '''"""库存存储（内存实现）。"""
from .exceptions import InsufficientStock, UnknownSku
from .models import StockEntry


class Store:
    def __init__(self):
        self._products = {}
        self._stock = {}

    def add_product(self, product):
        self._products[product.sku] = product
        self._stock.setdefault(product.sku, StockEntry(sku=product.sku, qty=0))
        return product

    def get_product(self, sku):
        if sku not in self._products:
            raise UnknownSku(sku)
        return self._products[sku]

    def set_stock(self, sku, qty):
        self.get_product(sku)
        self._stock[sku] = StockEntry(sku=sku, qty=qty)
        return self._stock[sku]

    def get_entry(self, sku):
        self.get_product(sku)
        return self._stock[sku]

    def reserve(self, sku, qty):
        entry = self.get_entry(sku)
        if entry.available < qty:
            raise InsufficientStock(sku, qty, entry.available)
        entry.reserved += qty
        return entry

    def release(self, sku, qty):
        entry = self.get_entry(sku)
        entry.reserved = max(0, entry.reserved - qty)
        return entry

    def commit(self, sku, qty):
        entry = self.get_entry(sku)
        entry.qty -= qty
        entry.reserved = max(0, entry.reserved - qty)
        return entry
'''

_FULFILL = '''"""订单履约（已有骨架，缺核心逻辑）。"""
from .exceptions import InsufficientStock


class Fulfiller:
    """把一个 Order 的每一行转为库存预留。"""

    def __init__(self, store):
        self.store = store

    def reserve_order(self, order):
        """为订单预留库存。

        要求（由 test_existing.py 与 test_price 共同约束）：
        - 返回预留成功的行列表（OrderLine）
        - 任一行库存不足时，必须**回滚已经预留的行**，然后抛出 InsufficientStock
        - 不允许出现"部分扣减"的脏状态
        """
        raise NotImplementedError("待实现")
'''

_PRICING = '''"""定价（已有完整实现，供新功能复用）。"""


def line_total(product, qty):
    return round(product.price * qty, 2)


def order_total(store, order):
    """按行计算订单原价总额。"""
    total = 0.0
    for line in order.lines:
        total += line_total(store.get_product(line.sku), line.qty)
    return round(total, 2)
'''

_TESTS_EXISTING = '''"""既有测试：描述项目当前行为，不可修改。"""
import pytest

from inventory.exceptions import InsufficientStock, UnknownSku
from inventory.models import Order, Product
from inventory.store import Store
from inventory.pricing import order_total


def test_add_and_reserve():
    s = Store()
    s.add_product(Product("A1", "苹果", 3.0))
    s.set_stock("A1", 10)
    s.reserve("A1", 4)
    assert s.get_entry("A1").available == 6
    s.commit("A1", 4)
    assert s.get_entry("A1").qty == 6


def test_reserve_too_much():
    s = Store()
    s.add_product(Product("A1", "苹果", 3.0))
    s.set_stock("A1", 2)
    with pytest.raises(InsufficientStock):
        s.reserve("A1", 5)


def test_unknown_sku():
    s = Store()
    with pytest.raises(UnknownSku):
        s.get_product("NOPE")


def test_order_total():
    s = Store()
    s.add_product(Product("A1", "苹果", 3.0))
    s.add_product(Product("B2", "香蕉", 2.5))
    o = Order("o1").add("A1", 2).add("B2", 4)
    assert order_total(s, o) == 16.0
'''

_TESTS_PRICE = '''"""新功能测试（会失败，直到你实现 discount.py）。"""
import pytest

from inventory.discount import apply_discount


def test_no_discount():
    assert apply_discount(100.0, 0) == 100.0


def test_basic_discount():
    assert apply_discount(100.0, 0.1) == 90.0


def test_rounding_half_up():
    # 四舍五入到分
    assert apply_discount(19.99, 0.1) == 17.99


def test_floor_threshold():
    # 满 200 减 20（先减后折不可，必须是固定规则）
    assert apply_discount(200.0, 0) == 180.0


def test_invalid_rate():
    with pytest.raises(ValueError):
        apply_discount(100.0, 1.5)
'''


def _setup(wd):
    pkg = wd / "inventory"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "exceptions.py").write_text(_EXCEPTIONS, encoding="utf-8")
    (pkg / "models.py").write_text(_MODELS, encoding="utf-8")
    (pkg / "store.py").write_text(_STORE, encoding="utf-8")
    (pkg / "fulfill.py").write_text(_FULFILL, encoding="utf-8")
    (pkg / "pricing.py").write_text(_PRICING, encoding="utf-8")
    (wd / "test_existing.py").write_text(_TESTS_EXISTING, encoding="utf-8")
    (wd / "test_discount.py").write_text(_TESTS_PRICE, encoding="utf-8")
    # 给足"规模感"：再放一个较大的说明性模块，逼 agent 读多文件而不是凭猜
    (pkg / "reports.py").write_text(_REPORTS, encoding="utf-8")
    (pkg / "seed.py").write_text(_SEED, encoding="utf-8")


_REPORTS = '''"""报表（与本次任务无关，但增加项目规模）。"""
from .pricing import line_total


def low_stock_report(store, threshold=5):
    out = []
    for sku in list(store._products):
        entry = store.get_entry(sku)
        if entry.available <= threshold:
            out.append({"sku": sku, "available": entry.available})
    return sorted(out, key=lambda r: r["sku"])


def valuation_report(store):
    total = 0.0
    for sku in list(store._products):
        p = store.get_product(sku)
        e = store.get_entry(sku)
        total += line_total(p, e.qty)
    return round(total, 2)
'''

_SEED = '''"""演示数据。"""
from .models import Product
from .store import Store


def build_demo_store():
    s = Store()
    for sku, name, price, qty in [
        ("A1", "苹果", 3.0, 50),
        ("B2", "香蕉", 2.5, 30),
        ("C3", "橘子", 4.2, 10),
    ]:
        s.add_product(Product(sku, name, price))
        s.set_stock(sku, qty)
    return s
'''


def _verify(wd):
    # 1) 既有测试必须原样通过（不得修改/删除测试文件）
    for name in ["test_existing.py", "test_discount.py"]:
        f = wd / name
        if not f.exists():
            return False, f"缺少 {name}"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_existing.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试回归（被改坏了）rc={p.returncode} {tail!r}"

    # 2) 新功能测试必须通过
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_discount.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"discount 未实现正确 rc={p.returncode} {tail!r}"

    # 3) 履约回滚必须正确（隐藏用例，仓库里没有现成测试）
    code = (
        "from inventory.store import Store\n"
        "from inventory.models import Product, Order\n"
        "from inventory.fulfill import Fulfiller\n"
        "from inventory.exceptions import InsufficientStock\n"
        "s = Store()\n"
        "s.add_product(Product('A1','a',1.0)); s.set_stock('A1', 5)\n"
        "s.add_product(Product('B2','b',1.0)); s.set_stock('B2', 1)\n"
        "o = Order('o1').add('A1', 3).add('B2', 2)\n"
        "f = Fulfiller(s)\n"
        "try:\n"
        "    f.reserve_order(o)\n"
        "    raise AssertionError('应抛 InsufficientStock')\n"
        "except InsufficientStock:\n"
        "    pass\n"
        "assert s.get_entry('A1').reserved == 0, s.get_entry('A1').reserved\n"
        "assert s.get_entry('B2').reserved == 0\n"
        "o2 = Order('o2').add('A1', 2)\n"
        "lines = Fulfiller(s).reserve_order(o2)\n"
        "assert len(lines) == 1 and s.get_entry('A1').reserved == 2\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    if rc != 0 or "OK" not in out:
        return False, f"履约回滚不正确: {(err or out).strip()[-300:]!r}"

    return True, "既有测试无回归 + discount 通过 + 履约回滚正确"


TASK = {
    "id": "t23",
    "name": "既有项目跨模块功能(隐式耦合)",
    "prompt": (
        "`inventory/` 是一个已有的仓储库存项目（8 个文件，约 600 行）。请完成两件事：\n\n"
        "**一、新建 `inventory/discount.py`，实现 `apply_discount(amount, rate)`**\n"
        "仓库里已有 `test_discount.py`，它是这个函数行为的唯一权威说明——"
        "请先读它，把函数写对（注意：里面有一条与'满减'有关的规则，以及一条与'四舍五入精度'有关的规则，"
        "只看函数名猜不出来）。\n\n"
        "**二、实现 `inventory/fulfill.py` 里 `Fulfiller.reserve_order`**\n"
        "当前是 `NotImplementedError`。要求：为订单每行预留库存并返回成功的行列表；"
        "**任一行库存不足时必须回滚此前已预留的行**，不允许留下部分扣减的脏状态。\n\n"
        "完成后必须运行 `pytest test_existing.py test_discount.py` 确认全部通过；"
        "既有测试文件不得修改。"
    ),
    "setup": _setup,
    "verify": _verify,
}
