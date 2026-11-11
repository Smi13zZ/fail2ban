#!/usr/bin/env python3
"""备份恢复（POST /api/backup/restore）的回归测试。

跑法（项目根为工作目录）：

    python tools/test_backup_restore.py

刻意不用 tempfile.mkdtemp()：Windows 沙箱下新建目录后续写入会 PermissionError，
所以目录固定落在工作区 tmp/_restore_test 下，每次开跑先清干净。
"""

import io
import json
import os
import shutil
import sys
import tarfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.dirname(HERE)
BIN = os.path.join(PROJECT, "src", "pkg", "fn-fail2ban", "app", "bin")
WORK = os.path.join(PROJECT, "tmp", "_restore_test")

# 必须在 import store / jailconf / backup 之前落定路径：
# 这几个模块在导入时就把环境变量读成模块级常量。
os.environ["TRIM_PKGVAR"] = os.path.join(WORK, "var")
os.environ["TRIM_PKGETC"] = os.path.join(WORK, "etc")
os.environ["TRIM_APPDEST"] = os.path.join(WORK, "target")
os.environ["F2B_SYS_ETC"] = os.path.join(WORK, "system-fail2ban")
os.environ.pop("F2B_MANAGED", None)

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import backup          # noqa: E402
import f2b             # noqa: E402
import jailconf        # noqa: E402
import notify          # noqa: E402
import server          # noqa: E402
import store           # noqa: E402

failures = []


def check(condition, label, detail=""):
    if condition:
        print(f"  PASS  {label}")
        return True
    print(f"  FAIL  {label}" + (f"  <- {detail}" if detail else ""))
    failures.append(label)
    return False


def expect_value_error(fn, message, label):
    try:
        fn()
    except ValueError as exc:
        check(str(exc) == message, label, f"实际信息：{exc!r}")
        return
    except Exception as exc:  # noqa: BLE001 - 报错类型不对也要算失败
        check(False, label, f"抛出了 {type(exc).__name__}: {exc}")
        return
    check(False, label, "没有抛出异常")


def reset_workdir():
    if os.path.isdir(WORK):
        shutil.rmtree(WORK, ignore_errors=True)
    for path in (store.VAR_DIR, store.ETC_DIR, store.APP_DIR,
                 jailconf.JAIL_D, jailconf.FILTER_D, store.BACKUP_DIR):
        os.makedirs(path, exist_ok=True)


def write_text(path, text):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def add_text(tar, name, text, kind="file", linkname=""):
    info = tarfile.TarInfo(name)
    info.mtime = int(time.time())
    if kind == "dir":
        info.type = tarfile.DIRTYPE
        tar.addfile(info)
        return
    if kind == "sym":
        info.type = tarfile.SYMTYPE
        info.linkname = linkname or "/etc/passwd"
        tar.addfile(info)
        return
    data = text.encode("utf-8")
    info.size = len(data)
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def archive_path(name):
    os.makedirs(store.VAR_DIR, exist_ok=True)
    return os.path.join(backup.resolve_directory(), name)


def build_archive(name, members, manifest=None):
    path = archive_path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "manifest.json": json.dumps(manifest if manifest is not None else {
            "app": "fn-fail2ban",
            "appVersion": "26.11.20",
            "createdAt": int(time.time()),
        }, ensure_ascii=False, indent=2) + "\n",
    }
    payload.update(members)
    with tarfile.open(path, "w:gz") as tar:
        for arcname, text in payload.items():
            add_text(tar, arcname, text)
    return name


def seed_current_state():
    """先把「当前配置」写成与归档不同的值，恢复后才看得出真的被覆盖。"""
    write_text(jailconf.MANAGED_FILE, "[sshd]\nenabled = false\n")
    write_text(os.path.join(jailconf.FILTER_D, "demo.local"), "current-filter\n")
    store.save_prefs({"theme": "dark", "lang": "zh", "logLines": 120})
    store.atomic_write_json(notify.SETTINGS_FILE, {
        "enabled": False, "channels": [],
    })


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------

