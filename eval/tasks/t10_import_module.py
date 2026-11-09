from ._util import run_python


def _verify(wd):
    code = "from app import compute; assert compute(3)==10; print('OK')"
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t10",
    "name": "多文件模块导入",
    "prompt": "创建两个文件：mathops.py 定义 square(x) 返回 x*x；app.py 里 `from mathops import square` 并定义 compute(n) 返回 square(n)+1。然后用 Bash 验证 compute(3) 等于 10。",
    "setup": None,
    "verify": _verify,
}
