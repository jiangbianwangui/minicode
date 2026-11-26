"""对历史 workspace 重跑判卷，重建逐次运行记录 —— **不调用 LLM**。

为什么需要它：

`STATS.md` 里那句 "base 0/31" 是手抄进表格的，而唯一的原始记录
`eval/EVAL*.md` 被 gitignore 了。所以 clone 之后，这个数字**没有任何东西可以核对**——
面试官问"你怎么知道是 0"时，只能打开本地目录给他看，而本地目录不在仓库里。

本脚本把"历史上真实跑过哪些次、每次判卷结果如何"固化成 `eval/runs/*.jsonl`
（几十 KB，入库），于是主结论可以一条命令重算：

    python eval/rebuild_runs.py --tasks t27,t28,t29,t33,t34,t35,t36,t37,t38

口径上的一个决定：**不去争"哪 31 次算数"**。
workspace 目录数是 46（比 STATS.md 记的 31 多），差额是零交付和废弃重跑，
而"哪一批算数"只存在于当时的报告里，无法从目录名确定。
所以这里把**所有存在的目录全部重判**：
- base 侧一个都不通过 → "0/31" 被 46 次运行托住，比精确复刻更硬；
- 有任何一个通过 → 说明判卷漂了或数字错了，必须查。

这不是统计替代品，是**审计**：它回答"这批产物今天再判一次，还是不是那个结果"。
"""

from __future__ import annotations

import argparse
import importlib
import json
import pathlib
import re
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

from tasks import TASKS                                    # noqa: E402
from tasks._util import run_python                         # noqa: E402

WORKSPACES = _HERE / "workspaces"
RUNS_DIR = _HERE / "runs"

_MAIN_TASKS = ["t27", "t28", "t29", "t33", "t34", "t35", "t36", "t37", "t38"]

_CHECKS_MODULES = {
    "t27": "t27_external_api_contract",
    "t28": "t28_mq_contract",
    "t29": "t29_log_standard",
    "t31": "t31_retry_budget",
    "t33": "t33_sla_budget",
    "t34": "t34_canary_gate",
    "t35": "t35_tracking_validate",
    "t36": "t36_rate_limit",
    "t37": "t37_backfill_planner",
    "t38": "t38_export_approval",
}

_DIR_RE = re.compile(r"^(t\d+)_(base|skill)_r\d+_")
_DIR_RE_CP = re.compile(r"^(t\d+)_cp_(base[123]b|skill[123])$")


def _checks(tid: str):
    mod = importlib.import_module(f"tasks.{_CHECKS_MODULES[tid]}")
    return getattr(mod, "_CHECKS", None)


def _bitmap(tid: str, wd: pathlib.Path, timeout: int) -> tuple[str, int]:
    checks = _checks(tid)
    if not checks:
        return "", 0
    bits = []
    for _label, code in checks:
        rc, _o, _e = run_python(wd, code=code, timeout=timeout)
        bits.append("1" if rc == 0 else "0")
    return "".join(bits), len(checks)


def discover(tasks: list[str], groups: list[str],
             batch: str = "main") -> list[tuple[str, str, pathlib.Path]]:
    """返回 [(task, group, 目录)]，按任务、组、目录名排序。

    batch="main"：主对照的 `tXX_base_rN_<时间戳>` / `tXX_skill_rN_<时间戳>`
    batch="cp"  ：检查点剖析的 `tXX_cp_base{N}b` / `tXX_cp_skill{N}`

    cp 批次为什么精确到数字：
    - base 只认 `base1b/base2b/base3b` —— `CHECKPOINTS.md` 里那条命令带了 `--tag b`；
      无 b 的 `_cp_base1` 是更早一次试跑，不在统计内。
    - skill 只认 `skill1/2/3` —— 排除 `skill1sc`（2026-11-25 的自检实验，条件已变）。
    **规则先于数据**：先按当时的命令确定哪批算数，再跑，而不是看结果挑。
    """
    pat = _DIR_RE_CP if batch == "cp" else _DIR_RE
    out = []
    for d in sorted(WORKSPACES.iterdir()):
        if not d.is_dir():
            continue
        m = pat.match(d.name)
        if not m:
            continue
        if batch == "cp":
            tid, who = m.group(1), m.group(2)
            group = "base" if who.startswith("base") else "skill"
        else:
            tid, group = m.group(1), m.group(2)
        if tid in tasks and group in groups:
            out.append((tid, group, d))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", default=",".join(_MAIN_TASKS))
    ap.add_argument("--groups", default="base,skill")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--out", default="")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 个，用于试跑")
    ap.add_argument("--batch", choices=["main", "cp"], default="main",
                    help="main=主对照 76 个目录；cp=检查点剖析 54 个目录")
    args = ap.parse_args()

    tasks = [t.strip() for t in args.tasks.split(",") if t.strip()]
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]
    by_id = {t["id"]: t for t in TASKS}

    targets = discover(tasks, groups, batch=args.batch)
    if args.limit:
        targets = targets[: args.limit]
    if not targets:
        print("没找到匹配的 workspace 目录。", file=sys.stderr)
        return 2

    print(f"发现 {len(targets)} 个目录，开始重判（不调用 LLM，纯本地判卷）\n")
    rows = []
    t_start = time.time()

    for i, (tid, group, wd) in enumerate(targets, 1):
        task = by_id[tid]
        t0 = time.time()
        try:
            passed, detail = task["verify"](wd)
            status = "ok"
        except Exception as exc:                       # 目录损坏 / 判卷异常
            passed, detail, status = False, f"{type(exc).__name__}: {exc}", "error"
        bit, nchk = _bitmap(tid, wd, args.timeout) if status == "ok" else ("", 0)
        got = bit.count("1")
        dt = time.time() - t0
        rows.append({
            "task": tid,
            "group": group,
            "dir": wd.name,
            "passed": bool(passed),
            "status": status,
            "bitmap": bit,
            "checks_passed": got,
            "checks_total": nchk,
            "seconds": round(dt, 1),
            "detail": str(detail)[:200],
        })
        flag = "PASS" if passed else "FAIL"
        print(f"[{i:>3}/{len(targets)}] {tid} {group:<5} {flag} "
              f"{got}/{nchk} {bit or '-':<8} {dt:4.1f}s  {wd.name}", flush=True)

    elapsed = time.time() - t_start
    print(f"\n判卷完成，用时 {elapsed:.1f}s\n")

    for group in groups:
        sub = [r for r in rows if r["group"] == group]
        if not sub:
            continue
        ok = sum(1 for r in sub if r["passed"])
        err = sum(1 for r in sub if r["status"] != "ok")
        print(f"{group:<6} 通过 {ok}/{len(sub)}"
              + (f"（其中 {err} 个判卷异常）" if err else ""))
        per_task = {}
        for r in sub:
            a, b = per_task.get(r["task"], (0, 0))
            per_task[r["task"]] = (a + (1 if r["passed"] else 0), b + 1)
        for tid in sorted(per_task):
            a, b = per_task[tid]
            print(f"    {tid}  {a}/{b}")

    if args.out:
        out = pathlib.Path(args.out)
    elif args.limit:
        out = RUNS_DIR / "smoke.jsonl"
    elif args.batch == "cp":
        out = RUNS_DIR / "checkpoints_9task.jsonl"
    else:
        out = RUNS_DIR / "main_9task.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    try:
        shown = out.resolve().relative_to(_ROOT)
    except ValueError:
        shown = out
    print(f"\n已写入 {shown}（{len(rows)} 条）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
