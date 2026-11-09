"""t24：模糊需求 + 隐式契约 —— 只说"要什么"，不说"怎么做"

难度来源（这是真正的难点所在）：
1) **需求不写 API 形状**：prompt 只描述业务意图，函数签名必须从既有代码风格中推断。
   本任务在 `slots.py` 里留了一个空壳签名，逼 agent 先读代码而不是凭空发明。
2) **隐式契约**：`test_contract.py` 约束了"时间槽按本地时区边界对齐""结束时刻是开区间"等细节，
   若不自测就必然踩坑。
3) **必须处理空输入/重叠/跨日**等边界，且不得破坏既有测试。

这与"把需求写得含糊然后判它失败"完全不同：所有约束都是**可验证的、写在仓库里的**，
agent 只要肯读、肯测就能做对。考的是"信息搜集与验证"能力，不是"猜谜"能力。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""会议预约系统。"""
'''

_MODELS = '''"""领域模型。"""
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Slot:
    """时间槽：[start, end) 左闭右开，单位为分钟（自当日 00:00 起）。"""

    start: int
    end: int

    def overlaps(self, other):
        return self.start < other.end and other.start < self.end

    def __repr__(self):
        return f"Slot({self.start}, {self.end})"
'''

_ROOMS = '''"""会议室仓库。"""
from .models import Slot


class Room:
    def __init__(self, name, capacity=4):
        self.name = name
        self.capacity = capacity
        self.bookings = []

    def add(self, slot):
        self.bookings.append(slot)
        self.bookings.sort()

    def free_slots(self):
        """返回当前空闲（未被占用）的时间槽列表。"""
        if not self.bookings:
            return [Slot(0, 1440)]
        out, cursor = [], 0
        for b in self.bookings:
            if b.start > cursor:
                out.append(Slot(cursor, b.start))
            cursor = max(cursor, b.end)
        if cursor < 1440:
            out.append(Slot(cursor, 1440))
        return out


class RoomBook:
    def __init__(self):
        self._rooms = {}

    def add_room(self, room):
        self._rooms[room.name] = room
        return room

    def get(self, name):
        return self._rooms[name]

    def names(self):
        return sorted(self._rooms)
'''

_SLOTS = '''"""时间槽操作（本次要实现的部分）。"""
from .models import Slot


def merge_slots(slots):
    """把一组时间槽合并成互不重叠、按时间排序的最小集合。

    例如 [Slot(0, 10), Slot(5, 20), Slot(30, 40)] -> [Slot(0, 20), Slot(30, 40)]

    要求（由 test_contract.py 约束，请先读它）：
    - 左闭右开语义：Slot(0,10) 与 Slot(10,20) **相邻不重叠**，合并后应为 Slot(0, 20)
    - 空输入返回空列表
    - 单个输入返回等价的单元素列表
    - 输入可能乱序、可能包含完全包含关系的槽
    - 不得修改传入的列表本身
    """
    raise NotImplementedError("待实现")
'''

_FINDER = '''"""会议室查找（本次要实现的另一部分）。"""
from .slots import merge_slots


def find_room_slots(book, slots, duration, capacity=1):
    """在容量足够的会议室里，找出能容纳指定时长的空闲时段。

    要求（由 test_contract.py 约束，请先读它）：
    - 只考虑 capacity >= 参数 capacity 的会议室
    - 空闲时段 = 会议室空闲槽 与 slots 的交集（用 merge_slots 先归一）
    - 只保留长度 >= duration 的时段，结果按 (会议室名, start) 升序
    - 返回形如 [(room_name, Slot), ...]
    - 没有任何匹配时返回空列表
    """
    raise NotImplementedError("待实现")
'''

_TESTS_ROOM = '''"""既有测试：会议室基础行为，不可修改。"""
from meeting.models import Slot
from meeting.rooms import Room, RoomBook


def test_free_when_empty():
    r = Room("A")
    assert r.free_slots() == [Slot(0, 1440)]


def test_free_after_booking():
    r = Room("A")
    r.add(Slot(100, 200))
    assert r.free_slots() == [Slot(0, 100), Slot(200, 1440)]


def test_book_names():
    b = RoomBook()
    b.add_room(Room("B"))
    b.add_room(Room("A"))
    assert b.names() == ["A", "B"]
'''

_TESTS_CONTRACT = '''"""契约测试：描述 merge_slots / find_room_slots 的精确行为。"""
from meeting.models import Slot
from meeting.rooms import Room, RoomBook
from meeting.slots import merge_slots
from meeting.finder import find_room_slots


def test_merge_adjacent():
    # 左闭右开：相邻不算重叠，但要合并
    assert merge_slots([Slot(0, 10), Slot(10, 20)]) == [Slot(0, 20)]


def test_merge_overlap():
    assert merge_slots([Slot(0, 10), Slot(5, 20), Slot(30, 40)]) == [
        Slot(0, 20), Slot(30, 40)
    ]


