"""MiniCode 评测运行器（Phase 0）。

对每个任务：在全新 workdir 里跑 CodingAgent，再用 verify() 自动判定是否通过，
并收集 LLM 调用数 / token / 工具步数 / 耗时，最后写 EVAL.md 基线表。

用法（在 cli_coding_agent 目录下）：
    python eval/runner.py                 # 跑全部 10 个任务
    python eval/runner.py --ids t01 t03  # 只跑指定任务（调试用）
    python eval/runner.py --max-iter 15  # 控制 Agent 最大步数

注意：会真实调用 LLM（DeepSeek 等），需 .env 里有 LLM_API_KEY。
"""

from __future__ import annotations

import argparse
import shutil
import sys
import pathlib
import time

_HERE = pathlib.Path(__file__).resolve().parent
_ROOT = _HERE.parent                       # cli_coding_agent
sys.path.insert(0, str(_ROOT))

from agent import CodingAgent              # noqa: E402
from tasks import TASKS                    # noqa: E402
from metrics import build_metrics, assign_buckets, bucket_of, BUCKET_NAMES  # noqa: E402

WORKSPACES = _HERE / "workspaces"
TRACES = _HERE / "traces"


def run_one(task: dict, max_iter: int, plan: bool = False, compress: bool = False,
            adaptive: bool = False, stall: bool = False, skills: bool = False,
            ws_suffix: str = "", trace: bool = False) -> dict:
    # 每个模式用独立 workspace（按 tag 区分），避免跨模式文件残留污染 verify，
    # 也避免重复运行时对大目录做 rmtree。
    wd = WORKSPACES / f"{task['id']}{ws_suffix}"
    shutil.rmtree(wd, ignore_errors=True)
    wd.mkdir(parents=True, exist_ok=True)
    if task.get("setup"):
        task["setup"](wd)

    # 轨迹写到 workspaces 之外：workdir 里多一个文件会污染 verify 的产物检查。
    trace_path = (TRACES / f"{task['id']}{ws_suffix}.jsonl") if trace else None

    agent = CodingAgent(
        workdir=wd, verbose=False, max_iter=max_iter,
        plan_mode=plan, adaptive_plan=adaptive, compress=compress, stall=stall,
        skills=skills, trace_path=trace_path,
    )
    try:
        _text, stats = agent.run_with_stats(task["prompt"])
        passed, detail = task["verify"](wd)
    except Exception as e:  # 单个任务失败不应中断整轮评测
        import traceback
        passed, detail = False, f"run error: {type(e).__name__}: {e}\n{traceback.format_exc()}"
        stats = agent.stats

    m = build_metrics(task, passed, detail, stats, max_iter)
    return {
        "id": task["id"],
        "name": task["name"],
        "passed": passed,
        "detail": detail,
        "status": stats.status,
        "llm_calls": stats.llm_calls,
        "prompt_tokens": stats.prompt_tokens,
        "completion_tokens": stats.completion_tokens,
        "tool_steps": stats.tool_steps,
        "seconds": round(stats.total_seconds, 1),
        # 技能路由：召回 ≠ 使用，两个都记才能说清"到底靠什么答对的"
        "skill_hits": ", ".join(stats.skill_hits),
        "skill_loaded": ", ".join(stats.skill_loaded),
        **m,
    }


def _md_table(rows: list[dict]) -> str:
    head = "| 任务 | 通过 | 状态 | LLM调用 | 提示token | 生成token | 工具步数 | 耗时(s) | p50延迟(s) | 基础设施 |"
    sep = "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |"
    lines = [head, sep]
    for r in rows:
        lines.append(
            f"| {r['id']} {r['name']} | {'✅' if r['passed'] else '❌'} | {r['status']} "
            f"| {r['llm_calls']} | {r['prompt_tokens']} | {r['completion_tokens']} "
            f"| {r['tool_steps']} | {r['seconds']} "
            f"| {r.get('lat_p50', 0)} | {_infra_label(r.get('infra', 'unknown'))} |"
        )
    return "\n".join(lines)


