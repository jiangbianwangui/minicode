"""ONNX 句向量编码器（bge-small-zh-v1.5 int8，约 24MB）。

为什么不用 sentence-transformers / torch：
    本机 `torch` 是个空壳（`import torch` 连 `__version__` 都没有），
    而真 torch 的 wheel 200MB+，为了一篇 13 文档的检索不值得。
    `onnxruntime + tokenizers` 加起来约 40MB，推理一条中文短句在 CPU 上几十毫秒。

模型下载（24MB，`eval/models/` 已 gitignore）：
    mkdir -p eval/models/bge-small-zh-v1.5 && cd eval/models/bge-small-zh-v1.5
    curl -L -o model_int8.onnx https://modelscope.cn/models/Xenova/bge-small-zh-v1.5/resolve/master/onnx/model_int8.onnx
    curl -L -o tokenizer.json   https://modelscope.cn/models/Xenova/bge-small-zh-v1.5/resolve/master/tokenizer.json
"""

from __future__ import annotations

import pathlib

import numpy as np

MAX_LEN = 256


class OnnxEmbedder:
    def __init__(self, model_dir: str | pathlib.Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        d = pathlib.Path(model_dir)
        model_file, tok_file = d / "model_int8.onnx", d / "tokenizer.json"
        if not model_file.exists() or not tok_file.exists():
            raise FileNotFoundError(f"缺少模型文件：{model_file} / {tok_file}")

        self.tok = Tokenizer.from_file(str(tok_file))
        self.tok.enable_truncation(max_length=MAX_LEN)
        self.tok.enable_padding()
        self.sess = ort.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        self.input_names = {i.name for i in self.sess.get_inputs()}

    def embed(self, texts: list[str], batch: int = 16) -> np.ndarray:
        vecs = []
        for i in range(0, len(texts), batch):
            enc = self.tok.encode_batch(texts[i:i + batch])
            feed = {
                "input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64),
            }
            if "token_type_ids" in self.input_names:
                feed["token_type_ids"] = np.zeros_like(feed["input_ids"])
            hidden = self.sess.run(None, feed)[0]     # (B, T, H)
            v = hidden[:, 0, :]                       # bge 用 [CLS] 做句向量
            vecs.append(v / np.linalg.norm(v, axis=1, keepdims=True))
        return np.vstack(vecs)