def test_merge_contained():
    assert merge_slots([Slot(0, 100), Slot(20, 30)]) == [Slot(0, 100)]


def test_merge_empty_and_single():
    assert merge_slots([]) == []
    assert merge_slots([Slot(5, 9)]) == [Slot(5, 9)]


def test_merge_does_not_mutate_input():
    src = [Slot(10, 20), Slot(0, 5)]
    merge_slots(src)
    assert src == [Slot(10, 20), Slot(0, 5)]


def test_find_basic():
    book = RoomBook()
    a = book.add_room(Room("A", capacity=10))
    a.add(Slot(0, 100))
    a.add(Slot(200, 1440))
    res = find_room_slots(book, [Slot(0, 1440)], 60, capacity=5)
    assert ("A", Slot(100, 200)) in res


def test_find_capacity_filter():
    book = RoomBook()
    book.add_room(Room("small", capacity=2))
    res = find_room_slots(book, [Slot(0, 1440)], 60, capacity=5)
    assert res == []


def test_find_duration_filter():
    book = RoomBook()
    a = book.add_room(Room("A", capacity=10))
    a.add(Slot(0, 30))
    # 空闲为 Slot(30,1440)；查询窗口只到 60，交集仅 Slot(30,60) 长度 30 < 60，应不命中
    assert find_room_slots(book, [Slot(0, 60)], 60, capacity=1) == []
    # 时间窗放宽后同一房间应命中
    res = find_room_slots(book, [Slot(0, 1440)], 60, capacity=1)
    assert ("A", Slot(30, 1440)) in res
'''


def _setup(wd):
    pkg = wd / "meeting"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "models.py").write_text(_MODELS, encoding="utf-8")
    (pkg / "rooms.py").write_text(_ROOMS, encoding="utf-8")
    (pkg / "slots.py").write_text(_SLOTS, encoding="utf-8")
    (pkg / "finder.py").write_text(_FINDER, encoding="utf-8")
    (wd / "test_rooms.py").write_text(_TESTS_ROOM, encoding="utf-8")
    (wd / "test_contract.py").write_text(_TESTS_CONTRACT, encoding="utf-8")


def _verify(wd):
    for f in ["test_rooms.py", "test_contract.py"]:
        if not (wd / f).exists():
            return False, f"缺少 {f}"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_rooms.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"既有测试回归 rc={p.returncode} {tail!r}"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_contract.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"契约测试未通过 rc={p.returncode} {tail!r}"

    # 隐藏边界用例：跨多房排序、零长度时段
    code = (
        "from meeting.models import Slot\n"
        "from meeting.rooms import Room, RoomBook\n"
        "from meeting.slots import merge_slots\n"
        "from meeting.finder import find_room_slots\n"
        "assert merge_slots([Slot(0,5), Slot(5,5)]) == [Slot(0,5)] or merge_slots([Slot(0,5), Slot(5,5)]) == [Slot(0,5)]\n"
        "book = RoomBook()\n"
        "b = book.add_room(Room('B', capacity=9))\n"
        "a = book.add_room(Room('A', capacity=9))\n"
        "res = find_room_slots(book, [Slot(0, 1440)], 30, capacity=1)\n"
        "names = [r[0] for r in res]\n"
        "assert names == sorted(names), names\n"
        "assert set(names) == {'A','B'}, names\n"
        "print('OK')\n"
    )
    rc, out, err = run_python(wd, code=code)
    if rc != 0 or "OK" not in out:
        return False, f"隐藏边界用例未通过: {(err or out).strip()[-300:]!r}"

    return True, "契约测试 + 隐藏边界用例全过，既有测试无回归"


TASK = {
    "id": "t24",
    "name": "模糊需求+隐式契约",
    "prompt": (
        "`meeting/` 是一个会议预约系统。现在需要增加'按容量和时长找空会议室'的能力。\n\n"
        "**要实现的**：\n"
        "1) `meeting/slots.py` 的 `merge_slots(slots)` —— 合并重叠/相邻时间槽；\n"
        "2) `meeting/finder.py` 的 `find_room_slots(book, slots, duration, capacity)` —— "
        "在容量达标的会议室中找出足够长的空闲时段。\n\n"
        "这两个函数目前都是 `NotImplementedError`。**具体的边界规则没有写在提示里**——"
        "仓库中的 `test_contract.py` 是它们的权威说明（注意时间槽的左闭右开语义、"
        "相邻是否合并、是否允许修改入参、结果排序方式）。\n"
        "请先读 `test_contract.py` 与 `test_rooms.py`，再动手实现。\n"
        "完成后运行 `pytest test_rooms.py test_contract.py` 确认全部通过，"
        "既有测试文件不得修改。"
    ),
    "setup": _setup,
    "verify": _verify,
}
