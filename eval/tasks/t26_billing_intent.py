"""t26：真假 bug 混排 —— 考"该不该改"，不考"会不会改"

难度来源（与 t01–t25 的本质差别）：
1) **不可脚本化**：三处真 bug 的修法互不相同（累进计价 / 用日历天数 / 分转元格式），
   没有任何一处能靠"重复替换 N 次"解决，写 Bash 脚本没有下手的地方。
2) **真假混排**：另有两处"看起来像 bug、实际是对接约定"的写法，
   改掉就失败。判断依据只在代码注释与调用方关系里——不读全就会踩。
3) **规则藏在 docstring**：阶梯计价的"仅超出部分"、折算的"当月总天数"，
   既有测试恰好只覆盖不触发 bug 的区间（4 月刚好 30 天、用量 ≤100），
   `pytest` 全绿也发现不了，必须读懂文字而不是只跑测试。
   **注意**：prompt 里必须明说"绿灯是假信号"——否则 agent 会陷入
   "我已完成 / 任务未完成"的矛盾，反复跑测试直到预算耗尽（见 ABLATION Batch Q）。
4) **失败昂贵**：五处里改错任何一处都失败，逼出"改之前先确认意图"。

这三条合起来，针对的是昨天扫描暴露的短板：agent 擅长"定位 + 脚本批处理"，
而这题每一处都要先做语义判断，正好落在它的策略盲区上。
"""

import subprocess
import sys

from ._util import run_python

# ---------------- 既有项目（9 个文件） ----------------

_INIT = '''"""在线计费系统核心模块。"""
'''

_MODELS = '''"""领域模型。

全仓库金额约定：
- 计算过程一律用 **分（int）**，避免浮点误差；
- 只在最终导出/展示时转成元（两位小数）。
"""
from dataclasses import dataclass


@dataclass
class Account:
    account_id: str
    plan: str = "basic"


@dataclass
class UsageRecord:
    account_id: str
    qty: int
    month: str


@dataclass
class Invoice:
    account_id: str
    month: str
    charge_cents: int = 0
    tax_cents: int = 0

    @property
    def total_cents(self) -> int:
        """含税总额（分）。注意：税额已经算在里面了，不要再加一次。"""
        return self.charge_cents + self.tax_cents
'''

_RATING = '''"""用量计费（阶梯计价）。"""


def usage_charge_cents(qty: int) -> int:
    """按阶梯计算用量费用，返回**分**（int，向下取整到分）。

    计价阶梯（元/单位）：
      - 第 1 档：0 ~ 100 单位，0.50 元/单位
      - 第 2 档：100 单位以上，**仅超出部分**按 0.30 元/单位

    退款单（credit_note）会传 <= 0 的 qty，此时返回 0 表示无需计费，
    不校验、不抛异常 —— 这是与退款流程的约定。
    """
    if qty <= 0:
        return 0
    rate = 0.50 if qty <= 100 else 0.30
    return int(qty * rate * 100)
'''

_PRORATION = '''"""按月折算（月中开通、月中退订）。"""

# 折算用的大致天数（历史实现遗留）。
BILLING_DAYS = 30


def prorate_cents(monthly_cents: int, year: int, month: int, day: int) -> int:
    """按"当月剩余天数 / 当月总天数"折算月费，返回**分**（int，向下取整）。

    day 为 1-based，表示从当月第几天开始计费。
    例：4 月（30 天）从 16 号开始 → 剩余 15 天 → 收一半。
    """
    remaining = BILLING_DAYS - day + 1
    return int(monthly_cents * remaining / BILLING_DAYS)
'''

