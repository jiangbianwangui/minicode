"""工具层离线测试：在临时沙箱里验证 6 个工具与校验/越界拦截。"""

import pytest
from pathlib import Path

# 让导入在没有 installed package 的情况下也能工作
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from tools import call, TOOL_SCHEMAS, _validate  # noqa: E402


@pytest.fixture
def wd(tmp_path):
    return tmp_path


def test_write_and_read(wd):
    obs = call("WriteFile", {"path": "a.txt", "content": "hello\nworld"}, wd)
    assert "已写入" in obs
    obs = call("ReadFile", {"path": "a.txt"}, wd)
    assert "hello" in obs and "world" in obs


def test_edit_unique_and_ambiguous(wd):
    call("WriteFile", {"path": "b.txt", "content": "aaa bbb aaa"}, wd)
    # 唯一片段替换
    obs = call("EditFile", {"path": "b.txt", "old_string": "bbb", "new_string": "ZZZ"}, wd)
    assert "已替换 1 处" in obs
    # 不唯一应被拒绝
    obs2 = call("EditFile", {"path": "b.txt", "old_string": "aaa", "new_string": "x"}, wd)
    assert "不唯一" in obs2
    # 不存在应被拒绝
    obs3 = call("EditFile", {"path": "b.txt", "old_string": "nope", "new_string": "x"}, wd)
    assert "未找到" in obs3


def test_glob_and_grep(wd):
    call("WriteFile", {"path": "x.py", "content": "def foo(): pass\n"}, wd)
    call("WriteFile", {"path": "sub/y.py", "content": "import os\n"}, wd)
    obs = call("Glob", {"pattern": "**/*.py"}, wd)
    obs_norm = obs.replace("\\", "/")  # Windows 路径分隔符兼容
    assert "x.py" in obs_norm and "sub/y.py" in obs_norm
    obs = call("Grep", {"pattern": "def foo", "path": "."}, wd)
    assert "x.py" in obs and "def foo" in obs


def test_sandbox_rejects_escape(wd):
    # 尝试写到沙箱外
    obs = call("WriteFile", {"path": "../escape.txt", "content": "x"}, wd)
    assert "越界" in obs or "拒绝" in obs


def test_validation_missing_required(wd):
    ok, err = _validate("ReadFile", {})
    assert not ok and "path" in err


def test_validation_returns_schema_count():
    names = [t["function"]["name"] for t in TOOL_SCHEMAS]
    assert names == ["ReadFile", "WriteFile", "EditFile", "Bash", "Glob", "Grep"]


def test_bash_runs(wd):
    obs = call("Bash", {"command": "echo hi-from-agent"}, wd)
    assert "hi-from-agent" in obs


def test_bash_deny_destructive(wd):
    obs = call("Bash", {"command": "rm -rf /"}, wd)
    assert "黑名单" in obs or "拒绝" in obs
