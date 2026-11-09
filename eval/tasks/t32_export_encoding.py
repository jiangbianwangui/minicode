"""t32：导出字符集 —— 一行编码约定，决定文件能不能被下游 Excel 打开

## 缺的是什么知识

"UTF-8 到底要不要 BOM、换行用 \\r\\n 还是 \\n" 这件事，**在任何 Python 教程里都是
两种写法都对**，只有"下游是谁"能决定。这个下游在 t29 里出现过（运营用 Excel 打开），
但仓库里没有一处写着"导出的文件必须给 Office 看"。

## 为什么它会静默失败

- 编码选错：`pandas.read_csv` 读得好好的、`pytest` 断言也过 ——
  只有下游双击打开才会看到"锟斤拷"乱码。
- 换行选错：本机能正常显示 —— 但下游打开时**整张表会挤成一行**。
- 这两种错误**不会抛任何异常**，是典型的"本地全对、交付退回"。

## 🔴 2026-10-28 实测：这题 base 5/5 全通过 —— 因为"业务方"三个字就够了

prompt 里我从没写 `utf-8-sig`，但只写了"业务方打开""Excel""乱码 / 挤成一行"，
agent 就能反推出完整答案（它在自己写的注释里写"没有 BOM 会按本地 ANSI/GBK 解码"）。

这和 t28 第一次的失败是同一个教训的升级版：
**不是"症状描述 → 反推修法"，而是"领域名词 → 反推该领域的常识"。**
`Excel / CSV / 中文` 三个词一出现，"Windows 下 CSV 要 BOM + CRLF"就是它的先验知识，
根本不需要查文档。

结论：**本题不具备区分度，不进统计**（保留为"知识其实在模型里"的反例）。
t27–t31 那 5 题之所以成立，是因为缺的是**某个对端 / 某个 broker 的具体数字**，
不是某个大众场景的通用做法。

## 判卷：读字节，不看调用

不看 `encoding=` 参数（换个写法就匹配不到），直接读文件头 3 个字节 + 换行序列。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""报表导出。"""
'''

_RECORDS = '''"""待导出的记录（数据源，不要改）。"""

RECORDS = [
    {"订单号": "A-001", "客户": "张三", "金额": 128.5, "备注": "加急"},
    {"订单号": "A-002", "客户": "李四", "金额": 90.0, "备注": "含运费"},
    {"订单号": "A-003", "客户": "王五", "金额": 302.75, "备注": ""},
]
'''

_EXPORT = '''"""把记录导出成 CSV。"""
from .records import RECORDS  # noqa: F401


def export_report(path: str) -> int:
    """把 RECORDS 写成 CSV 到 path，返回写出的数据行数（不含表头）。

    ⚠️ 还没实现。
    """
    raise NotImplementedError("尚未实现")
'''

# 🔴 既有测试不得包含编码提示。
#
# 第一版写了 `open(p, encoding="utf-8-***")` —— 一行就把答案漏了：
# agent 只读代码不读 prompt 也能拿到那个编码名，t32 base 2/2 通过。
# **测试代码也是提示**，写测试时要按"最不泄露"的写法写。
_TESTS = '''"""既有测试：只验证接口形状。"""
import csv

from report.export import export_report
from report.records import RECORDS


def test_row_count(tmp_path):
    p = tmp_path / "out.csv"
    n = export_report(str(p))
    assert n == len(RECORDS)


def test_header(tmp_path):
    p = tmp_path / "out.csv"
    export_report(str(p))
    # 按 utf-8 宽松解码回读：不预设文件是哪种编码，
    # 避免把交付要求（BOM）顺手写进"给人看的测试"里
    text = p.read_bytes().decode("utf-8", errors="replace").lstrip("\ufeff")
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == ["订单号", "客户", "金额", "备注"]
'''

_HEAD = '''
from report.export import export_report
import tempfile, os
p = os.path.join(tempfile.mkdtemp(), "out.csv")
export_report(p)
raw = open(p, "rb").read()
assert raw.startswith(b"\\xef\\xbb\\xbf"), f"文件头应为 UTF-8 BOM，实际前 8 字节 = {raw[:8]!r}"
'''

_NEWLINE = '''
from report.export import export_report
import tempfile, os
p = os.path.join(tempfile.mkdtemp(), "out.csv")
export_report(p)
raw = open(p, "rb").read()
assert b"\\r\\n" in raw, "行分隔符应为 CRLF"
assert raw.count(b"\\n") == raw.count(b"\\r\\n"), (
    "存在没有 \\\\r 的裸 \\\\n —— 老版 Excel 会把整张表挤成一行")
'''

_ROUNDTRIP = '''
from report.export import export_report
from report.records import RECORDS
import tempfile, os, csv, io
p = os.path.join(tempfile.mkdtemp(), "out.csv")
export_report(p)
raw = open(p, "rb").read()
text = raw.decode("utf-8-sig")
rows = list(csv.reader(io.StringIO(text, newline="")))
assert len(rows) == len(RECORDS) + 1, f"行数应含表头共 {len(RECORDS)+1}，实际 {len(rows)}"
assert rows[1][1] == "张三", f"中文回读错乱：{rows[1]}"
assert rows[3][2] == "302.75", f"金额格式不对：{rows[3]}"
'''

_CHECKS = [
    ("文件头带 UTF-8 BOM", _HEAD),
    ("行分隔符为 CRLF", _NEWLINE),
    ("中文与数字可无损回读", _ROUNDTRIP),
]


def _setup(wd):
    pkg = wd / "report"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "records.py").write_text(_RECORDS, encoding="utf-8")
    (pkg / "export.py").write_text(_EXPORT, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_export.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    f = wd / "tests" / "test_export.py"
    if not f.exists():
        return False, "缺少 tests/test_export.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_export.py 必须保持原样）"

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
    return True, "BOM / CRLF / 回读三项符合下游交付约定"


TASK = {
    "id": "t32",
    "name": "导出字符集(需查知识库)",
    "prompt": (
        "`report/export.py` 的 `export_report(path)` 还没实现："
        "把 `report/records.py` 里的 `RECORDS` 写成 CSV，返回数据行数（不含表头）。\n\n"
        "约束：\n"
        "- `report/records.py` 是数据源，**不可修改**；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿。\n\n"
        "⚠️ 这份报表是交给**业务方**的，他们不看代码、也不装任何工具，"
        "是**直接双击文件**打开的。这类交付对文件本身有约定——**不在仓库里，搜不到**。\n"
        "**不要试图在文件系统里搜索它们**（搜不到）；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最稳妥的方式判断。\n"
        "注意：交错了不会报错——`pandas` / `pytest` / 你自己回读全都能正常读，"
        "只有业务方双击打开的那一刻才会出问题。"
    ),
    "setup": _setup,
    "verify": _verify,
}
