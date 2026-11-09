"""压缩器的「触发预算」必须包含固定开销（system prompt + 工具 schema）。

## 这个 bug 为什么值得单独一个测试文件

它的表现极其隐蔽：压缩器**一直在被调用**，日志里能看到 `compress()`，
但**一次都没真正压缩**（每次都从"预算是 0"的路径提前返回）。
外部只看到「压了，但没效果」，于是把原因误判成"这个 agent 不需要压缩"。

实测（t26，13 次调用）：messages 估算从 79 一路爬到 3921，
而阈值是 4000 —— **永远差 79，永远不触发**。真实每次请求 7,478 token。
漏掉的正是 system prompt（177）+ 6 个工具的 JSON schema（521）。

教训：**"调用了"不等于"执行了"。** 任何"没效果"的结论，先确认功能真的跑起来了。
"""

import sys
import pathlib

_here = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_here.parents[1]))  # cli_coding_agent
sys.path.insert(0, str(_here.parents[2]))  # agent-prototypes 根

from langchain_core.messages import AIMessage  # noqa: E402

from agent import CodingAgent  # noqa: E402


class _ScriptedModel:
    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, schemas):
        return self

    def invoke(self, messages):
        return self.script.pop(0)


def test_tool_schema_tokens_measured(tmp_path):
    """6 个工具的 JSON schema 至少几百 token，不能当 0。"""
    agent = CodingAgent(workdir=tmp_path, llm=_ScriptedModel([]), verbose=False)
    assert agent._tool_tokens > 300, f"工具 schema 开销被算成 {agent._tool_tokens}"


def test_compress_budget_includes_fixed_overhead(tmp_path):
    """交给压缩器的 extra_tokens 必须 > 固定开销（否则阈值永不触发）。"""
    seen: dict = {}

    class _SpyCompressor:
        def compress(self, msgs, stats=None, extra_tokens=0, remaining_turns=4):
            seen["extra"] = extra_tokens
            return msgs

    agent = CodingAgent(
        workdir=tmp_path, llm=_ScriptedModel([AIMessage(content="done")]),
        verbose=False, max_iter=3,
    )
    agent.compressor = _SpyCompressor()
    agent.run("随便做点什么")

    # system prompt 177 + tool schema 521 ≈ 698；给足余量只要求 > 600
    assert seen.get("extra", 0) > 600, f"预算只算了 {seen.get('extra')}，固定开销被漏掉"
