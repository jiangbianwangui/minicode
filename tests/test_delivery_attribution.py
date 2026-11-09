"""失败归因的单测：**必须先分清「零交付」和「交付了但错」**。

两者在通过率上完全一样（都是 0），但归因相反：
- 零交付 = agent 从没进入"改代码"这一步 → 通常是我的任务/prompt 有缺陷
  （t27 第一次跑：prompt 让它查文档，它没有工具，于是 `find /` 烧了 8 轮，零改动）
- 交付了但错 = 改了，但改错 → 这才是"知识/判断不够"，是优化该瞄准的

分不清，就会把"我的 bug"记成"它不行"，A/B 结论直接反。
"""

import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from eval.metrics import build_metrics  # noqa: E402


class _Stats:
    def __init__(self, writes=0, status="done", calls=6, tools=None):
        self.llm_calls = calls
        self.tool_calls = tools or [("Bash", True)] * calls
        self.status = status
        self.llm_latencies = [1.0]
        self.read_paths = []
        self.writes_ok = writes
        self.stall_warned = False
        self.stall_stopped = False
        self.stall_warn_at = None
        self.stall_idle_max = 0


_T = {"id": "x", "name": "x"}


def test_zero_writes_is_no_delivery():
    """一个字没改 → 必须归为 no_delivery，而不是 max_iter / wrong_behavior。"""
    m = build_metrics(_T, False, "达到最大步数仍未结束", _Stats(writes=0, status="max_iter"), 15)
    assert m["fail_reason"] == "no_delivery"
    assert m["delivered"] is False


def test_writes_but_wrong_keeps_normal_reason():
    """改过文件 → 保留正常归因（这才是真正的能力问题）。"""
    m = build_metrics(_T, False, "hidden AssertionError: 值不对", _Stats(writes=2), 15)
    assert m["fail_reason"] == "wrong_behavior"
    assert m["delivered"] is True


def test_passed_never_no_delivery():
    """通过的任务不该被这条规则改写。"""
    m = build_metrics(_T, True, "", _Stats(writes=0), 15)
    assert m["fail_reason"] == "-"
