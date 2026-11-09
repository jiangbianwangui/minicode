"""停滞检测（stall detection）：识别 agent「反复探索但没有任何产出」的空转状态。

## 为什么需要它（2026-10-23 实测）

同一任务、同一配置、同一 temperature 下，token 分布不是正态，而是**双峰**：

| 簇 | 次数 | 均值 token | 工具步数 |
| --- | ---: | ---: | --- |
| 快路径（想通策略） | 4 | 66,331 | 11–17 |
| 空转（没想通，绕圈） | 6 | 410,118 | 39–40（跑满） |

**相差 6.2 倍。** 一个 18% 的优化效应被这 6 倍路径方差完全淹没，所以 Batch F/G/H
反复测出"t 值极低"——不是效应小，是方差来源是策略分叉。
**在空转被治理前，任何 A/B 都不可能显著。**

## 判据：看「有没有新信息 / 有没有副作用」，而不是「有没有写文件」

这一点很关键。解题前连续读 12 个**不同**的文件是完全正常的推进
（t26 的快路径就是 `ReadFile×13` 之后才动手），绝不能算空转。
所以判据是：

- 本轮有一个**之前没见过的读/搜/命令签名** → 有进展（在做新的事）
- 本轮有**成功的写操作** → 有进展（在产出副作用）
- 两者都没有 → 空转轮 +1

## 动作：软干预 + 兜底终止

- 连续 `warn_after` 轮无进展 → 注入一次进度警告（加进 system prompt，不污染对话历史）
- 警告后继续无进展到 `stop_after` 轮 → 终止，status = "stalled"

只警告不终止的话，空转仍会烧光预算；直接终止又会误杀"正在想"的轨迹。
所以两级：先给一次机会，再兜底。
"""

WRITE_TOOLS = {"WriteFile", "EditFile"}


def _signature(name: str, args: dict) -> tuple:
    """给一次工具调用生成「意图签名」，用于判断是不是在重复同一件事。

    只看"做了哪一类事、对象是谁"，不看具体参数细节——否则 agent 每次微调命令
    都会被算成新动作，永远判不出空转。
    """
    args = args or {}
    path = str(args.get("path") or args.get("file_path") or "")
    if name == "ReadLines":
        return ("read", f"{path}:{args.get('start', args.get('offset', 0))}")
    if name in ("ReadFile", "ViewFile"):
        return ("read", path)
    if name in WRITE_TOOLS:
        return ("write", path)
    if name in ("Grep", "GrepFile"):
        return ("search", str(args.get("pattern", ""))[:80])
    if name == "Glob":
        return ("glob", str(args.get("pattern", ""))[:80])
    if name == "Bash":
        cmd = " ".join(str(args.get("command", "")).split())
        return ("bash", cmd[:100])
    return (name, "")


STALL_HINT = """

## ⚠️ 进度警告（系统自动检测到停滞）
你已经连续 {idle} 轮**没有获得任何新信息、也没有成功修改任何文件**。
继续用同样的方式重复探索不会带来新线索。

请立刻切换策略，二选一：
1) 基于**已经掌握的信息直接动手修改**（不必先看完所有内容）；
2) 如果确实卡住，先用一句话说清你缺哪条信息，然后只做一次针对性查询。
"""


class StallDetector:
    """记录每轮是否产生进展，并在停滞时给出 warn / stop 事件。"""

    def __init__(self, warn_after: int = 6, stop_after: int = 10) -> None:
        self.warn_after = max(2, int(warn_after))
        self.stop_after = max(self.warn_after + 1, int(stop_after))
        self._seen: set[tuple] = set()
        self.idle = 0            # 连续无进展轮数
        self.idle_max = 0        # 全程最大连续空转轮数（落盘用）
        self.warned = False
        self.stopped = False
        self.warn_at: int | None = None   # 第几轮触发的警告
        self.rounds = 0

    def observe(self, calls: list[tuple[str, bool, dict]]) -> str | None:
        """观察一轮工具调用，返回 None / 'warn' / 'stop'。

        calls: [(工具名, 是否成功, 参数), ...]，本轮可能有多个（并行调用）。
        """
        self.rounds += 1
        progressed = False
        for name, ok, args in calls:
            if not ok:
                continue
            if name in WRITE_TOOLS:
                progressed = True          # 写成功就算推进（即使是修正同一文件）
                continue
            sig = _signature(name, args)
            if sig not in self._seen:
                self._seen.add(sig)
                progressed = True

        if progressed:
            self.idle = 0
            return None

        self.idle += 1
        self.idle_max = max(self.idle_max, self.idle)

        if not self.warned and self.idle >= self.warn_after:
            self.warned = True
            self.warn_at = self.rounds
            return "warn"
        if self.warned and self.idle >= self.stop_after:
            self.stopped = True
            return "stop"
        return None

    def reset(self) -> None:
        """同一个 agent 实例可能被复用多次 run()，状态必须清零。"""
        self._seen.clear()
        self.idle = 0
        self.idle_max = 0
        self.warned = False
        self.stopped = False
        self.warn_at = None
        self.rounds = 0

    def summary(self) -> dict:
        return {
            "stall_rounds": self.rounds,
            "stall_warned": self.warned,
            "stall_stopped": self.stopped,
            "stall_warn_at": self.warn_at,
            "stall_idle_max": self.idle_max,
        }
