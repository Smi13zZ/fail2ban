#!/usr/bin/env python3
"""一条命令跑齐全部本地闸门，顺序与 CI 完全一致。

「本地过了」和「CI 过了」要是同一件事，两边就必须跑同一串命令、同一个顺序。
所以 CI 的每一步都从这里能跑到的闸门里挑，反过来新加的闸门也要在这里补一行。

    python tools/preflight.py            # 全跑
    python tools/preflight.py --list     # 只列出会跑哪些闸门
    python tools/preflight.py --quick    # 跳过需要 node 的步骤

退出码为失败的闸门数（0 = 全绿）。
"""
import argparse
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

PY = sys.executable
TOOLS = os.path.join(G.ROOT, "tools")


def run(argv, cwd=None):
    """跑子进程，输出固定按 UTF-8 解码。

    text=True 用的是系统区域编码：Windows 上那是 GBK，而闸门输出是 UTF-8 中文，
    读取线程会死在 UnicodeDecodeError 上，子进程的 stderr 于是整段丢失 ——
    失败的闸门反而什么原因都不显示。
    """
    if not os.path.isfile(argv[0]) and not shutil.which(argv[0]):
        return None, "", "找不到可执行文件：%s" % argv[0], -1
    try:
        proc = subprocess.run(argv, cwd=cwd or G.ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        return None, "", "超时（600 秒）", -1
    return proc, proc.stdout or "", proc.stderr or "", proc.returncode


class Gate:
    def __init__(self, title, argv):
        self.title = title
        self.argv = argv
        self.failed = False
        self.skipped = False
        self.detail = ""


def build_gates(node, quick):
    gates = [
        # 先跑结构检查的自测：闸门本身如果被改坏了，后面的全绿没有意义
        Gate("结构检查自测（变异样本）",
             [PY, os.path.join(TOOLS, "check_structure.py"), "--self-test"]),
        Gate("结构检查（缩进跑偏 / 未定义名字 / 死读属性）",
             [PY, os.path.join(TOOLS, "check_structure.py")]),
        Gate("Python 编译", [PY, "-m", "compileall", "-q", G.BIN]),
        Gate("i18n 自测（变异样本）",
             [PY, os.path.join(TOOLS, "check_i18n.py"), "--self-test"]),
        Gate("i18n 字典一致性", [PY, os.path.join(TOOLS, "check_i18n.py")]),
        Gate("CSS 与静态资源", [PY, os.path.join(TOOLS, "check_css.py")]),
        Gate("文档链接与图片", [PY, os.path.join(TOOLS, "check_docs.py")]),
    ]
    if quick:
        return gates
    gates.insert(4, Gate("前端 JS 语法与反查表", [PY, os.path.join(TOOLS, "check_web.py")]))
    return gates


def main(argv=None):
    G.setup_stdio()
    parser = argparse.ArgumentParser(description="跑齐本地闸门")
    parser.add_argument("--list", action="store_true", help="只列出闸门")
    parser.add_argument("--quick", action="store_true", help="跳过需要 node 的步骤")
    args = parser.parse_args(argv)

    node = "" if args.quick else G.find_node()
    if node:
        os.environ["DSH_NODE"] = node
    gates = build_gates(node, args.quick)

    if args.list:
        for gate in gates:
            print("  " + gate.title)
        return 0

    print("Python：%s" % sys.version.split()[0])
    print("node  ：%s" % (node or "未找到（前端语法检查会因此失败，不算通过）"))
    print("")

    failures = []
    for gate in gates:
        print("== %s" % gate.title)
        proc, out, err, code = run(gate.argv)
        lines = [line for line in out.strip().splitlines() if line.strip()]
        if code == 0:
            # 通过也要把警告打出来：一条被悄悄吞掉的警告，下一次就会变成故障。
            for line in lines:
                if line.startswith("WARN"):
                    print("   " + line)
            tail = [line for line in lines if line.startswith("RESULT:")] or lines[-1:]
            for line in tail:
                print("   " + line)
            print("   ok")
            continue
        gate.failed = True
        failures.append(gate.title)
        print("   FAILED（退出码 %s）" % code)
        # 把子进程自己的结论行优先打出来，比一屏 traceback 有用得多
        important = [line for line in lines
                     if line.startswith(("FAIL", "RESULT", "WARN"))] or lines[-8:]
        for line in important[:20]:
            print("   " + line)
        for line in err.strip().splitlines()[-8:]:
            print("   ! " + line)

    print("")
    if failures:
        print("失败的闸门：")
        for title in failures:
            print("  - " + title)
        print("RESULT: FAIL")
        return 1
    print("全部 %d 道闸门通过" % len(gates))
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())