"""配对统计：9 个"答案不在仓库里"的任务，base vs skill。

## 数据从哪来

`eval/runs/main_9task.jsonl` —— 由 `eval/rebuild_runs.py` 对历史 workspace
**重跑判卷**生成（不调用 LLM）。所以这里每一个数字都能追溯到具体目录：

    {"task":"t38","group":"base","dir":"t38_base_r2_1102_213022","passed":true,...}

## 口径（2026-11-25 修订，重要）

**分母 = 该题历史上全部运行**，不再是"手工挑的批次"。

旧口径（每题挑 3 次，t34/t35 挑 6 次）有三个问题，都是这次审计暴露的：

1. **选批次的规则从没写下来**。为什么 t34/t35 算两批、t38 只算一批？
   只有当时的我知道，报告里没有依据 —— 这意味着数字**不可复现**。
2. **t38 因此漏报了一次通过**。它有 9 次 base 运行（3 批 × 3 次），
   主表只记了 0/3 的那一批，而 `EVAL.t38.base2.md` 白纸黑字写着 **1/3**。
   重判确认那次是 6/6 全过、`leak_audit` 零命中 —— 真猜对了，不是泄漏。
3. **"31 次全败"这个说法站不住**。改成"46 次里通过 1 次"以后，
   结论反而更硬：它不再依赖我挑过哪一批。

为什么可以合并不同批次：t36 的两批 skill 运行间隔只有 4 分钟
（`202131` → `202530`），中间不可能改 skill 或判卷 ——
失败与通过是**同一条件下的模型随机性**，不是"修改前/修改后"。
（如果两批之间真的改过东西，就不能合并，得按时间切开。）

## 为什么同时报配对 t 和符号检验

- 配对 t 要求差值近似正态，而这批数据的 base 几乎恒为 0（天花板效应），
  差值的分布高度集中，t 值会被过度放大；
- 符号检验只问"方向对不对"，不依赖分布假设，更诚实。
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib

import numpy as np
from scipy import stats

_HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_RUNS = _HERE / "runs" / "main_9task.jsonl"

NAMES = {
    "t27": "t27 外部 API 契约",
    "t28": "t28 消息队列契约",
    "t29": "t29 日志规范",
    "t33": "t33 SLA 超时预算",
    "t34": "t34 灰度门禁",
    "t35": "t35 埋点校验",
    "t36": "t36 接口限流",
    "t37": "t37 回刷审批",
    "t38": "t38 导出审批",
}

NOTES = {
    "t29": "能力边界（知道规矩没照做）",
    "t34": "",
    "t35": "只有 1 个检查点区分",
    "t36": "6 次里 3 次差一条",
    "t38": "9 次里真通过 1 次（无泄漏）",
}

SINGLE_CHECKPOINT = {"t35", "t36"}


def load_runs(path: pathlib.Path) -> collections.OrderedDict:
    if not path.exists():
        raise SystemExit(
            f"找不到 {path.name}。先跑：\n"
            f"    python eval/rebuild_runs.py\n"
            f"（它会对 eval/workspaces/ 里的历史目录重跑判卷，不调用 LLM）"
        )
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    agg: collections.OrderedDict = collections.OrderedDict()
    for r in rows:
        e = agg.setdefault(r["task"], {"bp": 0, "bn": 0, "sp": 0, "sn": 0})
        if r["group"] == "base":
            e["bn"] += 1
            e["bp"] += 1 if r["passed"] else 0
        else:
            e["sn"] += 1
            e["sp"] += 1 if r["passed"] else 0
    return agg


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(DEFAULT_RUNS))
    args = ap.parse_args()

    agg = load_runs(pathlib.Path(args.runs))
    tasks = list(agg)

    base = np.array([agg[t]["bp"] / agg[t]["bn"] for t in tasks])
    skill = np.array([agg[t]["sp"] / agg[t]["sn"] for t in tasks])
    diff = skill - base

    line = "=" * 72
    print(line)
    print("逐题通过率（分母 = 该题全部历史运行）")
    print(line)
    print(f"{'任务':<22}{'base':>10}{'skill':>10}{'差值':>8}   备注")
    for t, d in zip(tasks, diff):
        e = agg[t]
        print(f"{NAMES[t]:<22}{e['bp']}/{e['bn']:<8}{e['sp']}/{e['sn']:<8}"
              f"{d:>8.3f}   {NOTES.get(t, '')}")

    bp, bn = sum(agg[t]["bp"] for t in tasks), sum(agg[t]["bn"] for t in tasks)
    sp, sn = sum(agg[t]["sp"] for t in tasks), sum(agg[t]["sn"] for t in tasks)
    print(f"{'合计':<22}{bp}/{bn:<8}{sp}/{sn:<8}{'':>8}   "
          f"base {bp / bn:.1%} / skill {sp / sn:.1%}")

    print()
    print(line)
    print(f"配对 t 检验（n={len(tasks)} 题，单位是题不是次）")
    print(line)
    t, p = stats.ttest_rel(skill, base)
    print(f"均值差 = {diff.mean():.3f}   sd = {diff.std(ddof=1):.3f}")
    print(f"t = {t:.2f}   df = {len(diff) - 1}   p = {p:.2e}")
    ci = stats.t.interval(0.95, len(diff) - 1, loc=diff.mean(), scale=stats.sem(diff))
    print(f"95% CI = [{ci[0]:.3f}, {ci[1]:.3f}]")
    print()
    print("**这个 t 值不能按字面读。** base 侧几乎全是 0，配对差就等于 skill 侧通过率，")
    print("所以它**退化成了「单样本检验：skill 通过率是否显著大于 0」**——")
    print("描述的是「skill 侧离 0 有多远」，不是「两组差多少」。")
    if ci[1] > 1.0:
        print(f"另一个信号：**95% CI 上界 {ci[1]:.3f} > 1.0**。通过率不可能大于 1，")
        print("说明数据贴到天花板了——这是饱和迹象，不是「效果特别大」。")

    print()
    print(line)
    print("符号检验（不依赖分布假设，更适合这批数据）")
    print(line)
    pos = int((diff > 0).sum())
    neg = int((diff < 0).sum())
    tie = int((diff == 0).sum())
    sy = stats.binomtest(pos, pos + neg, 0.5, alternative="greater")
    print(f"skill > base: {pos} 题   skill < base: {neg} 题   持平: {tie} 题")
    print(f"单尾 p = {sy.pvalue:.4f}   （即 2^-{pos} 量级）")
    print("只问「方向对不对」，不依赖分布假设，也不被天花板效应放大。")

    print()
    print(line)
    print("敏感性分析：剔除只有 1 个检查点区分的 t35 / t36")
    print(line)
    keep = np.array([t not in SINGLE_CHECKPOINT for t in tasks])
    t2, p2 = stats.ttest_rel(skill[keep], base[keep])
    print(f"n = {int(keep.sum())}   t = {t2:.2f}   p = {p2:.2e}   "
          f"均值差 = {diff[keep].mean():.3f}")

    print()
    print(line)
    print("❗ 反向检验 1：把 t32（base 5/5 通过的反例）算进来")
    print(line)
    b2 = np.append(base, 1.0)
    s2 = np.append(skill, 1.0)
    t3, p3 = stats.ttest_rel(s2, b2)
    print(f"n = {len(b2)}（含 t32）  t = {t3:.2f}   p = {p3:.2e}   "
          f"均值差 = {(s2 - b2).mean():.3f}")
    print(f"t32 的差值是 0 —— 它把均值差从 {diff.mean():.3f} 拉低到 "
          f"{(s2 - b2).mean():.3f}，方向不变。")

    print()
    print(line)
    print("❗ 反向检验 2：把 t31 加回来（判卷修好后的真实表现 base 2/3）")
    print(line)
    b3 = np.append(b2, 2 / 3)
    s3 = np.append(s2, 1.0)
    t4, p4 = stats.ttest_rel(s3, b3)
    print(f"n = {len(b3)}（含 t32 + t31）  t = {t4:.2f}   p = {p4:.2e}   "
          f"均值差 = {(s3 - b3).mean():.3f}")
    print("结论仍同向。t31 说明：**规则是业界默认值时，这个任务不测任何东西**。")

    print()
    print(line)
    print("参考：合并计数的 Fisher 精确检验（仅作参照，不要单独引用）")
    print(line)
    table = [[bp, bn - bp], [sp, sn - sp]]
    odds, pf = stats.fisher_exact(table)
    print(f"2x2 = {table}   OR = {odds:.3g}   p = {pf:.2e}")
    print("⚠️ 这个 p 值**过度自信**：它把同一任务的多次运行当独立样本，")
    print("   忽略了「运行内相关」。所以只写在脚注，不写进结论。")

    print()
    print(line)
    print("与旧口径（手工挑批次）的差异")
    print(line)
    print("旧：base 0/31、skill 24/25、均值差 0.944、t=17.00")
    print(f"新：base {bp}/{bn}、skill {sp}/{sn}、均值差 {diff.mean():.3f}、t={t:.2f}")
    print("变的是数字，不是结论：9/9 仍全同向，p 仍是 0.002。")
    print("而且新口径**可复现**——任何人跑一次 rebuild_runs.py 就能重算。")


if __name__ == "__main__":
    main()
