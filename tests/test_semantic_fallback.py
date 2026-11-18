"""语义兜底通道的离线测试。

要锁住四件事（第三条最重要）：
1. margin 判据：最近锚点明显更近才召回，两个差不多就不召回；
2. NONE 锚点能挡住"什么都不该查"的查询；
3. **tag 有结果时，语义通道一次都不许被调用**——否则它会挤掉本来对的 tag 结果；
4. 默认（不启用）行为与今天完全一致：`semantic_fallback is None`。

全部用注入的假 embedder，不依赖 onnxruntime / 模型文件。
"""

import pathlib
import sys

import numpy as np
import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from skill_router import SkillRouter, load_skills  # noqa: E402
from skill_semantic import SemanticFallback  # noqa: E402

SKILL_DIR = _ROOT / "skills"


def _fake_embed(texts):
    """按关键词把文本映射成三个方向之一，其余全部落到"无关"方向。

    「无关」方向同时是 NONE 锚点的方向 —— 所以无关查询与 NONE 锚点几乎同距，
    margin ≈ 0，正好用来验证"两个差不多时不该召回"。
    """
    out = []
    for t in texts:
        if "灰度" in t or "放量" in t:
            out.append([1.0, 0.0, 0.0])
        elif "汇率" in t:
            out.append([0.0, 1.0, 0.0])
        else:
            out.append([0.0, 0.0, 1.0])
    return np.array(out, dtype=np.float32)


@pytest.fixture()
def fallback():
    return SemanticFallback(SKILL_DIR, embed_fn=_fake_embed)


def test_margin_hit(fallback):
    assert fallback.retrieve("灰度放量到一半指标抖了") == "canary-gate-policy"


def test_ambiguous_query_returns_none(fallback):
    """与一堆锚点同向等距 → margin 不到阈值 → 宁可不召回。"""
    assert fallback.retrieve("帮我看看这个函数") is None


def test_tag_result_wins_and_semantic_not_called():
    """tag 通道有结果时，语义通道必须一次都不被调用。"""
    calls = {"n": 0}

    def counting_embed(texts):
        calls["n"] += 1
        return _fake_embed(texts)

    fb = SemanticFallback(SKILL_DIR, embed_fn=counting_embed)
    router = SkillRouter(load_skills(SKILL_DIR), semantic_fallback=fb)
    # t36 类查询：tag 通道本来就能召回到 rate-limit-policy
    hits = router.retrieve("给接口加限流，按租户维度统计配额，超了就拒绝，客户端重试要做幂等")
    assert hits and hits[0].skill.name == "rate-limit-policy"
    # 语义通道是**完全惰性**的：tag 有结果 → 连锚点都不嵌入
    assert calls["n"] == 0, "tag 命中时语义通道不该被碰（连锚点嵌入都不该发生）"


def test_semantic_used_only_when_tag_empty(fallback):
    router = SkillRouter(load_skills(SKILL_DIR), semantic_fallback=fallback)
    hits = router.retrieve("灰度放量到一半指标抖了，要不要停")   # tag 闸门过不了
    assert hits and hits[0].skill.name == "canary-gate-policy"


def test_default_router_has_no_semantic_channel():
    from skill_router import default_router

    assert default_router().semantic_fallback is None


def test_agent_wiring_defaults_off(tmp_path):
    """agent 侧默认不挂语义通道；显式开启时才挂，且模型缺失也只能降级不能抛错。"""
    from langchain_core.messages import AIMessage

    from agent import CodingAgent

    class _M:
        def bind_tools(self, schemas):
            return self

        def invoke(self, messages):
            return AIMessage(content="done")

    off = CodingAgent(workdir=tmp_path / "a", llm=_M(), verbose=False, skills=True)
    assert off.router is not None
    assert off.router.semantic_fallback is None

    on = CodingAgent(workdir=tmp_path / "b", llm=_M(), verbose=False,
                     skills=True, skills_semantic=True)
    assert on.router is not None
    fb = on.router.semantic_fallback
    if fb is not None:                     # 模型在就挂上，不在就 None —— 都不许抛
        assert fb.texts, "锚点不能为空"


@pytest.mark.skipif(
    not (pathlib.Path(_ROOT) / "eval" / "models" / "bge-small-zh-v1.5" / "model_int8.onnx").exists(),
    reason="本地未下载 ONNX 模型（CI 不下载；见 eval/RECALL.md 的下载命令）",
)
def test_real_model_smoke():
    fb = SemanticFallback(SKILL_DIR)
    assert fb.available()
    assert fb.retrieve("导出的 CSV 用 Excel 打开中文全是乱码") == "report-export-encoding"
