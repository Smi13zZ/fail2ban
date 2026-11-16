#!/usr/bin/env python3
"""一键升级的后端验收（规格第三节）。打桩网络与安装器，真跑一遍。

跑法（项目根为工作目录）：

    python tools/test_update_flow.py

刻意不用 tempfile.mkdtemp()：Windows 沙箱下新建目录后续写入会 PermissionError，
所以目录固定落在工作区 tmp/_update_test 下，每次开跑先清干净。

这里不碰真实网络、不碰 appcenter-cli、不真的卸载任何东西：
  * 下载用本地起的 HTTP 服务（真的走 socket + urllib，因此 .part / 原子改名
    / Content-Length 校验都是真跑的）；
  * 安装脚本永远不执行（spawn 换成记录调用）。
"""

import http.server
import os
import shutil
import socket
import ssl
import sys
import threading
import time
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
BIN = os.path.join(PROJECT, "src", "pkg", "fn-fail2ban", "app", "bin")
WORK = os.path.join(PROJECT, "tmp", "_update_test")

# 路径必须在 import store 之前落定：模块导入时就把环境变量读成常量。
os.environ["TRIM_PKGVAR"] = os.path.join(WORK, "var")
os.environ["TRIM_PKGETC"] = os.path.join(WORK, "etc")
os.environ["TRIM_APPDEST"] = os.path.join(WORK, "target")
os.environ["TRIM_PKGHOME"] = os.path.join(WORK, "home")
os.environ.pop("F2B_MANAGED", None)

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import installer  # noqa: E402
import storage    # noqa: E402
import update     # noqa: E402

failures = []


def check(condition, label, detail=""):
    if condition:
        print("  PASS  %s" % label)
        return True
    print("  FAIL  %s  <- %s" % (label, detail))
    failures.append(label)
    return False


def reset_workdir():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    for name in ("var", "home", "etc", "target"):
        os.makedirs(os.path.join(WORK, name), exist_ok=True)
    installer.reset()


# ---------------------------------------------------------------------------
# 本地 HTTP 服务：真跑 socket，才能验到 .part 与原子改名
# ---------------------------------------------------------------------------

class _Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        mode = ROUTES.get(self.path, "ok")
        if mode == "404":
            self.send_error(404, "not found")
            return
        body = PAYLOAD
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        if mode == "lying":
            # 承诺比实际更多的字节：真实的「下载了一半」就是这样伪装成成功的
            self.send_header("Content-Length", str(len(body) + 4096))
        else:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if mode == "lying":
            self.close_connection = True

    def log_message(self, *_args):
        pass


ROUTES = {}
PAYLOAD = b""


def start_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, "http://127.0.0.1:%d" % server.server_address[1]


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_partial_download_leaves_no_package():
    print("[1] 中断下载不留假包")
    reset_workdir()
    server, base = start_server()
    try:
        globals()["ROUTES"] = {"/short.fpk": "lying"}
        globals()["PAYLOAD"] = b"FPKDATA" * 1000
        dest = os.path.join(WORK, "home", "update", "short.fpk")
        code = ""
        try:
            installer.download(base + "/short.fpk", dest, timeout=5)
        except installer.UpdateError as exc:
            code = exc.code
        check(not os.path.exists(dest), "被截断的下载没有落成成品",
              "成品文件仍然存在")
        check(not os.path.exists(dest + ".part"), ".part 被清掉了",
              "半截文件留在目录里")
        check(code == "netReset", "错误码是 netReset", "实际：%r" % code)
    finally:
        server.shutdown()


def test_download_uses_part_then_rename():
    print("[2] 正常下载走 .part → 原子改名")
    reset_workdir()
    server, base = start_server()
    try:
        globals()["ROUTES"] = {"/ok.fpk": "ok"}
        globals()["PAYLOAD"] = b"FPKDATA" * 500
        dest = os.path.join(WORK, "home", "update", "ok.fpk")
        seen = []
        total = installer.download(base + "/ok.fpk", dest, timeout=5,
                                   on_progress=lambda d, t: seen.append((d, t)))
        check(os.path.isfile(dest), "成品就位")
        check(total == len(PAYLOAD), "字节数等于载荷", "%d" % total)
        check(not os.path.exists(dest + ".part"), "没有残留 .part")
        check(len(seen) >= 2 and seen[-1][0] == total,
              "进度回调收到了中间值与最终总量", repr(seen[-1:]))
    finally:
        server.shutdown()


