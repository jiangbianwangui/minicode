"""语义通道探针：先确认「向量到底有没有用」，再决定要不要改路由。

只用 onnxruntime + tokenizers（24MB int8 模型），**不装 torch**（本机 torch 是空壳）。

评测口径与 tag 通道完全对齐，四个数必须一起看：
    原地 44 条：该召回 12 条的 recall@1（不能低于 12）
    原地 44 条：不该召回 32 条的误召（不能高于 0）
    改写 22 条：recall@1（要从 0 提上去）
    短负例 9 条：误召（不能高于 0）

用法：
    # 先下载模型（约 24MB）
    mkdir -p eval/models/bge-small-zh-v1.5 && cd eval/models/bge-small-zh-v1.5
    curl -L -o model_int8.onnx https://modelscope.cn/models/Xenova/bge-small-zh-v1.5/resolve/master/onnx/model_int8.onnx
    curl -L -o tokenizer.json   https://modelscope.cn/models/Xenova/bge-small-zh-v1.5/resolve/master/tokenizer.json
    cd - && python eval/semantic_probe.py
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))
sys.path.insert(0, str(_ROOT / "tests"))

from skill_router import default_router  # noqa: E402
from paraphrase_set import PARAPHRASES, SHORT_NEGATIVES  # noqa: E402
from tasks import TASKS  # noqa: E402
from test_recall_snapshot import EXPECTED  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []

MODEL_DIR = _ROOT / "eval" / "models" / "bge-small-zh-v1.5"
MAX_LEN = 256


class Embedder:
    def __init__(self, model_dir: pathlib.Path = MODEL_DIR):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.tok = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tok.enable_truncation(max_length=MAX_LEN)
        self.tok.enable_padding()          # 批量推理时动态 pad
        self.sess = ort.InferenceSession(
            str(model_dir / "model_int8.onnx"), providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.sess.get_inputs()}

    def embed(self, texts: list[str], batch: int = 16) -> np.ndarray:
        out = []
        for i in range(0, len(texts), batch):
            enc = self.tok.encode_batch(texts[i:i + batch])
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            }
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
            hidden = self.sess.run(None, feed)[0]      # (B, T, H)
            vec = hidden[:, 0, :]                      # bge 用 [CLS] 做句向量
            out.append(vec / np.linalg.norm(vec, axis=1, keepdims=True))
        return np.vstack(out)


def skill_text(s) -> str:
    """与 router 的索引口径一致：只用元数据，不碰正文。

    正文里全是"代码/文件/测试"这类通用词，第一版把正文也索引进去时，
    26 个任务每一个都召回了 2–3 篇（见 Skill.__post_init__ 的注释）。
    """
    return f"{s.name}。{s.when_to_use} {s.description} 关键词：{'、'.join(s.tags)}"


def main() -> None:
    router = default_router()
    names = [s.name for s in router.skills]
    emb = Embedder()
    M = emb.embed([skill_text(s) for s in router.skills])

    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    pos = [tid for tid in by_id if tid in EXPECTED]
    neg = [tid for tid in by_id if tid not in EXPECTED]

    P = emb.embed([by_id[t] for t in pos])
    N = emb.embed([by_id[t] for t in neg])
    Q = emb.embed([q for q, _, _ in PARAPHRASES])
    S = emb.embed(SHORT_NEGATIVES)

    print(f"模型：bge-small-zh-v1.5 (int8)   技能 {len(names)} 篇   "
          f"原地 {len(pos)} 正 / {len(neg)} 负   改写 {len(Q)} 条   短负例 {len(S)} 条\n")
    print("| cos 阈值 | 原地 正例首位 | 原地 误召 | 改写 首位 | 改写 召错 | 短负例 误召 |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: |")

    for th in [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70]:
        hit = sum(1 for i, tid in enumerate(pos)
                  if names[int((P[i] @ M.T).argmax())] == EXPECTED[tid]
                  and (P[i] @ M.T).max() >= th)
        fp = sum(1 for i in range(len(N)) if (N[i] @ M.T).max() >= th)

        qhit = qwrong = 0
        for i, (q, want, alt) in enumerate(PARAPHRASES):
            sims = Q[i] @ M.T
            top = names[int(sims.argmax())]
            if sims.max() >= th:
                if top == want:
                    qhit += 1
                elif top not in alt:
                    qwrong += 1
        sfp = sum(1 for i in range(len(S)) if (S[i] @ M.T).max() >= th)
        print(f"| {th:.2f} | {hit}/{len(pos)} | {fp}/{len(neg)} | "
              f"{qhit}/{len(PARAPHRASES)} | {qwrong} | {sfp}/{len(S)} |")

    # 逐条看：改写集在 0.55 阈值下的实际去向
    print("\n改写集逐条（cos ≥ 0.55 才认为召回）：")
    for i, (q, want, alt) in enumerate(PARAPHRASES):
        sims = Q[i] @ M.T
        order = np.argsort(-sims)[:3]
        top = names[int(order[0])]
        mark = "✅" if top == want and sims.max() >= 0.55 else ("·次优" if top in alt else "❌")
        print(f"  {mark} top1={top:24s} {sims[order[0]]:.3f}  want={want:24s} | {q[:34]}")


if __name__ == "__main__":
    main()
