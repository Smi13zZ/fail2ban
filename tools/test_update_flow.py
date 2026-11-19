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


def test_outcome_detects_success_without_version_change():
    """升级成功但版本号没变时，收尾判据不能只靠版本号。

    真实事故：26.11.62 的包 manifest 是 26.11.62，但 app/bin/server.py 的
    APP_VERSION 漏改仍是 26.11.61。从 26.11.61 升到 26.11.62 时，新面板装好、
    起来了，却仍然自报 26.11.61 —— 界面靠「版本号变了」判断收尾，于是永远等不到，
    遮罩挂到 10 分钟超时。用户看到的就是「更新完卡住、不能自动刷新」。

    修法：让安装脚本的日志成为独立证据（"=== 已升级到 X ==="），后端把它
    识别成 outcome=succeeded，前端据此收尾，不再依赖版本号比较。
    """
    print("[11] 升级成功但版本号没变也能收尾")
    # 后端把成功那句话认成 succeeded
    tail_ok = "=== 已升级到 26.11.62 ===\n"
    check(installer._outcome(tail_ok) == "succeeded",
          "脚本报「已升级到」→ outcome=succeeded", "实际：%r" % installer._outcome(tail_ok))
    # 同理覆盖「由 cmd/main 启动」那条分支
    tail_ok2 = "=== 已升级到 26.11.62（由 cmd/main 启动） ===\n"
    check(installer._outcome(tail_ok2) == "succeeded",
          "cmd/main 启动分支也算成功", "实际：%r" % installer._outcome(tail_ok2))
    # 回滚与无包仍各自归类，不能被成功判据吞掉
    check(installer._outcome("=== 已尝试回滚，当前版本：26.11.61 ===") == "rolledBack",
          "回滚仍识别为 rolledBack")
    check(installer._outcome("=== 没有可用的上一个安装包，无法回滚 ===") == "noRollback",
          "无包仍识别为 noRollback")
    check(installer._outcome("") == "", "空日志 → 无结论")
    check(installer._outcome("=== start 中 ===\n") == "", "只有无关日志 → 无结论")

    # 前端必须在版本号比较之外，单独认这个 outcome。
    # 断言要锚定到具体那一段代码 —— 全文搜 "succeeded" 会被超时分支里的
    # 同名字样满足，把主判据删掉也照样 PASS（变异测试验证过这个坑）。
    app_path = os.path.join(os.path.dirname(BIN), "web", "app.js")
    with open(app_path, encoding="utf-8") as fh:
        app = fh.read()
    # 主判据：pollUpdateProgress 里在版本比较之前，按 outcome 收尾
    i_poll = app.find("function pollUpdateProgress")
    i_ver = app.find("state.update.before && prog && prog.current", i_poll)
    seg_main = app[i_poll:i_ver] if (i_poll >= 0 and i_ver > i_poll) else ""
    check(bool(seg_main) and "succeeded" in seg_main,
          "前端在版本比较之前按 outcome 收尾")
    check("outcomeNow" in seg_main,
          "主判据取的是本轮 outcome 变量")
    # 超时分支：最后一刻脚本报成功时不能报成超时
    i_to = app.find('kind === "timeout"')
    seg_to = app[i_to: i_to + 3000] if i_to >= 0 else ""
    check(bool(seg_to) and "succeeded" in seg_to,
          "超时分支里也处理 succeeded，不误报超时")
    check("=== 已升级到" in installer._SUCCEEDED,
          "后端成功标记与安装脚本输出一致")

    # 重载后不能「因为版本号没变又开一轮轮询」，否则会自我刷新成死循环
    i_resume = app.find("function resumePendingUpgrade")
    # 用「下一个顶层函数注释或函数定义」当边界：函数体以右花括号结束，
    # 直接找下一个 "function " 会把整段切空（相邻函数之间有注释块）。
    i_after = -1
    if i_resume >= 0:
        for marker in ("\n  /* ---", "\n  function "):
            k = app.find(marker, i_resume + 40)
            if k >= 0 and (i_after < 0 or k < i_after):
                i_after = k
    seg_resume = app[i_resume:i_after] if (i_resume >= 0 and i_after > i_resume) else app[i_resume:i_resume + 2500]
    check(bool(seg_resume), "找到 resumePendingUpgrade")
    check("outcome" in seg_resume,
          "重载恢复时先看服务端 outcome，不只看版本号")
    # outcome 判据必须出现在版本号比较之前
    i_out = seg_resume.find("outcome")
    i_ver2 = seg_resume.find("now !== before")
    check(i_out >= 0 and i_ver2 >= 0 and i_out < i_ver2,
          "outcome 判据排在版本号比较之前（否则会反复自我刷新）")


def test_stale_log_does_not_finish_new_round():
    """新一轮刚开始时，不能继承上一轮日志里的「已升级到 X」。

    真实事故（2026-11-19，生产机 26.11.65 → 26.11.66）：安装脚本只追加日志，
    上一轮留下的 "=== 已升级到 26.11.65 ===" 还在尾部。点了「一键更新」之后，
    下载线程刚起来，进度接口就把这句旧话当成这一轮的结论发给界面 —— 界面当场
    收尾、1.5 秒后刷新，而这时脚本才刚要停服务，刷新撞在面板离线的窗口上，
    页面停在遮罩里再也刷不出来。修法两条：开工时清空日志；本轮没跑完（仍在
    checking/downloading/installing）时不下发 outcome。
    """
    print("[12] 新一轮不继承上一轮的成功结论")
    var = os.environ.get("TRIM_PKGVAR")
    updir = installer._update_dir(var)
    check(bool(updir), "能定位更新日志目录")
    os.makedirs(updir, exist_ok=True)
    log = os.path.join(updir, "install.log")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("=== 已升级到 26.11.65 ===\n")

    installer.begin_attempt()
    check(installer.progress()["outcome"] == "",
          "checking 阶段不把上一轮的日志当结论",
          "实际：%r" % installer.progress().get("outcome"))
    installer._set(installer.PHASE_DOWNLOADING, "26.11.66", "", done=0, total=0)
    check(installer.progress()["outcome"] == "",
          "downloading 阶段不发 outcome",
          "实际：%r" % installer.progress().get("outcome"))
    installer._set(installer.PHASE_INSTALLING, "26.11.66", "")
    check(installer.progress()["outcome"] == "",
          "installing 阶段不发 outcome",
          "实际：%r" % installer.progress().get("outcome"))

    installer._reset_install_log(var)
    with open(log, "rb") as fh:
        check(fh.read() == b"", "开工时安装日志被清空")

    installer._set(installer.PHASE_IDLE, "", "")
    check(installer.progress()["outcome"] == "", "idle + 空日志 → 无结论")
    with open(log, "w", encoding="utf-8") as fh:
        fh.write("=== 已升级到 26.11.66 ===\n")
    check(installer.progress()["outcome"] == "succeeded",
          "跑完后仍按本轮日志认成功",
          "实际：%r" % installer.progress().get("outcome"))


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
        test_outcome_detects_success_without_version_change,
        test_stale_log_does_not_finish_new_round,
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
