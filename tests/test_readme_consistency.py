"""README 里写的单测数必须等于真实收集到的数。

为什么需要这条：

这个数字漂过三次（100 → 106 → 107 → 108），每次都是"加了测试忘了改 README"。
它不像代码错误会报错，只会静默过期——而 README 是面试官最先看的东西，
写 106 而仓库实际有 108 个测试，是一种**最廉价的不可信**。

用子进程跑 `--collect-only`（只收集不执行），所以不会递归触发测试本身。
"""

import pathlib
import re
import subprocess
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _collected_count() -> int:
    p = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
        cwd=str(_ROOT), capture_output=True, text=True, timeout=180,
    )
    m = re.search(r"(\d+) tests? collected", p.stdout)
    assert m, f"没能从 pytest 输出里解析出数量：\n{p.stdout[-500:]}\n{p.stderr[-500:]}"
    return int(m.group(1))


def test_readme_test_count_matches_reality():
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"#\s*(\d+)\s*个单测", readme)
    assert m, "README 里找不到 `# N 个单测` 这样的标记"
    claimed = int(m.group(1))
    actual = _collected_count()
    assert claimed == actual, (
        f"README 写的是 {claimed} 个单测，实际收集到 {actual} 个。"
        f"改了测试就要同步 README —— 这个数字已经漂过三次了。"
    )
