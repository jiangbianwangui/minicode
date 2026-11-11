"""工具调用轨迹落盘（--trace）的离线测试。

由来：2026-11-11 排查 t34「连续 19 次 Bash 零交付」时，`runner` 只记了工具**名字**，
看不出它到底敲了什么命令 —— 而根因（沙箱拒绝文案在邀请重试）只能从命令文本读到。
当时只能临时 monkeypatch `agent.call` 才抓到。这个能力应该常驻，不该每次重造。

测试要锁住三件事：
1. 开启后每轮工具调用都落一行，且参数/结果都在；
2. 超长内容被截断（否则轨迹文件会失控）；
3. **关闭时零开销、且不会凭空造文件** —— 默认路径不能因为加了这个功能而变慢或变脏。
"""

import json
import sys
import pathlib

_here = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_here.parents[1]))

from langchain_core.messages import AIMessage  # noqa: E402

from agent import CodingAgent, _trace_line  # noqa: E402


class ScriptedModel:
    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, schemas):
        return self

    def invoke(self, messages):
        return self.script.pop(0)


def _ai(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def test_trace_records_tool_args(tmp_path):
    trace = tmp_path / "traces" / "t99.jsonl"
    model = ScriptedModel([
        _ai([{"name": "Bash", "args": {"command": "ls -la .."}, "id": "c1", "type": "tool"}]),
        AIMessage(content="完成"),
    ])
    agent = CodingAgent(workdir=tmp_path / "wd", llm=model, verbose=False, trace_path=trace)
    agent.run("随便跑个命令")

    lines = [json.loads(x) for x in trace.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    rec = lines[0]
    assert rec["tool"] == "Bash"
    # 关键：命令原文必须在，否则又一次「只知道它调了 Bash，不知道它调了什么」
    assert rec["args"]["command"] == "ls -la .."
    assert rec["ok"] is False          # 被沙箱拒绝
    assert "不可访问" in rec["obs"]     # 拒绝理由也要留证


def test_trace_truncates_long_content(tmp_path):
    line = _trace_line("WriteFile", {"content": "x" * 5000}, "y" * 5000, True, 3)
    rec = json.loads(line)
    assert len(rec["args"]["content"]) == 300
    assert len(rec["obs"]) == 300
    assert rec["step"] == 3


def test_no_trace_file_when_disabled(tmp_path):
    model = ScriptedModel([
        _ai([{"name": "Bash", "args": {"command": "ls"}, "id": "c1", "type": "tool"}]),
        AIMessage(content="完成"),
    ])
    agent = CodingAgent(workdir=tmp_path / "wd", llm=model, verbose=False)
    agent.run("跑个命令")
    assert agent.trace_path is None
    assert not list(tmp_path.rglob("*.jsonl"))
