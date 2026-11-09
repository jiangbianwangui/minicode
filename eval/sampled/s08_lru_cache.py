"""s08 LRU 缓存（容量与淘汰规则全给）。

抽样任务（见 eval/SAMPLED.md）：规格全给，不需要仓库外知识。
"""

import subprocess
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tasks._util import run_python  # noqa: E402

_STUB = '''"""一个定容量的 LRU 缓存。"""


class LRU:
    def __init__(self, capacity: int):
        """capacity <= 0 → 抛 ValueError。"""
        raise NotImplementedError("尚未实现")

    def get(self, key):
        """命中返回对应值，并把该 key 记为最近使用；未命中返回 None。"""
        raise NotImplementedError("尚未实现")

    def put(self, key, value) -> None:
        """写入。key 已存在则更新值并记为最近使用（不增加条目数）；
        超出容量时淘汰最久未使用的那个。"""
        raise NotImplementedError("尚未实现")

    def keys(self) -> list:
        """按「最久未使用 → 最近使用」的顺序返回全部 key。"""
        raise NotImplementedError("尚未实现")
'''

_TESTS = '''"""既有测试：容量与基本读写。"""

import pytest

from cache.lru import LRU


def test_put_get():
    c = LRU(2)
    c.put("a", 1)
    assert c.get("a") == 1
    assert c.get("missing") is None


def test_capacity_must_be_positive():
    with pytest.raises(ValueError):
        LRU(0)
'''

_EVICT = '''
from cache.lru import LRU

c = LRU(2)
c.put("a", 1); c.put("b", 2); c.put("c", 3)
assert c.get("a") is None, "容量 2 写入第三个时，最久未用的 a 应被淘汰"
assert c.get("b") == 2 and c.get("c") == 3
'''

_GET_REFRESHES = '''
from cache.lru import LRU

c = LRU(2)
c.put("a", 1); c.put("b", 2)
c.get("a")          # a 变成最近使用
c.put("c", 3)       # 应淘汰 b（不是 a）
assert c.get("b") is None, "get 命中后应刷新为最近使用，被淘汰的应是 b"
assert c.get("a") == 1
'''

_UPDATE_REFRESHES = '''
from cache.lru import LRU

c = LRU(2)
c.put("a", 1); c.put("b", 2)
c.put("a", 10)      # 更新已有 key：不增加条目数，且 a 变最近使用
assert c.keys() == ["b", "a"], f"key 顺序应为最久→最近 ['b', 'a']，实际 {c.keys()}"
assert c.get("a") == 10
'''

_KEYS_ORDER = '''
from cache.lru import LRU

c = LRU(3)
for k in "xyz":
    c.put(k, k)
assert c.keys() == ["x", "y", "z"], c.keys()
c.get("x")
assert c.keys() == ["y", "z", "x"], c.keys()
'''

_NO_OVERFLOW = '''
from cache.lru import LRU

c = LRU(2)
for i in range(10):
    c.put(f"k{i}", i)
assert len(c.keys()) == 2, f"容量 2 时条目数不应超过 2，实际 {c.keys()}"
assert c.keys() == ["k8", "k9"], c.keys()
'''

_CHECKS = [
    ("超容量淘汰最久未用", _EVICT),
    ("get 命中刷新顺序", _GET_REFRESHES),
    ("更新已有 key 不增条目且刷新", _UPDATE_REFRESHES),
    ("keys 顺序（最久→最近）", _KEYS_ORDER),
    ("连续写入不超容量", _NO_OVERFLOW),
]


def setup(wd):
    (wd / "cache").mkdir(parents=True, exist_ok=True)
    (wd / "cache" / "__init__.py").write_text("", encoding="utf-8")
    (wd / "cache" / "lru.py").write_text(_STUB, encoding="utf-8")
    (wd / "tests").mkdir(parents=True, exist_ok=True)
    (wd / "tests" / "test_lru.py").write_text(_TESTS, encoding="utf-8")


def verify(wd):
    f = wd / "tests" / "test_lru.py"
    if not f.exists():
        return False, "缺少 tests/test_lru.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_lru.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试失败 {tail}"

    for label, code in _CHECKS:
        rc, out, err = run_python(wd, code=code, timeout=60)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            msg = detail[-1] if detail else ""
            return False, f"未满足：{label} —— {msg}"
    return True, "5 项行为符合需求"


SOLUTION = '''"""一个定容量的 LRU 缓存。"""


class LRU:
    def __init__(self, capacity):
        if capacity <= 0:
            raise ValueError("capacity 必须为正")
        self._cap = capacity
        self._data = {}

    def get(self, key):
        if key not in self._data:
            return None
        value = self._data.pop(key)
        self._data[key] = value
        return value

    def put(self, key, value):
        if key in self._data:
            self._data.pop(key)
        self._data[key] = value
        while len(self._data) > self._cap:
            oldest = next(iter(self._data))
            self._data.pop(oldest)

    def keys(self):
        return list(self._data)
'''

_MUT_NO_REFRESH_ON_GET = SOLUTION.replace(
    """        if key not in self._data:
            return None
        value = self._data.pop(key)
        self._data[key] = value
        return value""",
    """        return self._data.get(key)""",
)

_MUT_EVICT_NEWEST = SOLUTION.replace(
    "            oldest = next(iter(self._data))",
    "            oldest = list(self._data)[-2]",
)

MUTANTS = {
    "get 不刷新顺序（读不影响淘汰）": _MUT_NO_REFRESH_ON_GET,
    "淘汰第二新的（差一位）": _MUT_EVICT_NEWEST,
}

TASK = {
    "id": "s08",
    "name": "LRU 缓存",
    "prompt": (
        "在 cache/lru.py 里实现 LRU 类（三个方法都已给出签名和 docstring）。\n"
        "需求如下（规格已给全，不需要查任何外部文档）：\n"
        "- 构造时 capacity <= 0 → 抛 ValueError。\n"
        "- get(key)：命中返回对应值并把它记为最近使用；未命中返回 None。\n"
        "- put(key, value)：key 已存在则更新值并记为最近使用（条目数不变）；\n"
        "  写入后若条目数超过容量，淘汰最久未使用的那个。\n"
        "- keys()：按「最久未使用 → 最近使用」的顺序返回全部 key。\n"
        "既有测试在 tests/test_lru.py，必须保持原样并通过。\n"
        "改完自己跑一遍 `python -m pytest tests -q` 确认。"
    ),
    "setup": setup,
    "verify": verify,
}
