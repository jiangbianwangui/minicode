"""LangGraph 版主循环离线测试：用假的 langchain 模型验证图编排与工具执行闭环。

不发起任何网络/真实 API 请求，只验证：agent(决策) -> tools(执行) -> agent 的
状态机走向正确，且工具真的被执行（文件被写出 / 步数护栏生效）。
"""

import sys
import pathlib

_here = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(_here.parents[1]))  # 仓库根（能 import agent）

from langchain_core.messages import AIMessage  # noqa: E402

from agent import CodingAgent  # noqa: E402


class ScriptedModel:
    """按预设序列返回 AIMessage（带/不带 tool_calls）的假模型。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, schemas):
        # 验证 agent 真的把 schema 交给了模型（等价于手写版的 tools 参数）
        assert schemas, "必须传入 tools schema"
        return self

    def invoke(self, messages):
        self.calls += 1
        return self.script.pop(0)


def _ai(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def test_loop_writes_then_finishes(tmp_path):
    # 第1轮：调用 WriteFile；第2轮：不再调用工具，给出最终总结
    model = ScriptedModel([
        _ai([{"name": "WriteFile", "args": {"path": "hello.py", "content": "print(1+1)"}, "id": "c1", "type": "tool"}]),
        AIMessage(content="已完成：创建了 hello.py"),
    ])
    agent = CodingAgent(workdir=tmp_path, llm=model, verbose=False)
    result = agent.run("创建一个 hello.py")
    assert model.calls == 2
    assert (tmp_path / "hello.py").read_text() == "print(1+1)"
    # 最终返回的是最后一条无 tool_calls 的 AIMessage 内容
    assert result == "已完成：创建了 hello.py"


def test_loop_two_tool_rounds(tmp_path):
    model = ScriptedModel([
        _ai([{"name": "Glob", "args": {"pattern": "**/*.py"}, "id": "c1", "type": "tool"}]),
        _ai([{"name": "ReadFile", "args": {"path": "x.py"}, "id": "c2", "type": "tool"}]),
        _ai([]),
    ])
    agent = CodingAgent(workdir=tmp_path, llm=model, verbose=False)
    result = agent.run("看看有什么 python 文件")
    assert model.calls == 3  # 3 轮模型调用


def test_max_iter_guard(tmp_path):
    # 永不结束：每轮都返回工具调用；recursion_limit 应截断
    model = ScriptedModel([
        _ai([{"name": "Bash", "args": {"command": "echo loop"}, "id": f"c{i}", "type": "tool"}])
        for i in range(5)
    ])
    agent = CodingAgent(workdir=tmp_path, llm=model, max_iter=3, verbose=False)
    result = agent.run("无限循环任务")
    assert "最大步数" in result
    # recursion_limit = max_iter + 3，模型最多被调用 max_iter+1 次左右
    assert model.calls <= 5
