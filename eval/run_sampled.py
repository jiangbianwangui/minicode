"""跑随机抽样任务集（阴性对照）。

用法：
    python eval/run_sampled.py --selftest
    python eval/run_sampled.py --modes base skill --runs 3 --out eval/EVAL.sampled.md

为什么单独一个入口：
    这 5 题**不进** eval/tasks 的主注册表，也不进 10 题的配对统计。
    它们回答的是另一个问题：「规格给全时，base 天然能过多少」。
    混进主表会让「10 题 base 全 0」这个结论看起来又多了 5 个样本，是错的。
"""

from __future__ import annotations

import argparse
import datetime
import pathlib
import shutil
import statistics
import sys
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

from runner import run_one  # noqa: E402
from sampled import SOLUTIONS, TARGETS, TASKS  # noqa: E402

MODE_FLAGS = {"base": {}, "skill": {"skills": True}}


def selftest() -> int:
    """参考解必须能过；每个变异必须被拦。任一条不成立就退出码 1。"""
    bad = 0
    for t in TASKS:
        tid = t["id"]
        solution, mutants = SOLUTIONS[tid]
        target = TARGETS[tid]

        wd = pathlib.Path(tempfile.mkdtemp(prefix=f"self_{tid}_"))
        try:
            t["setup"](wd)
            (wd / target).write_text(solution, encoding="utf-8")
            ok, detail = t["verify"](wd)
            if not ok:
                print(f"[FAIL] {tid} 参考解未通过：{detail}")
                bad += 1
            else:
                print(f"[ok]   {tid} 参考解通过")

            for name, src in mutants.items():
                wd2 = pathlib.Path(tempfile.mkdtemp(prefix=f"mut_{tid}_"))
                try:
                    t["setup"](wd2)
                    (wd2 / target).write_text(src, encoding="utf-8")
                    ok2, detail2 = t["verify"](wd2)
                    if ok2:
                        print(f"[FAIL] {tid} 变异未被拦：{name}")
                        bad += 1
                    else:
                        print(f"[ok]   {tid} 变异被拦：{name}")
                finally:
                    shutil.rmtree(wd2, ignore_errors=True)
        finally:
            shutil.rmtree(wd, ignore_errors=True)

    print()
    print("全部通过" if bad == 0 else f"{bad} 项失败")
    return 0 if bad == 0 else 1


def run(modes: list[str], runs: int, max_iter: int, ids: list[str] | None) -> list[dict]:
    tasks = [t for t in TASKS if not ids or t["id"] in ids]
    rows = []
    for mode in modes:
        flags = MODE_FLAGS[mode]
        for rep in range(runs):
            suffix = f"_smpl_{mode}_r{rep}_{datetime.datetime.now().strftime('%m%d_%H%M%S')}"
            for t in tasks:
                r = run_one(t, max_iter, ws_suffix=suffix, **flags)
                r["mode"] = mode
                r["rep"] = rep
                rows.append(r)
                tokens = r.get("prompt_tokens", 0) + r.get("completion_tokens", 0)
                print(
                    f"{mode:5s} r{rep} {r['id']} "
                    f"{'PASS' if r['passed'] else 'FAIL'} "
                    f"tok={tokens} calls={r['llm_calls']} "
                    f"hits={r.get('skill_hits') or '-'}",
                    flush=True,
                )
    return rows


def _total(r: dict) -> int:
    return r.get("prompt_tokens", 0) + r.get("completion_tokens", 0)


def report(rows: list[dict], modes: list[str], runs: int) -> str:
    lines = ["# 随机抽样任务集（阴性对照）", ""]
    lines.append(f"生成：{datetime.datetime.now():%Y-%m-%d %H:%M} | 每题每模式 {runs} 次")
    lines.append("")

    # 逐任务通过率
    head = "| 任务 | " + " | ".join(f"{m} 通过" for m in modes) + " |"
    sep = "| --- | " + " | ".join("---:" for _ in modes) + " |"
    lines += [head, sep]
    for t in TASKS:
        cells = []
        for m in modes:
            sub = [r for r in rows if r["id"] == t["id"] and r["mode"] == m]
            cells.append(f"{sum(r['passed'] for r in sub)} / {len(sub)}")
        lines.append(f"| {t['id']} {t['name']} | " + " | ".join(cells) + " |")

    # 合计
    cells = []
    for m in modes:
        sub = [r for r in rows if r["mode"] == m]
        cells.append(f"**{sum(r['passed'] for r in sub)} / {len(sub)}**")
    lines.append("| **合计** | " + " | ".join(cells) + " |")
    lines.append("")

    # token
    lines += ["## token（每题各次总和的均值）", "", "| 任务 | " + " | ".join(modes) + " |",
              "| --- | " + " | ".join("---:" for _ in modes) + " |"]
    for t in TASKS:
        cells = []
        for m in modes:
            sub = [_total(r) for r in rows if r["id"] == t["id"] and r["mode"] == m]
            cells.append(f"{statistics.mean(sub):.0f}" if sub else "-")
        lines.append(f"| {t['id']} | " + " | ".join(cells) + " |")
    lines.append("")

    # 若两模式都有，算配对差
    if len(modes) == 2:
        a, b = modes
        lines += [f"## 配对差（{b} − {a}）", "", "| 任务 | token 差 | 通过差 |",
                  "| --- | ---: | --- |"]
        diffs = []
        for t in TASKS:
            ta = [statistics.mean([_total(r) for r in rows if r["id"] == t["id"] and r["mode"] == a])]
            tb = [statistics.mean([_total(r) for r in rows if r["id"] == t["id"] and r["mode"] == b])]
            if ta[0] and tb[0]:
                d = tb[0] - ta[0]
                diffs.append(d)
                pa = sum(r["passed"] for r in rows if r["id"] == t["id"] and r["mode"] == a)
                pb = sum(r["passed"] for r in rows if r["id"] == t["id"] and r["mode"] == b)
                lines.append(f"| {t['id']} | {d:+.0f} | {pb - pa:+d} |")
        if diffs:
            lines.append("")
            lines.append(f"token 差值：均值 {statistics.mean(diffs):+.0f}，"
                         f"区间 [{min(diffs):+.0f}, {max(diffs):+.0f}]，"
                         f"同向 {sum(1 for d in diffs if d > 0)}/{len(diffs)} 为正")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--modes", nargs="+", default=["base", "skill"], choices=list(MODE_FLAGS))
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--max-iter", type=int, default=20)
    ap.add_argument("--ids", nargs="+", default=None)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    if args.selftest:
        sys.exit(selftest())

    rows = run(args.modes, args.runs, args.max_iter, args.ids)
    md = report(rows, args.modes, args.runs)
    if args.out:
        pathlib.Path(args.out).write_text(md, encoding="utf-8")
        print(f"\n写入 {args.out}")
    else:
        print()
        print(md)


if __name__ == "__main__":
    main()
