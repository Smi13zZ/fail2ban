#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""manifest 的行尾闸门。

CRLF 混进来会让 CI 拼出错误的产物文件名：
  工作流里 `out="output/${app}-${ver}-${plat}.fpk"`
  其中 ${ver} 来自 awk，只 gsub 掉空格和制表符，\r 会留在变量里，
  于是 test -f 指向一个带 \r 的名字，打包明明成功却报文件不存在。

这只是其中一个后果。更根本的是：这个文件一直是 LF，
混进 CRLF 说明它被某个「按行读再按行写」的脚本动过，
那种脚本往往还会连带改动别的行 —— 值得停下来看一眼。
"""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = [
    ROOT / "src" / "pkg" / "fn-fail2ban" / "manifest",
    ROOT / "src" / "pkg" / "fn-fail2ban" / "wizard" / "install",
    ROOT / "src" / "pkg" / "fn-fail2ban" / "wizard" / "config",
]


def main():
    problems = []
    for path in FILES:
        if not path.is_file():
            continue
        raw = path.read_bytes()
        crlf = raw.count(b"\r\n")
        if crlf:
            problems.append("%s 里有 %d 处 CRLF（应为纯 LF）" % (
                path.relative_to(ROOT).as_posix(), crlf))
        # 单独一个 \r 同样危险（awk 一样会带进变量）
        lone = raw.count(b"\r") - crlf
        if lone:
            problems.append("%s 里有 %d 个孤立 \\r" % (
                path.relative_to(ROOT).as_posix(), lone))

    if problems:
        for item in problems:
            print("FAIL", item)
        print("")
        print("修法：把文件统一成 LF 后重新打包。")
        print("注意 CI 拼产物文件名时只去空格，\\r 会原样进文件名。")
        return 1

    print("OK   manifest 与向导文件都是纯 LF")
    return 0


if __name__ == "__main__":
    sys.exit(main())
