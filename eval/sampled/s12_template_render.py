"""s12 简单模板渲染（占位符替换，规则全给）。

抽样任务（见 eval/SAMPLED.md）：规格全给，不需要仓库外知识。
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tasks._util import run_python  # noqa: E402

_STUB = '''"""把模板里的占位符替换成变量值。"""


def render(template: str, variables: dict) -> str:
    """把 {{name}} 替换成 variables[name]。

    - 占位符两侧允许有空格：{{ name }} 与 {{name}} 等价；
    - name 必须是标识符（字母或下划线开头，只含字母数字下划线）；
    - variables 里没有 name → 抛 KeyError（异常消息里要包含该变量名）；
    - 出现无法解析的 {{ 或 }}（比如未闭合）→ 抛 ValueError；
    - 变量值一律用 str() 转成字符串。

    ⚠️ 还没实现。
    """
    raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：单变量与无变量。"""

from text.template import render


def test_single_placeholder():
    assert render("你好 {{name}}", {"name": "老王"}) == "你好 老王"


def test_no_placeholder():
    assert render("原样返回", {}) == "原样返回"
'''

_MULTI = '''
from text.template import render

got = render("{{a}}-{{b}}-{{a}}", {"a": "x", "b": "y"})
assert got == "x-y-x", f"期望 'x-y-x'，实际 {got!r}"
'''

_SPACES = '''
from text.template import render

assert render("{{ name }}", {"name": "v"}) == "v"
assert render("{{name }}", {"name": "v"}) == "v"
assert render("{{  name}}", {"name": "v"}) == "v"
'''

_MISSING = '''
from text.template import render

try:
    render("hi {{who}}", {})
except KeyError as e:
    assert "who" in str(e), f"KeyError 消息里要包含变量名，实际 {str(e)!r}"
else:
    raise AssertionError("变量缺失时应抛 KeyError")
'''

_UNCLOSED = '''
from text.template import render

for bad in ("hi {{name", "hi name}}", "{{1bad}}", "{{ }}"):
    try:
        render(bad, {"name": "v"})
    except ValueError:
        continue
    raise AssertionError(f"{bad!r} 含无法解析的占位符，应抛 ValueError")
'''

_TO_STR = '''
from text.template import render

got = render("n={{n}}", {"n": 42})
assert got == "n=42", f"值要用 str() 转换，实际 {got!r}"
'''

_CHECKS = [
    ("多占位符与重复使用", _MULTI),
    ("两侧空格容错", _SPACES),
    ("缺失变量抛 KeyError（含变量名）", _MISSING),
    ("未闭合/非法占位符抛 ValueError", _UNCLOSED),
    ("值统一 str()", _TO_STR),
]


def setup(wd):
    (wd / "text").mkdir(parents=True, exist_ok=True)
    (wd / "text" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "text" / "template.py").write_text(_STUB, encoding="utf-8")
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "tests" / "test_template.py").write_text(_TESTS, encoding="utf-8")


def verify(wd):
    f = wd / "tests" / "test_template.py"
    if not f.exists():
        return False, "缺少 tests/test_template.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_template.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试失败 {tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code, timeout=60)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            msg = detail[-1] if detail else ""
            return False, f"未满足：{label} —— {msg}"
    return True, "5 项行为符合需求"


SOLUTION = '''"""把模板里的占位符替换成变量值。"""
import re

_TOKEN = re.compile(r"\\{\\{\\s*([A-Za-z_][A-Za-z0-9_]*)\\s*\\}\\}")


def render(template, variables):
    def repl(m):
        name = m.group(1)
        if name not in variables:
            raise KeyError(name)
        return str(variables[name])

    out = _TOKEN.sub(repl, template)
    if "{{" in out or "}}" in out:
        raise ValueError(f"存在无法解析的占位符: {template!r}")
    return out
'''

_MUT_EMPTY_ON_MISSING = SOLUTION.replace('raise KeyError(name)', 'return ""')

_MUT_NO_SPACE_TOLERANCE = SOLUTION.replace(
    r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*",
    r"([A-Za-z_][A-Za-z0-9_]*)",
)

MUTANTS = {
    "缺失变量替换成空串（不报错）": _MUT_EMPTY_ON_MISSING,
    "不支持占位符两侧空格": _MUT_NO_SPACE_TOLERANCE,
}

TASK = {
    "id": "s12",
    "name": "简单模板渲染",
    "prompt": (
        "在 text/template.py 里实现 render(template, variables)。\n"
        "需求如下（规格已给全，不需要查任何外部文档）：\n"
        "- 把 {{name}} 替换成 variables[name]；占位符两侧允许有空格"
        "（{{ name }} 与 {{name}} 等价）。\n"
        "- name 必须是标识符：字母或下划线开头，只含字母数字下划线。\n"
        "- variables 里没有 name → 抛 KeyError，异常消息里要包含该变量名。\n"
        "- 出现无法解析的 {{ 或 }}（比如未闭合、{{1bad}}、{{ }}）→ 抛 ValueError。\n"
        "- 变量值一律用 str() 转成字符串。\n"
        "既有测试在 tests/test_template.py，必须保持原样并通过。\n"
        "改完自己跑一遍 `python -m pytest tests -q` 确认。"
    ),
    "setup": setup,
    "verify": verify,
}