def test_positive_restore():
    print("[1] 正向恢复：托管配置 / 自定义过滤器 / 偏好 / 通知")
    seed_current_state()
    jail_text = "[sshd]\nenabled = true\nmaxretry = 3\n"
    filter_text = "[Definition]\nfailregex = ^boom <HOST>$\n"
    prefs = {"theme": "light", "lang": "en", "logLines": 800, "density": "compact"}
    notifications = {"enabled": True, "pollInterval": 60,
                     "channels": [{"id": "abc123", "type": "generic", "name": "sink",
                                   "url": "http://127.0.0.1:1/hook"}]}
    name = build_archive("fn-fail2ban-manual-20260101-000000.tar.gz", {
        "jail.d/" + os.path.basename(jailconf.MANAGED_FILE): jail_text,
        "filter.d/demo.local": filter_text,
        "prefs.json": json.dumps(prefs, ensure_ascii=False, indent=2),
        "notifications.json": json.dumps(notifications, ensure_ascii=False, indent=2),
        "README.txt": "不在白名单里，恢复时应当被忽略\n",
    })

    result = backup.restore_backup(name)
    check(result.get("ok") is True, "返回 ok=true")
    check(result.get("name") == name, "返回归档名", str(result.get("name")))
    restored = result.get("restored") or {}
    check(restored.get("jail") is True, "restored.jail = true", repr(restored))
    check(restored.get("filters") == 1, "restored.filters = 1", repr(restored))
    check(restored.get("prefs") is True, "restored.prefs = true", repr(restored))
    check(restored.get("notifications") is True, "restored.notifications = true", repr(restored))
    check("reload" in result, "返回里带 reload 字段", repr(list(result)))

    check(read_text(jailconf.MANAGED_FILE) == jail_text, "托管配置回到归档内容")
    check(read_text(os.path.join(jailconf.FILTER_D, "demo.local")) == filter_text,
          "filter.d/demo.local 回到归档内容")
    check("demo" in store.read_custom_filters(), "过滤器记进自定义清单",
          repr(store.read_custom_filters()))
    current = store.load_prefs()
    check(current.get("theme") == "light" and current.get("lang") == "en"
          and current.get("logLines") == 800, "偏好回到归档内容", repr(current))
    saved = store.read_json(notify.SETTINGS_FILE) or {}
    check(saved.get("enabled") is True and len(saved.get("channels") or []) == 1,
          "通知配置回到归档内容", repr(saved))

    backups = [item["name"] for item in backup.list_backups()]
    check(name in backups, "list_backups 能看到这份归档", repr(backups))

    # 覆盖前留底：库目录里应当多出一份包含旧内容的 .bak
    leftovers = []
    for entry in sorted(os.listdir(store.BACKUP_DIR)):
        if entry.endswith(".bak"):
            try:
                leftovers.append(read_text(os.path.join(store.BACKUP_DIR, entry)))
            except OSError:
                pass
    check(any("enabled = false" in text for text in leftovers),
          "覆盖前把旧托管配置留了底", repr(leftovers))

    if not result.get("reload", {}).get("ok"):
        # 测试机上通常没装 fail2ban：允许失败，但不能把恢复本身判成失败
        check(result.get("reload", {}).get("error") is not None,
              "reload 失败时带上了原因", repr(result.get("reload")))


def test_foreign_manifest():
    print("[2] manifest.app 不是本应用 -> 拒绝")
    seed_current_state()
    name = build_archive("fn-fail2ban-manual-20260102-000000.tar.gz",
                         {"jail.d/x.local": "[sshd]\n"},
                         manifest={"app": "some-other-app"})
    expect_value_error(lambda: backup.restore_backup(name),
                       "不是本应用的备份文件", "提示「不是本应用的备份文件」")
    check(read_text(jailconf.MANAGED_FILE) == "[sshd]\nenabled = false\n",
          "校验失败时没有改掉现有配置")


def test_missing_manifest():
    print("[3] 缺 manifest.json -> 按「不是本应用的备份文件」处理")
    seed_current_state()
    name = "fn-fail2ban-manual-20260103-000000.tar.gz"
    with tarfile.open(archive_path(name), "w:gz") as tar:
        add_text(tar, "prefs.json", json.dumps({"theme": "light"}))
    expect_value_error(lambda: backup.restore_backup(name),
                       "不是本应用的备份文件", "提示「不是本应用的备份文件」")


