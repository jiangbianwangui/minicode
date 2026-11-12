"""改写集上的召回评测（baseline vs 未来任何改动都用同一个命令跑）。

指标定义（三种，缺一不可）：
    recall@1  首位就是期望 skill 的比例 —— 最严格，agent 只会读首位附近那几篇
    recall@k  期望 skill 出现在返回列表里的比例 —— 宽松，但会被「全召回」钻空子
    precision 返回列表里第一条就是对的比例（与 recall@1 同源，但单独报是为了
              配合「误召到别的 skill」一起看：召回了错的比不召回更糟，
              因为它会把 agent 引到一篇不相关的规范上）

用法：
    python eval/paraphrase_eval.py            # 跑改写集
    python eval/paraphrase_eval.py --verbose  # 打印每条的召回列表与诊断
"""

from __future__ import annotations

import argparse
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))

from skill_router import default_router  # noqa: E402
from paraphrase_set import PARAPHRASES, SHORT_NEGATIVES  # noqa: E402


def evaluate(router, verbose: bool = False) -> dict:
    r1 = rk = wrong = empty = 0
    rows = []
    for q, want, alt in PARAPHRASES:
        names = [h.skill.name for h in router.retrieve(q)]
        if not names:
            empty += 1
            verdict = "漏召"
        elif names[0] == want:
            r1 += 1
            verdict = "首位正确"
        elif names[0] in alt:
            verdict = f"次优（{names[0]}）"
        else:
            wrong += 1
            verdict = f"召错（{names[0]}）"
        if want in names:
            rk += 1
        rows.append((q, want, names, verdict))
        if verbose:
            print(f"  {verdict:16s} 期望={want:24s} 实际={names or '—'}")
            print(f"      Q: {q}")
    n = len(PARAPHRASES)
    # 短负例：一句都不该召回。只看长负例会被「阈值对短查询太严」这一假象骗到。
    fp_short = []
    for q in SHORT_NEGATIVES:
        names = [h.skill.name for h in router.retrieve(q)]
        if names:
            fp_short.append((q, names))
            if verbose:
                print(f"  短负例误召        {names}")
                print(f"      Q: {q}")
    return {"n": n, "r1": r1, "rk": rk, "wrong": wrong, "empty": empty,
            "rows": rows, "fp_short": fp_short}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    router = default_router()
    print(f"改写集 {len(PARAPHRASES)} 条 · 技能库 {len(router.skills)} 篇\n")
    m = evaluate(router, verbose=args.verbose)
    n = m["n"]
    print("\n" + "=" * 58)
    print(f"recall@1  {m['r1']}/{n}  = {m['r1'] / n:.0%}   首位即为期望 skill")
    print(f"recall@K  {m['rk']}/{n}  = {m['rk'] / n:.0%}   出现在返回列表里")
    print(f"漏召      {m['empty']}/{n}      一个都没召回")
    print(f"召错      {m['wrong']}/{n}      召回了不相干的 skill（比漏召更糟）")
    print(f"短负例误召 {len(m['fp_short'])}/{len(SHORT_NEGATIVES)}    "
          f"不该召回却召回（任何改阈值/打分的改动都必须先看这一行）")


if __name__ == "__main__":
    main()
