"""停滞检测（stall detector）测试。

覆盖两类风险：
1. **误报**：解题前连续读多个不同文件是正常推进，不能被判成空转
   （t26 的快路径就是 ReadFile×13 之后才动手）。
2. **漏报**：反复读同一文件 / 重复同样的命令，必须被识别并被拦下。
"""

import sys
import pathlib

_here = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_here.parents[1]))  # cli_coding_agent
sys.path.insert(0, str(_here.parents[2]))  # agent-prototypes 根

from langchain_core.messages import AIMessage  # noqa: E402

from stall import StallDetector  # noqa: E402
from agent import CodingAgent  # noqa: E402


def _read(path, ok=True):
    return ("ReadFile", ok, {"path": path})


def _write(path, ok=True):
    return ("WriteFile", ok, {"path": path})


def _bash(cmd, ok=True):
    return ("Bash", ok, {"command": cmd})


# ---------------- 单元：判据本身 ----------------


def test_reading_new_files_is_progress():
    """连续读 13 个不同文件 → 一直是推进，绝不能警告（防误报）。"""
    d = StallDetector(warn_after=3, stop_after=5)
    for i in range(13):
        assert d.observe([_read(f"f{i}.py")]) is None
    assert d.idle == 0
    assert d.warned is False


def test_reading_same_file_repeatedly_warns():
    d = StallDetector(warn_after=3, stop_after=5)
    events = [d.observe([_read("same.py")]) for _ in range(5)]
    # 第 1 次读是新信息，之后 3 次重复 → 第 4 次触发警告
    assert events[:3] == [None, None, None]
    assert events[3] == "warn"
    assert d.warned is True
    assert d.warn_at == 4


def test_write_resets_idle():
    d = StallDetector(warn_after=3, stop_after=6)
    d.observe([_read("a.py")])
    d.observe([_read("a.py")])
    d.observe([_read("a.py")])
    assert d.idle == 2
    assert d.observe([_write("a.py")]) is None
    assert d.idle == 0


def test_after_warning_still_idle_then_stops():
    d = StallDetector(warn_after=2, stop_after=4)
    d.observe([_read("x.py")])
    events = [d.observe([_read("x.py")]) for _ in range(6)]
    assert "warn" in events
    assert events[-1] == "stop"
    assert d.stopped is True


def test_failed_calls_are_not_progress():
    """工具报错的轮次不算进展。"""
    d = StallDetector(warn_after=2, stop_after=4)
    for _ in range(3):
        d.observe([_read("bad.py", ok=False)])
    assert d.idle == 3


def test_different_bash_commands_are_progress():
    """agent 写脚本批量改文件时命令各不相同，这是真实产出，不能判空转。"""
    d = StallDetector(warn_after=3, stop_after=5)
    for i in range(6):
        assert d.observe([_bash(f"python patch_{i}.py")]) is None
    assert d.idle == 0


def test_identical_bash_is_stall():
    d = StallDetector(warn_after=2, stop_after=4)
    assert d.observe([_bash("ls -la")]) is None          # 第一次是新动作
    assert d.observe([_bash("ls  -la")]) is None         # 空白归一化后同一条 → idle=1
    assert d.observe([_bash("ls -la ")]) == "warn"       # idle=2 → 警告


# ---------------- 集成：真的会终止 ----------------


class ScriptedModel:
    def __init__(self, script):
        self.script = list(script)

    def bind_tools(self, schemas):
        return self

    def invoke(self, messages):
        return self.script.pop(0)


def _ai(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def test_agent_stops_when_stalled(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    script = [_ai([{"name": "ReadFile", "args": {"path": "a.py"}, "id": f"c{i}"}])
              for i in range(12)]
    llm = ScriptedModel(script)
    agent = CodingAgent(
        workdir=tmp_path, llm=llm, verbose=False, max_iter=20,
        stall=True, stall_warn_after=3, stall_stop_after=5,
    )
    agent.run("什么都不用做")
    assert agent.stats.stall_stopped is True
    assert agent.stats.status == "stalled"
    # 关键：真的提前终止了，而不是跑满 20 轮
    assert agent.stats.llm_calls < 12


def test_agent_without_stall_keeps_going(tmp_path):
    """关掉之后行为必须和以前完全一致（可插拔）。"""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    script = [_ai([{"name": "ReadFile", "args": {"path": "a.py"}, "id": f"c{i}"}])
              for i in range(6)]
    llm = ScriptedModel(script)
    agent = CodingAgent(workdir=tmp_path, llm=llm, verbose=False, max_iter=20)
    agent.run("什么都不用做")
    assert agent.stats.stall_stopped is False
    assert agent.stats.stall_warned is False
    assert agent.stats.llm_calls == 6
