"""t19：长链路数据管道 —— 考验「多步推进 + 结果可复算」。

难度来源：清洗规则 + 聚合 + 落盘 + 自测，四步串成一条链，任一步出错最终结果就不对。
且 verify 会用独立实现把答案重算一遍，任何"看起来完成了"的糊弄都过不了。
"""

import csv
import json
import subprocess
import sys

_HEADER = "region,amount,qty"
_ROWS = [
    "east,10,2",
    "east,5,3",
    "west,,4",        # amount 为空 → 丢弃
    "west,abc,2",     # amount 非数字 → 丢弃
    "east,10,2",      # 与第一行完全重复 → 去重
    "north,7.5,2",
    "north,3,1",
    "south,4,0",
    "south,6,1",
    "west,8,2",
]
_CSV = "\n".join([_HEADER] + _ROWS) + "\n"


def _expected():
    """用独立实现把正确答案重算一遍，不依赖 Agent 产出的任何代码。"""
    seen, agg = set(), {}
    for r in csv.DictReader(_CSV.splitlines()):
        key = (r["region"], r["amount"], r["qty"])
        if key in seen:
            continue
        seen.add(key)
        try:
            amount = float(r["amount"])
        except (TypeError, ValueError):
            continue
        qty = int(r["qty"])
        d = agg.setdefault(r["region"], {"total": 0.0, "count": 0})
        d["total"] += amount * qty
        d["count"] += 1
    return agg


def _setup(wd):
    (wd / "sales.csv").write_text(_CSV, encoding="utf-8")


def _verify(wd):
    rp = wd / "report.json"
    if not rp.exists():
        return False, "缺少 report.json"

    try:
        got = json.loads(rp.read_text(encoding="utf-8"))
    except Exception as e:
        return False, f"report.json 解析失败: {type(e).__name__}: {e}"

    exp = _expected()
    if set(got.keys()) != set(exp.keys()):
        return False, f"region 集合不符 期望={sorted(exp)} 实际={sorted(got)}"
    for region, e in exp.items():
        g = got[region]
        if set(g.keys()) != {"total", "count"}:
            return False, f"{region} 字段应只有 total/count，实际={sorted(g)}"
        if abs(float(g["total"]) - e["total"]) > 1e-6:
            return False, f"{region} total 期望 {e['total']} 实际 {g['total']}"
        if int(g["count"]) != e["count"]:
            return False, f"{region} count 期望 {e['count']} 实际 {g['count']}"

    # 还要求自带测试并真的跑通
    if not (wd / "test_report.py").exists():
        return False, "缺少 test_report.py（要求为管道写测试）"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "test_report.py", "-q"],
        cwd=str(wd), capture_output=True, text=True, timeout=120,
    )
    if p.returncode != 0:
        tail = (p.stdout or p.stderr).strip()[-300:]
        return False, f"test_report.py 未通过 rc={p.returncode} {tail!r}"

    return True, f"聚合结果正确 {exp} 且自带测试通过"


TASK = {
    "id": "t19",
    "name": "CSV清洗聚合管道+自测",
    "prompt": (
        "处理 `sales.csv`（列：region,amount,qty），规则如下：\n"
        "1) 丢弃 `amount` 为空或无法转成数字的行；\n"
        "2) 完全相同的行去重（只保留第一条）；\n"
        "3) 按 `region` 聚合：`total` = sum(amount * qty)，`count` = 该 region 的行数。\n"
        "把结果写入 `report.json`，格式严格为 "
        '{"<region>": {"total": <数字>, "count": <整数>}, ...}。\n'
        "最后创建 `test_report.py` 用 pytest 断言聚合结果正确，并运行确认通过。"
    ),
    "setup": _setup,
    "verify": _verify,
}