_TAX = '''"""税费计算。

与财税系统（金税接口）的对接约定，**不要修改**：
  税额一律"全额向下取整到分"，不做四舍五入。
  历史申报数据按此口径生成；改成四舍五入会让 2023 年以来的申报记录对不上。
  完整说明见仓库根目录的 INTEGRATION.md，对账脚本 legacy_recon.py 依赖此口径。
"""

TAX_ROUNDING = "floor"


def tax_for(charge_cents: int, rate: float) -> int:
    """按税率计算税额，返回**分**（int）。

    舍入口径由 TAX_ROUNDING 决定，当前为 floor（见模块 docstring）。
    """
    return int(charge_cents * rate)
'''

_PIPELINE = '''"""月度账单生成流程（编排层）。

链路：用量 → 计费 → 折算 → 计税 → 出账。
金额全链路用分，只有导出时才转元（见 export.py）。
"""
from .models import Invoice
from .proration import prorate_cents
from .rating import usage_charge_cents
from .tax import tax_for

DEFAULT_TAX_RATE = 0.06


def build_invoice(account_id, month, usage_qty, monthly_cents=None,
                  start_day=None, tax_rate=DEFAULT_TAX_RATE):
    """生成一张账单。

    - 按量路径：给 usage_qty，走阶梯计价；
    - 包月路径：给 monthly_cents + start_day，按天折算；
    - 两条路径互斥，包月优先。
    """
    if monthly_cents is not None:
        if start_day is None:
            raise ValueError("包月计费必须给 start_day")
        year, mon = (int(x) for x in month.split("-"))
        charge = prorate_cents(monthly_cents, year, mon, start_day)
    else:
        charge = usage_charge_cents(usage_qty)

    return Invoice(
        account_id=account_id,
        month=month,
        charge_cents=charge,
        tax_cents=tax_for(charge, tax_rate),
    )


def build_batch(records, monthly_cents=None, start_day=None,
                tax_rate=DEFAULT_TAX_RATE):
    """批量出账。records 为 (account_id, month, qty) 三元组。"""
    return [
        build_invoice(aid, month, qty, monthly_cents=monthly_cents,
                      start_day=start_day, tax_rate=tax_rate)
        for aid, month, qty in records
    ]


def summarize(invoices):
    """汇总张数 / 含税总额（分）/ 税额（分）。"""
    if not invoices:
        return {"count": 0, "total_cents": 0, "tax_cents": 0}
    return {
        "count": len(invoices),
        "total_cents": sum(i.total_cents for i in invoices),
        "tax_cents": sum(i.tax_cents for i in invoices),
    }
'''

_LEGACY_RECON = '''"""历史对账脚本（每月 1 号定时运行）。

依赖 tax.tax_for 的 floor 口径：对账结果会与历史存档逐条比对，
口径一变就整月不平。**不要为了"看起来更对"改它。**
"""
from .tax import tax_for


def reconcile(pairs, rate=0.06):
    """pairs: [(charge_cents, archived_tax_cents), ...]

    重新算税并与存档比对，返回 (不平条数, 明细)。
    """
    bad = []
    for charge, archived in pairs:
        if tax_for(charge, rate) != archived:
            bad.append((charge, archived, tax_for(charge, rate)))
    return len(bad), bad
'''

_INTEGRATION_MD = '''# 外部系统对接说明

本文件记录本仓库与外部系统之间的**行为约定**。这些约定不是"bug"，
改动会导致对接失败，请勿在修 bug 时顺手改动。

## 1. 金额单位

内部计算一律用「分」（int），只在导出/展示时转成「元」（两位小数）。
CSV 导出、报表汇总都属于"导出"，必须走元。

## 2. 财税接口（金税）

税额口径：**全额向下取整到分**，不做四舍五入。

- 原因：2023 年以前的申报系统只接受整数分，历史数据全部按此口径生成。
- 影响：改成四舍五入会让历史申报记录对不上，对账脚本报警。
- 变更流程：需财税接口方评审，不接受单方面修改。

## 3. 退款流程

退款单的 `qty` 传**负数**，走 `usage_charge_cents` 时约定返回 `0`。
退款金额由退款流程单独结算，不在计费模块里重算，也不做参数校验。

## 4. 历史对账

`legacy_recon.py` 每月 1 号运行，逐条比对历史存档。
**它依赖第 2 节的 floor 口径**，改动税算会直接导致整月不平。

## 5. 报表口径

月度报表（`report.summary_by_month`）用**含税总额** `Invoice.total_cents`。
该属性已经包含税额，汇总时不要再单独加 `tax_cents`。
'''


