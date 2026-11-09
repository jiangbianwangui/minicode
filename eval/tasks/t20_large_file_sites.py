"""t20：大文件多处一致性改造 —— 考验「不漏改 + 每处带上下文」。

难度来源：10 个调用点散落在约 400 行文件里，漏一处即败；
且每处必须填上**所在函数名**作为 module 参数，不能靠全局替换一把梭。
"""

import re
import subprocess
import sys

_FUNCS = [
    "load_config", "parse_input", "validate_schema", "transform_rows",
    "enrich_data", "dedupe_rows", "sort_rows", "serialize_out",
    "write_disk", "notify_done",
]

_FILLER = '''    # --- 业务逻辑骨架（占位）---
    rows = []
    for i in range(3):
        rows.append({"id": i, "value": i * 2})
    if not rows:
        return None
    total = sum(r["value"] for r in rows)
    meta = {"count": len(rows), "total": total}
    for r in rows:
        r["ok"] = r["value"] >= 0
    rows.sort(key=lambda r: r["id"])
    return {"meta": meta, "rows": rows}
'''


def _gen_legacy() -> str:
    out = ['"""遗留模块：需要统一改造日志调用。"""', "", "", "def _helper(x):", "    return x * 2", ""]
    for name in _FUNCS:
        out.append("")
        out.append(f"def {name}():")
        out.append(f'    """{name} 的处理流程。"""')
        out.append(_FILLER.rstrip("\n"))
        out.append(f'    log_it("{name} done")')
        out.append("    return True")
    return "\n".join(out) + "\n"


def _setup(wd):
    (wd / "legacy.py").write_text(_gen_legacy(), encoding="utf-8")


def _verify(wd):
    f = wd / "legacy.py"
    if not f.exists():
        return False, "缺少 legacy.py"
    src = f.read_text(encoding="utf-8")

    if "log_it" in src:
        return False, "仍残留旧调用 log_it"

    n = src.count("logger.info(")
    if n != len(_FUNCS):
        return False, f"logger.info 调用数应为 {len(_FUNCS)}，实际 {n}"

    # 每处必须带正确的 func=<所在函数名>
    # 注：这里刻意不用 module= —— module 是 logging.LogRecord 的保留属性，
    # 传入会抛 KeyError，那是考运气不是考能力。
    cur, bad = None, []
    for line in src.splitlines():
        m = re.match(r"def (\w+)\(", line)
        if m:
            cur = m.group(1)
        if "logger.info(" in line:
            if cur is None or (f"func='{cur}'" not in line and f'func="{cur}"' not in line):
                bad.append(line.strip())
    if bad:
        return False, f"以下调用缺少/写错 func 参数: {bad[:3]}"

    # 改造后代码必须仍能跑通
    calls = "; ".join(f"legacy.{n}()" for n in _FUNCS)
    code = f"import legacy\n{calls}\nprint('OK')\n"
    p = subprocess.run(
        [sys.executable, "-c", code], cwd=str(wd),
        capture_output=True, text=True, timeout=60,
    )
    if p.returncode != 0 or "OK" not in p.stdout:
        return False, f"运行失败 rc={p.returncode} err={p.stderr.strip()[-200:]!r}"

    return True, f"{len(_FUNCS)} 处调用点全部改造且 func 正确，代码可运行"


TASK = {
    "id": "t20",
    "name": "大文件10处调用点改造",
    "prompt": (
        "`legacy.py` 里有 10 个函数，每个函数末尾都调用了一次 `log_it(\"...\")`。\n"
        "请把**全部 10 处**改成 `logger.info(...)`，并给每次调用额外加上 "
        "`func=\"<该函数自己的名字>\"` 参数（例如 `load_config` 里就是 func=\"load_config\"）。\n"
        "要求：文件里不再出现 `log_it`；`logger` 需要自行用 logging 定义好；"
        "改造后直接运行 `python -c \"import legacy\"` 不能报错"
        "（注意：这条**当前就已经通过**，它只能证明你没把 import 弄坏，"
        "不能说明改造做完了）。"
    ),
    "setup": _setup,
    "verify": _verify,
    # 审计豁免：这条命令初始就通过，作者已知，且 prompt 里已写明它不算完成信号。
    "audit_ok": ['python -c "import legacy"'],
}
