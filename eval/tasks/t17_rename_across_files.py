"""t17：跨文件符号重命名 —— 考验「全量调用点一致性」。

难度来源：调用点分散在 3 个模块 + 1 个测试文件里，漏改任何一处都会让 import 或测试失败。
规格完全明确（改哪个名字、改成什么），不含任何需要猜测的成分。
"""

import subprocess
import sys

from ._util import run_python

_MODELS = '''def calc_total(items):
    """计算订单总金额：sum(price * qty)。"""
    return sum(i["price"] * i["qty"] for i in items)
'''

_SERVICE = '''from .models import calc_total


def build_order(items):
    """构造订单摘要。"""
    return {"total": calc_total(items), "count": len(items)}
'''

_REPORT = '''from .models import calc_total


def summary(items):
    """生成人类可读的订单摘要。"""
    t = calc_total(items)
    return f"合计 {t} 元，共 {len(items)} 项"
'''

_TESTS = '''from order.models import calc_total
from order.service import build_order
from order.report import summary


def test_total():
    items = [{"price": 3, "qty": 2}, {"price": 5, "qty": 1}]
    assert calc_total(items) == 11
    assert build_order(items)["total"] == 11


def test_summary():
    items = [{"price": 3, "qty": 2}]
    assert "6" in summary(items)
'''


def _setup(wd):
    (wd / "order").mkdir(parents=True, exist_ok=True)
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "order" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "order" / "models.py").write_text(_MODELS, encoding="utf-8")
    (wd / "order" / "service.py").write_text(_SERVICE, encoding="utf-8")
    (wd / "order" / "report.py").write_text(_REPORT, encoding="utf-8")
    (wd / "tests" / "test_order.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    targets = [
        wd / "order" / "models.py",
        wd / "order" / "service.py",
        wd / "order" / "report.py",
        wd / "tests" / "test_order.py",
    ]
    for f in targets:
        if not f.exists():
            return False, f"缺少文件 {f.relative_to(wd)}"

    # 1) 旧名字必须在整个工作区彻底消失（含测试文件）
    leftovers = []
    for p in wd.rglob("*.py"):
        if "calc_total" in p.read_text(encoding="utf-8"):
            leftovers.append(str(p.relative_to(wd)))
    if leftovers:
        return False, f"仍残留旧符号 calc_total: {leftovers}"

    # 2) 新名字必须出现在全部 4 个文件里（漏改任一处即失败）
    missing = [str(f.relative_to(wd)) for f in targets
               if "compute_total" not in f.read_text(encoding="utf-8")]
    if missing:
        return False, f"以下文件未改名: {missing}"

    # 3) 改名后测试必须仍然通过
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"pytest 未通过 rc={p.returncode} {tail!r}"

    return True, "全量调用点已一致改名，pytest 通过"


TASK = {
    "id": "t17",
    "name": "跨文件重命名+调用点",
    "prompt": (
        "把 `order` 包里的函数 `calc_total` 全局重命名为 `compute_total`。\n"
        "注意：该函数在 `order/models.py` 定义，并在 `order/service.py`、`order/report.py`、"
        "`tests/test_order.py` 中都有调用或导入，**每一处都要改**。\n"
        "改名后必须运行 pytest 确认 `tests/` 全部通过；旧名字不允许在任何文件里残留。"
    ),
    "setup": _setup,
    "verify": _verify,
}
