from ._util import run_script


def _verify(wd):
    rc, out, err = run_script(wd, "hello.py")
    ok = rc == 0 and "Hello, World!" in out
    return ok, f"rc={rc} out={out.strip()!r}"


TASK = {
    "id": "t01",
    "name": "Hello World 创建并运行",
    "prompt": "在沙箱里创建 hello.py，运行后向标准输出打印 'Hello, World!'。请先用 WriteFile 创建文件，再用 Bash 运行确认输出。",
    "setup": None,
    "verify": _verify,
}
