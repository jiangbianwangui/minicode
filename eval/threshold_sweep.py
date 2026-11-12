"""阈值扫描：把「召回差」拆成两个可能原因。

改写集 baseline 是 0/22 —— 全部漏召。这个结果太整齐，不像「语义不匹配」，
更像被某个绝对阈值一刀切掉。所以先做一个**不涉及任何新模型**的对照：

    对每个 min_abs，同时看两个集合
      ① 44 条原始查询：该召回的 12 条命中几条、不该召回的 32 条误召几条
      ② 22 条改写查询：首位命中几条

如果存在某个阈值能同时让两边都好 → 问题只是阈值没调好，不需要向量。
如果两边的最优阈值互斥 → **单一绝对阈值无法同时服务两类问法**，
   这才是有数据支撑的「需要第二条通道」的理由。

注意：绝对分数与查询长度强相关（改写查询通常只有一句话，tag 命中数天然更少），
所以这张表同时也在量化「阈值是在惩罚短查询」这件事。

用法：python eval/threshold_sweep.py
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))
sys.path.insert(0, str(_ROOT / "tests"))

from skill_router import default_router  # noqa: E402
from tasks import TASKS  # noqa: E402
from paraphrase_set import PARAPHRASES  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []

from test_recall_snapshot import EXPECTED  # noqa: E402

THRESHOLDS = [10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]


def main() -> None:
    router = default_router()
    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    positives = [tid for tid in by_id if tid in EXPECTED]
    negatives = [tid for tid in by_id if tid not in EXPECTED]

    print(f"原始集 {len(by_id)} 条（该召回 {len(positives)} / 不该召回 {len(negatives)}）"
          f" · 改写集 {len(PARAPHRASES)} 条\n")
    print("| min_abs | 原始集 正确首位 | 原始集 漏召 | 原始集 误召 | 改写集 首位正确 | 改写集 漏召 |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: |")

    for t in THRESHOLDS:
        hit = miss = false_pos = 0
        for tid in positives:
            names = [h.skill.name for h in router.retrieve(by_id[tid], min_abs=t)]
            if names and names[0] == EXPECTED[tid]:
                hit += 1
            elif not names:
                miss += 1
        for tid in negatives:
            if router.retrieve(by_id[tid], min_abs=t):
                false_pos += 1

        p_hit = p_miss = 0
        for q, want, _alt in PARAPHRASES:
            names = [h.skill.name for h in router.retrieve(q, min_abs=t)]
            if names and names[0] == want:
                p_hit += 1
            elif not names:
                p_miss += 1

        star = "  ← 当前" if t == 10.0 else ""
        print(f"| {t:4.1f} | {hit}/{len(positives)} | {miss} | "
              f"{false_pos}/{len(negatives)} | {p_hit}/{len(PARAPHRASES)} | {p_miss} |{star}")


if __name__ == "__main__":
    main()
