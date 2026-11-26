"""CHECKPOINTS.md 的逐题检查点数，必须能由 `eval/runs/checkpoints_9task.jsonl` 重算。

这套数据和主表（`main_9task.jsonl`）是**两批不同的运行**：
- 主表：`tXX_base_rN_<时间戳>`，看"整题过没过"；
- 检查点剖析：`tXX_cp_base{N}b` / `tXX_cp_skill{N}`，看"五六个检查点各过没过"。

为什么要单独守：
`CHECKPOINTS.md` 里那句「base 38/126 = 30%」和「skill 125/126 = 99%」
原来只有当时的终端输出，没有可复算的原始记录。2026-11-26 用
`rebuild_runs.py --batch cp` 重判了 54 个目录，逐题和文档完全对上 ——
这条测试就是把"对上"这件事固定下来，以后改判卷或改数字都会红。
"""

import collections
import json
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_EVAL = _ROOT / "eval"
_RUNS = _EVAL / "runs" / "checkpoints_9task.jsonl"

_ROW = re.compile(
    r"^\|\s*(t\d+)[^|]*\|\s*(\d+)\s*/\s*(\d+)\*?\s*"
    r"\|\s*(\d+)\s*/\s*(\d+)\*?\s*\|",
    re.M,
)
_TOTAL_AFTER_EXCL = re.compile(
    r"剔 t31 后\*\*）\s*\|\s*\*\*(\d+)\s*/\s*(\d+)\*\*\s*\|\s*\*\*(\d+)%"
)


def _load() -> list[dict]:
    assert _RUNS.exists(), (
        "缺少 eval/runs/checkpoints_9task.jsonl —— 先跑 "
        "`python eval/rebuild_runs.py --batch cp`"
    )
    return [json.loads(x) for x in _RUNS.open(encoding="utf-8") if x.strip()]


def _agg(rows) -> dict:
    agg = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        i = 0 if r["group"] == "base" else 2
        agg[r["task"]][i] += r["checks_passed"]
        agg[r["task"]][i + 1] += r["checks_total"]
    return dict(agg)


def test_runs_file_wellformed():
    rows = _load()
    assert len(rows) == 54, f"应该有 54 条（9 题 × 2 侧 × 3 次），实际 {len(rows)}"
    for r in rows:
        for key in ("task", "group", "dir", "checks_passed", "checks_total"):
            assert key in r, f"记录缺字段 {key}: {r}"
        assert r["checks_total"] > 0, f"{r['dir']} 没有检查点信息"


def test_per_task_matches_doc():
    agg = _agg(_load())
    text = (_EVAL / "CHECKPOINTS.md").read_text(encoding="utf-8")
    table = {
        tid: (int(b), int(bn), int(s), int(sn))
        for tid, b, bn, s, sn in _ROW.findall(text)
        if tid != "t31"
    }

    assert table, "CHECKPOINTS.md 的逐题表没解析到 —— 格式变了？"
    assert set(table) == set(agg), (
        f"题目集合不一致：表格 {sorted(table)} vs 运行记录 {sorted(agg)}"
    )

    for tid in sorted(table):
        assert table[tid] == tuple(agg[tid]), (
            f"{tid} 与重判结果不一致：表格 {table[tid]}，"
            f"jsonl 重算 {tuple(agg[tid])}"
        )


def test_totals_match_doc():
    agg = _agg(_load())
    bp = sum(v[0] for v in agg.values())
    bn = sum(v[1] for v in agg.values())
    sp = sum(v[2] for v in agg.values())
    sn = sum(v[3] for v in agg.values())

    text = (_EVAL / "CHECKPOINTS.md").read_text(encoding="utf-8")
    found = _TOTAL_AFTER_EXCL.findall(text)
    assert len(found) == 2, (
        f"应该找到「剔 t31 后」的 base 和 skill 两行，实际 {len(found)} 行"
    )
    (db, dbn, _pct_b), (ds, dsn, _pct_s) = found

    assert (bp, bn) == (int(db), int(dbn)), (
        f"base 合计：重判 {bp}/{bn}，文档写 {db}/{dbn}"
    )
    assert (sp, sn) == (int(ds), int(dsn)), (
        f"skill 合计：重判 {sp}/{sn}，文档写 {ds}/{dsn}"
    )
