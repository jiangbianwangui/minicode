"""t21：表达式求值（含边界用例） —— 考验「真算法 + 迭代调试」。

难度来源：必须实现真正的优先级/括号/一元负号解析，靠 eval() 之外的思路或靠猜都过不了；
负号、空格、浮点除法这几类边界是常见翻车点。
"""

import subprocess
import sys

from ._util import run_python

# 隐藏用例：覆盖优先级、括号、一元负号、空白、浮点除法、嵌套
_CASES = [
    ("1+2*3", 7),
    ("(1+2)*3", 9),
    ("-5+3", -2),
    ("2*(3+4)/7", 2.0),
    (" 1 +  2 ", 3),
    ("10/4", 2.5),
    ("((1+2)*(3+4))", 21),
    ("-(2+3)", -5),
    ("1.5*4", 6.0),
]


def _verify(wd):
    if not (wd / "expr_eval.py").exists():
        return False, "缺少 expr_eval.py"

    lines = ["from expr_eval import eval_expr"]
    for src, want in _CASES:
        lines.append(f"assert abs(eval_expr({src!r}) - {want}) < 1e-9, {src!r}")
    lines.append("print('OK')")
    rc, out, err = run_python(wd, code="\n".join(lines))
    if rc != 0 or "OK" not in out:
        msg = (err or out).strip()[-300:]
        return False, f"隐藏用例未通过: {msg!r}"

    if not (wd / "test_expr_eval.py").exists():
        return False, "缺少 test_expr_eval.py（要求自带测试）"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_expr_eval.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"自带测试未通过 rc={p.returncode} {tail!r}"

    return True, f"{len(_CASES)} 个隐藏用例全过且自带 pytest 通过"


TASK = {
    "id": "t21",
    "name": "表达式求值+边界用例",
    "prompt": (
        "在 `expr_eval.py` 里实现 `eval_expr(s: str) -> float`，对四则运算表达式求值。\n"
        "要求：支持 `+ - * /`、括号 `()`、一元负号（如 `-5+3`、`-(2+3)`）、"
        "任意空白、小数与浮点除法（`10/4` 应为 2.5），并正确处理运算优先级。\n"
        "请自己实现解析逻辑（不要直接把整个字符串交给 eval 处理未校验的输入）。\n"
        "最后创建 `test_expr_eval.py` 用 pytest 覆盖上述情形并运行确认通过。"
    ),
    "setup": None,
    "verify": _verify,
}
