"""Bash 沙箱逃逸拦截的单测（2026-10-27）。

由来：写 t27 时 agent 为了"查技能文档"执行 `find / -name SKILL.md`，
真的读到了 C:\\Users\\王\\.claude\\plugins 下的内容。
`cwd=workdir` 只限制默认起点，不限制能去哪——这是个真漏洞，不是配置问题。

这批用例锁两件事：
1. 明确指向沙箱外的命令必须被拦；
2. **正常命令一个都不能误伤** —— 误伤会让 agent 以为工具坏了，
   比不拦更糟（它可能改用更绕的写法继续尝试）。
"""

import sys
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT.parent))

from tools import _ESCAPE_PATTERNS  # noqa: E402


def blocked(cmd: str) -> bool:
    return bool(_ESCAPE_PATTERNS.search(cmd))


# ---------------- 必须拦住 ----------------

def test_find_from_root():
    assert blocked("find / -name SKILL.md")
    assert blocked("find / 2>/dev/null | head -50")


def test_cd_outside():
    assert blocked("cd / && ls")
    assert blocked("cd ~/projects")
    assert blocked("cd C:\\Users")


def test_absolute_system_paths():
    assert blocked("cat /etc/passwd")
    assert blocked("ls /")


def test_home_dir_paths():
    """家目录是隐私边界，出现即拦（t27 实际踩到的就是这条）。"""
    assert blocked('find /c/Users/wang -name "*.py"')
    assert blocked("grep -rn x /Users/someone/docs")
    assert blocked("cat /home/u/.ssh/id_rsa")


# ---------------- 必须放行（误伤比漏拦更糟） ----------------

def test_normal_commands_allowed():
    for cmd in [
        "python -m pytest tests -q",
        "pip install requests",
        "ls -la rates/",
        "cat rates/rates.py",
        "grep -rn CACHE_TTL .",
        'find . -name "*.py"',
        'python -c "import rates"',
        "python -m py_compile rates/rates.py",
        "cd rates && python -m pytest",
    ]:
        assert not blocked(cmd), f"正常命令被误伤: {cmd}"


def test_relative_parent_navigation_blocked():
    """`../` 不能放行 —— 2026-11-02 改判。

    原判断（10-27）是"有些任务会用它，只拦 ../../ 这种明显出界的"。
    这个判断是错的，两个理由：

    1. **不存在这种任务**：所有评测任务的工作区都是独立的，
       没有任何一个引用过 `../shared/`。那个理由是想象出来的。
    2. **一步就够了**：workdir 是 `eval/workspaces/<run>/`，
       上一级是 `workspaces/`，再上一级是 `eval/` —— 而 `eval/tasks/`
       里放着全部任务的 prompt 和 `_CHECKS` 判卷代码。
       `cd ..` 一步就能开始摸到答案。

    绝对路径已经拦了，那 `..` 就是**唯一还能走出去的写法**。
    沙箱的边界必须按"解析后的真实路径"算，不能按"字符串里斜杠的数量"算。
    """
    assert blocked("cat ../shared/util.py")


# ---------------- 2026-11-02 补：相对路径回退 ----------------
#
# t38 的 base 有一次 129k token 并且**通过了**，产物注释逐字复述了
# `skills/export-approval-policy/SKILL.md` 的内容。
# 它用的命令 `grep -rn ... ../../../skills` **不含任何绝对路径**，
# 但 `..` 一路退到了 workdir 外面 —— 原正则只拦 `../../` 开头的 cd，
# 单级 `..` 和 grep 后面的 `../../../` 全漏了。


def test_grep_into_parent_dirs():
    assert blocked('grep -rn "mask" ../../../skills')


def test_cat_into_parent_dirs():
    assert blocked("cat ../../../skills/export-approval-policy/SKILL.md")


def test_cd_single_parent():
    assert blocked("cd ..")


def test_cd_single_parent_then_run():
    assert blocked("cd .. && ls")


def test_python_open_parent():
    assert blocked('python -c "print(open(\'../secret\').read())"')


def test_windows_parent_sep():
    assert blocked("type ..\\secrets.txt")


# ---------------- 修完不能误伤 ----------------

def test_pytest_not_blocked():
    assert not blocked("python -m pytest tests -q")


def test_relative_read_not_blocked():
    assert not blocked("cat export/decider.py")


def test_grep_inside_workdir_not_blocked():
    assert not blocked('grep -rn "allow" export/')


def test_dotted_filename_not_blocked():
    """`decider.py` / `.gitignore` 这种点号不能被当成 `..`。"""
    assert not blocked("cat export/decider.py .gitignore")
