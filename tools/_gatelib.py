#!/usr/bin/env python3
"""各道闸门共用的路径、输出与统计工具。

所有闸门都按同一套格式打印，CI 日志和本地终端看起来一模一样：

    FAIL  <位置>: <问题>
    WARN  <位置>: <可疑但不确定>
    NOTE  <位置>: <只供人看一眼>
    RESULT: PASS | FAIL

路径统一相对仓库根、统一用正斜杠，从 Windows 终端里复制一条消息到 CI 日志里
比对时不会因为分隔符不同而看不出是同一条。
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 便携运行时里的 node 不在 PATH 上，但确实存在。写死一个绝对路径在别的机器上会失效，
# 所以按「环境变量 → PATH → 用户主目录下的 DSH 运行时」顺序找，最后才报告找不到。
NODE_FALLBACK = os.path.join(
    os.path.expanduser("~"), ".dsh", "dsh-runtimes",
    "dsh-next-primary-runtime", "dependencies", "node", "bin", "node.exe")

PKG_NAME = "fn-fail2ban"
PKG = os.path.join(ROOT, "src", "pkg", PKG_NAME)
APP = os.path.join(PKG, "app")
BIN = os.path.join(APP, "bin")
WEB = os.path.join(APP, "web")
UI = os.path.join(APP, "ui")
SRC = os.path.join(ROOT, "src")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git", "node_modules"}


def rel(path):
    """仓库内路径 -> 相对路径（正斜杠）；仓库外路径原样返回。"""
    try:
        return os.path.relpath(path, ROOT).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def setup_stdio():
    """把 stdout/stderr 固定成 UTF-8。

    这台机器的默认代码页是 GBK，而源码里的消息是中文（UTF-8）。不显式设置时，
    一次 print 就可能抛 UnicodeEncodeError；更糟的是管道被重定向到文件时它不报错、
    只是写进去一堆乱码，排查时看不出是编码问题。CI 上本来就是 UTF-8，多设一次无害。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def find_node():
    """按固定顺序找 node：DSH_NODE → PATH → DSH 便携运行时里的固定位置。

    找不到时返回空串，由调用方明确打印「跳过」。这道闸门不能静默失效：
    静默跳过在日志里和通过长得一模一样，而前端语法恰恰是最容易改坏的部分。
    """
    for candidate in (os.environ.get("DSH_NODE"), os.environ.get("NODE_BIN")):
        if candidate and os.path.isfile(candidate):
            return candidate
    found = shutil.which("node") or shutil.which("node.exe")
    if found:
        return found
    if os.path.isfile(NODE_FALLBACK):
        return NODE_FALLBACK
    return ""


SHIP_SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SHIP_SKIP_EXT = {".pyc", ".pyo", ".log", ".orig", ".rej"}


def ship_blockers(directory):
    """列出不该进安装包的文件。

    两条打包路径（build_fpk.py 经 SSH、make_package.py 在 Linux 本机）必须按同一套规则
    拒绝同一批文件，否则「本地能打」和「CI 能打」会悄悄不同。曾经把英文残留扫描的
    输出（_missed.json）打进过安装包，所以以 _ 开头的散落文件一律视为临时产物。

    返回空列表表示干净。
    """
    bad = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = [d for d in dirnames if d not in SHIP_SKIP_DIRS]
        for name in sorted(filenames):
            if name.startswith("_") or os.path.splitext(name)[1] in SHIP_SKIP_EXT:
                bad.append(os.path.join(dirpath, name))
    return bad


def read_text(path):
    """按 UTF-8 读文件，坏字节用替换字符顶上。

    闸门宁可给出带「」的乱码提示，也不要在读文件这步抛异常 —— 抛了就等于
    整道闸门没跑，而「没跑」在日志里和「通过」长得一样。
    """
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8", "replace")


def walk_files(directory, suffixes):
    """递归列出指定后缀的文件，跳过缓存目录。结果按路径排序，输出稳定。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(directory):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(tuple(suffixes)):
                out.append(os.path.join(dirpath, name))
    return out


class Report:
    """收集一条条结论，最后一次性打印并给出退出码。

    先收集再打印的原因：一道闸门可能要扫几十个文件，边扫边打会让「共发现 N 个问题」
    这类汇总行被埋在中途，CI 折叠日志后更难找。
    """

    def __init__(self, title):
        self.title = title
        self.failures = []
        self.warnings = []
        self.notes = []

    def fail(self, where, message):
        self.failures.append("%s: %s" % (where, message))
        # 立即回显：CI 日志被截断时，末尾的汇总行往往是丢掉的那几行
        print("FAIL  %s: %s" % (where, message))

    def warn(self, where, message):
        self.warnings.append("%s: %s" % (where, message))

    def note(self, where, message):
        self.notes.append("%s: %s" % (where, message))

    def ok(self, message):
        print("  ok  " + message)

    def finish(self, strict=False):
        print("")
        for item in self.warnings:
            print("WARN  " + item)
        for item in self.notes:
            print("NOTE  " + item)
        if self.failures:
            print("")
            print("%s: %d 个问题" % (self.title, len(self.failures)))
            print("RESULT: FAIL")
            return 1
        if strict and self.warnings:
            print("")
            print("RESULT: WARN (%d) -- strict 模式" % len(self.warnings))
            return 1
        suffix = "" if not self.warnings else "（%d 条警告）" % len(self.warnings)
        print("RESULT: PASS" + suffix)
        return 0
