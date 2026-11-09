"""随机抽样任务集（阴性对照）。

见 eval/SAMPLED.md：这 5 题和 t27–t39 同形，但**规格全给**，
用途是测 base 在这类任务上的天然通过率，从而给主结论一个诚实的「分母」。

这些任务**不进** eval/tasks 的主注册表，也不进 10 题的配对统计。
"""

from . import s01_date_months, s03_split_amount, s05_version_compare
from . import s08_lru_cache, s12_template_render

TASKS = [
    s01_date_months.TASK,
    s03_split_amount.TASK,
    s05_version_compare.TASK,
    s08_lru_cache.TASK,
    s12_template_render.TASK,
]

# id → (参考解源码, 变异字典)，给 selftest 用
SOLUTIONS = {
    "s01": (s01_date_months.SOLUTION, s01_date_months.MUTANTS),
    "s03": (s03_split_amount.SOLUTION, s03_split_amount.MUTANTS),
    "s05": (s05_version_compare.SOLUTION, s05_version_compare.MUTANTS),
    "s08": (s08_lru_cache.SOLUTION, s08_lru_cache.MUTANTS),
    "s12": (s12_template_render.SOLUTION, s12_template_render.MUTANTS),
}

# id → 参考解该写到哪个文件
TARGETS = {
    "s01": "reporting/months.py",
    "s03": "billing/split.py",
    "s05": "versioning/version.py",
    "s08": "cache/lru.py",
    "s12": "text/template.py",
}
