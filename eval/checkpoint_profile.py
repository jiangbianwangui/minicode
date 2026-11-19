"""按检查点看 base 到底"会"多少 —— 主实验的一个已知盲区。

主实验用**二元判卷**（六条全对才算过）。这有个副作用：
一个把五条都猜对、只错一条的实现，和一个完全乱写的实现，
在统计表上是同一个数：0。

留出实验（`eval/HELDOUT.md`）就撞见了这件事——A 组和 B 组的通过率完全一样（0/3 vs 0/3），
拆到检查点却是 1.7/6 vs 4.2/6。既然那里能看见，主实验里大概率也有，
所以主结论里那句「base 恒为 0」需要分类讨论：

- 若 base 平均能过 3/6 → 「base 完全不会」不成立，应改成「base 会一部分，但从不过关」
- 若 base 平均只过 0.5/6 → 「恒为 0」这句话站得住

用法：
    python eval/checkpoint_profile.py --mode base  --tasks t27,t28,t29,t31,t33,t34,t35,t36,t37,t38
    python eval/checkpoint_profile.py --mode skill --tasks t27 --repeats 3
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

from runner import run_one, WORKSPACES     # noqa: E402
from tasks._util import run_python         # noqa: E402

_MODULES = {
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


def _checks(tid: str):
    return importlib.import_module(f"tasks.{_MODULES[tid]}")._CHECKS


def _bitmap(tid: str, wd: pathlib.Path) -> str:
    bits = []
    for _label, code in _checks(tid):
        rc, _o, _e = run_python(wd, code=code, timeout=60)
        bits.append("1" if rc == 0 else "0")
    return "".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["base", "skill"], default="base")
    ap.add_argument("--tasks", required=True, help="逗号分隔，如 t27,t28")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--max-iter", type=int, default=20)
    ap.add_argument("--tag", default="", help="追加到 workspace 后缀；目录里文件太多时删除会被沙箱拦，换个 tag 即可")
    args = ap.parse_args()

    tids = [t.strip() for t in args.tasks.split(",") if t.strip()]
    from tasks import TASKS
    by_id = {t["id"]: t for t in TASKS}

    rows = []
    for tid in tids:
        task = by_id[tid]
        nchk = len(_checks(tid))
        for r in range(1, args.repeats + 1):
            suffix = f"_cp_{args.mode}{r}{args.tag}"
            res = run_one(task, args.max_iter, skills=(args.mode == "skill"), ws_suffix=suffix)
            bit = _bitmap(tid, WORKSPACES / f"{tid}{suffix}")
            got = bit.count("1")
            rows.append({"tid": tid, "r": r, "bit": bit, "got": got, "n": nchk,
                         "passed": res["passed"], "tok": res["prompt_tokens"] + res["completion_tokens"],
                         "hits": res["skill_hits"]})
            print(f"[{tid} {args.mode} r{r}] {'PASS' if res['passed'] else 'FAIL'} "
                  f"{got}/{nchk} {bit} tok={rows[-1]['tok']} hits={res['skill_hits'] or '-'}",
                  flush=True)

    print("\n| 任务 | 检查点 | 通过率 | 位图 |")
    print("| --- | ---: | :---: | --- |")
    for row in rows:
        print(f"| {row['tid']} | {row['got']}/{row['n']} | {'✅' if row['passed'] else '❌'} | {row['bit']} |")

    tot_got = sum(r["got"] for r in rows)
    tot_n = sum(r["n"] for r in rows)
    print(f"\n合计：{tot_got}/{tot_n} 个检查点通过 "
          f"（平均 {tot_got / len(rows):.1f}/{tot_n // len(rows)}），"
          f"整体通过 {sum(r['passed'] for r in rows)}/{len(rows)}")


if __name__ == "__main__":
    main()
