from pathlib import Path

from ._util import run_python


def setup(wd):
    (wd / "compute.py").write_text(
        "def compute(a,b):\n"
        "    x=a*b\n"
        "    return x\n",
        encoding="utf-8",
    )


def _verify(wd):
    code = "from compute import compute; assert compute(3,4)==12; print('OK')"
    rc, out, err = run_python(wd, code=code)
    content = (wd / "compute.py").read_text(encoding="utf-8", errors="replace")
    has_doc = '"""' in content or "'''" in content
    has_hint = "->" in content
    ok = rc == 0 and "OK" in out and has_doc and has_hint
    return ok, f"rc={rc} out={out.strip()!r} doc={has_doc} hint={has_hint}"


TASK = {
    "id": "t08",
    "name": "函数重构（保留行为）",
    "prompt": "compute.py 里 compute(a,b) 返回 a*b，但缺少类型标注和 docstring、格式也很随意。请在保持函数名与行为不变的前提下重构：加上类型标注、docstring 并格式化代码。先用 ReadFile 确认内容。",
    "setup": setup,
    "verify": _verify,
}
