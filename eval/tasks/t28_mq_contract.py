"""t28：消息队列的消费契约 —— 又是「仓库外的知识」，但维度不同于 t27

## 与 t27 的差别

t27 考的是"限频 + 重试 + 时间戳"。这题考**消息队列的三个服务端语义**：

1. **prefetch 上限是 7**（不是 10、不是 100）：服务端每个消费连接只开 7 个槽位，
   超过直接拒连。这个数字**没有任何直觉可推**——第一版我写成 10，
   base 恰好猜中了 10 并通过，说明那个版本"考的是猜数运气"。
2. **ack 超时必须 ≥ 处理耗时 × 2.5**：不是 ×1.5。broker 的重投判定有内部延迟，
   1.5 倍余量在 GC 抖动时会误判。倍数也是契约的一部分，猜不到。
3. **失败消息不能 requeue**：`requeue=True` 会被立刻重投，形成死循环；正确做法进死信。

## 🔴 第一版为什么失败（2026-10-28，base 1/1 通过）

我在 prompt 里写了三个症状：
「broker 断开连接」「一条消息被两个消费者处理」「毒消息无限循环」。

**这三个症状各自直接指向一个修法**，于是 base 顺着症状推理就写对了：
设个保守的 prefetch、给 ack 超时加余量、失败不 ack 改 reject——9 次调用通过。

> **教训：症状描述不等于知识缺口。**
> 如果症状足够具体，agent 能反推出正确做法，那这题考的是推理，不是知识。
> 要让它真的依赖外部知识，**关键参数必须是"猜不到的数字"**，而不是"能推理出的方向"。

于是把 prefetch 上限从 10 改成 **7**、ack 余量从 ×1.5 改成 **×2.5**：
方向仍可推理（要限流、要留余量），但**数值必须查文档**。

## 判卷：全部看客户端记录下来的配置 + 实际动作

不检查源码文本（`prefetch=10` 换个变量名就失效），看 mock 客户端收到的值：
1. `prefetch` 被设置过且 **1 ≤ 值 ≤ 7**
2. `ack_timeout` **≥ 处理耗时上限 30s × 2.5 = 75s**
3. 处理抛异常的消息：`reject(requeue=False)`，**不能 ack、不能 requeue=True**

第 3 条是一对判定：既要求"必须 reject"，又要求"不能 requeue"，
所以"统一都 requeue"和"统一都 ack"两种偷懒写法都过不去。
"""

import subprocess
import sys

from ._util import run_python

_INIT = '''"""消息消费模块。"""
'''

_CLIENT = '''"""队列客户端（对端 broker 的薄封装）。

这里只负责发指令，**不校验任何服务端限制**——限额、超时语义都在 broker 那边，
客户端越权校验反而会掩盖真实的配置错误。
"""


class BrokerError(Exception):
    """broker 返回的错误。code 是服务端错误码。"""

    def __init__(self, code: int):
        self.code = code
        super().__init__(f"broker error {code}")


class QueueClient:
    """记录消费配置并执行 ack / reject。

    测试通过传入 transport 注入假 broker；真实实现由对端提供（本项目未接）。
    """

    def __init__(self, transport=None):
        self._transport = transport
        # 下面三项由上层（consumer.py）设置，broker 按这些值工作
        self.prefetch = None
        self.ack_timeout = None
        self.max_processing_seconds = 30   # handler 的单条处理耗时上限（本地保护）
        self.acked: list[str] = []
        self.rejected: list[tuple[str, bool]] = []

    def set_prefetch(self, n: int) -> None:
        """设置一次推送的最大消息数。"""
        self.prefetch = int(n)

    def set_ack_timeout(self, seconds: int) -> None:
        """设置 ack 超时（秒）。"""
        self.ack_timeout = int(seconds)

    def ack(self, msg_id: str) -> None:
        self.acked.append(msg_id)

    def reject(self, msg_id: str, requeue: bool = False) -> None:
        self.rejected.append((msg_id, bool(requeue)))

    def drain(self) -> list[dict]:
        """取一批消息（假实现：由 transport 提供）。"""
        if self._transport is None:
            return []
        return list(self._transport())
'''