def test_path_traversal():
    print("[4] 成员路径含 .. -> 判定归档损坏")
    seed_current_state()
    name = build_archive("fn-fail2ban-manual-20260104-000000.tar.gz", {})
    # 往合法归档里追加一条越界成员：路径校验排在白名单之前，依然要拒绝
    with tarfile.open(archive_path(name), "r:gz") as src:
        saved = [(m.name, src.extractfile(m).read()) for m in src.getmembers() if m.isfile()]
    with tarfile.open(archive_path(name), "w:gz") as tar:
        for member_name, data in saved:
            add_text(tar, member_name, data.decode("utf-8"))
        add_text(tar, "../evil.txt", "escape\n")
    expect_value_error(lambda: backup.restore_backup(name),
                       "备份文件不完整或已损坏", "提示「备份文件不完整或已损坏」")

    absolute = build_archive("fn-fail2ban-manual-20260104-000001.tar.gz", {})
    with tarfile.open(archive_path(absolute), "r:gz") as src:
        saved = [(m.name, src.extractfile(m).read()) for m in src.getmembers() if m.isfile()]
    with tarfile.open(archive_path(absolute), "w:gz") as tar:
        for member_name, data in saved:
            add_text(tar, member_name, data.decode("utf-8"))
        add_text(tar, "/etc/passwd", "root::0:0\n")
    expect_value_error(lambda: backup.restore_backup(absolute),
                       "备份文件不完整或已损坏", "绝对路径成员同样拒绝")


def test_missing_archive():
    print("[5] 归档不存在 -> find_backup 返回 None，Handler 回 404")
    check(backup.find_backup("fn-fail2ban-manual-29990101-000000.tar.gz") is None,
          "find_backup 返回 None")
    expect_value_error(
        lambda: backup.restore_backup("fn-fail2ban-manual-29990101-000000.tar.gz"),
        "找不到该备份文件", "restore_backup 抛「找不到该备份文件」")
    for bad in ("../escape.tar.gz", "sub/dir.tar.gz", "other-manual-20260101-000000.tar.gz"):
        check(backup.find_backup(bad) is None, f"find_backup 拒绝 {bad!r}")

    # handler 层把「找不到」映射成 404，其它 ValueError 是 400
    try:
        server.h_backup_restore(None, {}, {"name": "fn-fail2ban-manual-29990101-000000.tar.gz"})
    except server.ApiError as exc:
        check(exc.status == 404, "h_backup_restore 回 404", f"status={exc.status}")
    except Exception as exc:  # noqa: BLE001
        check(False, "h_backup_restore 应抛 ApiError", f"{type(exc).__name__}: {exc}")
    else:
        check(False, "h_backup_restore 应抛 ApiError", "没有抛异常")


def test_handler_maps_400():
    print("[6] Handler：坏归档 -> 400，成功 -> 200 且带 backups")
    foreign = build_archive("fn-fail2ban-manual-20260105-000000.tar.gz", {},
                            manifest={"app": "nope"})
    try:
        server.h_backup_restore(None, {}, {"name": foreign})
    except server.ApiError as exc:
        check(exc.status == 400 and exc.message == "不是本应用的备份文件",
              "坏归档回 400 + 中文提示", f"status={exc.status} msg={exc.message!r}")
    except Exception as exc:  # noqa: BLE001
        check(False, "应抛 ApiError", f"{type(exc).__name__}: {exc}")

    seed_current_state()
    good = build_archive("fn-fail2ban-manual-20260106-000000.tar.gz", {
        "prefs.json": json.dumps({"theme": "light", "lang": "en"}),
    })
    payload = server.h_backup_restore(None, {}, {"name": good})
    check(payload.get("ok") is True, "成功响应 ok=true")
    check(isinstance(payload.get("backups"), list), "响应里带 backups 列表",
          repr(type(payload.get("backups"))))


def test_bad_notifications_skipped():
    print("[7] notifications.json 结构不对 -> 跳过并把 notifications 记 false")
    seed_current_state()
    before = store.read_json(notify.SETTINGS_FILE) or {}
    name = build_archive("fn-fail2ban-manual-20260107-000000.tar.gz", {
        "notifications.json": json.dumps({"enabled": True, "channels": "not-a-list"}),
        "prefs.json": json.dumps({"theme": "light"}),
    })
    result = backup.restore_backup(name)
    check(result["restored"]["notifications"] is False,
          "restored.notifications = false", repr(result["restored"]))
    check(result["restored"]["prefs"] is True, "其余部分照常恢复", repr(result["restored"]))
    after = store.read_json(notify.SETTINGS_FILE) or {}
    check(after.get("channels") == before.get("channels"),
          "通知配置没有被写坏", repr(after))


