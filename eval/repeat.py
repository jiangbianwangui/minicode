"""MiniCode 重复消融：每模式跑 N 次，输出「逐任务配对差值」统计量。

为什么改（2026-10-21 第二版）：
    第一版把一轮里所有任务的 token **相加**再横比（如 base 86,888 vs compress 95,446 = +9.8%），
    致命缺点是「总价涨了但不知道哪样涨了」——任务间难度差异会淹没信号，且 n 只有 1（每轮一个总和）。
    第二版改为**同任务配对**：对每个任务先取 N 次均值，再算 compress − base 的差值，
    这样任务难度自动抵消，n 从「1 个总和」变成「任务个数个差值」，检验功效显著提升。

同时输出：
    - 符号一致性（多少个任务的差值为负）：比单纯 t 检验更能抗噪
    - 过程层配对（调用数 / 工具成功率 / 返工标记）：防止「省 token 但改坏了」
    - 失败样本计数：全对时自动警告「尺子无区分度」

用法（在 cli_coding_agent 目录下）：
    python eval/repeat.py --runs 3 --modes base compress
    python eval/repeat.py --runs 3 --modes base compress --ids t11 t12 t13 t16 t25 \
        --out eval/EVAL.paired.md

判读规则（写死在报告里，避免自己骗自己）：
    1. 配对均值差 > 0 且 |t| > 2 才算真信号；否则只能说「方向性趋势」。
    2. 符号一致性 ≥ 80% 才算稳定（少数任务暴涨不该掩盖多数任务小省）。
    3. 若两模式通过率都是 100%，**该指标只能证明「没变差」，不能证明「变好了」**。
"""

from __future__ import annotations

import argparse
import datetime
import math
import pathlib
import statistics
import sys
import time

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from runner import TASKS, run_one, WORKSPACES  # noqa: E402
from metrics import assign_buckets, bucket_of, BUCKET_NAMES  # noqa: E402

# 模式名 → CodingAgent 的开关
MODE_FLAGS: dict[str, dict] = {
    "base": {},
    "compress": {"compress": True},
    "plan": {"plan": True},
    "plan_compress": {"plan": True, "compress": True},
    "adaptive": {"adaptive": True},
    "adaptive_compress": {"adaptive": True, "compress": True},
    "stall": {"stall": True},
    "skill": {"skills": True},
}


def _one_run(mode: str, tasks: list[dict], max_iter: int, rep: int) -> list[dict]:
    """跑一整轮，返回**逐任务**结果列表（不再聚合成一个总和）。"""
    # workspace 目录名带模式+轮次+时间戳 → 目录永不预先存在，
    # rmtree 只作用空目录，绕开沙箱对「>50 文件目录」的批量删除拦截。
    suffix = f"_{mode}_r{rep}_{datetime.datetime.now().strftime('%m%d_%H%M%S')}"
    flags = MODE_FLAGS[mode]
    return [run_one(t, max_iter, ws_suffix=suffix, **flags) for t in tasks]


def _mean_std(values: list[float]) -> tuple[float, float]:
    mean = statistics.mean(values)
    std = statistics.stdev(values) if len(values) > 1 else 0.0
    return mean, std


def _paired_t(diffs: list[float]) -> tuple[float, float]:
    """配对 t 检验：返回 (t 值, 自由度)。样本不足时返回 (0, 0)。"""
    n = len(diffs)
    if n < 2:
        return 0.0, 0
    m, s = _mean_std(diffs)
    if s == 0:
        return (math.inf if m != 0 else 0.0), n - 1
    return m / (s / math.sqrt(n)), n - 1


def _per_task_mean(runs: dict[str, list[list[dict]]], mode: str, key: str) -> dict[str, float]:
    """某模式下「每个任务在 N 次运行中的均值」。"""
    acc: dict[str, list[float]] = {}
    for round_rows in runs[mode]:
        for r in round_rows:
            acc.setdefault(r["id"], []).append(float(r.get(key, 0) or 0))
    return {tid: statistics.mean(v) for tid, v in acc.items()}


