import subprocess
import sys

from ._util import run_python


def _verify(wd):
    code = (
        "from calc import add, sub, mul, div\n"
        "assert add(2, 3) == 5\n"
        "assert sub(5, 2) == 3\n"
        "assert mul(2, 3) == 6\n"
        "try:\n    div(1, 0)\n    raise AssertionError('no err')\n"
        "except ValueError:\n    pass\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    if rc != 0 or "OK" not in out:
        return False, f"func rc={rc} out={out.strip()!r} err={err.strip()!r}"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    ok = p.returncode == 0
    tail = (p.stdout or p.stderr).strip()[-200:]
    return ok, f"pytest rc={p.returncode} {tail!r}"


TASK = {
    "id": "t13",
    "name": "多模块包 + pytest",
    "prompt": (
        "实现一个迷你计算器包 `calc/`：`__init__.py` 暴露 add(a,b)、sub(a,b)、mul(a,b)、"
        "div(a,b) 四个函数，其中 div 在 b==0 时抛 ValueError，其余返回正常运算结果。"
        "再创建 `tests/test_calc.py`，用 pytest 覆盖四个函数的正常值以及 div 的异常分支"
        "（用 pytest.raises(ValueError)）。最后运行 pytest 确保全部通过。"
    ),
    "setup": None,
    "verify": _verify,
}
