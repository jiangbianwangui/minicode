"""PRESERVE_TOOLS 必须与真实注册的工具名严格对齐。

历史问题（2026-10）：这里曾手写 "ViewFile" / "ReadLines" / "GrepFile"，
三个工具都不存在 —— 集合永远匹配不到，配置与实现悄悄脱钩，
而当时没有任何测试能发现这件事。

这三条测试分别锁住三件事：
1. 集合里的名字必须真的注册过（防漂移）
2. 文件类工具必须在集合里（否则压缩会切断模型按字节行动的依据）
3. 非文件类工具必须不在集合里（否则压缩等于不生效）
"""

import pytest

from context import PRESERVE_TOOLS
from tools import TOOL_SCHEMAS


def _registered_names() -> set[str]:
    return {s["function"]["name"] for s in TOOL_SCHEMAS}


def test_preserve_tools_all_registered():
    unknown = PRESERVE_TOOLS - _registered_names()
    assert not unknown, f"PRESERVE_TOOLS 含未注册的工具名：{sorted(unknown)}"


@pytest.mark.parametrize("name", ["ReadFile", "WriteFile", "EditFile"])
def test_file_tools_are_preserved(name):
    assert name in PRESERVE_TOOLS, f"{name} 是文件类工具，必须保留原文"


@pytest.mark.parametrize("name", ["Bash", "Glob", "Grep"])
def test_non_file_tools_not_preserved(name):
    assert name not in PRESERVE_TOOLS, f"{name} 属可丢轮次，不应进保留集合"


def test_preserve_is_non_empty():
    assert PRESERVE_TOOLS, "保留集合为空意味着压缩会吃掉文件内容"
