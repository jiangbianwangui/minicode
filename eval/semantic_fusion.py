"""融合口径：tag 通道优先，语义通道兜底。这才是要上线的行为。

规则（刻意做得很笨，因为每加一条规则都要能被解释）：
    1. tag 通道召回非空 → 直接用（它已经原地 12/12、0 误召，别动它）
    2. tag 通道为空 → 走 exemplar kNN，且要求 margin(top1-top2) ≥ θ，否则宁可不召回
    3. 两路都不给 → 返回空（模型靠任务描述与仓库内代码自行决策）

为什么用"兜底"而不是"融合打分"：
    融合需要把两个不可比的分数（tag bonus + bm25 与 cos 距离）放到同一个尺度上，
    而我们刚刚两次证明"调标量阈值"这条路走不通。串行兜底没有这个自由度，
    出错也只能出在一边，归因清楚。

用法：python eval/semantic_fusion.py
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))
sys.path.insert(0, str(_ROOT / "tests"))

from paraphrase_set import PARAPHRASES, SHORT_NEGATIVES  # noqa: E402
from semantic_probe import Embedder  # noqa: E402
from skill_router import default_router  # noqa: E402
from tasks import TASKS  # noqa: E402
from test_recall_snapshot import EXPECTED  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []

THETA = 0.02


def main() -> None:
    router = default_router()
    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    pos = [tid for tid in by_id if tid in EXPECTED]
    neg = [tid for tid in by_id if tid not in EXPECTED]

    emb = Embedder()
    A = emb.embed([by_id[t] for t in pos] + [by_id[t] for t in neg])
    labels = [EXPECTED[t] for t in pos] + [None] * len(neg)

    def semantic(q: str) -> list[str]:
        x = emb.embed([q])[0]
        sims = x @ A.T
        order = np.argsort(-sims)
        if sims[order[0]] - sims[order[1]] < THETA:
            return []
        lab = labels[int(order[0])]
        return [lab] if lab else []

    def channel(q: str) -> tuple[list[str], str]:
        tag = [h.skill.name for h in router.retrieve(q)]
        if tag:
            return tag, "tag"
        sem = semantic(q)
        return sem, ("semantic" if sem else "none")

    rows = []
    for tid in pos:
        got, src = channel(by_id[tid])
        rows.append(("原地 正例", tid, EXPECTED[tid], got, src))
    for tid in neg:
        got, src = channel(by_id[tid])
        rows.append(("原地 负例", tid, None, got, src))
    for q, want, _alt in PARAPHRASES:
        got, src = channel(q)
        rows.append(("改写", q[:20], want, got, src))
    for q in SHORT_NEGATIVES:
        got, src = channel(q)
        rows.append(("短负例", q[:20], None, got, src))

    def stat(kind: str, want_hit: bool) -> tuple[int, int]:
        sub = [r for r in rows if r[0] == kind]
        if want_hit:
            return sum(1 for r in sub if r[3] and r[3][0] == r[2]), len(sub)
        return sum(1 for r in sub if r[3]), len(sub)

    print(f"θ = {THETA}   规则：tag 优先，空则语义兜底\n")
    print("| 集合 | 指标 | 结果 |")
    print("| --- | --- | ---: |")
    h, n = stat("原地 正例", True)
    print(f"| 原地 44 条 | 该召回 12 条的 recall@1 | {h}/{n} |")
    f, n = stat("原地 负例", False)
    print(f"| 原地 44 条 | 不该召回的误召 | {f}/{n} |")
    h, n = stat("改写", True)
    print(f"| 改写 22 条 | recall@1 | **{h}/{n}** |")
    f, n = stat("短负例", False)
    print(f"| 短负例 9 条 | 误召 | {f}/{n} |")

    print("\n语义通道救回来的改写查询：")
    for r in rows:
        if r[0] == "改写" and r[4] == "semantic":
            mark = "✅" if r[3][0] == r[2] else "❌"
            print(f"  {mark} {r[1]:22s} → {r[3][0]:24s} (期望 {r[2]})")


if __name__ == "__main__":
    main()
