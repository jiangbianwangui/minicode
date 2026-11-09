from pathlib import Path

from ._util import run_python


def setup(wd):
    (wd / "a.py").write_text(
        "import json\n\n"
        "def read_config(path):\n"
        "    with open(path, 'r', encoding='utf-8') as f:\n"
        "        return json.load(f)\n\n"
        "def do_a():\n    return read_config('a.json')\n",
        encoding="utf-8",
    )
    (wd / "b.py").write_text(
        "import json\n\n"
        "def read_config(path):\n"
        "    with open(path, 'r', encoding='utf-8') as f:\n"
        "        return json.load(f)\n\n"
        "def do_b():\n    return read_config('b.json')\n",
        encoding="utf-8",
    )


def _verify(wd):
    common_txt = (wd / "common.py").read_text(encoding="utf-8", errors="replace")
    a_txt = (wd / "a.py").read_text(encoding="utf-8", errors="replace")
    b_txt = (wd / "b.py").read_text(encoding="utf-8", errors="replace")
    has_common_def = "def read_config" in common_txt
    a_dup = "def read_config" in a_txt
    b_dup = "def read_config" in b_txt
    rc, out, err = run_python(
        wd,
        code="from common import read_config\nimport a, b\nassert a.do_a is not None\nassert b.do_b is not None\nprint('OK')",
    )
    ok = has_common_def and (not a_dup) and (not b_dup) and rc == 0 and "OK" in out
    return ok, f"common_def={has_common_def} a_dup={a_dup} b_dup={b_dup} rc={rc} out={out.strip()!r}"


TASK = {
    "id": "t15",
    "name": "跨文件提取公共逻辑",
    "prompt": (
        "a.py 和 b.py 各自定义了完全相同的 `read_config(path)` 函数（读取 json 文件）。"
        "请把这份重复逻辑抽取到新建的 `common.py` 中，并让 a.py 和 b.py 改为从 common 导入 "
        "read_config（删掉各自的副本）。抽取后 a.do_a() 和 b.do_b() 仍能正常被导入。"
    ),
    "setup": setup,
    "verify": _verify,
}
