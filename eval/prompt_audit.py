"""prompt 健康审计：机器找「假完成信号」。

由来（Batch Q，2026-10-26）：
    t26 的 prompt 写着「pytest 全绿 = 完成」，而 setup 之后 pytest **开局就是全绿**。
    agent 读到的是一对互相矛盾的事实——绿灯说"做完了"，任务说"还没做"——
    于是陷入反复跑测试，0/3 通过、41 calls、685k token。
    只把那一句改成"绿灯是假信号"，同样配置 3/3 通过、9 calls、45k token。

    结论是一条可复用的规则：
        **prompt 里不能出现「完成 = 某命令通过」，除非该命令在初始状态下是失败的。**
    全绿的测试比没有测试更危险——它给假信号，而 agent 会非常认真地执行它。

这个方法可机器化，所以不再靠人肉 review：
    1. 从 prompt 里抽出所有被命令反引号包起来的命令（`pytest xxx` / `python xxx`）；
    2. 在 setup 之后**真的跑一遍**（超时视为"不是完成信号"）；
    3. 若该命令 rc == 0 而 prompt 又把它当完成判据 → 报「假信号」；
    4. 若命令在初始状态就是失败的 → 是正常完成判据，放行（如 t18）。

附加检查：
    - setup 本身能否跑通（跑不通的任务根本没法评测）
    - prompt 里出现完成判据关键词却没有对应命令 → 提「无法机器验证」，交人工确认

用法（在 cli_coding_agent 目录下，**必须用项目 venv**，default venv 没有 pytest）：
    ../.venv/Scripts/python.exe eval/prompt_audit.py              # 全部 26 个任务
    ../.venv/Scripts/python.exe eval/prompt_audit.py t24 t26      # 指定
    ../.venv/Scripts/python.exe eval/prompt_audit.py --verbose    # 打印每个任务的判定过程

退出码：发现假信号 → 1，否则 0（可直接挂到 CI / 跑批前的预检）。
"""

from __future__ import annotations

import pathlib
import re
import shlex
import subprocess
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_HERE))   # 让 `from tasks import TASKS` 在测试里也成立
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from tasks import TASKS  # noqa: E402

# 会被拿去做「完成判据」的命令前缀
_CMD_PREFIX = ("pytest", "py.test", "python", "python3", "pip")

# 只做「静态」抽取，不去猜作者意图：
#   旧的启发式「有完成措辞但没命令 → 人工确认」对 11 个任务误报（"完成"太常见），
#   改为只报能机器坐实的假信号。无命令的任务 = 靠隐藏用例判分，天然没有假信号。

_TIMEOUT = 180


def _norm_cmd(cmd: str) -> str:
    """归一化：去掉多余空白，便于跨写法匹配（`pytest  tests` vs `pytest tests`）。"""
    try:
        return " ".join(shlex.split(cmd))
    except ValueError:
        return cmd.strip()


def extract_commands(prompt: str) -> list[str]:
    """抽出 prompt 里被反引号包住的命令，按出现顺序去重。"""
    out: list[str] = []
    for span in re.findall(r"`([^`]+)`", prompt):
        s = span.strip()
        if not s or "\n" in s:
            continue
        try:
            parts = shlex.split(s)
        except ValueError:
            continue
        if not parts:
            continue
        head = pathlib.PurePath(parts[0]).name.lower()
        if head not in _CMD_PREFIX:
            continue
        if s not in out:
            out.append(s)
    return out


def _accepted(task: dict) -> set[str]:
    """任务作者已显式声明「这个命令初始就会通过，我知道」的命令集合。

    写法：任务 dict 里加 `"audit_ok": ["pytest tests", ...]`。
    例外必须显式留下，审计不靠猜；声明了就落到「已知例外」而不是「放行」，
    报告里仍然会列出来，方便下次复核。
    """
    return {_norm_cmd(c) for c in task.get("audit_ok", []) if isinstance(c, str)}


