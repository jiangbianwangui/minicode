"""配对统计：10 个"答案不在仓库里"的任务，base vs skill。

口径（按 2026-11-02 定的纪律）：
- 分母是**有交付的运行数**，零交付单列、不进分母；
- 每题用"通过率"配对，不是用原始次数（各题运行次数 2–6 不等）。

为什么同时报配对 t 和符号检验：
- 配对 t 要求差值近似正态，而这批数据的 base 恒为 0（天花板效应），
  差值的分布高度集中，t 值会被过度放大；
- 符号检验只问"方向对不对"，不依赖分布假设，更诚实。
"""

from scipy import stats
import numpy as np

# (任务, base 通过, base 有交付次数, skill 通过, skill 次数, 备注)
#
# ⚠️ 2026-11-19 变更：t31 从主表剔除，移到反向检验。
# 原因：它的六条检查点（最多 3 次 / 退避 0.5,1.0 / 422 不重试 / 尊重 Retry-After /
# 超 60 秒放弃）**全是业界默认值** —— 换个公司照样成立，按造题第一道闸门就不该进这个任务族。
# 原记录的 base 0/3 是**判卷 bug 的产物**：当时只认 `sleep=time.sleep`，
# 把参数化注入这种更规范的写法判成失败。判卷修好后重跑，base 是 2/3（6/6、6/6、3/6），
# 三次产物都过了 leak_audit（零命中），不是偷看，是真的猜对了。
DATA = [
    ("t27 外部 API 契约", 0, 3, 3, 3, ""),
    ("t28 消息队列契约", 0, 2, 2, 2, ""),
    ("t29 日志规范", 0, 2, 1, 2, "能力边界"),
    ("t33 SLA 超时预算", 0, 3, 3, 3, ""),
    ("t34 灰度门禁", 0, 6, 3, 3, "含 1 次零交付已剔除"),
    ("t35 埋点校验", 0, 6, 3, 3, "只有 1 个检查点区分"),
    ("t36 接口限流", 0, 3, 3, 3, "只有 1 个检查点区分"),
    ("t37 回刷审批", 0, 3, 3, 3, "多步链"),
    ("t38 导出审批", 0, 3, 3, 3, "多步链"),
]

base = np.array([b / n for _, b, n, _, _, _ in DATA])
skill = np.array([s / m for _, _, _, s, m, _ in DATA])
diff = skill - base

print("=" * 68)
print("逐题通过率")
print("=" * 68)
print(f"{'任务':<22}{'base':>10}{'skill':>10}{'差值':>8}   备注")
for (name, b, n, s, m, note), d in zip(DATA, diff):
    print(f"{name:<22}{b}/{n:<8}{s}/{m:<8}{d:>8.2f}   {note}")

print()
print("=" * 68)
print("配对 t 检验（n=%d 题）" % len(DATA))
print("=" * 68)
t, p = stats.ttest_rel(skill, base)
print(f"均值差 = {diff.mean():.3f}   sd = {diff.std(ddof=1):.3f}")
print(f"t = {t:.2f}   df = {len(diff) - 1}   p = {p:.2e}")
ci = stats.t.interval(0.95, len(diff) - 1, loc=diff.mean(),
                      scale=stats.sem(diff))
print(f"95% CI = [{ci[0]:.3f}, {ci[1]:.3f}]")

print()
print("=" * 68)
print("符号检验（不依赖分布假设，更适合这批数据）")
print("=" * 68)
pos = int((diff > 0).sum())
neg = int((diff < 0).sum())
tie = int((diff == 0).sum())
sp = stats.binomtest(pos, pos + neg, 0.5, alternative="greater")
print(f"skill > base: {pos} 题   skill < base: {neg} 题   持平: {tie} 题")
print(f"单尾 p = {sp.pvalue:.4f}   （即 2^-{pos} 量级）")

print()
print("=" * 68)
print("敏感性分析：剔除只有 1 个检查点区分的 t35 / t36")
print("=" * 68)
keep = np.array([True] * len(DATA))
for i, (_, _, _, _, _, note) in enumerate(DATA):
    if "只有 1 个" in note:
        keep[i] = False
t2, p2 = stats.ttest_rel(skill[keep], base[keep])
print(f"n = {keep.sum()}   t = {t2:.2f}   p = {p2:.2e}   均值差 = {diff[keep].mean():.3f}")
print(f"（剔除后结论方向不变，均值差反而升高 —— 说明 t35/t36 是拉低效应的那两题）")

print()
print("=" * 68)
print("❗ 反向检验 1：把 t32（base 5/5 通过）算进来")
print("=" * 68)
all_base = np.append(base, 1.0)
all_skill = np.append(skill, 1.0)
t3, p3 = stats.ttest_rel(all_skill, all_base)
print(f"n = {len(all_base)}（含 t32）  t = {t3:.2f}   p = {p3:.2e}   "
      f"均值差 = {(all_skill - all_base).mean():.3f}")
print(f"t32 的差值是 0 —— 它把均值差从 {diff.mean():.3f} 拉低到 "
      f"{(all_skill - all_base).mean():.3f}，结论方向不变。")

print()
print("=" * 68)
print("❗ 反向检验 2：把 t31 加回来（判卷修好后的真实表现 base 2/3）")
print("=" * 68)
b31, s31 = 2 / 3, 3 / 3
b2 = np.append(all_base, b31)
s2 = np.append(all_skill, s31)
t4, p4 = stats.ttest_rel(s2, b2)
print(f"n = {len(b2)}（含 t32 + t31）  t = {t4:.2f}   p = {p4:.2e}   "
      f"均值差 = {(s2 - b2).mean():.3f}")
print(f"t31 的差值只有 {s31 - b31:.2f}（base 自己就会），把均值差从 {diff.mean():.3f} "
      f"拉到 {(s2 - b2).mean():.3f}。")
print("结论仍同向，但 t31 说明一件事：**规则是业界默认值时，这个任务不测任何东西**。")

print()
print("=" * 68)
print("参考：合并计数的 Fisher 精确检验（仅作参照，不要单独引用）")
print("=" * 68)
# base 有交付 30 次全失败；skill 24 通过 / 1 失败（已剔除 t31）
table = [[0, 30], [24, 1]]
odds, pf = stats.fisher_exact(table)
print(f"2x2 = {table}   OR = {odds:.3g}   p = {pf:.2e}")
print("⚠️ 这个 p 值**过度自信**：它把同一任务的多次运行当独立样本，")
print("   忽略了「运行内相关」。所以只写在脚注，不写进结论。")
