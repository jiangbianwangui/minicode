from pathlib import Path

from ._util import run_python

_NOISE = '''def noise_func_{i}(a, b):
    """noise {i}"""
    out = []
    for t in range({i}):
        out.append((a + t) * (b - t))
    return out

'''


def setup(wd):
    lines = [
        '"""Large data processing module (edit target buried at the end)."""\n',
        "from typing import List, Dict\n\n",
    ]
    for i in range(1, 25):
        lines.append(_NOISE.format(i=i))
    lines.append(
        '''
class DataStore:
    def __init__(self, records):
        self.records = list(records)

    def count(self):
        return len(self.records)

    def total_amount(self):
        return sum(r.get("amount", 0) for r in self.records)

    def avg_amount(self):
        if not self.records:
            return 0.0
        return self.total_amount() / len(self.records)
'''
    )
    (wd / "data_proc.py").write_text("\n".join(lines), encoding="utf-8")


def _verify(wd):
    code = (
        "from data_proc import DataStore\n"
        "ds = DataStore([{'amount': 10}, {'amount': 20}, {'amount': 30}])\n"
        "s = ds.summary()\n"
        "assert s['count'] == 3\n"
        "assert s['total'] == 60\n"
        "assert abs(s['avg'] - 20.0) < 1e-9\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t14",
    "name": "向大文件类插入方法",
    "prompt": (
        "data_proc.py 是一个较大的模块，其中定义了类 `DataStore`（含 records 属性与 "
        "count / total_amount / avg_amount 方法）。请在不破坏现有代码的前提下，为 DataStore "
        "新增一个方法 `summary(self)`，返回一个 dict：{'count': 记录数, 'total': 金额总和, "
        "'avg': 平均金额}。可直接复用已有的 total_amount / avg_amount 或 records。"
        "修改后用 Bash 运行验证 summary() 在 records=[{'amount':10},{'amount':20},{'amount':30}] "
        "时返回 count=3, total=60, avg=20.0。"
    ),
    "setup": setup,
    "verify": _verify,
}