def test_broken_json():
    print("[8] prefs.json 不是合法 JSON -> 判定归档损坏")
    seed_current_state()
    name = build_archive("fn-fail2ban-manual-20260108-000000.tar.gz", {
        "prefs.json": "{ this is not json",
    })
    expect_value_error(lambda: backup.restore_backup(name),
                       "备份文件不完整或已损坏", "提示「备份文件不完整或已损坏」")


def test_reload_failure_recorded():
    print("[9] fail2ban 重载失败 -> 只记录在 reload，不让恢复失败")
    seed_current_state()
    name = build_archive("fn-fail2ban-manual-20260109-000000.tar.gz", {
        "prefs.json": json.dumps({"theme": "light"}),
    })
    original = f2b.reload

    def boom():
        raise RuntimeError("fail2ban 没有响应")

    f2b.reload = boom
    try:
        result = backup.restore_backup(name)
    finally:
        f2b.reload = original
    check(result.get("ok") is True, "恢复仍然算成功", repr(result))
    reload_state = result.get("reload") or {}
    check(reload_state.get("ok") is False, "reload.ok = false", repr(reload_state))
    check(reload_state.get("error") == "fail2ban 没有响应", "reload.error 是异常信息",
          repr(reload_state))


def test_roundtrip_with_create_backup():
    print("[10] create_backup -> restore 回环")
    seed_current_state()
    jailconf.ensure_dirs()
    write_text(jailconf.MANAGED_FILE, "[sshd]\nenabled = true\nbantime = 600\n")
    write_text(os.path.join(jailconf.FILTER_D, "roundtrip.local"), "[Definition]\nfailregex = ^x <HOST>$\n")
    store.record_custom_filter("roundtrip", True)
    created = backup.create_backup("manual")
    check(bool(created.get("name")), "create_backup 返回归档名", repr(created.get("name")))
    # 打乱当前配置，再从自己生成的归档恢复
    write_text(jailconf.MANAGED_FILE, "[sshd]\nenabled = false\n")
    write_text(os.path.join(jailconf.FILTER_D, "roundtrip.local"), "broken\n")
    result = backup.restore_backup(created["name"])
    check(result["restored"]["jail"] is True, "回环恢复 jail", repr(result["restored"]))
    check(result["restored"]["filters"] >= 1, "回环恢复过滤器", repr(result["restored"]))
    check(read_text(jailconf.MANAGED_FILE) == "[sshd]\nenabled = true\nbantime = 600\n",
          "回环后托管配置与备份时一致", read_text(jailconf.MANAGED_FILE))
    check(read_text(os.path.join(jailconf.FILTER_D, "roundtrip.local")).startswith("[Definition]"),
          "回环后过滤器内容与备份时一致")


