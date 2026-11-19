"""泄漏审计的自测：三个必须区分的场景。

这三条对应校准过程中真实撞到的三类误报/漏报，写成测试是为了
**以后调阈值时立刻知道有没有调坏**。
"""

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "eval"))

from leak_audit import audit, _load_skills, _scaffold_text  # noqa: E402

SKILLS = _ROOT / "skills"


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _longest_skill_line(skill: str, min_len: int = 50) -> str:
    raw = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    body = raw.split("---", 2)[2] if raw.startswith("---") else raw
    lines = [ln.strip() for ln in body.splitlines() if len(ln.strip()) >= min_len]
    assert lines, f"{skill} 里找不到足够长的句子"
    return max(lines, key=len)


def test_flags_verbatim_skill_text(tmp_path):
    """产物里照抄了 skill 的原句 → 必须报。"""
    line = _longest_skill_line("canary-gate-policy")
    _write(tmp_path, "gate.py", f'"""说明。\n\n{line}\n"""\n')
    found = audit(tmp_path, _load_skills())
    assert found, "照抄了 skill 原句却没被检测到"
    assert max(f["k"] for f in found) >= 40


def test_ignores_common_boilerplate(tmp_path):
    """`with pytest.raises(ValueError):` 这种样板不能当泄漏。"""
    _write(tmp_path, "tests/test_x.py",
           "import pytest\n\n\n"
           "def test_a():\n"
           "    with pytest.raises(ValueError):\n"
           "        int('abc')\n")
    found = audit(tmp_path, _load_skills())
    assert not [f for f in found if f["k"] >= 40], f"样板代码被当成泄漏：{found}"


def test_scaffold_text_is_not_leakage(tmp_path):
    """脚手架自带、产物只是续写的句子，不算泄漏。

    原样复现 t35：脚手架写着「窗口从上一次被接受的上报开始算；被丢弃的重复请求不刷新窗口」，
    产物续了半句「否则一直重试的事件会永远刷新不了」——整段 40 字和 skill 重合，
    但其中 31 字是任务自己给的。
    """
    scaffold = _scaffold_text("t35")
    assert scaffold, "取不到 t35 的脚手架文本"

    sentence = ("窗口从**上一次被接受**的上报开始算；被丢弃的重复请求**不刷新**窗口，"
                "否则一直重试的事件会永远刷新不了")
    _write(tmp_path, "tracking/validator.py", f'"""{sentence}"""\n')
    found = audit(tmp_path, _load_skills(), scaffold)
    assert not [f for f in found if f["k"] >= 40], f"脚手架续写被误判成泄漏：{found}"
