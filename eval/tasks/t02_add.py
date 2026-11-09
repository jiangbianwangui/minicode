from ._util import run_python


def _verify(wd):
    code = "from add import add; assert add(2,3)==5 and add(-1,1)==0; print('OK')"
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t02",
    "name": "add 函数 + 断言自测",
    "prompt": "创建 add.py，定义函数 add(a, b) 返回 a + b。然后用 Bash 运行一段校验代码：import add 并断言 add(2,3)==5 与 add(-1,1)==0 都成立。",
    "setup": None,
    "verify": _verify,
}
