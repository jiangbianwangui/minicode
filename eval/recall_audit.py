"""召回现状审计：把 44 条真实 prompt 的召回行为逐条摊开。

为什么需要它：
    `test_recall_snapshot.py` 只锁结论（12/44、每个都对），但**不告诉你另外 32 条错在哪**。
    想改召回，先得知道失败模式分成几类——是「该召回的压根没过准入闸门」，
    还是「过了闸门但被别的技能挤下去」，这两种的修法完全不同（换通道 vs 调排序）。

顺带纠正一个说法：
    `skill_router` 不是「BM25 召回」，是「tag 字面准入 + BM25 微调」。
    `if not specific and not name_hit: continue` 这一行决定了：
    **BM25 分数再高也进不来**（它只占 score 的 0.2 权重）。
    所以「同义改写召不回来」的根因不在排序，在准入通道本身 —— 这也是加向量通道
    必须**旁路这条 gate**、而不是塞进 BM25 的原因。

用法：python eval/recall_audit.py
"""

from __future__ import annotations

import pathlib
import sys
from collections import Counter

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))

from skill_router import default_router, tokenize  # noqa: E402
from tasks import TASKS  # noqa: E402

try:
    from sampled import TASKS as SAMPLED
except ImportError:  # pragma: no cover
    SAMPLED = []

# 该召回的 12 条（与 test_recall_snapshot.EXPECTED 保持一致）
EXPECTED = {
    "t27": "rates-api",
    "t28": "messaging-contract",
    "t29": "logging-standard",
    "t31": "retry-budget",
    "t32": "report-export-encoding",
    "t33": "sla-timeout-policy",
    "t34": "canary-gate-policy",
    "t35": "tracking-event-policy",
    "t36": "rate-limit-policy",
    "t37": "backfill-approval-policy",
    "t38": "export-approval-policy",
    "t39": "backfill-approval-policy",
}

# 留出查询：改路由时**不许看这几条的细节**，只在最后报一次总数。
# 按 skill 分层抽（尽量一 skill 一条），否则「在 44 条上调好再在 44 条上报」＝过拟合。
HELDOUT = {"t28", "t29", "t31", "t32", "t34", "t36", "t37", "s05"}


def gate_status(router, task: str, skill) -> tuple[bool, list[str], float, float]:
    """返回 (是否通过准入闸门, 命中的 specific tag, bonus, bm25)。"""
    task_low = task.lower()
    q_tokens = set(tokenize(task))
    tag_hits = router._tag_hits(task_low, q_tokens, skill)
    specific = [t for t in tag_hits if t not in router.GENERIC_TAGS]
    name_hit = len(skill.name) >= 3 and skill.name.lower() in task_low
    bonus = sum(1.0 + 0.15 * len(t) for t in specific) + (3.0 if name_hit else 0.0)
    bm = router._bm25(Counter(tokenize(task)), skill)
    return bool(specific or name_hit), specific, bonus, bm


def audit(router, prompts: dict[str, str]) -> list[dict]:
    rows = []
    for tid in sorted(prompts, key=lambda x: (x[0], int("".join(c for c in x if c.isdigit()) or 0))):
        prompt = prompts[tid]
        hits = router.retrieve(prompt)
        names = [h.skill.name for h in hits]
        want = EXPECTED.get(tid)
        rec = {"id": tid, "want": want or "-", "got": names, "heldout": tid in HELDOUT}
        if want is None:
            rec["cls"] = "误召" if names else "正确静默"
            rec["why"] = "不该召回却召回" if names else ""
        elif names and names[0] == want:
            rec["cls"] = "命中"
            rec["why"] = ""
        elif not names:
            rec["cls"] = "漏召"
            skill = router.get(want)
            if skill is None:
                rec["why"] = f"技能库里没有 {want}"
            else:
                passed, specific, bonus, bm = gate_status(router, prompt, skill)
                if not passed:
                    rec["why"] = "没过准入闸门（tag 未字面命中）"
                elif bonus + 0.2 * bm < 10.0:
                    rec["why"] = f"过闸门但分数不足（{bonus + 0.2 * bm:.1f} < 10.0）"
                else:
                    rec["why"] = "过闸门且分数够，但被 top_k/rel 截掉"
                rec["detail"] = f"bonus={bonus:.1f} bm25={bm:.1f} tags={specific}"
        else:
            rec["cls"] = "排错"
            rec["why"] = f"首位是 {names[0]}"
        rows.append(rec)
    return rows


def main() -> None:
    router = default_router()
    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    rows = audit(router, by_id)

    print(f"技能库：{len(router.skills)} 篇   prompt：{len(by_id)} 条\n")
    print(f"{'id':6s} {'期望':22s} {'实际召回':30s} {'分类':8s} 诊断")
    print("-" * 118)
    for r in rows:
        got = ", ".join(r["got"]) or "—"
        mark = "*" if r["heldout"] else " "
        print(f"{r['id']:5s}{mark} {r['want']:22s} {got:30s} {r['cls']:8s} {r['why']}")
        if r.get("detail") and r["cls"] == "漏召":
            print(f"{'':6s} {'':22s} {'':30s} {'':8s}   {r['detail']}")

    print("\n" + "=" * 60)
    for split, subset in (("开发集", [r for r in rows if not r["heldout"]]),
                          ("留出集", [r for r in rows if r["heldout"]])):
        c = Counter(r["cls"] for r in subset)
        pos = [r for r in subset if r["want"] != "-"]
        print(f"{split}（{len(subset)} 条，其中该召回 {len(pos)} 条）："
              f"命中 {c['命中']} / 漏召 {c['漏召']} / 排错 {c['排错']} / "
              f"误召 {c['误召']} / 正确静默 {c['正确静默']}")

    miss = [r for r in rows if r["cls"] == "漏召"]
    if miss:
        print("\n漏召的根因分布：")
        for why, n in Counter(r["why"].split("（")[0] for r in miss).most_common():
            print(f"  {n:2d} 条 · {why}")


if __name__ == "__main__":
    main()
