"""语义通道方案 B：exemplar kNN（查询 ↔ 历史查询），而不是 查询 ↔ 说明文档。

为什么换判据：
    `semantic_probe.py` 显示 cos(查询, skill 元数据) 在中文里整体偏高——
    32 条"不该召回"的编码任务几乎全部 cos ≥ 0.55。绝对阈值又一次不可用
    （和 min_abs 那次是同一个错误的变体：**把标量绝对分当相关性**）。
    而"说明文档"本身是为**读了才知道怎么用**写的，不是为"什么时候该召"写的，
    拿它做检索目标，口径一开始就错位了。

方案 B：
    每个 skill 用它的**历史任务描述**当锚点（exemplar），新查询找最近的锚点。
    这等价于"这类问题我以前见过吗、当时用的是哪篇规范"——
    也是 RAG 里 few-shot exemplar 检索的标准做法。
    负例锚点（不该召回的编码任务）也进库并标成 None，模型才有机会说"都不像"。

评测口径不变，仍然是四行：
    原地 12 正例 recall@1 / 原地 32 负例误召 / 改写 22 条 recall@1 / 短负例 9 条误召

⚠️ 锚点来自原地 44 条（那是"开发集"），改写集 22 条是测试集 —— 这个划分是干净的。
   但**改写集是我写的**，仍属构造效度风险，结论只能当方向证据。

用法：python eval/semantic_knn.py
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
from tasks import TASKS  # noqa: E402
from test_recall_snapshot import EXPECTED  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []


def main() -> None:
    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    pos = [tid for tid in by_id if tid in EXPECTED]
    neg = [tid for tid in by_id if tid not in EXPECTED]

    emb = Embedder()
    anchor_texts = [by_id[t] for t in pos] + [by_id[t] for t in neg]
    anchor_labels = [EXPECTED[t] for t in pos] + [None] * len(neg)
    A = emb.embed(anchor_texts)

    Q = emb.embed([q for q, _, _ in PARAPHRASES])
    S = emb.embed(SHORT_NEGATIVES)
    P = emb.embed([by_id[t] for t in pos])
    N = emb.embed([by_id[t] for t in neg])

    def knn(X, margin_th):
        """返回每条查询的 (标签, 是否过 margin)。

        margin = top1 - top2：只有"最近的那个明显比第二近更像"才算召回。
        这一步是把「绝对分」换成「相对分」—— 上一轮失败的根因就是把绝对分当相关性。
        """
        out = []
        for x in X:
            sims = x @ A.T
            order = np.argsort(-sims)
            m = sims[order[0]] - sims[order[1]]
            out.append((anchor_labels[int(order[0])], m, sims[order[0]]))
        return [(lab, m) for lab, m, _ in out]

    print(f"锚点 {len(A)} 条（{len(pos)} 个正例锚 + {len(neg)} 个负例锚）\n")
    print("| margin 阈值 | 原地 正例首位 | 原地 误召 | 改写 首位 | 改写 召错 | 短负例 误召 |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: |")

    for th in [0.00, 0.01, 0.02, 0.03, 0.05, 0.08]:
        ph = sum(1 for (lab, m), tid in zip(knn(P, th), pos)
                 if lab == EXPECTED[tid] and m >= th)
        nf = sum(1 for lab, m in knn(N, th) if lab is not None and m >= th)

        qh = qw = 0
        for (lab, m), (q, want, alt) in zip(knn(Q, th), PARAPHRASES):
            if m < th or lab is None:
                continue
            if lab == want:
                qh += 1
            elif lab not in alt:
                qw += 1
        sf = sum(1 for lab, m in knn(S, th) if lab is not None and m >= th)
        print(f"| {th:.2f} | {ph}/{len(pos)} | {nf}/{len(neg)} | "
              f"{qh}/{len(PARAPHRASES)} | {qw} | {sf}/{len(S)} |")

    print("\n改写集逐条（margin ≥ 0.02 且最近锚点非负例 才认为召回）：")
    for (lab, m), (q, want, alt) in zip(knn(Q, 0.02), PARAPHRASES):
        mark = "✅" if lab == want and m >= 0.02 else ("·次优" if lab in alt else
                                                     ("❌漏" if m < 0.02 or lab is None else "❌错"))
        print(f"  {mark:4s} 最近锚点={str(lab):24s} margin={m:+.3f}  want={want:24s} | {q[:30]}")


if __name__ == "__main__":
    main()
