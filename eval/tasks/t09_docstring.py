from pathlib import Path

from ._util import run_python


def _verify(wd):
    code = "from utils import is_even; assert is_even(4) and not is_even(3); print('OK')"
    rc, out, err = run_python(wd, code=code)
    content = (wd / "utils.py").read_text(encoding="utf-8", errors="replace")
    has_doc = '"""' in content or "'''" in content
    has_hint = "->" in content
    ok = rc == 0 and "OK" in out and has_doc and has_hint
    return ok, f"rc={rc} out={out.strip()!r} doc={has_doc} hint={has_hint}"


TASK = {
    "id": "t09",
    "name": "带 docstring 的函数",
    "prompt": "写 utils.py，包含函数 is_even(n) 返回 n 是否为偶数。要求带 docstring 和类型标注。然后用 Bash 验证 is_even(4) 为 True、is_even(3) 为 False。",
    "setup": None,
    "verify": _verify,
}
