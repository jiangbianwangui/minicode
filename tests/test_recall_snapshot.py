"""召回快照测试：用**真实语料**锁住 13 篇库的召回行为。

为什么单独一个文件：
    绝对分数里 `0.2 × BM25` 会随语料规模剧烈变化 —— 只有 1–2 篇 skill 的迷你语料里
    BM25 退化成噪声，真命中也只有 5–6 分；真实 13 篇库的真命中是 13–30 分。
    所以 `test_skill_router.py` 只测打分机制（显式 `min_abs=0`），
    **"哪些任务该召回、哪些不该"只能拿真实语料测**。

这个文件锁的是 2026-11-09 那次修复的结果：
    修复前 44 个 prompt 里有 24 个会召回（其中一大半是噪声：
    `pytest-style` 靠 `pytest`+`test` 命中了 20 个任务，连 5 个随机抽样任务都被塞了文档）；
    修复后**只有 12 个**，且每一个都对着正确的那一篇。
"""

import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "eval"))

from skill_router import default_router  # noqa: E402


# 该召回的（每个任务对着一篇）
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


@pytest.fixture(scope="module")
def prompts():
    from tasks import TASKS
    from sampled import TASKS as SAMPLED

    by_id = {t["id"]: t["prompt"] for t in list(TASKS) + list(SAMPLED)}
    return by_id


@pytest.fixture(scope="module")
def router():
    return default_router()


@pytest.mark.parametrize("tid,skill", sorted(EXPECTED.items()))
def test_expected_recall(router, prompts, tid, skill):
    """每个"答案不在仓库里"的任务，都要召回到它那一篇，并且排在第一位。"""
    hits = router.retrieve(prompts[tid])
    names = [h.skill.name for h in hits]
    assert names, f"{tid} 应当召回 {skill}，实际一个都没召回"
    assert names[0] == skill, f"{tid} 首位应为 {skill}，实际 {names}"


def test_sampled_tasks_get_no_skill(router, prompts):
    """规格给全的任务不该被塞任何文档。

    这是 2026-11-09 阴性对照里发现的缺陷：5/5 抽样任务都被硬塞了
    `pytest-style`（靠 `pytest`+`test`）以及 `rates-api`/`messaging-contract`
    （靠 `cache` 和 `消息` 这种过宽的 tag）。
    """
    for tid in ("s01", "s03", "s05", "s08", "s12"):
        names = [h.skill.name for h in router.retrieve(prompts[tid])]
        assert names == [], f"{tid} 不该召回任何 skill，实际 {names}"


def test_recall_is_not_too_wide(router, prompts):
    """整体召回率不能失控：44 个 prompt 里只该有 12 个召回。

    没有这条断言的话，"把所有 prompt 都召回" 和 "只召回对的" 都能通过上面两条。
    """
    hit_ids = [tid for tid, p in prompts.items() if router.retrieve(p)]
    assert len(hit_ids) == len(EXPECTED), f"召回了 {len(hit_ids)} 个：{sorted(hit_ids)}"