_EXPORT = '''"""发票导出（CSV）。"""
import csv


def invoice_row(inv) -> dict:
    """导出一行。金额列以**元**为单位、保留两位小数（见 models 的金额约定）。"""
    return {
        "account": inv.account_id,
        "month": inv.month,
        "charge": f"{inv.charge_cents:.2f}",
        "tax": f"{inv.tax_cents:.2f}",
        "total": f"{inv.total_cents:.2f}",
    }


def export_csv(invoices, path) -> int:
    """把发票列表写成 CSV，返回写出的行数。"""
    rows = [invoice_row(i) for i in invoices]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["account", "month", "charge", "tax", "total"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)
'''

_CREDIT_NOTE = '''"""退款单（credit note）。

退款单的用量 qty 为负数，走 usage_charge_cents 时**约定返回 0**：
退款金额由退款流程单独结算，不在这里重算。
"""
from .rating import usage_charge_cents


def credit_for(qty: int) -> int:
    """退款单的计费金额（分）。按约定恒为 0。"""
    return usage_charge_cents(-abs(qty))
'''

_REPORT = '''"""月度账单报表（与本次修复无关，但共享 models 的金额约定）。"""
from .models import Invoice  # noqa: F401


def summary_by_month(invoices):
    """按月汇总含税总额（元）。total_cents 已含税，不要再加 tax_cents。"""
    out = {}
    for inv in invoices:
        out.setdefault(inv.month, 0)
        out[inv.month] += inv.total_cents
    return {m: round(c / 100, 2) for m, c in sorted(out.items())}


def top_accounts(invoices, n=3):
    """按含税总额取前 n 个账户。"""
    totals = {}
    for inv in invoices:
        totals[inv.account_id] = totals.get(inv.account_id, 0) + inv.total_cents
    ranked = sorted(totals.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:n]
'''

_TESTS = '''"""既有测试：覆盖当前稳定行为，不得修改。"""
from billing.export import invoice_row
from billing.models import Invoice
from billing.proration import prorate_cents
from billing.rating import usage_charge_cents
from billing.report import summary_by_month


def test_usage_first_tier():
    assert usage_charge_cents(40) == 2000
    assert usage_charge_cents(100) == 5000


def test_prorate_april():
    # 4 月 30 天，16 号开始 → 剩余 15 天 → 一半
    assert prorate_cents(3100, 2026, 4, 16) == 1550


def test_export_columns():
    inv = Invoice("a1", "2026-04", 3100, 186)
    row = invoice_row(inv)
    assert list(row) == ["account", "month", "charge", "tax", "total"]


def test_summary_by_month():
    invs = [Invoice("a1", "2026-04", 3100, 186), Invoice("a2", "2026-04", 1000, 60)]
    assert summary_by_month(invs) == {"2026-04": 43.46}
'''


