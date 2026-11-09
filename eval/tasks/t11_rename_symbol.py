from pathlib import Path

from ._util import run_python


def setup(wd):
    (wd / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def total(items):\n    return sum(add(i, 0) for i in items)\n",
        encoding="utf-8",
    )
    (wd / "report.py").write_text(
        "from calc import total\n\n"
        "def make_report(items):\n    return {\"sum\": total(items)}\n",
        encoding="utf-8",
    )
    (wd / "main.py").write_text(
        "from calc import total\nfrom report import make_report\n\n"
        "def run():\n    data = [1, 2, 3]\n    return make_report(data)\n",
        encoding="utf-8",
    )


def _verify(wd):
    code = (
        "from calc import grand_total\n"
        "from report import make_report\n"
        "assert grand_total([1, 2, 3]) == 6\n"
        "assert make_report([1, 2, 3]) == {'sum': 6}\n"
        "import calc\n"
        "assert not hasattr(calc, 'total')\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t11",
    "name": "跨文件重命名符号",
    "prompt": (
        "项目由 calc.py / report.py / main.py 组成。calc.py 里定义了函数 `total(items)`，"
        "report.py 和 main.py 都调用了它。请把 `total` 重命名为 `grand_total`：包括 calc.py "
        "中的定义，以及 report.py、main.py 中的所有调用处。重命名后行为必须完全不变"
        "（make_report([1,2,3]) 应返回 {'sum': 6}，且不能再存在名为 total 的函数）。"
    ),
    "setup": setup,
    "verify": _verify,
}
