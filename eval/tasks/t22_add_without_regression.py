"""t22：新增功能且不得回归 —— 考验「改动后仍守得住既有测试」。

难度来源：不只是写对新函数，还必须在改完之后真的跑一遍原有测试并确认没弄坏。
"""

import subprocess
import sys

from ._util import run_python

_CART = '''class Cart:
    """简易购物车。"""

    def __init__(self):
        self._items = {}

    def add_item(self, name, price, qty=1):
        self._items[name] = {"price": price, "qty": qty}

    def remove_item(self, name):
        self._items.pop(name, None)

    def total(self):
        return sum(i["price"] * i["qty"] for i in self._items.values())

    def clear(self):
        self._items.clear()

    def size(self):
        return len(self._items)
'''

_TESTS = '''from cart.cart import Cart


def test_add_and_total():
    c = Cart()
    c.add_item("apple", 3, 2)
    assert c.total() == 6


def test_remove():
    c = Cart()
    c.add_item("apple", 3, 2)
    c.remove_item("apple")
    assert c.total() == 0


def test_clear():
    c = Cart()
    c.add_item("a", 1)
    c.clear()
    assert c.size() == 0


def test_multiple_items():
    c = Cart()
    c.add_item("a", 2, 3)
    c.add_item("b", 5, 1)
    assert c.total() == 11


def test_empty_total():
    assert Cart().total() == 0
'''


def _setup(wd):
    (wd / "cart").mkdir(parents=True, exist_ok=True)
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "cart" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "cart" / "cart.py").write_text(_CART, encoding="utf-8")
    (wd / "tests" / "test_cart.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    tf = wd / "tests" / "test_cart.py"
    if tf.read_text(encoding="utf-8").strip() != _TESTS.strip():
        return False, "既有测试被修改（必须保持原样）"

    code = (
        "from cart.cart import apply_discount\n"
        "items = [{'price': 100, 'qty': 2}, {'price': 50, 'qty': 1}]\n"
        "out = apply_discount(items, 0.1)\n"
        "assert abs(out[0]['price'] - 90.0) < 1e-9 and abs(out[1]['price'] - 45.0) < 1e-9, out\n"
        "assert items[0]['price'] == 100, '不得修改入参（需返回新列表）'\n"
        "assert apply_discount([], 0.2) == []\n"
        "assert abs(apply_discount(items, 0)[0]['price'] - 100) < 1e-9\n"
        "for bad in (-0.1, 1.5):\n"
        "    try:\n"
        "        apply_discount(items, bad)\n"
        "        raise AssertionError(f'rate={bad} 应抛 ValueError')\n"
        "    except ValueError:\n"
        "        pass\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    if rc != 0 or "OK" not in out:
        return False, f"apply_discount 行为不符: {(err or out).strip()[-300:]!r}"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试回归 rc={p.returncode} {tail!r}"

    return True, "新函数行为正确且既有 5 个测试无回归"


TASK = {
    "id": "t22",
    "name": "新增功能且不回归",
    "prompt": (
        "在 `cart/cart.py` 中新增函数 `apply_discount(items, rate)`：\n"
        "- `items` 是形如 `{'price': 数值, 'qty': 整数}` 的字典列表；\n"
        "- 返回一个**新列表**，每项 price 变为 `round(price * (1 - rate), 2)`，qty 不变；\n"
        "- **不得修改传入的原列表/原字典**；\n"
        "- `rate` 必须在 [0, 1] 区间，否则抛 `ValueError`；空列表返回 `[]`。\n"
        "完成后必须运行 `pytest tests` 确认既有 5 个测试**全部继续通过**"
        "（既有测试文件不允许改动）。\n"
        "**注意**：这 5 个测试在你动手之前就已经全绿，它们**只能证明你没弄坏既有功能**，"
        "不能证明新函数写对了——新函数是否满足上面的规则，需要你自己逐条核对。"
    ),
    "setup": _setup,
    "verify": _verify,
    # 审计豁免：`pytest tests` 初始就 5 passed（设计如此，它是"不许回归"的护栏），
    # prompt 里已写明它是假信号，故不算缺陷。
    "audit_ok": ["pytest tests"],
}
