"""用 LLM 生成的 exemplar 当锚点重测：这一版的锚点**不含任何评测集 prompt**。

上一版 7/22 用的锚点是评测集那 12 条任务描述，属于"用答案找答案"，
数字好看但经不起追问。现在锚点换成 `skills/<name>/examples.txt`
（由 `gen_exemplars.py` 只根据规范内容生成，没见过评测集）。

要回答的问题只有一个：**去掉泄漏之后，召回还在不在？**
如果掉到 0，说明上一版是自问自答；如果还有相当一部分，才说明语义通道真有作用。

用法：python eval/semantic_exemplar_eval.py
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


NONE_LABEL = "__none__"

# 「什么都不该查」的锚点：普通编码任务。
# 没有它，任何查询都会找到"最像的一篇"——最近邻只回答"像哪篇"，回答不了"该不该查"。
# 这批是**手写**的代表性任务（不是评测集里的 t01–t26），覆盖最常见的几类无规范需求。
NONE_ANCHORS = [
    "写个函数把字符串反转",
    "修复这个列表越界的问题",
    "给这个类加一个构造函数",
    "把这段循环改写成列表推导",
    "写一个脚本批量重命名文件",
    "帮我把这个 JSON 转成字典",
    "这里有个死循环，找一下原因",
    "给函数补上类型标注",
    "把这几个模块的 import 理顺",
    "写个正则匹配手机号",
    "这个报错是什么意思",
    "把测试跑一遍看哪里挂了",
    "给接口加一个参数校验",
    "把这段代码拆成两个函数",
    "用 pandas 读这个表格并算平均值",
    # 补两条：`t01`（"向标准输出打印 Hello, World!"）曾被判成 report-export-encoding ——
    # 「打印到控制台」和「导出报表」在向量空间里很近，而它们语义上完全无关。
    # 这是「通用词连起一切」第二次冒头（第一次是 skill 索引把正文也收进去）。
    "往控制台打印一段输出",
    "把结果 print 出来看看对不对",
]


def load_exemplars(router) -> tuple[list[str], list[str]]:
    texts, labels = [], []
    for s in router.skills:
        f = s.path.parent / "examples.txt"
        if not f.exists():
            continue
        for ln in f.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln:
                texts.append(ln)
                labels.append(s.name)
    return texts, labels


def main() -> None:
    router = default_router()
    texts, labels = load_exemplars(router)
    if not texts:
        print("没有 examples.txt，先跑 python eval/gen_exemplars.py")
        return

    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    pos = [tid for tid in by_id if tid in EXPECTED]
    neg = [tid for tid in by_id if tid not in EXPECTED]

    emb = Embedder()
    A = emb.embed(texts)
    # 两组锚点都跑到，用来回答同一个问题：没有"无关"这个类，最近邻会不会瞎认。
    A_none = emb.embed(texts + NONE_ANCHORS)
    labels_none = labels + [NONE_LABEL] * len(NONE_ANCHORS)
    P = emb.embed([by_id[t] for t in pos])
    N = emb.embed([by_id[t] for t in neg])
    Q = emb.embed([q for q, _, _ in PARAPHRASES])
    S = emb.embed(SHORT_NEGATIVES)

    print(f"锚点 = {len(texts)} 条 LLM 生成的 exemplar（{len(set(labels))} 篇 skill，"
          f"**不含评测集 prompt**）\n")

    def make_top(mat, labs):
        def top(x):
            sims = x @ mat.T
            order = np.argsort(-sims)
            return labs[int(order[0])], sims[order[0]] - sims[order[1]]
        return top

    for title, mat, labs in (("A. 只有 skill exemplar（无「无关」类）", A, labels),
                             ("B. skill exemplar + 15 条普通编码任务当 NONE 锚点",
                              A_none, labels_none)):
        top = make_top(mat, labs)
        print(f"### {title}")
        print("| margin | 原地 正例 | 原地 误召 | 改写 首位 | 改写 召错 | 短负例 误召 |")
        print("| ---: | ---: | ---: | ---: | ---: | ---: |")
        for th in [0.00, 0.01, 0.02, 0.03, 0.05]:
            ph = sum(1 for x, tid in zip(P, pos) if (lambda r: r[0] == EXPECTED[tid] and r[1] >= th)(top(x)))
            nf = sum(1 for x in N if (lambda r: r[0] != NONE_LABEL and r[1] >= th)(top(x)))
            qh = qw = 0
            for x, (q, want, alt) in zip(Q, PARAPHRASES):
                lab, m = top(x)
                if m < th or lab == NONE_LABEL:
                    continue
                if lab == want:
                    qh += 1
                elif lab not in alt:
                    qw += 1
            sf = sum(1 for x in S if (lambda r: r[0] != NONE_LABEL and r[1] >= th)(top(x)))
            print(f"| {th:.2f} | {ph}/{len(pos)} | {nf}/{len(neg)} | "
                  f"{qh}/{len(PARAPHRASES)} | {qw} | {sf}/{len(S)} |")
        print()

    print("### C. tag 优先 + 语义兜底（B 配置，才是上线行为）")
    top = make_top(A_none, labels_none)
    print("| margin | 原地 正例 | 原地 误召 | 改写 首位 | 短负例 误召 |")
    print("| ---: | ---: | ---: | ---: | ---: |")
    for th in [0.00, 0.01, 0.02, 0.03, 0.05]:
        def ch(q):
            tag = [h.skill.name for h in router.retrieve(q)]
            if tag:
                return tag[0]
            lab, m = top(emb.embed([q])[0])
            return lab if (m >= th and lab != NONE_LABEL) else None

        ph = sum(1 for tid in pos if ch(by_id[tid]) == EXPECTED[tid])
        nf = sum(1 for tid in neg if ch(by_id[tid]) is not None)
        qh = sum(1 for q, want, _ in PARAPHRASES if ch(q) == want)
        sf = sum(1 for q in SHORT_NEGATIVES if ch(q) is not None)
        print(f"| {th:.2f} | {ph}/{len(pos)} | {nf}/{len(neg)} | "
              f"{qh}/{len(PARAPHRASES)} | {sf}/{len(S)} |")


if __name__ == "__main__":
    main()
