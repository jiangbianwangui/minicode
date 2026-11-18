"""语义兜底通道：tag 闸门空召回时，用向量最近邻再给一次机会。

为什么需要它（数字都在 `eval/RECALL.md`）：
    tag 是**字面准入**——查询里没出现 tag 词，那篇 skill 再相关也进不来。
    在这个仓库自己的 22 条改写查询上，tag 通道命中 **0/22**；即使把阈值降到 1.0 也只有 10/22，
    代价是 32 条不该召回的任务里 13 条被误召。所以问题不在阈值，在通道。

设计上的三个关键选择：

1. **旁路 gate，而不是塞进 BM25**
   BM25 只占 score 的 0.2 权重，且 `if not specific and not name_hit: continue` 会先把它筛掉。
   往 BM25 里加向量，等于给一个进不来的候选加分。

2. **锚点是 exemplar 问句，不是 skill 说明**
   `description/when_to_use` 是写给"读了才知道怎么做"的，不是写给"什么时候该召"的。
   实测 cos(查询, 说明) 在中文里整体偏高：32 条无关任务几乎全部 ≥0.55。

3. **必须有「无关」这个类**（`NONE` 锚点）
   纯最近邻只会回答"最像哪篇"，回答不了"该不该查"。没有 NONE 锚点时，
   改写召回 15/22 但无关任务误召 10/32；加上 17 条普通编码任务当 NONE 后，
   误召降到 0/32，召回不变。

判据用 **margin = top1 − top2**（相对分），不用 cos 绝对值：
   同一批查询换语料规模，cos 会整体漂移，margin 不会。

⚠️ 这是 opt-in：`SkillRouter(semantic=True)` 才挂载，且模型/依赖缺失时 `available()` 为 False，
   路由退回纯 tag 通道。CI 上不下载模型，行为与今天完全一致。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass

DEFAULT_MODEL_DIR = pathlib.Path(__file__).resolve().parent / "eval" / "models" / "bge-small-zh-v1.5"
DEFAULT_MARGIN = 0.03
_NONE_LABEL = "__none__"


@dataclass
class _Hit:
    label: str
    margin: float


class SemanticFallback:
    """exemplar kNN + margin 判据。依赖 onnxruntime / tokenizers 与本地模型，全部惰性加载。"""

    def __init__(
        self,
        skill_dir: str | pathlib.Path,
        model_dir: str | pathlib.Path = DEFAULT_MODEL_DIR,
        margin: float = DEFAULT_MARGIN,
        embed_fn=None,
    ) -> None:
        self.skill_dir = pathlib.Path(skill_dir)
        self.model_dir = pathlib.Path(model_dir)
        self.margin = margin
        self._embed_fn = embed_fn          # 测试可注入，生产走 ONNX
        self._embedder = None
        self.labels: list[str] = []
        self.texts: list[str] = []
        self._matrix = None
        self._load_error: str | None = None
        self._build_anchors()

    # ---------- 锚点 ----------
    def _build_anchors(self) -> None:
        for d in sorted(self.skill_dir.iterdir()):
            f = d / "examples.txt"
            if not (d.is_dir() and f.exists()):
                continue
            for ln in self._lines(f):
                self.texts.append(ln)
                self.labels.append(d.name)
        none_file = self.skill_dir / "_none_anchors.txt"
        if none_file.exists():
            for ln in self._lines(none_file):
                self.texts.append(ln)
                self.labels.append(_NONE_LABEL)

    @staticmethod
    def _lines(path: pathlib.Path) -> list[str]:
        """读一行一条的锚点文件，**跳过注释**。

        `_none_anchors.txt` 开头有一段解释，若不跳注释，那几行长句会被当成锚点——
        实测锚点数会从 56 变成 65，改写召回 12/22 掉到 11/22、短负例误召 0 升到 1。
        长句锚点几乎和任何查询都"有点像"，是最近邻里最脏的一种噪声。
        """
        out = []
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                out.append(ln)
        return out

    # ---------- 惰性加载 ----------
    def _ensure(self) -> bool:
        if self._embedder is not None:
            return True
        if self._load_error is not None:
            return False
        try:
            if self._embed_fn is None:
                from semantic_embedder import OnnxEmbedder   # 惰性：不 import 就不拉 onnxruntime

                self._embedder = OnnxEmbedder(self.model_dir)
                self._embed_fn = self._embedder.embed
            self._matrix = self._embed_fn(self.texts)
            return True
        except Exception as e:                       # 缺依赖 / 缺模型 / 版本不符
            self._load_error = f"{type(e).__name__}: {e}"
            return False

    def available(self) -> bool:
        return bool(self.texts) and self._ensure()

    # ---------- 检索 ----------
    def _top(self, query: str) -> _Hit | None:
        import numpy as np

        if not self._ensure():
            return None
        v = self._embed_fn([query])[0]
        sims = v @ self._matrix.T
        order = np.argsort(-sims)
        if len(order) < 2:
            return None
        return _Hit(self.labels[int(order[0])], float(sims[order[0]] - sims[order[1]]))

    def retrieve(self, query: str) -> str | None:
        """返回应该召回哪篇 skill；判为"都不该查"返回 None。"""
        hit = self._top(query)
        if hit is None or hit.margin < self.margin or hit.label == _NONE_LABEL:
            return None
        return hit.label
