from pathlib import Path

from ._util import run_python


def setup(wd):
    (wd / "buggy.py").write_text(
        "def mul(a, b):\n    return a + b\n", encoding="utf-8"
    )


def _verify(wd):
    code = "from buggy import mul; assert mul(3,4)==12; print('OK')"
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t03",
    "name": "修复一个 bug",
    "prompt": "buggy.py 里的 mul 函数当前返回 a + b，但它本应返回 a * b。请修复它，使断言 `from buggy import mul; assert mul(3,4)==12` 通过。先 ReadFile 确认内容再改。",
    "setup": setup,
    "verify": _verify,
}