def _infra_label(infra: str) -> str:
    return {"ok": "🟢 正常", "api_jitter": "🟡 偶发慢",
            "api_slow": "🔴 整体限流", "unknown": "⚪ 未采集"}.get(infra, infra)


def _process_table(rows: list[dict], max_iter: int) -> str:
    """过程层归因表：工具序列 / 成功率 / 首个失败步 / 失败归类。"""
    lines = []
    for r in rows:
        seq = r.get("tool_seq") or "—"
        if len(seq) > 46:
            seq = seq[:46] + "…"
        lines.append(
            f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['llm_calls']} "
            f"| {r.get('iter_usage', 0):.2f} | {r.get('tool_n', 0)} "
            f"| {r.get('tool_ok_rate', 0):.2f} "
            f"| {r.get('first_fail_step') if r.get('first_fail_step') else '—'} "
            f"| {r.get('fail_reason', '-')} | `{seq}` |"
        )
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-iter", type=int, default=15, help="Agent 最大工具轮数")
    ap.add_argument("--ids", nargs="*", default=None, help="只跑指定任务 id（调试用）")
    ap.add_argument("--plan", action="store_true", help="启用 Plan 模式")
    ap.add_argument("--adaptive", action="store_true", help="自适应规划（仅复杂任务开 Plan）")
    ap.add_argument("--compress", action="store_true", help="启用分层上下文压缩")
    ap.add_argument("--tag", default="", help="报告文件名后缀，如 --tag .plan → EVAL.plan.md")
    ap.add_argument("--out", default=None, help="输出报告路径（默认 EVAL{tag}.md）")
    ap.add_argument("--trace", action="store_true",
                    help="把每次工具调用的（名字/参数/结果摘要）落到 eval/traces/*.jsonl")
    args = ap.parse_args()

    out_path = pathlib.Path(args.out) if args.out else (_HERE / f"EVAL{args.tag}.md")
    mode_bits = []
    if args.plan:
        mode_bits.append("Plan")
    if args.adaptive:
        mode_bits.append("AdaptivePlan")
    if args.compress:
        mode_bits.append("Compress")
    mode_label = "+".join(mode_bits) if mode_bits else "baseline(v0)"

    tasks = [t for t in TASKS if args.ids is None or t["id"] in args.ids]
    # 每次运行用「时间戳唯一 workspace 目录」：rmtree 只作用在空/不存在的目录上，
    # 不会触发沙箱对「>50 文件目录」的批量删除确认卡死；历史 workspace 安静堆积，不影响 verify。
    import datetime
    run_id = datetime.datetime.now().strftime("%m%d_%H%M%S")
    ws_suffix = (args.tag or "") + "_" + run_id
    print(f"▶ 运行 {len(tasks)} 个评测任务（max_iter={args.max_iter}）...\n")

    rows: list[dict] = []
    for t in tasks:
        print(f"  · {t['id']} {t['name']} ...", end=" ", flush=True)
        r = run_one(t, args.max_iter, plan=args.plan, compress=args.compress,
                    adaptive=args.adaptive, ws_suffix=ws_suffix, trace=args.trace)
        rows.append(r)
        print(f"{'✅' if r['passed'] else '❌'} ({r['seconds']}s, {r['llm_calls']} calls)")

    n = len(rows)
    passed = sum(1 for r in rows if r["passed"])
    avg = lambda k: round(sum(r[k] for r in rows) / n, 1) if n else 0
    total_pt = sum(r["prompt_tokens"] for r in rows)
    total_ct = sum(r["completion_tokens"] for r in rows)

    # 分桶：按「本模式实测 prompt_tokens」三分位动态切（低/中/高成本），
    # 不再按任务编号硬编码——编号大 ≠ 任务难，且加任务不必改代码。
    buckets = assign_buckets(rows)

    def _bucket(rows):
        if not rows:
            return (0, 0, 0, 0, 0)
        ne = len(rows)
        npass = sum(1 for r in rows if r["passed"])
        pt = sum(r["prompt_tokens"] for r in rows)
        calls = round(sum(r["llm_calls"] for r in rows) / ne, 2)
        return (ne, npass, pt, round(pt / ne) if ne else 0, calls)

    bstats = {}
    for bname in BUCKET_NAMES:
        ids = buckets[bname]
        sub = [r for r in rows if r["id"] in ids]
        bstats[bname] = _bucket(sub)

    md = f"""# MiniCode 评测报告（{out_path.name}）

> 由 `python eval/runner.py` 自动生成。**运行模式：{mode_label}**。
> 后续每个 feature 都会在此对比，证明「可量化的增量」。
>
> ⚠️ 数字来自真实 LLM 运行（DeepSeek 等），会随模型/提示词波动；只作趋势参考，绝不编造。

## 汇总（N={n}）

| 指标 | 值 |
| --- | --- |
| 运行模式 | {mode_label} |
| 通过率 | **{passed}/{n} = {round(100*passed/n,1)}%** |
| 平均 LLM 调用 | {avg('llm_calls')} |
| 平均工具步数 | {avg('tool_steps')} |
| 平均耗时 | {avg('seconds')} s |
| 总提示 token | {total_pt} |
| 总生成 token | {total_ct} |
| max_iter | {args.max_iter} |
| 生成时间 | {time.strftime('%Y-%m-%d %H:%M')} |

## 分桶对比（按实测成本三分位动态切分）

| 桶 | 任务 | 任务数 | 通过 | 提示token | 平均/任务 | 平均调用 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| cheap（低成本） | {" ".join(buckets["cheap"])} | {bstats['cheap'][0]} | {bstats['cheap'][1]}/{bstats['cheap'][0]} | {bstats['cheap'][2]} | {bstats['cheap'][3]} | {bstats['cheap'][4]} |
| mid（中成本） | {" ".join(buckets["mid"])} | {bstats['mid'][0]} | {bstats['mid'][1]}/{bstats['mid'][0]} | {bstats['mid'][2]} | {bstats['mid'][3]} | {bstats['mid'][4]} |
| expensive（高成本） | {" ".join(buckets["expensive"])} | {bstats['expensive'][0]} | {bstats['expensive'][1]}/{bstats['expensive'][0]} | {bstats['expensive'][2]} | {bstats['expensive'][3]} | {bstats['expensive'][4]} |

> 桶按本模式实测 `prompt_tokens` 三分位切分，**不是按任务编号**：编号大 ≠ 任务难。
> 压缩器的价值应在「expensive 桶」（长任务/大文件/多文件）体现为正收益，在「cheap 桶」应为零惩罚。
> ⚠️ 跨模式对比时桶成员会变（各自按自己成本切），要严格配对请看 `repeat.py` 的**配对差值**报告。

## 逐任务

{_md_table(rows)}

## 过程层归因（新增：失败为什么失败 / 省钱是不是靠少干活）

| 任务 | 通过 | 调用数 | 占上限 | 工具数 | 工具成功率 | 首个失败步 | 失败归类 | 工具序列 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
{_process_table(rows, args.max_iter)}

> - **占上限** = `llm_calls / max_iter`。若所有任务都远低于 1.0，说明**上限不是瓶颈**，调 max_iter 无意义。
> - **工具成功率** < 1 说明该次运行中有工具报错（❌/⛔），是定位问题的最快入口。
> - **首个失败步** 可快速看出是「一上来就错」还是「改到一半崩」。
> - `read_repeats` 当前未采集（RunStats 不保存工具参数），报告如实标 0，**不编造**。

## 备注

- 通过判定由每个任务的 `verify()` 自动执行（运行生成代码 / 断言 / 文件检查）。
- 失败任务的 detail 见下，可用于定位 Agent 短板：
"""
    for r in rows:
        if not r["passed"]:
            md += f"\n- **{r['id']} {r['name']}** ({r['status']}): {r['detail']}"

    out_path.write_text(md, encoding="utf-8")
    print(f"\n✅ 完成[{mode_label}]：通过 {passed}/{n}，报告已写入 {out_path}")


if __name__ == "__main__":
    main()
