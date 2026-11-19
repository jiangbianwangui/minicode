"""留出任务的三组对照（A 什么都不给 / B 现有库 / C 专属 skill）。

为什么要有脚本，而不是手动跑：

C 组要把参考 skill 临时复制进 `skills/`，让路由能召回它。手动做这件事，
**忘了移回来就会污染之后所有 base 运行**——t38 已经因此被击穿过一次
（base 读到了刚写进去的 skill，0/3 变成"通过"）。
所以这里用 try/finally 保证恢复，并且在结束时断言目录已清干净。

用法：
    python eval/run_heldout.py --task t40 --repeats 3
    python eval/run_heldout.py --task t39 --repeats 3     # 复跑旧题做对照
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_HERE))

from runner import run_one          # noqa: E402
from runner import WORKSPACES       # noqa: E402
from tasks import TASKS             # noqa: E402
from tasks import t39_alert_mute, t40_access_elevation  # noqa: E402
from tasks._util import run_python  # noqa: E402

SKILLS_DIR = _ROOT / "skills"
HELDOUT_DIR = _HERE / "heldout"

# 留出任务的检查点清单是模块私有的（_CHECKS）。做对照时必须逐条跑：
# verify() 只报第一个失败点，看不出"错在哪几处"，而**内部结构的变化比总分重要**
# ——t39 的 B 组总分没变，但把直通挪对了位置。
_CHECKS_BY_ID = {
    "t39": t39_alert_mute._CHECKS,
    "t40": t40_access_elevation._CHECKS,
}


def _frontmatter_name(md: pathlib.Path) -> str:
    """从 SKILL.md 的 frontmatter 里取 name（loader 用的就是这个）。"""
    for ln in md.read_text(encoding="utf-8").splitlines():
        if ln.startswith("name:"):
            return ln.split(":", 1)[1].strip()
    raise ValueError(f"{md} 没有 name 字段")


def _check_bitmap(task_id: str, wd: pathlib.Path) -> str:
    """逐条跑行为检查，返回形如 '100100' 的通过位图（1 = 通过）。"""
    bits = []
    for _label, code in _CHECKS_BY_ID[task_id]:
        rc, _out, _err = run_python(wd, code=code, timeout=60)
        bits.append("1" if rc == 0 else "0")
    return "".join(bits)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task", default="t40", choices=sorted(_CHECKS_BY_ID))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-iter", type=int, default=12)
    args = ap.parse_args()

    task = next((t for t in TASKS if t["id"] == args.task), None)
    if task is None:
        sys.exit(f"任务 {args.task} 未注册")

    checks = _CHECKS_BY_ID[args.task]

    ref_md = HELDOUT_DIR / f"{args.task}_reference_skill.md"
    if not ref_md.exists():
        sys.exit(f"缺少参考 skill：{ref_md}")
    ref_name = _frontmatter_name(ref_md)
    ref_dir = SKILLS_DIR / ref_name

    if ref_dir.exists():
        sys.exit(f"⛔ {ref_dir} 已存在——先确认是不是上次没清干净，不要带着它跑 A/B 组")

    rows = []
    groups = [("A", False), ("B", True), ("C", True)]

    for gname, use_skills in groups:
        for r in range(1, args.repeats + 1):
            injected = False
            bit = "?"
            try:
                if gname == "C":
                    ref_dir.mkdir(parents=True)
                    shutil.copyfile(ref_md, ref_dir / "SKILL.md")
                    injected = True
                res = run_one(
                    task, args.max_iter, skills=use_skills,
                    ws_suffix=f"_ho_{gname}{r}",
                )
                # 位图必须在 workspace 还在时算
                bit = _check_bitmap(args.task, WORKSPACES / f"{task['id']}_ho_{gname}{r}")
            finally:
                if injected:
                    shutil.rmtree(ref_dir, ignore_errors=True)

            rows.append({
                "g": gname, "r": r, "passed": res["passed"],
                "bit": bit, "detail": res["detail"][:60],
                "tok": res["prompt_tokens"] + res["completion_tokens"],
                "sec": res["seconds"], "hits": res["skill_hits"],
                "loaded": res["skill_loaded"],
            })
            print(
                f"[{gname}{r}] {'PASS' if res['passed'] else 'FAIL'} "
                f"checks={bit} tok={rows[-1]['tok']} {res['seconds']}s "
                f"hits={res['skill_hits'] or '-'} loaded={res['skill_loaded'] or '-'}",
                flush=True,
            )
            if not res["passed"]:
                print(f"      {res['detail'][:150]}", flush=True)

    if ref_dir.exists():
        sys.exit("⛔ 参考 skill 目录残留，请手动删除后再跑")

    labels = [c[0] for c in checks]
    print("\n## 逐检查点（1 = 通过）\n")
    print("| 组 | 轮 | 通过 | " + " | ".join(f"C{i + 1}" for i in range(len(labels))) + " |")
    print("| --- | ---: | :---: | " + " | ".join(":---:" for _ in labels) + " |")
    for row in rows:
        cells = " | ".join(row["bit"])
        print(f"| {row['g']} | {row['r']} | {'✅' if row['passed'] else '❌'} | {cells} |")
    print("\n检查点：")
    for i, lb in enumerate(labels):
        print(f"  C{i + 1} {lb}")

    print("\n## 汇总\n")
    for gname, _ in groups:
        g = [r for r in rows if r["g"] == gname]
        print(f"{gname}: {sum(r['passed'] for r in g)}/{len(g)}")
    print(f"\nskills/ 目录已恢复：{'是' if not ref_dir.exists() else '否（请手动清理）'}")


if __name__ == "__main__":
    main()