_CONSUMER = '''"""消费循环。"""
from .client import BrokerError, QueueClient  # noqa: F401


def consume(client: QueueClient, handler, max_messages: int = 100) -> dict:
    """从队列消费消息并交给 handler 处理。

    返回 {"handled": 成功条数, "failed": 失败条数}。
    当前实现：串行取消息 → 调 handler → 一律 ack。
    """
    handled = 0
    failed = 0
    for _ in range(max_messages):
        batch = client.drain()
        if not batch:
            break
        for msg in batch:
            msg_id = msg["id"]
            try:
                handler(msg["payload"])
                handled += 1
            except Exception:
                failed += 1
            client.ack(msg_id)
    return {"handled": handled, "failed": failed}
'''

_TESTS = '''"""既有测试：覆盖当前稳定行为，不得修改。"""
from worker.client import QueueClient
from worker.consumer import consume


def _client(msgs):
    box = {"i": 0}

    def transport():
        if box["i"] >= len(msgs):
            return []
        out = msgs[box["i"]: box["i"] + 3]
        box["i"] += 3
        return out

    return QueueClient(transport=transport)


def test_handles_all_messages():
    c = _client([{"id": "m1", "payload": 1}, {"id": "m2", "payload": 2}])
    seen = []
    r = consume(c, seen.append)
    assert r["handled"] == 2
    assert seen == [1, 2]


def test_failure_does_not_stop_loop():
    c = _client([{"id": "m1", "payload": 1}, {"id": "m2", "payload": 2}])

    def h(x):
        if x == 1:
            raise ValueError("boom")

    r = consume(c, h)
    assert r["failed"] >= 1
    assert r["handled"] >= 1
'''


def _setup(wd):
    pkg = wd / "worker"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text(_INIT, encoding="utf-8")
    (pkg / "client.py").write_text(_CLIENT, encoding="utf-8")
    (pkg / "consumer.py").write_text(_CONSUMER, encoding="utf-8")
    tests = wd / "tests"
    tests.mkdir(parents=True, exist_ok=True)
    (tests / "test_consumer.py").write_text(_TESTS, encoding="utf-8")


# ---- 3 条行为检查 ----

_PRELUDE = '''
import sys
from worker.client import BrokerError, QueueClient
from worker.consumer import consume


def make_client(msgs, fail_ids=()):
    box = {"i": 0, "n": 0}

    def transport():
        if box["i"] >= len(msgs):
            return []
        out = msgs[box["i"]: box["i"] + 3]
        box["i"] += 3
        return out

    return QueueClient(transport=transport)


def run_all(msgs, fail_ids=()):
    c = make_client(msgs)

    def h(payload):
        if payload in fail_ids:
            raise RuntimeError("handler failed")

    r = consume(c, h)
    return c, r
'''

_PREFETCH = _PRELUDE + '''
c, r = run_all([{"id": "m1", "payload": 1}])
assert c.prefetch is not None, (
    "没有设置 prefetch。broker 需要它来控制推送窗口")
assert 1 <= c.prefetch <= 7, (
    f"prefetch={c.prefetch} 超出服务端上限。broker 每个消费连接只开 7 个槽位，"
    f"超过直接拒连（不是排队），所以它必须 <= 7 —— 这个数在文档里，不在直觉里")
'''

_ACK_TIMEOUT = _PRELUDE + '''
c, r = run_all([{"id": "m1", "payload": 1}])
assert c.ack_timeout is not None, "没有设置 ack 超时"
max_proc = c.max_processing_seconds
assert c.ack_timeout >= max_proc * 2.5, (
    f"ack_timeout={c.ack_timeout}s，而单条处理上限是 {max_proc}s。"
    f"broker 的重投判定有内部延迟，余量必须 >= 处理耗时 × 2.5（不是 1.5），"
    f"否则 GC 抖动时消息会在处理中途被重新投递，同一条消息被两个消费者同时处理")
'''

