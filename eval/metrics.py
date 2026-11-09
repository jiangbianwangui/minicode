"""评测「过程层」指标：把一次运行拆成可归因的三层。

为什么需要（一把只有「通过/不通过」的尺子量不出差异时的补丁）：
- **交付层**（passed）：唯一被用到的轴。当基线 24/24 全过时，它顶到天花板，零区分度。
- **效率层**（llm_calls / prompt_tokens / seconds）：有数，但过去只会「跨任务相加」，
  任务难度差会淹没信号 → 必须做**同任务配对差值**（见 repeat.py）。
- **过程层**（本模块）：工具调用序列、首个失败步、同文件重读次数、调用数占用率。
  它回答「为什么失败 / 省钱是不是靠少干活」，是效率数字的**校验器**。

设计约束：只读 agent 已采集的 `RunStats`（tool_calls 一直存着却从没被用过），
不改 agent.py，避免测量逻辑污染被测对象。
"""

from __future__ import annotations

import re
from collections import Counter

# 读类工具：同一文件在「不同步骤」被反复调用 = 上下文没记住，压缩器最该优化的指标
_READ_TOOLS = {"ReadFile", "ReadLines", "ViewFile"}


def _path_of(args: dict) -> str | None:
    """从工具参数里取文件路径（不同工具字段名不同，做兜底）。"""
    for k in ("path", "file", "filepath", "filename", "file_path"):
        v = args.get(k)
        if isinstance(v, str) and v:
            return v
    return None


def tool_sequence(stats) -> list[dict]:
    """把 stats.tool_calls 转成有序明细（含步序号）。

    注意：RunStats 里只存了 (name, ok)——没有参数、没有真实步号。
    为了不修改 agent.py，这里用「每出现一次 plan 之外的调用就 +1 步」的方式
    近似步号（一个 AIMessage 可能并发多个 tool_call，会共享同一近似步号，
    但对「是否反复读同一个文件」这类判断足够）。
    """
    seq = []
    for i, (name, ok) in enumerate(getattr(stats, "tool_calls", []) or [], start=1):
        seq.append({"i": i, "name": name, "ok": bool(ok)})
    return seq


def first_fail_step(seq: list[dict]) -> int | None:
    """第一个失败工具的序号；全成功返回 None。"""
    for item in seq:
        if not item["ok"]:
            return item["i"]
    return None


def tool_ok_rate(seq: list[dict]) -> float:
    if not seq:
        return 0.0
    return round(sum(1 for x in seq if x["ok"]) / len(seq), 3)


def read_repeats(stats=None) -> int:
    """同一文件被重复读取的次数（sum of 次数-1）。

    为什么这个指标重要：它是**压缩代价**的直接度量。
    压缩把旧轮次摘要化 → agent 丢掉"文件长什么样" → 只能重读。
    所以「压缩净收益 = 省下的重发成本 − 重读成本」里的第二项，就落在它身上。

    2026-10-26 之前这里是恒返回 0 的占位实现（RunStats 不存工具参数）。
    现在 `_node_tools` 已经收集了 args，可以如实统计了。
    """
    paths = getattr(stats, "read_paths", None) or []
    if not paths:
        return 0
    counts = Counter(paths)
    return sum(c - 1 for c in counts.values() if c > 1)


# ---- 时间维度：区分「任务真的重」和「API 在限流」 ----
# 阈值依据：DeepSeek 正常往返 2–8s；>30s 已属异常，>60s 接近客户端 timeout(120s) 的一半。
SLOW_CALL_SEC = 30.0