def test_http_errors_map_to_codes():
    print("[3] HTTP 失败码")
    reset_workdir()
    server, base = start_server()
    try:
        globals()["ROUTES"] = {"/missing.fpk": "404"}
        globals()["PAYLOAD"] = b"x"
        dest = os.path.join(WORK, "home", "update", "missing.fpk")
        code = ""
        try:
            installer.download(base + "/missing.fpk", dest, timeout=5)
        except installer.UpdateError as exc:
            code = exc.code
        check(code == "gh404", "404 → gh404", "实际：%r" % code)
    finally:
        server.shutdown()


def test_error_code_mapping():
    print("[4] 错误码映射")
    cases = [
        (socket.gaierror(1, "dns"), "netDns"),
        (urllib.error.HTTPError("u", 403, "f", {}, None), "gh403"),
        (socket.timeout(), "netTimeout"),
        (ssl.SSLError("bad"), "netTls"),
        (ConnectionRefusedError(111, "no"), "netRefused"),
        (ConnectionResetError(104, "rst"), "netReset"),
        (urllib.error.URLError(socket.gaierror(1, "dns")), "netDns"),
    ]
    for exc, want in cases:
        got = installer.error_code(exc)
        check(got == want, "%s → %s" % (type(exc).__name__, want), "实际：%r" % got)
    check(update.net_code(socket.gaierror(1, "dns")) == "netDns",
          "update.net_code 也认 gaierror")
    reason, _msg, code = update._describe(urllib.error.HTTPError("u", 403, "f", {}, None))
    check(code == "gh403", "版本检查的 403 → gh403", "实际：%r" % code)
    check(reason == "rate_limited", "403 仍按 rate_limited 降级", reason)


def test_rollback_package_found_in_package_dir():
    print("[5] 回滚包能在包目录里被找到")
    reset_workdir()
    work = installer.package_dir()
    os.makedirs(work, exist_ok=True)
    old = os.path.join(work, "fn-fail2ban-26.11.59-all.fpk")
    open(old, "wb").write(b"x")
    os.utime(old, (time.time() - 5000, time.time() - 5000))
    newer = os.path.join(work, "fn-fail2ban-26.11.59-all-fix.fpk")
    open(newer, "wb").write(b"y")
    os.utime(newer, (time.time() - 100, time.time() - 100))
    pending = os.path.join(work, "fn-fail2ban-26.11.60-all.fpk")
    open(pending, "wb").write(b"z")

    found = installer.find_previous_fpk("26.11.59", exclude=[pending])
    check(found == newer, "取同前缀里最新的一份", "实际：%s" % os.path.basename(found))
    older = installer.find_previous_fpk("26.11.59", exclude=[pending, newer])
    check(older == old, "排掉最新的之后退到更早的一份",
          "实际：%s" % (os.path.basename(older) if older else "空"))
    check(installer.find_previous_fpk("26.11.99") == "", "没有对应版本时返回空串")
    check(installer.rollback_available("26.11.59") is True, "rollback_available 为真")
    check(installer.rollback_available("26.11.99") is False, "没有副本时为假")
    check(installer.rollback_available("") is None, "版本未知时为 None")


def test_storage_total_excludes_nested():
    print("[6] 存储合计 = 非嵌套行之和")
    reset_workdir()
    var = os.environ["TRIM_PKGVAR"]
    updir = os.path.join(var, "update")
    os.makedirs(updir, exist_ok=True)
    open(os.path.join(updir, "install.log"), "w").write("x" * 120)
    open(os.path.join(updir, "leftover.fpk"), "w").write("y" * 300)
    report = storage.report(var, current_version="26.11.60")
    total = sum(int(item["bytes"]) for item in report["items"] if not item["nested"])
    check(total == int(report["total"]), "合计等于非嵌套行之和",
          "逐行 %d vs report %d" % (total, report["total"]))
    log_row = [i for i in report["items"] if i["key"] == "install_log"][0]
    dir_row = [i for i in report["items"] if i["key"] == "update_dir"][0]
    inside = os.path.realpath(log_row["path"]).startswith(
        os.path.realpath(dir_row["path"]) + os.sep)
    check(bool(log_row["nested"]) == inside,
          "install_log 的 nested 与真实包含关系一致",
          "nested=%r 实际在目录内=%r" % (log_row["nested"], inside))


