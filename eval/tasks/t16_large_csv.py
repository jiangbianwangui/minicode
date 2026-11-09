import csv
import json
import random
from pathlib import Path

from ._util import run_python

_REGIONS = ["east", "west", "north", "south"]


def setup(wd):
    rng = random.Random(42)
    rows = ["date,region,amount"]
    for i in range(5000):
        region = _REGIONS[i % 4]
        if rng.random() < 0.02:
            amount = rng.choice(["N/A", "", "None", "abc"])
        else:
            amount = f"{rng.uniform(1, 100):.2f}"
        rows.append(f"2026-01-{i % 28 + 1:02d},{region},{amount}")
    (wd / "data.csv").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _expected(wd):
    totals = {r: 0.0 for r in _REGIONS}
    with open(wd / "data.csv", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            amt = (row.get("amount") or "").strip()
            try:
                totals[row["region"]] += float(amt)
            except ValueError:
                pass
    return totals


def _verify(wd):
    exp = _expected(wd)
    rc, out, err = run_python(
        wd,
        code=(
            "from aggregate import region_totals\n"
            "res = region_totals('data.csv')\n"
            "import json\nprint(json.dumps(res))\n"
        ),
    )
    if rc != 0:
        return False, f"rc={rc} err={err.strip()!r}"
    try:
        got = json.loads(out.strip().splitlines()[-1])
    except Exception as e:  # noqa: BLE001
        return False, f"parse err {e} out={out.strip()!r}"
    ok = all(abs(got.get(r, 0) - exp[r]) < 0.01 for r in _REGIONS)
    return ok, f"exp={exp} got={got}"


TASK = {
    "id": "t16",
    "name": "大 CSV 聚合（含脏数据）",
    "prompt": (
        "data.csv 有表头 date,region,amount，约 5000 行。其中少量行的 amount 不是合法数字"
        "（如 'N/A'、空串、'abc'），应跳过。请写 aggregate.py，实现函数 "
        "`region_totals(path) -> dict`，返回每个 region 的 amount 之和（跳过非法行），"
        "并在 `if __name__ == '__main__'` 中打印结果。只能用标准库 csv，不要引入 pandas。"
    ),
    "setup": setup,
    "verify": _verify,
}
