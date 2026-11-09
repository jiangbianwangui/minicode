from ._util import run_python


def _verify(wd):
    code = (
        "from fizzbuzz import fizzbuzz\n"
        "r = fizzbuzz(15)\n"
        "assert r[0] == '1'\n"
        "assert r[2] == 'Fizz'\n"
        "assert r[4] == 'Buzz'\n"
        "assert r[14] == 'FizzBuzz'\n"
        "print('OK')"
    )
    rc, out, err = run_python(wd, code=code)
    return rc == 0 and "OK" in out, f"rc={rc} out={out.strip()!r} err={err.strip()!r}"


TASK = {
    "id": "t04",
    "name": "FizzBuzz 实现",
    "prompt": "创建 fizzbuzz.py，定义函数 fizzbuzz(n) 返回 1..n 的列表：3 的倍数用 'Fizz'，5 的倍数用 'Buzz'，15 的倍数用 'FizzBuzz'，其余为数字字符串。",
    "setup": None,
    "verify": _verify,
}