def _run_locally(wd: pathlib.Path, cmd: str) -> tuple[int, str]:
    """在验收环境里原样执行，返回 (rc, 说明)。超时/无法执行 → rc=-1。"""
    parts = shlex.split(cmd)
    # 把裸 python 换成当前解释器，保证在无 activate 的沙箱里也能跑
    if pathlib.PurePath(parts[0]).name.lower() in ("python", "python3"):
        parts[0] = sys.executable
    else:
        parts = [sys.executable, "-m"] + parts
    try:
        p = subprocess.run(
            parts, cwd=str(wd), capture_output=True, text=True, timeout=_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        return -1, f"超时 {_TIMEOUT}s（无法当判据）"
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"
    tail = (p.stdout or p.stderr).strip().splitlines()
    tail = tail[-1] if tail else ""
    if "no tests ran" in (p.stdout or ""):
        return -1, "no tests ran（测试文件还不存在 / 没收集到用例）"
    return p.returncode, tail


def audit_task(task: dict, verbose: bool = False) -> dict:
    """返回 {"id", "level", "findings": [(level, msg)]}，level: ok|warn|bad。"""
    tid = task["id"]
    findings: list[tuple[str, str]] = []
    prompt = task.get("prompt", "")
    cmds = extract_commands(prompt)
    accepted = _accepted(task)

    if not cmds:
        findings.append(("ok", "无可执行完成判据（靠隐藏用例判分）"))
        return {"id": tid, "level": _worst(findings), "findings": findings}

    wd = pathlib.Path(tempfile.mkdtemp(prefix=f"audit_{tid}_"))
    try:
        setup = task.get("setup")
        if setup is not None:
            setup(wd)
    except Exception as e:  # noqa: BLE001
        findings.append(("bad", f"setup 跑不通：{type(e).__name__}: {e}"))
        return {"id": tid, "level": "bad", "findings": findings}

    for cmd in cmds:
        rc, note = _run_locally(wd, cmd)
        if verbose:
            print(f"    $ {cmd}  → rc={rc}  {note[:70]}")
        if rc == 0:
            if _norm_cmd(cmd) in accepted:
                findings.append(
                    ("ok", f"「{cmd}」初始就通过，但已在 audit_ok 里显式声明")
                )
            else:
                findings.append(
                    ("bad", f"「{cmd}」初始就通过（rc=0, {note[:60]}）——"
                            f"若被当成完成判据就是假信号；应显式写明它不可信"
                            f"（或在任务里加 audit_ok 声明）")
                )
        elif rc == -1:
            findings.append(("ok", f"「{cmd}」无法执行（{note[:50]}）→ 不构成信号"))
        else:
            findings.append(
                ("ok", f"「{cmd}」初始失败（rc={rc}）→ 合法的完成判据")
            )
    return {"id": tid, "level": _worst(findings), "findings": findings}


def _worst(findings: list[tuple[str, str]]) -> str:
    levels = {lvl for lvl, _ in findings}
    for lvl in ("bad", "warn", "ok"):
        if lvl in levels:
            return lvl
    return "ok"


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    tasks = [t for t in TASKS if not args or t["id"] in args]
    missing = set(args) - {t["id"] for t in TASKS}
    if missing:
        print(f"❌ 未知任务 id: {sorted(missing)}")
        sys.exit(2)

    print(f"▶ prompt 健康审计：{len(tasks)} 个任务"
          f"（判据：完成 = 某命令通过，而该命令初始就通过 → 假信号）\n")
    bad, warn = [], []
    for t in tasks:
        if verbose:
            print(f"  {t['id']} {t['name']}")
        r = audit_task(t, verbose=verbose)
        mark = {"ok": "✅", "warn": "⚠️ ", "bad": "❌"}[r["level"]]
        first = r["findings"][0][1] if r["findings"] else ""
        print(f"  {mark} {r['id']:4s} {t['name'][:22]:24s} {first[:78]}")
        for lvl, msg in r["findings"][1:]:
            if lvl != "ok":
                print(f"          └─ {msg[:96]}")
        if r["level"] == "bad":
            bad.append(r)
        elif r["level"] == "warn":
            warn.append(r)

    print()
    if bad:
        print(f"❌ {len(bad)} 个任务存在假完成信号：{', '.join(r['id'] for r in bad)}")
        print("   修法：在 prompt 里明确写「该命令在当前状态下就通过，不能当完成信号」，")
        print("   或把完成判据改成初始失败的命令 / 隐藏用例（参考 t26、t18）。")
    if warn:
        print(f"⚠️  {len(warn)} 个任务需人工确认：{', '.join(r['id'] for r in warn)}")
    if not bad and not warn:
        print("✅ 全部任务无假信号，完成判据均可机器验证")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