def latency_stats(stats) -> dict:
    """单次运行的 LLM 往返延迟分布 + 基础设施判读。

    为什么单独立一层：total_seconds 把「模型思考」和「网络排队」混成一个数。
    Batch count 48 那次 28 分钟没有产出，一度被误读成「任务规模超限」，
    实际是每次请求 100+s —— 只有逐次延迟才能分辨这两者。
    """
    lat = sorted(getattr(stats, "llm_latencies", []) or [])
    if not lat:
        return {"lat_p50": 0.0, "lat_p95": 0.0, "lat_max": 0.0,
                "slow_calls": 0, "infra": "unknown"}
    n = len(lat)
    p50 = lat[n // 2]
    p95 = lat[min(n - 1, int(round(0.95 * (n - 1))))]
    slow = sum(1 for x in lat if x >= SLOW_CALL_SEC)
    if p50 >= SLOW_CALL_SEC:
        infra = "api_slow"          # 连中位数都慢 → 整轮结论不可采信
    elif slow:
        infra = "api_jitter"        # 偶发慢请求 → 结论方向可用，耗时字段慎用
    else:
        infra = "ok"
    return {"lat_p50": round(p50, 1), "lat_p95": round(p95, 1),
            "lat_max": round(lat[-1], 1), "slow_calls": slow, "infra": infra}


def classify_fail_reason(detail: str, status: str) -> str:
    """把 verify/运行失败归类，便于聚合统计（没有失败样本时就全是 '-'）。"""
    d = (detail or "")
    if status == "max_iter":
        return "max_iter"
    if status == "stalled":
        return "stalled"       # 空转被停滞检测拦下（区别于「步数不够」）
    if status == "error":
        return "run_error"
    if not d:
        return "-"
    rules = [
        (r"缺少|missing|不存在", "missing_artifact"),
        (r"行数异常|疑似被重写", "overwritten"),
        (r"应 \d+ 处|实际 \d+", "count_mismatch"),
        (r"hidden|隐藏用例|AssertionError", "wrong_behavior"),
        (r"import 失败|ModuleNotFound|SyntaxError", "syntax_import"),
        (r"pytest|自带测试", "own_test_fail"),
        (r"超时|timeout", "timeout"),
    ]
    for pat, tag in rules:
        if re.search(pat, d, re.I):
            return tag
    return "other"


def build_metrics(task: dict, passed: bool, detail: str, stats, max_iter: int) -> dict:
    """产出一条「三层」结构化指标（runner 直接平铺进结果字典）。

    注意：`passed=True` 不代表过程干净——常见是「工具报错后自己纠正」。
    所以工具层指标独立于通过判定，始终记录，否则会掩盖真实的返工成本。
    """
    seq = tool_sequence(stats)
    calls = int(getattr(stats, "llm_calls", 0) or 0)
    ok_rate = tool_ok_rate(seq)
    ff = first_fail_step(seq)
    lat = latency_stats(stats)
    writes = int(getattr(stats, "writes_ok", 0) or 0)
    fail_reason = (
        classify_fail_reason(detail, getattr(stats, "status", ""))
        if not passed
        else ("passed_with_retry" if ff is not None else "-")
    )
    # 零交付覆盖：失败 + 一次写操作都没有 → 这是「流程崩溃」，不是「能力不足」。
    # 两者在通过率上完全一样（都是 0），但归因相反：
    # 前者通常意味着任务/prompt 有缺陷（t27 第一次跑：8 轮全在 `find /`，零改动），
    # 后者才是优化该瞄准的地方。不区分就会把"我的 bug"记成"它不行"。
    if not passed and writes == 0:
        fail_reason = "no_delivery"
    return {
        # 过程层（与是否通过解耦：通过也可能有过 1 次工具失败）
        "tool_seq": "→".join(x["name"] for x in seq),
        "tool_n": len(seq),
        "tool_ok_rate": ok_rate,
        "first_fail_step": ff,
        "had_tool_error": ff is not None,
        "read_repeats": read_repeats(stats),
        # 交付层：有没有真的改过文件（0 = 从未进入"修改"这一步）
        "writes_ok": writes,
        "delivered": writes > 0,
        # 效率层（归一化：调用数占上限的比例，判断「是否触顶」）
        "iter_usage": round(calls / max_iter, 3) if max_iter else 0.0,
        # 时间层：区分「任务重」和「API 慢」
        **lat,
        # 停滞层：空转是 token 双峰（66k vs 410k）的来源，必须逐次落盘
        "stall_warned": bool(getattr(stats, "stall_warned", False)),
        "stall_stopped": bool(getattr(stats, "stall_stopped", False)),
        "stall_warn_at": getattr(stats, "stall_warn_at", None),
        "stall_idle_max": int(getattr(stats, "stall_idle_max", 0) or 0),
        # 归因层：仅失败时归类；成功但有过工具报错标为 passed_with_retry
        "fail_reason": fail_reason,
    }


# ---- 动态分桶：按 baseline 实测成本三分位，而不是按任务编号硬编码 ----
BUCKET_NAMES = ("cheap", "mid", "expensive")


def assign_buckets(rows: list[dict], key: str = "prompt_tokens") -> dict[str, list[str]]:
    """按给定指标把任务分成三档（低/中/高成本）。

    为什么不用 `t01-t10 / t11-t16 / t17+`：编号大 ≠ 任务难，
    且新增任务就得改代码。改用实测成本后，桶的定义跟着数据走，
    还会顺手暴露「你以为难其实不难」的任务。
    """
    if not rows:
        return {k: [] for k in BUCKET_NAMES}
    ordered = sorted(rows, key=lambda r: r[key])
    n = len(ordered)
    if n < 3:
        # 任务太少时「三分位」无意义（会产生空桶），退回逐任务一桶，避免虚假聚合
        return {k: ([r["id"] for r in ordered] if i == 0 else []) for i, k in
                enumerate(BUCKET_NAMES)}
    # 三分位切点（含边界内即可，数量不均也无所谓，关键是单调）
    i1 = max(1, round(n / 3))
    i2 = max(i1, round(2 * n / 3))
    groups = {
        "cheap": ordered[:i1],
        "mid": ordered[i1:i2],
        "expensive": ordered[i2:],
    }
    return {k: [r["id"] for r in v] for k, v in groups.items()}


def bucket_of(task_id: str, buckets: dict[str, list[str]]) -> str:
    for name, ids in buckets.items():
        if task_id in ids:
            return name
    return "?"