def _setup(wd):
    pkg = wd / "billing"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "models.py").write_text(_MODELS, encoding="utf-8")
    (pkg / "rating.py").write_text(_RATING, encoding="utf-8")
    (pkg / "proration.py").write_text(_PRORATION, encoding="utf-8")
    (pkg / "tax.py").write_text(_TAX, encoding="utf-8")
    (pkg / "export.py").write_text(_EXPORT, encoding="utf-8")
    (pkg / "credit_note.py").write_text(_CREDIT_NOTE, encoding="utf-8")
    (pkg / "report.py").write_text(_REPORT, encoding="utf-8")
    (pkg / "pipeline.py").write_text(_PIPELINE, encoding="utf-8")
    (pkg / "legacy_recon.py").write_text(_LEGACY_RECON, encoding="utf-8")
    (wd / "INTEGRATION.md").write_text(_INTEGRATION_MD, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_billing.py").write_text(_TESTS, encoding="utf-8")


# ---------------- 隐藏用例：每项独立，便于归因 ----------------

_CHECKS = [
    (
        "阶梯计费（只有超出部分降档）",
        "from billing.rating import usage_charge_cents\n"
        "assert usage_charge_cents(101) == 5030, ('101', usage_charge_cents(101))\n"
        "assert usage_charge_cents(150) == 6500, ('150', usage_charge_cents(150))\n"
        "assert usage_charge_cents(300) == 11000, ('300', usage_charge_cents(300))\n",
    ),
    (
        "按月实际天数折算（31 天 / 28 天 / 闰年 29 天）",
        "from billing.proration import prorate_cents\n"
        "assert prorate_cents(3100, 2026, 1, 1) == 3100, ('jan', prorate_cents(3100, 2026, 1, 1))\n"
        "assert prorate_cents(3100, 2026, 2, 15) == 1550, ('feb28', prorate_cents(3100, 2026, 2, 15))\n"
        "assert prorate_cents(3100, 2028, 2, 15) == 1603, ('feb29', prorate_cents(3100, 2028, 2, 15))\n",
    ),
    (
        "导出金额以元为单位",
        "from billing.models import Invoice\n"
        "from billing.export import invoice_row\n"
        "r = invoice_row(Invoice('a1', '2026-01', 1234, 66))\n"
        "assert r['charge'] == '12.34', r\n"
        "assert r['tax'] == '0.66', r\n"
        "assert r['total'] == '13.00', r\n",
    ),
    (
        "税额仍向下取整（与财税接口的约定，改动即失败）",
        "from billing.tax import tax_for\n"
        "assert tax_for(1009, 0.06) == 60, tax_for(1009, 0.06)\n"
        "assert tax_for(999, 0.06) == 59, tax_for(999, 0.06)\n",
    ),
    (
        "退款单负数量仍返回 0（与退款流程的约定，改动即失败）",
        "from billing.rating import usage_charge_cents\n"
        "from billing.credit_note import credit_for\n"
        "assert usage_charge_cents(-5) == 0, usage_charge_cents(-5)\n"
        "assert credit_for(5) == 0, credit_for(5)\n",
    ),
]


def _verify(wd):
    f = wd / "tests" / "test_billing.py"
    if not f.exists():
        return False, "缺少 tests/test_billing.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_billing.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"既有测试失败（引入了回归）{tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            detail = detail[-1] if detail else "未知"
            return False, f"{label} 不满足 → {detail}"

    return True, "三处真 bug 修复 + 两处对接约定保持"


TASK = {
    "id": "t26",
    "name": "真假bug混排(需判断该不该改)",
    "prompt": (
        "`billing/` 是一个在线计费系统的核心模块（10 个 Python 文件 + 一份对接说明"
        " `INTEGRATION.md` + 一个测试目录）。"
        "财务反馈最近**三张账单算错了**，说明代码里有 bug。\n\n"
        "请找出并修掉这三处 bug。\n\n"
        "**重要**：这个仓库里还有一些写法看起来像 bug，但其实是跟外部系统"
        "（财税接口、退款流程、历史对账脚本）的对接约定——**改动它们会破坏兼容性**。"
        "判断依据只在代码注释和被谁调用的关系里，请先读懂代码再动手，"
        "不要\"顺手修复\"看着别扭的地方。\n\n"
        "**注意**：`tests/` 里的既有测试覆盖不到这三处问题——**它们在修复前后都是全绿的**。"
        "不要把绿灯当作完成信号；是否改对，只能靠你读懂代码后自行判断。"
    ),
    "setup": _setup,
    "verify": _verify,
}
