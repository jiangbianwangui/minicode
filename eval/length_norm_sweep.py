"""长度归一化阈值实验：验证「绝对阈值在惩罚短查询」这个混杂。

背景：
    改写集 0/22，但阈值降到 2.0 也只有 10/22，而代价是 13/32 误召。
    一个可能的解释是——`min_abs=10.0` 是**绝对分**，而 tag 命中数随查询长度增长：
    原始任务描述是多句规格（几十上百个 token，能撞上 3–5 个 tag），
    改写查询只有一句话（十来个 token，通常只撞上 1 个 tag）。
    换句话说，阈值本身在系统性地歧视短查询。

实验设计：
    把绝对阈值换成**按查询长度缩放的阈值**：
        eff = 10.0 * clip(n_tokens / REF, lo, 1.0)
    长任务（n_tokens ≥ REF）阈值仍是 10，行为完全不变 → 32 条负例的误召不该上升；
    短查询阈值自动降低 → 看改写集能不能被救回来。

    如果「改写集上升 + 原始集不退化」同时成立，说明问题一半在阈值形式，不在语义。
    如果改写集纹丝不动，说明剩下的失败确实是词面不重叠，必须上语义通道。

⚠️ 这个实验是在**已知结果**的改写集上调参数，属于探索性分析。
    任何提升都只能当方向证据；要引用必须先在新的一批查询上复验。

用法：python eval/length_norm_sweep.py
"""

from __future__ import annotations

import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))
sys.path.insert(0, str(_ROOT / "tests"))

from skill_router import default_router, tokenize  # noqa: E402
from tasks import TASKS  # noqa: E402
from paraphrase_set import PARAPHRASES  # noqa: E402
from test_recall_snapshot import EXPECTED  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []


def main() -> None:
    router = default_router()
    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    positives = [tid for tid in by_id if tid in EXPECTED]
    negatives = [tid for tid in by_id if tid not in EXPECTED]

    lens = sorted(len(tokenize(p)) for p in by_id.values())
    median = lens[len(lens) // 2]
    p_lens = [len(tokenize(q)) for q, _, _ in PARAPHRASES]
    print(f"原始查询 token 数中位数 = {median}；改写查询 = "
          f"min {min(p_lens)} / 中位 {sorted(p_lens)[len(p_lens) // 2]} / max {max(p_lens)}\n")

    print("| REF | 原始 正确首位 | 原始 误召 | 改写 首位正确 | 改写 漏召 |")
    print("| ---: | ---: | ---: | ---: | ---: |")
    for ref in [200, 160, 128, 110, 90, 75, 60, 45]:
        hit = fp = 0
        for tid in positives:
            n = len(tokenize(by_id[tid]))
            eff = 10.0 * min(1.0, max(0.15, n / ref))
            names = [h.skill.name for h in router.retrieve(by_id[tid], min_abs=eff)]
            if names and names[0] == EXPECTED[tid]:
                hit += 1
        for tid in negatives:
            n = len(tokenize(by_id[tid]))
            eff = 10.0 * min(1.0, max(0.15, n / ref))
            if router.retrieve(by_id[tid], min_abs=eff):
                fp += 1

        p_hit = p_miss = 0
        for q, want, _alt in PARAPHRASES:
            n = len(tokenize(q))
            eff = 10.0 * min(1.0, max(0.15, n / ref))
            names = [h.skill.name for h in router.retrieve(q, min_abs=eff)]
            if names and names[0] == want:
                p_hit += 1
            elif not names:
                p_miss += 1
        print(f"| {ref} | {hit}/{len(positives)} | {fp}/{len(negatives)} | "
              f"{p_hit}/{len(PARAPHRASES)} | {p_miss} |")

    print("\n对照（绝对阈值，来自 threshold_sweep）：10.0 → 改写 0/22、误召 0/32；"
          "2.0 → 改写 10/22、误召 13/32")


if __name__ == "__main__":
    main()
