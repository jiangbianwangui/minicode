"""t18：让失败测试通过 —— 考验「运行 → 读报错 → 定位根因」。

难度来源：不看测试输出就无法知道预期行为；且必须修根因而非症状（改测试文件直接判失败）。
"""

import subprocess
import sys

_STACK = '''class Stack:
    """一个简单的栈。"""

    def __init__(self):
        self._items = []

    def push(self, x):
        self._items.append(x)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop(0)

    def peek(self):
        if not self._items:
            return None
        return self._items[-1]

    def is_empty(self):
        return len(self._items) > 0

    def size(self):
        return len(self._items)
'''

_TESTS = '''from stack import Stack


def test_push_pop_order():
    s = Stack()
    s.push(1)
    s.push(2)
    s.push(3)
    assert s.pop() == 3
    assert s.pop() == 2
    assert s.pop() == 1


def test_is_empty():
    s = Stack()
    assert s.is_empty() is True
    s.push(0)
    assert s.is_empty() is False


def test_empty_pop_raises():
    s = Stack()
    try:
        s.pop()
        raise AssertionError("should raise")
    except IndexError:
        pass


def test_peek_size():
    s = Stack()
    assert s.peek() is None
    assert s.size() == 0
    s.push("a")
    assert s.peek() == "a"
    assert s.size() == 1
'''


def _setup(wd):
    (wd / "stack.py").write_text(_STACK, encoding="utf-8")
    (wd / "test_stack.py").write_text(_TESTS, encoding="utf-8")


def _verify(wd):
    tf = wd / "test_stack.py"
    sf = wd / "stack.py"
    if not tf.exists() or not sf.exists():
        return False, "缺少 stack.py 或 test_stack.py"

    # 硬性约束：不允许靠改测试/删测试来"通过"
    if tf.read_text(encoding="utf-8").strip() != _TESTS.strip():
        return False, "测试文件被修改（必须修 stack.py，不能改测试）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_stack.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"pytest 未通过 rc={p.returncode} {tail!r}"

    return True, "未改动测试文件且全部通过"


TASK = {
    "id": "t18",
    "name": "读报错修复失败测试",
    "prompt": (
        "`stack.py` 实现了一个栈，但 `test_stack.py` 目前是**失败的**。\n"
        "请运行 `pytest test_stack.py` 查看失败信息，然后**只修改 stack.py** "
        "让全部测试通过。\n"
        "严禁修改或删除 test_stack.py（改动即判定失败）。"
    ),
    "setup": _setup,
    "verify": _verify,
}
