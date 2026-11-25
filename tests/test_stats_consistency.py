"""STATS.md 的「合计」必须是逐行加出来的，跨文件的次数也必须一致。

为什么需要这条：

主结论的分母是**手工抄**进 STATS.md 的。抄错了不会报错，只会静默过期，
而且过期的是整个项目最小的那个数字——`base 0/30` 里的 30。

实测就这样错过一次：9 题的 base 分母逐行是 3+2+2+3+6+6+3+3+3 = **31**，
表格合计却写 30；下游的「主对照 30 次 + 复查 27 次 = 57 次」顺着错到 58。
README 那类数字已经有守卫了（`test_readme_consistency`），统计口径还没有。

这条测试只做两件机械的事：
1. 表格合计 == 逐行求和（用 `eval/recount.py` 的解析器，不另写一份）；
2. STATS.md 与 CHECKPOINTS.md 里「N 次里通过 1 次」的 N 必须相同。

它不判断口径本身对不对（那是人的事），只保证**能加出来的数不会被手抄篡改**。
"""

import importlib.util
import pathlib
import re

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_EVAL = _ROOT / "eval"

_TOTAL_TIMES = re.compile(r"base\s*\*{0,2}(\d+)\s*次里通过\s*\*{0,2}(\d+)\s*次")


def _load_recount():
    spec = importlib.util.spec_from_file_location("recount", _EVAL / "recount.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_stats_total_matches_row_sum():
    recount = _load_recount()
    text = (_EVAL / "STATS.md").read_text(encoding="utf-8")
    rows, total = recount.parse_stats(text)

    assert rows, "STATS.md 的逐题表没解析到 —— 格式变了？"
    assert total is not None, "STATS.md 找不到「合计」行"

    computed = (
        sum(r[1] for r in rows),
        sum(r[2] for r in rows),
        sum(r[3] for r in rows),
        sum(r[4] for r in rows),
    )
    assert computed == total, (
        f"合计与逐行不一致：逐行 base {computed[0]}/{computed[1]}、"
        f"skill {computed[2]}/{computed[3]}；表格写 base {total[0]}/{total[1]}、"
        f"skill {total[2]}/{total[3]}。跑 `python eval/recount.py --fix` 看建议值。"
    )


def test_combined_sample_count_consistent_across_docs():
    stats_text = (_EVAL / "STATS.md").read_text(encoding="utf-8")
    cp_text = (_EVAL / "CHECKPOINTS.md").read_text(encoding="utf-8")

    stats_n = _TOTAL_TIMES.findall(stats_text)
    cp_n = _TOTAL_TIMES.findall(cp_text)
    assert stats_n, "STATS.md 里没找到「base N 次里通过 M 次」"
    assert cp_n, "CHECKPOINTS.md 里没找到「base N 次里通过 M 次」"

    assert set(stats_n) == set(cp_n), (
        f"两份文档对合并样本量的说法不一致：STATS.md {set(stats_n)}、"
        f"CHECKPOINTS.md {set(cp_n)}"
    )


def test_combined_count_equals_primary_plus_recheck():
    """合并样本量 = 主对照分母 + 复查 27 次（9 题 × 3 次，剔 t31 后）。"""
    recount = _load_recount()
    text = (_EVAL / "STATS.md").read_text(encoding="utf-8")
    rows, total = recount.parse_stats(text)
    base_n = sum(r[2] for r in rows)

    m = _TOTAL_TIMES.search(text)
    assert m, "STATS.md 里没找到「base N 次里通过 M 次」"
    combined = int(m.group(1))

    recheck = 9 * 3
    assert combined == base_n + recheck, (
        f"合并样本量 {combined} ≠ 主对照 {base_n} + 复查 {recheck} = {base_n + recheck}"
    )
