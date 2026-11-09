from ._util import run_python


def _verify(wd):
    rc, out, err = run_python(wd, script="greet.py", args=["Alice"])
    ok = rc == 0 and "Hello, Alice!" in out
    return ok, f"rc={rc} out={out.strip()!r}"


TASK = {
    "id": "t07",
    "name": "命令行参数 CLI",
    "prompt": "写 greet.py，从 sys.argv 读取一个名字参数（sys.argv[1]），向标准输出打印 'Hello, <名字>!'。然后用 Bash 运行 greet.py Alice 确认输出是 'Hello, Alice!'。",
    "setup": None,
    "verify": _verify,
}
