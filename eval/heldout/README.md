# heldout/ — 留出任务的参考 skill

这个目录里的 skill **不参与路由加载**（router 只扫 `skills/`），
用途是验证某个留出任务的"可解性上限"。

## 为什么需要它

如果一篇 skill 留在 `skills/` 里，这个任务就变成"开卷"——
base 也能召回到答案（t38 就因此被击穿一次）。
留出任务的价值在于：**它永远没有对应 skill**，可以用来测"现有库能不能迁移过去"。

但判卷要证明"这题确实可解"，所以参考 skill 得留着 —— 放在这里，
C 组运行时由 `eval/run_heldout.py` 临时复制进 `skills/`，跑完在 `finally` 里删掉。

## 文件

| 文件 | 对应任务 |
| --- | --- |
| `t39_reference_skill.md` | t39 告警静默审批 |
| `t40_reference_skill.md` | t40 临时提权审批 |

## 结果

完整数据、结论与**自我更正**记录见 [`../HELDOUT.md`](../HELDOUT.md)。

一句话版本：**现有库能迁移「判定骨架」，不能迁移「阈值参数」；
所以 B 组检查点 4.2/6 远高于 A 组 1.7/6，但通过率都是 0。**

> 2026-11-07 那次记的「B 和 A 总分完全一样」没有复现，旧数字作废，原因见 HELDOUT.md 第 6 节。

## 跑法

```bash
python eval/run_heldout.py --task t39 --repeats 3
python eval/run_heldout.py --task t40 --repeats 3
```