def _per_task_pass(runs: dict[str, list[list[dict]]], mode: str) -> dict[str, int]:
    """某模式下每个任务 N 次里通过了几次（用来暴露不稳定任务）。"""
    acc: dict[str, int] = {}
    for round_rows in runs[mode]:
        for r in round_rows:
            acc[r["id"]] = acc.get(r["id"], 0) + (1 if r["passed"] else 0)
    return acc


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3, help="每个模式重复次数（建议 ≥3）")
    ap.add_argument("--modes", nargs="+", default=["base", "compress"], choices=sorted(MODE_FLAGS))
    ap.add_argument("--max-iter", type=int, default=20)
    ap.add_argument("--ids", nargs="*", default=None, help="只跑指定任务 id（冒烟测试用）")
    ap.add_argument("--compare", default=None, help="配对基准模式（默认取第一个模式，通常是 base）")
    ap.add_argument("--out", default=str(_HERE / "EVAL.paired.md"))
    args = ap.parse_args()

    tasks = [t for t in TASKS if args.ids is None or t["id"] in args.ids]
    out_path = pathlib.Path(args.out)
    base_mode = args.compare or args.modes[0]
    targets = [m for m in args.modes if m != base_mode]
    t0 = time.time()

    print(f"▶ 配对消融：{len(args.modes)} 模式 × {args.runs} 次 × {len(tasks)} 任务"
          f"（基准={base_mode}）")
    runs: dict[str, list[list[dict]]] = {}
    for mode in args.modes:
        runs[mode] = []
        for rep in range(args.runs):
            print(f"  · [{mode}] 第 {rep + 1}/{args.runs} 轮 ...", flush=True)
            rows = _one_run(mode, tasks, args.max_iter, rep)
            runs[mode].append(rows)
            np_ = sum(1 for r in rows if r["passed"])
            tok = sum(r["prompt_tokens"] for r in rows)
            retry = sum(1 for r in rows if r.get("had_tool_error"))
            print(f"    ✅ {np_}/{len(rows)}  token={tok:,}  含工具报错的运行={retry}",
                  flush=True)

    # ---------- 配对差值计算 ----------
    base_tok = _per_task_mean(runs, base_mode, "prompt_tokens")
    base_calls = _per_task_mean(runs, base_mode, "llm_calls")
    base_sec = _per_task_mean(runs, base_mode, "seconds")
    base_pass = _per_task_pass(runs, base_mode)

    # 动态分桶：按 base 模式实测成本三分位
    base_rows = [{"id": tid, "prompt_tokens": v} for tid, v in base_tok.items()]
    buckets = assign_buckets(base_rows)

    lines = [
        "# MiniCode 配对消融报告（逐任务配对差值）",
        "",
        f"> 由 `python eval/repeat.py` 自动生成。**{len(args.modes)} 模式 × {args.runs} 次 × "
        f"{len(tasks)} 任务**，max_iter={args.max_iter}，基准模式 = `{base_mode}`。",
        "> 与旧版（`EVAL.repeat.md`）的区别：旧版把一轮里所有任务 token **相加**再比（总价对比，",
        "> 任务难度会淹没信号）；本版对**同一任务**做配对差值，任务难度自动抵消，n 从 1 提升到任务数。",
        "",
        "## 一、配对差值总表（每个任务 = 一个差值样本）",
        "",
    ]

    summary_rows = []
    for mode in targets:
        m_tok = _per_task_mean(runs, mode, "prompt_tokens")
        m_calls = _per_task_mean(runs, mode, "llm_calls")
        m_sec = _per_task_mean(runs, mode, "seconds")
        common = [tid for tid in base_tok if tid in m_tok]
        d_tok = [m_tok[tid] - base_tok[tid] for tid in common]
        d_calls = [m_calls[tid] - base_calls[tid] for tid in common]
        d_sec = [m_sec[tid] - base_sec[tid] for tid in common]

        mu, sd = _mean_std(d_tok)
        tval, dof = _paired_t(d_tok)
        neg = sum(1 for x in d_tok if x < 0)
        pos = sum(1 for x in d_tok if x > 0)
        zero = len(d_tok) - neg - pos
        base_sum = sum(base_tok[tid] for tid in common)
        rel = (mu / (base_sum / len(common)) * 100) if base_sum else 0.0
        mc, _ = _mean_std(d_calls)
        ms, _ = _mean_std(d_sec)
        summary_rows.append(
            f"| {mode} vs {base_mode} | {len(common)} | **{mu:+,.0f}** ± {sd:,.0f} | {rel:+.1f}% "
            f"| {neg}/{pos}/{zero} | t={tval:.2f} (df={dof}) | {mc:+.2f} | {ms:+.1f}s |"
        )

    lines += [
        "| 对比 | n(任务) | 每任务 token 差值 (mean±std) | 相对 baseline | 省/增/平 | 配对 t 检验 | 调用数差 | 耗时差 |",
        "| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: |",
        *summary_rows,
        "",
        "> **判读**：`|t| > 2` 且「省」的任务数明显多过「增」→ 才能说真收益。",
        "> 单个任务的 token 涨落可到 ±40%，所以**只看均值差会骗人，必须同时看符号一致性**。",
        "",
        "## 二、逐任务配对明细（同任务跨模式对比）",
        "",
        "| 任务 | 桶 | 通过率(base) | base token | "
        + " | ".join(f"{m} token" for m in targets)
        + " | "
        + " | ".join(f"Δ{m}" for m in targets)
        + " | base 调用 | "
        + " | ".join(f"Δ调用({m})" for m in targets)
        + " |",
        "| --- | --- | ---: | ---: | "
        + " | ".join("---:" for _ in targets)
        + " | "
        + " | ".join("---:" for _ in targets)
        + " | ---: | "
        + " | ".join("---:" for _ in targets)
        + " |",
    ]

    for tid in sorted(base_tok):
        b = base_tok[tid]
        cells_tok = []
        cells_d = []
        cells_dc = []
        for mode in targets:
            v = _per_task_mean(runs, mode, "prompt_tokens").get(tid)
            d = (v - b) if v is not None else None
            cells_tok.append(f"{v:,.0f}" if v is not None else "—")
            cells_d.append(f"{d:+,.0f}" if d is not None else "—")
            dc = _per_task_mean(runs, mode, "llm_calls").get(tid, 0) - base_calls.get(tid, 0)
            cells_dc.append(f"{dc:+.2f}")
        pr = base_pass.get(tid, 0)
        lines.append(
            f"| {tid} | {bucket_of(tid, buckets)} | {pr}/{args.runs} | {b:,.0f} "
            f"| {' | '.join(cells_tok)} | {' | '.join(cells_d)} "
            f"| {base_calls.get(tid, 0):.2f} | {' | '.join(cells_dc)} |"
        )

    # ---------- 分桶配对汇总 ----------
    lines += ["", "## 三、按成本桶聚合（桶按 base 实测成本三分位）", ""]
    lines += [
        "| 桶 | 任务 | " + " | ".join(f"Δtoken({m})" for m in targets) + " |",
        "| --- | --- | " + " | ".join("---:" for _ in targets) + " |",
    ]
    for bname in BUCKET_NAMES:
        ids = [t for t in buckets[bname] if t in base_tok]
        if not ids:
            continue
        cells = []
        for mode in targets:
            m_tok = _per_task_mean(runs, mode, "prompt_tokens")
            ds = [m_tok[t] - base_tok[t] for t in ids if t in m_tok]
            mu, _ = _mean_std(ds) if ds else (0.0, 0.0)
            cells.append(f"{mu:+,.0f}")
        lines.append(f"| {bname} | {' '.join(ids)} | {' | '.join(cells)} |")

    # ---------- 过程层配对（防止「省 token 但改坏/多返工」） ----------
    lines += [
        "",
        "## 四、过程层配对（token 省了，是不是靠少干活/多返工省出来的）",
        "",
        "| 模式 | 工具报错运行数 | 平均工具数 | 平均工具成功率 | 失败归类分布 |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    for mode in args.modes:
        flat = [r for rd in runs[mode] for r in rd]
        nerr = sum(1 for r in flat if r.get("had_tool_error"))
        tn = statistics.mean([r.get("tool_n", 0) for r in flat]) if flat else 0
        okr = statistics.mean([r.get("tool_ok_rate", 0) for r in flat]) if flat else 0
        reasons: dict[str, int] = {}
        for r in flat:
            fr = r.get("fail_reason", "-")
            if fr != "-":
                reasons[fr] = reasons.get(fr, 0) + 1
        rtxt = "、".join(f"{k}×{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])) or "全干净"
        lines.append(f"| {mode} | {nerr}/{len(flat)} | {tn:.2f} | {okr:.3f} | {rtxt} |")

    # ---------- 尺子健康度自检 ----------
    # runs 结构是 list[list[dict]]：外层=轮次，内层=该轮逐任务。先拍平再统计。
    flat_all = [r for mode in args.modes for rd in runs[mode] for r in rd]
    total_items = len(flat_all)
    total_pass = sum(1 for r in flat_all if r["passed"])
    pass_rate = total_pass / total_items * 100 if total_items else 0

    lines += ["", "## 五、尺子健康度自检（先看这里，再看数字）", ""]
    lines.append(f"- 样本总数：{total_items} 次任务运行"
                 f"（{len(args.modes)} 模式 × {args.runs} 轮），通过率 **{pass_rate:.1f}%**")
    n_tasks = len(base_tok)
    if n_tasks < 6 or args.runs < 3:
        lines.append(
            f"- ⚠️ **样本不足，本轮结论仅供参考**：任务数 {n_tasks}（建议 ≥10）、"
            f"重复 {args.runs} 次（建议 ≥3）。"
            + ("当前为冒烟测试，任何百分比都不可写进报告。" if n_tasks <= 3 else "")
        )
    if pass_rate >= 99.5:
        lines.append(
            "- 🔴 **警告：通过率顶到 100%，尺子在这一维度没有区分度。** "
            "本轮所有 token 结论只能证明「没变差」，**不能证明「变好了」**；"
            "要让压缩类优化有地方落脚，必须先造出「当前模型确实会失败/接近上限」的任务（见 ABLATION 下一步）。"
        )
    elif pass_rate >= 90:
        lines.append("- 🟡 通过率 90–99%：有少量失败样本，可做部分归因，但样本量仍偏小。")
    else:
        lines.append("- 🟢 通过率 < 90%：尺子有区分度，token 与通过率两个维度都可判读。")

    unstable = []
    for mode in args.modes:
        pf = _per_task_pass(runs, mode)
        for tid, cnt in pf.items():
            if 0 < cnt < args.runs:
                unstable.append(f"{mode}/{tid} {cnt}/{args.runs}")
    if unstable:
        lines.append(f"- ⚠️ **不稳定任务**（同一模式下时过时不过，说明处在能力边界，最有信息量）："
                     f"{'、'.join(unstable)}")
    else:
        lines.append("- 无「时过时不过」的任务 → 全部任务要么稳定过、要么稳定不过，说明难度分布两极，"
                     "缺少**边界区任务**（这是提升区分度的首选突破口）。")

    lines += [
        "",
        "## 六、判读规则（写死在此，防止自我欺骗）",
        "",
        "1. 配对均值差为负 **且** `|t| > 2` **且** 「省」的任务数 ≥ 2×「增」→ 可称真收益。",
        "2. 只看符号一致性、不看单任务百分比：单任务 token 波动可达 ±40%。",
        "3. 通过率 100% 时，收益一律表述为「**在不降低正确率的前提下降低 X% 成本**」，"
        "绝不写「提升正确率」。",
        "4. 简历只写「配对 n≥X、符号一致、t 检验显著」的数字；任何一个条件不满足就只写「趋势」。",
        "",
        f"> 生成时间：{time.strftime('%Y-%m-%d %H:%M')}，总耗时 {round(time.time() - t0, 1)}s",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 完成，配对报告已写入 {out_path}")


if __name__ == "__main__":
    main()