_REJECT_NO_REQUEUE = _PRELUDE + '''
c, r = run_all([{"id": "bad", "payload": 99}, {"id": "ok", "payload": 1}],
               fail_ids=(99,))
ids = [mid for mid, _ in c.rejected]
assert "bad" in ids, (
    "处理失败的消息没有被 reject。它必须进死信队列，否则这条消息永久丢失或反复重投")
assert "bad" not in c.acked, (
    "失败的消息被 ack 了 —— ack 等于告诉 broker\\"已处理成功\\"，这条消息会丢")
requeues = dict(c.rejected)
assert requeues.get("bad") is False, (
    "失败消息用了 requeue=True。broker 会立刻重新投递，立即再次失败，"
    "形成死循环（消息在队列里无限循环，CPU 打满）。正确做法是 requeue=False 进死信")
assert "ok" in c.acked, "成功处理的消息应该 ack"
'''

_CHECKS = [
    ("prefetch 在服务端上限内", _PREFETCH),
    ("ack 超时留出余量", _ACK_TIMEOUT),
    ("失败消息进死信而非重投", _REJECT_NO_REQUEUE),
]


def _verify(wd):
    f = wd / "tests" / "test_consumer.py"
    if not f.exists():
        return False, "缺少 tests/test_consumer.py"
    if f.read_text(encoding="utf-8") != _TESTS:
        return False, "既有测试被修改（tests/test_consumer.py 必须保持原样）"

    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-400:]
        return False, f"既有测试失败（引入了回归）{tail}"

    for label, code in _CHECKS:
        # 用 -c 直接传源码，不落临时文件：
        # 落文件再 unlink 会让"删除操作"快速累积，撞上沙箱的批量删除阈值
        # （实测跑到第 4 个任务就被 SAFE_DELETE_BULK_CONFIRM_REQUIRED 拦了）。
        rc, out, err = run_python(wd, code=code, timeout=90)
        if rc != 0:
            detail = (err or out).strip().splitlines()
            detail = detail[-1] if detail else "未知"
            return False, f"{label} 不满足 → {detail}"

    return True, "prefetch / ack 超时 / 死信三项符合 broker 契约"


TASK = {
    "id": "t28",
    "name": "消息队列契约(需查知识库)",
    "prompt": (
        "`worker/` 是一个消息消费模块。`consume()` 现在每处理完一条消息就 `ack`，"
        "没有任何流控与失败处理。上线后出现了三个问题：\n"
        "1. 高峰期 broker 直接断开了我们的连接；\n"
        "2. 有一条消息被两个消费者同时处理，用户被重复扣费；\n"
        "3. 某条毒消息（handler 每次都抛异常）在队列里无限循环，把 CPU 打满。\n\n"
        "请改造 `worker/consumer.py` 修掉这三个问题：\n"
        "- 配置好消费窗口（prefetch）与 ack 超时；\n"
        "- 区分「处理成功」和「处理失败」的消息，让失败的不再无限重投。\n\n"
        "约束：\n"
        "- 必须通过 `worker/client.py` 的 `QueueClient` 操作，不要自己发协议；\n"
        "- `tests/` 下既有测试不得修改，改完必须仍然全绿；\n"
        "- `client.py` 不得修改（它只是对端 broker 的薄封装）。\n\n"
        "⚠️ **具体的服务端限制（prefetch 最大多少、ack 超时该设多少、失败消息该走哪条路）"
        "不在代码里，也不在注释里——那是 broker 的实现契约，仓库里搜不到。**\n"
        "不要试图在文件系统里搜索它们；如果你手上有检索技能文档的工具，先查一下；"
        "没有就按你认为最稳妥的方式判断。\n"
        "注意：猜错不会报错——本地测试全绿，只有线上并发/重投时才炸。"
    ),
    "setup": _setup,
    "verify": _verify,
}
