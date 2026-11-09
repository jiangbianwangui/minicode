import csv
from pathlib import Path

from ._util import run_python


def setup(wd):
    rows = [
        "id,name,score",
        "1,Alice,90",
        "2,Bob,85",
        "3,Carol,92",
        "4,Dave,78",
        "5,Eve,88",
    ]
    (wd / "data.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _verify(wd):
    # 期望行数 = 数据行（不含表头）
    with open(wd / "data.csv", encoding="utf-8") as f:
        n = len(list(csv.reader(f))) - 1
    rc, out, err = run_python(wd, script="count_rows.py")
    ok = rc == 0 and str(n) in out
    return ok, f"expected={n} rc={rc} out={out.strip()!r}"


TASK = {
    "id": "t05",
    "name": "读取 CSV 统计行数",
    "prompt": "data.csv 已存在（第一行为表头）。写 count_rows.py，读取 data.csv 并打印数据行数（不含表头），例如打印 '5'。然后用 Bash 运行确认输出正确。",
    "setup": setup,
    "verify": _verify,
}