def test_garbage_archive():
    print("[11] 根本不是 tar.gz -> 判定归档损坏")
    seed_current_state()
    name = "fn-fail2ban-manual-20260201-000000.tar.gz"
    with open(archive_path(name), "wb") as handle:
        handle.write(b"this is not a tar archive at all")
    expect_value_error(lambda: backup.restore_backup(name),
                       "备份文件不完整或已损坏", "非 tar 内容 -> 损坏")
    check(read_text(jailconf.MANAGED_FILE) == "[sshd]\nenabled = false\n",
          "失败后现有配置没被改")

    truncated = "fn-fail2ban-manual-20260201-000001.tar.gz"
    good = build_archive(truncated, {"prefs.json": json.dumps({"theme": "light"})})
    raw = open(archive_path(good), "rb").read()
    with open(archive_path(truncated), "wb") as handle:
        handle.write(raw[: len(raw) // 2])
    expect_value_error(lambda: backup.restore_backup(truncated),
                       "备份文件不完整或已损坏", "截断的 gz -> 损坏")


def test_dir_and_symlink_members():
    print("[12] 目录 / 符号链接成员被忽略，只写白名单里的普通文件")
    seed_current_state()
    name = "fn-fail2ban-manual-20260202-000000.tar.gz"
    with tarfile.open(archive_path(name), "w:gz") as tar:
        add_text(tar, "manifest.json", json.dumps({"app": "fn-fail2ban"}) + "\n")
        add_text(tar, "jail.d", "", kind="dir")
        add_text(tar, "evil-link", "", kind="sym", linkname="/etc/passwd")
        add_text(tar, "jail.d/50-fn-fail2ban.local", "[sshd]\nenabled = true\n")
        add_text(tar, "filter.d/Bad Name.local", "[Definition]\n")   # 名字不合法
        add_text(tar, "filter.d/ok.local", "[Definition]\nfailregex = ^x <HOST>$\n")
        # 正常用 tar 打包常见根目录条目与 "./" 前缀，不能被当成越界而整份判坏
        add_text(tar, ".", "", kind="dir")
        add_text(tar, "./prefs.json", json.dumps({"theme": "light"}))

    result = backup.restore_backup(name)
    check(result["restored"]["jail"] is True, "普通文件兜底恢复 jail", repr(result["restored"]))
    check(result["restored"]["filters"] == 1, "名字不合法的那份被跳过（只剩 1 个过滤器）",
          repr(result["restored"]))
    check(os.path.isfile(jailconf.MANAGED_FILE), "托管配置被写出")
    check(not os.path.exists(os.path.join(PROJECT, "evil-link")),
          "符号链接没有落到本地磁盘")
    check(not os.path.exists(os.path.join(jailconf.FILTER_D, "Bad Name.local")),
          "非法过滤器名没有被写出")
    check(result["restored"]["prefs"] is True, "带 ./ 前缀的成员照常识别",
          repr(result["restored"]))
    check(store.load_prefs().get("theme") == "light", "带 ./ 前缀的 prefs 确实生效",
          repr(store.load_prefs()))


def test_write_failure_is_500():
    print("[13] 写盘失败 -> ApiError 500「恢复失败：…」")
    seed_current_state()
    name = build_archive("fn-fail2ban-manual-20260203-000000.tar.gz", {
        "jail.d/" + os.path.basename(jailconf.MANAGED_FILE): "[sshd]\nenabled = true\n",
    })
    original = store.atomic_write

    def boom(*args, **kwargs):
        raise OSError("磁盘只读")

    store.atomic_write = boom
    try:
        server.h_backup_restore(None, {}, {"name": name})
    except server.ApiError as exc:
        check(exc.status == 500, "状态码 500", f"status={exc.status}")
        check(exc.message.startswith("恢复失败："), "提示以「恢复失败：」开头",
              repr(exc.message))
        check("磁盘只读" in exc.message, "带上底层原因", repr(exc.message))
    except Exception as exc:  # noqa: BLE001
        check(False, "应抛 ApiError", f"{type(exc).__name__}: {exc}")
    else:
        check(False, "应抛 ApiError", "没有抛异常")
    finally:
        store.atomic_write = original


def test_jail_prefers_managed_name():
    print("[14] 归档里有多份 jail.d 成员 -> 优先取与当前托管文件同名的那份")
    seed_current_state()
    target = os.path.basename(jailconf.MANAGED_FILE)
    name = "fn-fail2ban-manual-20260204-000000.tar.gz"
    with tarfile.open(archive_path(name), "w:gz") as tar:
        add_text(tar, "manifest.json", json.dumps({"app": "fn-fail2ban"}) + "\n")
        add_text(tar, "jail.d/aaa.local", "[sshd]\nenabled = false\n")
        add_text(tar, "jail.d/" + target, "[sshd]\nenabled = true\n")
    result = backup.restore_backup(name)
    check(result["restored"]["jail"] is True, "jail 恢复成功", repr(result["restored"]))
    check(read_text(jailconf.MANAGED_FILE) == "[sshd]\nenabled = true\n",
          "写到托管文件的是同名成员的内容", repr(read_text(jailconf.MANAGED_FILE)))


def main():
    print(f"工作目录：{WORK}")
    reset_workdir()
    print(f"备份目录：{backup.resolve_directory()}")
    test_positive_restore()
    test_foreign_manifest()
    test_missing_manifest()
    test_path_traversal()
    test_missing_archive()
    test_handler_maps_400()
    test_bad_notifications_skipped()
    test_broken_json()
    test_reload_failure_recorded()
    test_roundtrip_with_create_backup()
    test_garbage_archive()
    test_dir_and_symlink_members()
    test_write_failure_is_500()
    test_jail_prefers_managed_name()
    print()
    if failures:
        print(f"FAILED（{len(failures)} 项）：")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
