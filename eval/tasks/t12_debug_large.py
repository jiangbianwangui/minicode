from pathlib import Path

from ._util import run_python

_NOISE = '''def helper_{i}(x):
    """noise function {i}"""
    acc = 0
    for k in range({i}):
        acc += x * (k + 1)
    return acc % (x + {i})

class Widget{i}:
    def __init__(self, v):
        self.v = v
    def describe(self):
        return "Widget" + str({i}) + ":" + str(self.v)
    def transform(self, n):
        return [self.v * (j + 1) for j in range(n)]

'''


def setup(wd):
    lines = [
        '"""Simulated legacy service module (lots of noise to make debugging hard)."""\n',
        "from typing import List\n\n",
    ]
    for i in range(1, 19):
        lines.append(_NOISE.format(i=i))
    lines.append(
        '''
def process(data: List[float]) -> float:
    """Sum all elements of data. BUG: drops the first element."""
    total = 0.0
    for x in data[1:]:
        total += x
    return total

def format_result(value: float) -> str:
    return f"result={value:.2f}"
'''
    )
    (wd / "service.py").write_text("\n".join(lines), encoding="utf-8")


def _verify(wd):
    code = (
        "from service import process, format_result\n"
        "assert process([1, 2, 3]) == 6\n"
        "assert process([]) == 0\n"
        "assert format_result(process([1, 2, 3])) == 'result=6.00'\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t12",
    "name": "大文件中定位并修复 bug",
    "prompt": (
        "service.py 是一个约 400 行、含大量无关代码的“遗留”模块，其中 `process(data)` 函数有 bug："
        "给定输入 [1,2,3] 应返回 6（即所有元素求和），但当前实现结果错误；空列表应返回 0。"
        "`format_result(value)` 应把数值格式化为 'result=6.00' 这样的字符串。"
        "请先读懂文件、定位 process 的 bug 并修复，保持其它代码不变。修复后用 Bash 运行验证 "
        "`process([1,2,3])==6` 且 `format_result(process([1,2,3]))=='result=6.00'`。"
    ),
    "setup": setup,
    "verify": _verify,
}
