"""prompt 健康审计的回归测试 —— 防止审计自己失效。

核心场景只有一个：**全绿当完成信号的 prompt 必须被抓住**。
Batch Q 就用它抓到了 t22/t20 两个真实缺陷（初始就 5 passed / import 就能过）。
"""

import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from eval.prompt_audit import audit_task, extract_commands  # noqa: E402


def _task(prompt, setup_code=None, audit_ok=None):
    """临时造一个任务：setup 写一个必然通过的 test_sample.py。"""
    def setup(wd):
        (wd / "test_sample.py").write_text(
            "def test_ok():\n    assert True\n", encoding="utf-8"
        )
        if setup_code:
            (wd / "thing.py").write_text(setup_code, encoding="utf-8")

    t = {"id": "tmp", "name": "tmp", "prompt": prompt, "setup": setup}
    if audit_ok:
        t["audit_ok"] = audit_ok
    return t


def test_extract_only_command_spans():
    cmds = extract_commands("改 `cart/cart.py`，跑 `pytest tests -q`，别动 `apply_discount`")
    assert cmds == ["pytest tests -q"]


def test_green_on_start_is_caught():
    """pytest 开局全绿 + prompt 当完成判据 → 必须报 bad。"""
    task = _task("完成后运行 `pytest` 确认全部通过。")
    r = audit_task(task)
    assert r["level"] == "bad", r


def test_failing_command_is_legit():
    """初始就失败的命令是合法完成判据（t18 那种）。"""
    task = _task("请运行 `python -c \"raise SystemExit(1)\"` 修好它。")
    r = audit_task(task)
    assert r["level"] == "ok", r


def test_declared_exception_passes():
    """显式写进 audit_ok 的已知例外 → 不报错，但仍会出现在报告里。"""
    task = _task("完成后运行 `pytest` 确认不回归。", audit_ok=["pytest"])
    r = audit_task(task)
    assert r["level"] == "ok", r
    assert "audit_ok" in r["findings"][0][1]


def test_undeclared_exception_still_caught():
    """audit_ok 只豁免写进去的那条命令，不能顺手豁免别的。"""
    task = _task("跑 `pytest` 和 `pytest -q`。", audit_ok=["pytest"])
    r = audit_task(task)
    assert r["level"] == "bad", r
