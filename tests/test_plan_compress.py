"""Plan 模式 + 上下文压缩 的离线测试（无真实 LLM 调用）。"""

import sys
import pathlib

_here = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_here.parents[1]))  # cli_coding_agent
sys.path.insert(0, str(_here.parents[2]))  # agent-prototypes 根

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402

from agent import CodingAgent, RunStats  # noqa: E402
from context import Compressor  # noqa: E402


class ScriptedModel:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, schemas):
        assert schemas, "必须传入 tools schema"
        return self

    def invoke(self, messages):
        self.calls += 1
        return self.script.pop(0)


def _ai(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


# ---------------------------------------------------------------------------
# Plan 模式
# ---------------------------------------------------------------------------

def test_plan_mode_records_plan_and_writes(tmp_path):
    steps = ["创建 hello.py", "运行验证"]
    model = ScriptedModel([
        _ai([{"name": "plan", "args": {"steps": steps}, "id": "p1", "type": "tool"}]),
        _ai([{"name": "WriteFile", "args": {"path": "hello.py", "content": "print(1+1)"}, "id": "w1", "type": "tool"}]),
        AIMessage(content="已完成 hello.py"),
    ])
    agent = CodingAgent(workdir=tmp_path, llm=model, verbose=False, plan_mode=True)
    result = agent.run("创建并验证 hello.py")
    # plan 被正确记录
    assert agent._plan["steps"] == steps
    assert (tmp_path / "hello.py").read_text() == "print(1+1)"
    assert result == "已完成 hello.py"


def test_plan_default_off_has_no_plan_tool(tmp_path):
    agent = CodingAgent(workdir=tmp_path, llm=ScriptedModel([_ai([])]), verbose=False)
    names = [s["function"]["name"] for s in agent._tool_schemas]
    assert "plan" not in names


def test_adaptive_plan_gates_off_for_simple_task(tmp_path):
    # 简单任务（hello）应被规则式门控判为「简单」，不注入 plan（退回纯 ReAct）
    agent = CodingAgent(workdir=tmp_path, llm=ScriptedModel([_ai([])]), verbose=False,
                        adaptive_plan=True)
    agent.run("创建一个 hello.py 打印 Hello World")
    assert agent._use_plan is False
    # 系统提示里不应出现 plan 指令
    assert "Plan 模式已启用" not in agent._build_system().content


def test_adaptive_plan_gates_on_for_complex_task(tmp_path):
    # 含「跨文件/重构」等结构关键词的任务应被判为「复杂」，启用 plan
    agent = CodingAgent(workdir=tmp_path, llm=ScriptedModel([_ai([])]), verbose=False,
                        adaptive_plan=True)
    agent.run("跨文件重命名符号并在所有调用处同步修改")
    assert agent._use_plan is True
    assert "Plan 模式已启用" in agent._build_system().content


def test_compact_plan_shows_only_remaining_steps(tmp_path):
    steps = ["读文件", "改代码", "运行测试"]
    agent = CodingAgent(workdir=tmp_path, llm=ScriptedModel([_ai([])]), verbose=False, plan_mode=True)
    agent._plan = {"steps": steps, "done": [0]}  # 第 1 步已完成
    sys_prompt = agent._build_system().content
    assert "读文件" not in sys_prompt          # 已完成步骤不再展开
    assert "改代码" in sys_prompt               # 剩余步骤仍在
    assert "运行测试" in sys_prompt
    assert "1/3 步已完成" in sys_prompt or "1/3" in sys_prompt


# ---------------------------------------------------------------------------
# 上下文压缩
# ---------------------------------------------------------------------------

class FakeSummarizer:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        return AIMessage(content="摘要内容")


def _big_round(i: int, size: int = 4000):
    """制造一个 [AIMessage(tool_calls) + 大 ToolMessage] 轮次。"""
    tc = {"name": "Bash", "args": {"command": f"echo {i}"}, "id": f"c{i}", "type": "tool"}
    big = "x" * size
    return [
        AIMessage(content="", tool_calls=[tc]),
        ToolMessage(content=big, tool_call_id=f"c{i}", name="Bash"),
    ]


def test_compressor_below_threshold_returns_same():
    msgs = [HumanMessage(content="hi")] + _big_round(1, size=100)
    comp = Compressor(FakeSummarizer())
    out = comp.compress(msgs, None)
    assert out is msgs  # 未超阈值，原样返回


def test_compressor_reduces_on_long_history():
    summarizer = FakeSummarizer()
    msgs = [HumanMessage(content="task")]
    rounds = []
    for i in range(6):
        r = _big_round(i, size=4000)
        rounds.append(r)
        msgs.extend(r)
    original_len = len(msgs)
    comp = Compressor(summarizer, threshold=4000, keep_recent=2, batch=4)
    out = comp.compress(msgs, None)

    # 长度应明显减少
    assert len(out) < original_len
    # 保留 HumanMessage
    assert isinstance(out[0], HumanMessage)
    # 含至少一条压缩摘要
    assert any(isinstance(m, AIMessage) and m.content.startswith("[上下文压缩摘要") for m in out)
    # 最近 2 轮（4 条消息）保持原文
    recent_flat = [m for r in rounds[-2:] for m in r]
    assert recent_flat[0] in out and recent_flat[-1] in out
    # 摘要器被调用了
    assert summarizer.calls >= 1


def test_compressor_counts_stats():
    stats = RunStats()
    summarizer = FakeSummarizer()
    msgs = [HumanMessage(content="task")] + [m for i in range(6) for m in _big_round(i, 4000)]
    comp = Compressor(summarizer, threshold=4000, keep_recent=2, batch=4)
    comp.compress(msgs, stats)
    assert stats.llm_calls >= 1


def test_compressor_no_regret_skips_near_end():
    """无悔闸门：历史很长但剩余可摊销轮数=0（任务临尾）时，不应发起亏本压缩。"""
    summarizer = FakeSummarizer()
    msgs = [HumanMessage(content="task")] + [m for i in range(6) for m in _big_round(i, 4000)]
    comp = Compressor(summarizer, threshold=4000, keep_recent=2, batch=4)
    out = comp.compress(msgs, None, remaining_turns=0)
    assert out is msgs            # 无剩余轮数可摊销 → 原样返回，零成本
    assert summarizer.calls == 0


def test_compressor_no_regret_skips_when_small_batches():
    """全为小批（每批低于 min_batch_chars）时，没有值得压的批，应原样返回。"""
    summarizer = FakeSummarizer()
    msgs = [HumanMessage(content="task")]
    for i in range(6):
        msgs.extend(_big_round(i, size=200))  # 每轮仅 200 字符，远低于 min_batch_chars
    comp = Compressor(summarizer, threshold=4000, keep_recent=2, batch=4)
    out = comp.compress(msgs, None, remaining_turns=10)
    assert out is msgs
    assert summarizer.calls == 0
