from ._util import run_python


def _verify(wd):
    code = "from shapes import Circle; assert abs(Circle(2).area() - 12.56) < 0.01; print('OK')"
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t06",
    "name": "类与实例方法",
    "prompt": "创建 shapes.py，定义 Circle 类：__init__(self, r) 保存半径；area() 返回面积（3.14 * r * r），结果保留两位小数。然后用 Bash 验证 Circle(2).area() 约等于 12.56。",
    "setup": None,
    "verify": _verify,
}