def test_attempt_increments():
    print("[7] 两次尝试的 attempt 递增")
    reset_workdir()
    first = installer.begin_attempt()
    check(first == 1, "第一次尝试编号为 1", "实际：%r" % first)
    check(installer.progress()["phase"] == "checking",
          "认领后阶段是 checking（界面显示「检查中…」）")
    installer.fail_attempt("netDns", "域名解析失败")
    after_fail = installer.progress()
    check(after_fail["phase"] == "failed" and after_fail["code"] == "netDns",
          "失败后同时给出阶段与错误码")
    second = installer.begin_attempt()
    check(second == 2, "第二次尝试编号为 2", "实际：%r" % second)
    check(installer.progress()["code"] == "",
          "新尝试清掉了上一轮的错误码")
    check(installer.progress()["attempt"] == 2, "progress 下发本轮编号")


def test_busy_while_checking():
    print("[8] 「检查中」再点一次返回 busy")
    reset_workdir()
    no = installer.begin_attempt()
    check(no == 1, "第一次认领成功")
    again = installer.begin_attempt()
    check(again == 0, "checking 期间第二次认领被拒", "实际：%r" % again)
    check(installer.busy() is True, "busy() 覆盖 checking")
    result = installer.install("26.11.60", "http://127.0.0.1:1/x.fpk",
                               os.environ["TRIM_PKGVAR"], "26.11.59")
    check(result.get("ok") is False and result.get("code") == "busy",
          "install() 在检查中也拒绝", repr(result))
    installer.fail_attempt("busy", "")
    check(installer.busy() is False, "失败后不再 busy")


def test_failure_then_new_attempt_not_stale():
    print("[9] 新一轮不被上一轮的死信打断")
    reset_workdir()
    installer.begin_attempt()
    installer.fail_attempt("gh403", "GitHub 接口限流")
    stale = installer.progress()
    check(stale["phase"] == "failed" and stale["attempt"] == 1,
          "服务端留着上一轮的失败（终态不复位）")
    second = installer.begin_attempt()
    check(second == 2 and installer.progress()["phase"] == "checking",
          "新一轮立刻接管，不经过 failed")
    check(stale["attempt"] < second, "旧快照的编号严格小于本轮基线")


def test_validate_fpk_codes():
    print("[10] 校验失败带码")
    reset_workdir()
    path = os.path.join(WORK, "bad.fpk")
    open(path, "wb").write(b"not a tar")
    code = ""
    try:
        installer.validate_fpk(path, appcenter="/bin/true")
    except installer.UpdateError as exc:
        code = exc.code
    check(code == "pkgUnreadable", "坏包 → pkgUnreadable", "实际：%r" % code)
    empty = os.path.join(WORK, "empty.fpk")
    open(empty, "wb").close()
    code = ""
    try:
        installer.validate_fpk(empty, appcenter="/bin/true")
    except installer.UpdateError as exc:
        code = exc.code
    check(code == "pkgMissing", "空包 → pkgMissing", "实际：%r" % code)


def main():
    for name in ("TRIM_PKGVAR", "TRIM_PKGETC", "TRIM_PKGDEST"):
        pass
    tests = [
        test_partial_download_leaves_no_package,
        test_download_uses_part_then_rename,
        test_http_errors_map_to_codes,
        test_error_code_mapping,
        test_rollback_package_found_in_package_dir,
        test_storage_total_excludes_nested,
        test_attempt_increments,
        test_busy_while_checking,
        test_failure_then_new_attempt_not_stale,
        test_validate_fpk_codes,
    ]
    for fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 用例崩了要算失败，不能中断后面的
            print("  FAIL  %s 抛出 %s: %s" % (fn.__name__, type(exc).__name__, exc))
            failures.append(fn.__name__)
    print("")
    if failures:
        print("%d 项失败" % len(failures))
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS（%d 组用例）" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
