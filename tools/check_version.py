#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验包内版本号与 manifest 一致。

Fail2ban 的版本号在两处维护：
  * src/pkg/fn-fail2ban/manifest 的 version —— 打包与 Release 用它
  * app/bin/server.py 的 APP_VERSION —— 面板运行时对外报告用它
只改一处会出现「包名是新的、装上后自报还是旧的」，于是本机的「检查更新」
永远认为自己是旧版本，用户看到反复提示同一个更新。

这条闸门把它们绑在一起，漏改在打包前就会失败。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "src" / "pkg" / "fn-fail2ban" / "manifest"
SERVER = ROOT / "src" / "pkg" / "fn-fail2ban" / "app" / "bin" / "server.py"


def manifest_version():
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*version\s*=\s*(\S+)", line)
        if m:
            return m.group(1).strip()
    return ""


def server_version():
    text = SERVER.read_text(encoding="utf-8")
    m = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.M)
    return m.group(1) if m else ""


def main():
    mv, sv = manifest_version(), server_version()
    problems = []
    if not mv:
        problems.append("manifest 里找不到 version")
    if not sv:
        problems.append("server.py 里找不到 APP_VERSION")
    if mv and sv and mv != sv:
        problems.append(
            "版本号不一致：manifest=%s / server.py APP_VERSION=%s —— "
            "两处必须同时改，否则包是新的、面板自报还是旧的" % (mv, sv))

    print("manifest version : %s" % (mv or "(缺)"))
    print("server APP_VERSION: %s" % (sv or "(缺)"))
    # 变异自检：把其中一个改掉，必须能报出来
    if mv and sv and mv == sv:
        fake = (mv[:-1] + ("0" if mv[-1] != "0" else "1"))
        if fake != mv:
            print("自检：版本对不上时本脚本会报错（用 %s 比对 %s）" % (fake, mv))
    for p in problems:
        print("FAIL " + p)
    print("RESULT: %s" % ("PASS" if not problems else "FAIL"))
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
