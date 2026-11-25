"""STATS.md 的逐题数字，必须能由 `eval/runs/main_9task.jsonl` 重算出来。

为什么需要这条：

`eval/runs/main_9task.jsonl` 是 `eval/rebuild_runs.py` 对历史 workspace
重跑判卷的产物 —— 它是主结论的**唯一原始记录**（`eval/EVAL*.md` 不进仓库）。
STATS.md 里的表格是照它抄的，但抄就会错：2026-11-25 之前那份表里，
t38 写的是 0/3，而 jsonl 里是 1/9 —— 漏报了一次 base 通过。

这条测试不重跑判卷（那要 4 分钟，不能进 CI），只做一件事：
**把 jsonl 聚合一遍，逐题和 STATS.md 的表格对齐**。
抄错就红，改了数据不重跑也红。
"""

import collections
import json
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_EVAL = _ROOT / "eval"
_RUNS = _EVAL / "runs" / "main_9task.jsonl"

_ROW = re.compile(
    r"^\|\s*(t\d+)[^|]*\|\s*\*{0,2}(\d+)\s*/\s*(\d+)\*{0,2}\s*"
    r"\|\s*\*{0,2}(\d+)\s*/\s*(\d+)\*{0,2}\s*\|",
    re.M,
)


def _agg_from_runs() -> dict:
    rows = [json.loads(line) for line in _RUNS.open(encoding="utf-8") if line.strip()]
    agg = collections.defaultdict(lambda: {"bp": 0, "bn": 0, "sp": 0, "sn": 0})
    for r in rows:
        e = agg[r["task"]]
        if r["group"] == "base":
            e["bn"] += 1
            e["bp"] += 1 if r["passed"] else 0
        else:
            e["sn"] += 1
            e["sp"] += 1 if r["passed"] else 0
    return dict(agg)


def test_runs_file_exists_and_wellformed():
    assert _RUNS.exists(), (
        "缺少 eval/runs/main_9task.jsonl —— 先跑 `python eval/rebuild_runs.py`"
    )
    rows = [json.loads(line) for line in _RUNS.open(encoding="utf-8") if line.strip()]
    assert rows, "main_9task.jsonl 是空的"
    for r in rows:
        for key in ("task", "group", "dir", "passed"):
            assert key in r, f"记录缺字段 {key}: {r}"
        assert r["group"] in ("base", "skill"), f"未知分组: {r['group']}"


def test_stats_table_matches_runs():
    agg = _agg_from_runs()
    text = (_EVAL / "STATS.md").read_text(encoding="utf-8")
    table = {
        tid: (int(b), int(bn), int(s), int(sn))
        for tid, b, bn, s, sn in _ROW.findall(text)
    }

    assert table, "STATS.md 的逐题表没解析到 —— 格式变了？"
    assert set(table) == set(agg), (
        f"题目集合不一致：表格 {sorted(table)} vs 运行记录 {sorted(agg)}"
    )

    for tid in sorted(table):
        b, bn, s, sn = table[tid]
        e = agg[tid]
        assert (b, bn, s, sn) == (e["bp"], e["bn"], e["sp"], e["sn"]), (
            f"{tid} 与运行记录不一致：表格写 base {b}/{bn}、skill {s}/{sn}，"
            f"jsonl 重算是 base {e['bp']}/{e['bn']}、skill {e['sp']}/{e['sn']}。"
            f"改了数据就重跑 rebuild_runs.py，或者改表格。"
        )


def test_every_run_dir_is_traceable():
    """每条记录都要能指回一个真实目录名，否则"可追溯"是空话。"""
    rows = [json.loads(line) for line in _RUNS.open(encoding="utf-8") if line.strip()]
    for r in rows:
        assert r["dir"].startswith(r["task"] + "_"), (
            f"目录名 {r['dir']} 与任务 {r['task']} 不匹配"
        )
        assert f"_{r['group']}_" in r["dir"], (
            f"目录名 {r['dir']} 里找不到分组标记 _{r['group']}_"
        )
