"""评测任务共用的小工具：在 workdir 里跑 python 做自动校验。"""

import subprocess
import sys


def run_python(wd, code=None, script=None, args=None, timeout=60):
    """在 wd 内运行 python。

    - code: 传 -c 字符串（如 "from add import add; assert add(2,3)==5"）
    - script: 传文件名（如 "hello.py"），配合 args 给命令行参数
    返回 (returncode, stdout, stderr)。
    """
    cmd = [sys.executable]
    if script:
        cmd.append(str(wd / script))
    if args:
        cmd += list(args)
    if code is not None:
        cmd += ["-c", code]
    p = subprocess.run(cmd, cwd=str(wd), capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def run_script(wd, script, args=None, timeout=60):
    return run_python(wd, script=script, args=args, timeout=timeout)
