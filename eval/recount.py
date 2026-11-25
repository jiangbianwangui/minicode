"""校验 STATS.md 第一节表格：逐行数据之和 是否等于「合计」行。

为什么需要它：
主结论的分母（30 次 / 57 次）是手工抄进 STATS.md 的，抄的时候没有东西守着。
这份仓库里已经出现过 4 次数字漂移（README 任务数、单测数、自检条数、检查点比例），
所以凡是「能加出来的数」都应该有脚本能重算。

用法：
    python eval/recount.py            # 校验，不一致则退出码 1
    python eval/recount.py --fix      # 仅打印建议值，不写文件
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

_HERE = pathlib.Path(__file__).resolve().parent

_ROW = re.compile(
    r"^\|\s*(t\d+)[^|]*\|\s*\*{0,2}(\d+)\s*/\s*(\d+)\*{0,2}\s*"
    r"\|\s*\*{0,2}(\d+)\s*/\s*(\d+)\*{0,2}\s*\|",
    re.M,
)
_TOTAL = re.compile(
    r"^\|\s*\*\*合计\*\*\s*\|\s*\*\*(\d+)\s*/\s*(\d+)\*\*\s*\|\s*\*\*(\d+)\s*/\s*(\d+)\*\*",
    re.M,
)


def parse_stats(text: str) -> tuple[list[tuple], tuple | None]:
    rows = [
        (tid, int(b), int(bn), int(s), int(sm))
        for tid, b, bn, s, sm in _ROW.findall(text)
    ]
    m = _TOTAL.search(text)
    total = tuple(int(x) for x in m.groups()) if m else None
    return rows, total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", default=str(_HERE / "STATS.md"))
    ap.add_argument("--fix", action="store_true")
    args = ap.parse_args()

    path = pathlib.Path(args.path)
    text = path.read_text(encoding="utf-8")
    rows, total = parse_stats(text)

    if not rows:
        print(f"[recount] {path.name}: 没解析到逐题行 —— 表格格式变了？", file=sys.stderr)
        return 2

    got_base = sum(r[1] for r in rows)
    got_base_n = sum(r[2] for r in rows)
    got_skill = sum(r[3] for r in rows)
    got_skill_n = sum(r[4] for r in rows)
    computed = (got_base, got_base_n, got_skill, got_skill_n)

    print(f"[recount] {path.name}: {len(rows)} 题")
    for tid, b, bn, s, sm in rows:
        print(f"  {tid}  base {b}/{bn}   skill {s}/{sm}")
    print(f"[recount] 逐行求和 = base {got_base}/{got_base_n}   skill {got_skill}/{got_skill_n}")

    if total is None:
        print("[recount] 没找到「合计」行。", file=sys.stderr)
        return 2

    print(f"[recount] 表格合计 = base {total[0]}/{total[1]}   skill {total[2]}/{total[3]}")

    if computed == total:
        print("[recount] OK：合计与逐行一致")
        return 0

    print("[recount] ❌ 不一致：", file=sys.stderr)
    labels = ["base 通过数", "base 分母", "skill 通过数", "skill 分母"]
    for lab, got, said in zip(labels, computed, total):
        if got != said:
            print(f"  {lab}: 逐行是 {got}，表格写 {said}", file=sys.stderr)
    if args.fix:
        print(
            f"  建议改成：| **合计** | **{computed[0]} / {computed[1]}** | "
            f"**{computed[2]} / {computed[3]}** |",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
