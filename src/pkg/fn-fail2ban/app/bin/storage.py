"""应用自身目录的空间占用统计与一键清理。

面板在设备上只有三样东西会自己长大：运行日志、配置快照与归档备份、
以及一键升级下载的安装包。单看每一项都不大，跑上一年就是用户的问题，
所以这个模块分两个层次工作：

  * startup_purge() 在每次启动时的后台线程里跑一次：升级会替换面板进程，
    新进程启动正是「刚装完的那个包已经多余」的时刻，顺手清掉历史残留；
  * report() / cleanup() 支撑「存储」页，让人先看清占用，再决定回收哪一块。

删除逻辑有三条来自真实事故的硬规则：

  1. 运行日志只能就地截断。面板进程用追加方式打开 server.log，os.remove
     只删目录项，空间还挂在旧 inode 上 —— 用户看到的就是「清了没变小」。
     所以日志走 open(path, "wb") 截成 0 字节；只有明确不再被打开的轮转文件
     才真的删除。
  2. 当前运行版本的安装包永不删除，update/rollback.fpk 也永不删除。前者是
     回滚唯一能装回去的版本，后者是上次升级刻意留下的安全网。
  3. 下载 / 安装进行中整体拒绝清理。update/ 目录里那个 .part 是正在传输的
     文件，安装脚本此时也在往同一目录写日志。

所有路径都先 os.path.realpath 再判断是否落在本应用目录内：一个指向 /etc/shadow
的软链接不能被当成「日志文件」删掉。

纯标准库。
"""

from __future__ import annotations

import glob
import os

APP_NAME = "fn-fail2ban"

#: 更新目录里保留几个完整安装包。留 2 个是为了「刚装的这版 + 上一版」都能回滚。
UPDATES_KEEP = 2
UPDATES_KEEP_MAX = 20

#: 归档备份（backup.py 生成的 tar.gz）默认保留份数，与 backup.DEFAULT_SETTINGS 一致。
BACKUP_KEEP = 10
BACKUP_KEEP_MAX = 200

#: 配置文件快照（jailconf 每次写托管配置前留的 .bak）保留份数。
#: 每份只有几百字节，保留得比归档备份多一些，回滚时手工翻找才有意义。
SNAPSHOT_KEEP = 20
SNAPSHOT_KEEP_MIN = 5

#: 这些后缀代表「没传完」。续传本来就是从头来，留着纯属浪费。
PARTIAL_SUFFIXES = (".part", ".tmp")

#: 固定名字的回滚包，需要单独豁免而不是按版本号匹配。
ROLLBACK_FPK = "rollback.fpk"

_LOG = None


def set_logger(fn):
    """装上服务的 log()，让清理结果出现在面板日志里。"""
    global _LOG
    _LOG = fn if callable(fn) else None


def _emit(msg):
    fn = _LOG
    if callable(fn):
        try:
            fn(msg)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def update_dir(var_dir):
    return os.path.join(var_dir, "update") if var_dir else ""


def snapshot_dir(var_dir):
    """配置文件快照目录（jailconf.backup_file 的落点）。"""
    return os.path.join(var_dir, "backup") if var_dir else ""


def server_log(var_dir):
    return os.path.join(var_dir, "server.log") if var_dir else ""


def install_log(var_dir):
    d = update_dir(var_dir)
    return os.path.join(d, "install.log") if d else ""


def _within(path, root):
    """realpath 之后 path 是否真的在 root 里面。

    软链接必须按解析后的目标判断：一个指向 /etc/passwd 的 server.log 解析后
    不在本应用目录内，这种「日志」一律拒绝处理。
    """
    if not path or not root:
        return False
    try:
        real = os.path.realpath(path)
        base = os.path.realpath(root)
    except OSError:
        return False
    return real == base or real.startswith(base + os.sep)


def _safe_target(path, root):
    """path 可以安全地被截断或删除：是普通文件、不是软链接、且落在 root 内。

    realpath 校验单独用不够：server.log 如果被换成指向 /etc/shadow 的软链接，
    realpath 会解析到目标外，于是被拒 —— 这条已经有了。而软链接本身被
    os.remove 时只删链接、不动目标，那反而是安全的；真正危险的是「顺着
    软链接去截断目标」，所以截断路径必须先排除软链接。
    """
    if not path or not root:
        return False
    try:
        if os.path.islink(path):
            return False
        if not os.path.isfile(path):
            return False
    except OSError:
        return False
    return _within(path, root)


# ---------------------------------------------------------------------------
# 度量
# ---------------------------------------------------------------------------

def _dir_size(path):
    """递归统计 (字节, 文件数)。读不到的条目记 0。

    存储页是诊断工具，因为某个子目录没权限就把整页数字都藏起来，等于让人
    靠猜。单个文件失败只影响它自己那一项。
    """
    total = 0
    files = 0
    if not path or not os.path.isdir(path):
        return 0, 0
    try:
        for root, _dirs, names in os.walk(path):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                    files += 1
                except OSError:
                    pass
    except OSError:
        pass
    return total, files


def _file_size(path):
    try:
        if os.path.isfile(path):
            return os.path.getsize(path), 1
    except OSError:
        pass
    return 0, 0


# ---------------------------------------------------------------------------
# 更新包列表
# ---------------------------------------------------------------------------

def _is_partial(name):
    return str(name or "").lower().endswith(PARTIAL_SUFFIXES)


def _is_package(name):
    low = str(name or "").lower()
    if low.endswith(".fpk"):
        return True
    for suffix in PARTIAL_SUFFIXES:
        if low.endswith(".fpk" + suffix):
            return True
    return False


def updates_list(var_dir):
    """update/ 下的安装包与半截下载，新的在前。

    partial 标出没传完的文件：它们永远装不上，页面上标出来，用户才能理解
    「清理为什么能腾出这几个 MB」。
    """
    d = update_dir(var_dir)
    out = []
    if not d or not os.path.isdir(d):
        return out
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        if not _is_package(name):
            continue
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append({
            "name": name,
            "bytes": int(stat.st_size),
            "t": int(stat.st_mtime),
            "partial": _is_partial(name),
        })
    out.sort(key=lambda item: item["t"], reverse=True)
    return out


def snapshots_list(var_dir):
    """配置文件快照（*.bak），新的在前。"""
    d = snapshot_dir(var_dir)
    out = []
    if not d or not os.path.isdir(d):
        return out
    try:
        names = os.listdir(d)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".bak"):
            continue
        path = os.path.join(d, name)
        if not os.path.isfile(path):
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        out.append({
            "name": name,
            "bytes": int(stat.st_size),
            "t": int(stat.st_mtime),
        })
    out.sort(key=lambda item: item["t"], reverse=True)
    return out


# ---------------------------------------------------------------------------
# 报告
# ---------------------------------------------------------------------------

#: (key, 中文标签, var_dir 下的相对路径, kind, nested)。
#: nested=True 表示这一项位于另一个已列出的目录内部：升级安装日志在 update/ 里，
#: 再算一遍总量会让「合计」比目录实际占用还大。
LAYOUT = (
    ("update_dir", "更新缓存", ("update",), "dir", False),
    ("backup_dir", "配置快照", ("backup",), "dir", False),
    ("server_log", "运行日志", ("server.log",), "file", False),
    ("server_log1", "运行日志（上一份）", ("server.log.1",), "file", False),
    ("install_log", "升级安装日志", ("update", "install.log"), "file", True),
    ("deliveries", "通知投递记录", ("deliveries.json",), "file", False),
    ("events", "通知事件记录", ("events.json",), "file", False),
    ("notifications", "通知设置", ("notifications.json",), "file", False),
    ("backup_settings", "备份设置", ("backup.json",), "file", False),
    ("prefs", "界面偏好", ("prefs.json",), "file", False),
    ("fnknock", "敲门联动设置", ("fnknock.json",), "file", False),
    ("fnknock_log", "敲门联动日志", ("fnknock-log.json",), "file", False),
)


def _archive_backups(backups):
    """把调用方给的备份清单规整成 {name,size,mtime} 列表，按时间倒序。

    清单由 backup.list_backups() 提供（它的目录可以由用户改成任意存储路径），
    所以这里只做规整，不重新扫描 —— 判断「哪些是备份」的规则只应有一处。
    """
    items = []
    for raw in backups or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not name:
            continue
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        try:
            mtime = int(raw.get("mtime") or 0)
        except (TypeError, ValueError):
            mtime = 0
        items.append({"name": name, "size": size, "mtime": mtime})
    items.sort(key=lambda item: item["mtime"], reverse=True)
    return items


def _resolve_keep(keep, default, low, high):
    if keep is None or str(keep).strip() == "":
        return default
    try:
        value = int(keep)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def report(var_dir, current_version="", backups=None, keep=None):
    """目录占用 + 更新包 + 备份清单，就是「存储」页要的那份数据。"""
    var_dir = var_dir or ""
    keep_n = _resolve_keep(keep, BACKUP_KEEP, 1, BACKUP_KEEP_MAX)
    rows = []
    total = 0
    for key, label, parts, kind, nested in LAYOUT:
        path = os.path.join(var_dir, *parts) if var_dir else ""
        if kind == "dir":
            size, files = _dir_size(path)
        else:
            size, files = _file_size(path)
        rows.append({
            "key": key,
            "label": label,
            "bytes": int(size),
            "files": int(files),
            "path": path,
            "kind": kind,
            "nested": bool(nested),
        })
        if not nested:
            total += int(size)

    if backups is None:
        backups = _archives_from_module()
    return {
        "items": rows,
        "total": int(total),
        "var_dir": var_dir,
        "backups": _archive_backups(backups),
        "keep": int(keep_n),
        "current_version": str(current_version or ""),
        "packages": updates_list(var_dir),
        "updates_keep": UPDATES_KEEP,
        "snapshot_keep": SNAPSHOT_KEEP,
    }


def _archives_from_module():
    """没拿到清单时自己去问 backup 模块。

    延迟导入：storage 要能在没有 fail2ban 环境的测试里单独跑起来。
    """
    try:
        import backup as backup_mod
    except Exception:
        return []
    try:
        return backup_mod.list_backups()
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 忙碌判断
# ---------------------------------------------------------------------------

def installer_busy():
    """下载或安装进行中。

    延迟导入，让本模块在测试环境与「没装 installer」的构建里照样可用；
    导入失败一律按「没在忙」处理（清理不会因为读不到状态而永久停摆）。
    """
    try:
        import installer as installer_mod
    except Exception:
        return False
    try:
        return bool(installer_mod.busy())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 逐个删除
# ---------------------------------------------------------------------------

def _truncate(path, root):
    """就地清空一个可能正被其它进程打开的日志文件。

    返回 (腾出的字节数, 是否成功)。追加打开时每次写都会重新定位到文件末尾，
    所以截断后新内容从 0 开始写；而 os.remove 只会让空间继续挂在旧 inode 上，
    那正是「删了文件磁盘没变小」的成因。路径先过 _safe_target：不是普通文件、
    是软链接、或落在 root 之外的，一律不动。
    """
    if not _safe_target(path, root):
        _emit("storage: 拒绝截断 %s" % (path,))
        return 0, False
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0, False
    try:
        with open(path, "wb"):
            pass
    except OSError as exc:
        _emit("storage: 无法截断 %s：%r" % (path, exc))
        return 0, False
    return int(size), True


def _remove(path, root):
    """删除一个普通文件，必须先证明它真的在 root 里面。返回 (字节数, 是否成功)。"""
    if not _safe_target(path, root):
        _emit("storage: 拒绝删除越界路径 %s" % (path,))
        return 0, False
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0, False
    try:
        os.remove(path)
    except OSError as exc:
        _emit("storage: 无法删除 %s：%r" % (path, exc))
        return 0, False
    return int(size), True


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def log_files(var_dir):
    """会被「清理日志」处理的文件：(路径, 是否就地截断)。

    只有 server.log 与 install.log 是被进程持续写着的，它们一律截断；
    server.log.1 / server.log.2 … 是 logrotate 留下的历史副本，没人打开，
    直接删除才能真正腾出空间。
    """
    out = []
    main = server_log(var_dir)
    if main:
        out.append((main, True))
        for rotated in sorted(glob.glob(main + ".*")):
            if os.path.isfile(rotated):
                out.append((rotated, False))
    ilog = install_log(var_dir)
    if ilog:
        out.append((ilog, True))
    return out


def clear_logs(var_dir):
    """清空运行日志与升级安装日志，删掉轮转出来的历史副本。

    截断的文件必须在 var_dir 内部（server.log / install.log 都在那里）；
    轮转副本允许出现在 server.log 同目录，按那个目录做边界。
    """
    freed = 0
    removed = []
    failures = []
    root = var_dir or "."
    for path, in_place in log_files(var_dir):
        if not os.path.isfile(path):
            continue
        if not _within(path, root):
            failures.append(path)
            continue
        if in_place:
            got, ok = _truncate(path, root)
        else:
            got, ok = _remove(path, os.path.dirname(path) or root)
        if ok:
            freed += got
            removed.append(os.path.basename(path))
        else:
            failures.append(path)
    # 一个空文件本来就没有可回收的字节，它出现在 removed 里是诚实的：
    # 这次清理确实处理了它，只是回收量为 0。
    return {"freed": freed, "removed": removed,
            "failures": [item for item in failures if item]}


# ---------------------------------------------------------------------------
# 更新缓存
# ---------------------------------------------------------------------------

def purge_update_cache(var_dir, keep=UPDATES_KEEP, protect_version="", busy=None):
    """把 update/ 裁到最新 keep 个完整安装包。

    删除每个半截下载、每个超出 keep 的 .fpk，同时永不碰运行版本的安装包与
    update/rollback.fpk。返回 {removed:[], kept:[], count, freed, skipped}。

    天然幂等：同样的参数再跑一次没有可删的文件。永不抛异常。
    """
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        keep = UPDATES_KEEP
    keep = max(0, min(UPDATES_KEEP_MAX, keep))
    protect = str(protect_version or "").strip().lstrip("vV")
    d = update_dir(var_dir)
    out = {"removed": [], "kept": [], "count": 0, "freed": 0, "skipped": ""}
    if not d or not os.path.isdir(d):
        out["skipped"] = "no_update_dir"
        return out

    running = installer_busy() if busy is None else bool(busy)
    items = updates_list(var_dir)
    # 「保留最新几个」只数真正的安装包。半截下载本来就不算，rollback.fpk
    # 无论如何都豁免 —— 让它占掉一个名额等于实际只留了 keep-1 个包。
    full = [item for item in items
            if not item["partial"] and item["name"].lower() != ROLLBACK_FPK]
    keep_names = set(item["name"] for item in full[:keep])

    for item in items:
        name = item["name"]
        if name.lower() == ROLLBACK_FPK:
            out["kept"].append(name)
            continue
        if protect and protect in name:
            out["kept"].append(name)
            continue
        if not item["partial"] and name in keep_names:
            out["kept"].append(name)
            continue
        if running:
            # 这个 .part 可能就是正在传输的那一个，安装脚本此刻也在写同目录。
            continue
        path = os.path.join(d, name)
        if not _within(path, d):
            continue
        try:
            os.remove(path)
        except OSError as exc:
            _emit("storage: 无法删除 %s：%r" % (name, exc))
            continue
        out["removed"].append(name)
        out["freed"] += int(item["bytes"])
    if running:
        out["skipped"] = "busy"
        _emit("storage: 更新进行中，跳过安装包清理")
        return out
    out["count"] = len(out["removed"])
    if out["removed"]:
        _emit("storage: 清理了 %d 个安装包缓存，回收 %.1f MB"
              % (out["count"], out["freed"] / 1024.0 / 1024.0))
    return out


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------

def _delete_archive(name):
    """按名字删除一份归档备份，返回 (腾出字节, 失败路径或空串)。

    定位与合法性判断都交给 backup.find_backup()：它只接受纯文件名、必须是
    本应用自己的命名前缀，因此这里不会有机会删到清单之外的文件。备份目录
    可以是用户指定的任意存储路径，所以边界取「该文件所在目录」，并且要求
    它是普通文件、不是软链接。
    """
    try:
        import backup as backup_mod
    except Exception:
        return 0, str(name or "")
    try:
        path = backup_mod.find_backup(str(name or ""))
    except Exception:
        path = None
    if not path:
        return 0, str(name or "")
    folder = os.path.dirname(path) or "."
    if _safe_target(path, folder):
        got, ok = _remove(path, folder)
        if ok:
            return got, ""
    return 0, path


def trim_archives(backups, keep):
    """归档备份只留最新 keep 份，返回 (腾出字节, 删除数, 失败路径)。"""
    items = _archive_backups(backups)
    freed = 0
    removed = 0
    failures = []
    for item in items[keep:]:
        got, failed = _delete_archive(item["name"])
        if failed:
            failures.append(failed)
            continue
        freed += got
        removed += 1
    return freed, removed, failures


def trim_snapshots(var_dir, keep):
    """配置文件快照只留最新 keep 份，返回 (腾出字节, 删除数, 失败路径)。"""
    d = snapshot_dir(var_dir)
    freed = 0
    removed = 0
    failures = []
    if not d or not os.path.isdir(d):
        return 0, 0, failures
    for item in snapshots_list(var_dir)[keep:]:
        path = os.path.join(d, item["name"])
        got, ok = _remove(path, d)
        if ok:
            freed += got
            removed += 1
        else:
            failures.append(path)
    return freed, removed, failures


# ---------------------------------------------------------------------------
# 清理入口
# ---------------------------------------------------------------------------

TARGETS = ("logs", "backups", "all")


def cleanup(var_dir, target="logs", keep=None, current_version="", backups=None):
    """跑一次清理，返回 {ok, target, freed, removed, detail, removedItems}。

    target 取 logs / backups / all：

    * logs：截断运行日志与升级安装日志，删除轮转副本；
    * backups：归档备份保留最新 keep 份，配置文件快照保留最新 SNAPSHOT_KEEP 份；
    * all：以上两者，再加更新缓存（保留 UPDATES_KEEP 个包，豁免运行版本与
      rollback.fpk）。

    下载或安装进行中返回 {ok: False, busy: True}，调用方据此给 409 而不是
    删掉正在写的文件。detail 里只放具体路径或异常（界面自己拼提示语）。
    """
    target = str(target or "logs").strip().lower()
    if target not in TARGETS:
        return {"ok": False, "busy": False, "target": target,
                "error": "unknown_target", "freed": 0, "removed": 0,
                "detail": target, "removedItems": []}
    if not var_dir:
        return {"ok": False, "busy": False, "target": target,
                "error": "no_var_dir", "freed": 0, "removed": 0,
                "detail": "", "removedItems": []}

    if installer_busy():
        return {"ok": False, "busy": True, "target": target,
                "error": "busy", "freed": 0, "removed": 0,
                "detail": "installer", "removedItems": []}

    if backups is None:
        backups = _archives_from_module()
    keep_n = _resolve_keep(keep, BACKUP_KEEP, 1, BACKUP_KEEP_MAX)

    freed = 0
    removed_count = 0
    removed_items = []
    failures = []

    if target in ("logs", "all"):
        res = clear_logs(var_dir)
        freed += int(res.get("freed") or 0)
        names = res.get("removed") or []
        removed_count += len(names)
        removed_items.extend(names)
        failures.extend(res.get("failures") or [])

    if target in ("backups", "all"):
        got, count, failed = trim_archives(backups, keep_n)
        freed += got
        removed_count += count
        failures.extend(failed)
        got, count, failed = trim_snapshots(var_dir, SNAPSHOT_KEEP)
        freed += got
        removed_count += count
        failures.extend(failed)

    if target == "all":
        res = purge_update_cache(var_dir, keep=UPDATES_KEEP,
                                 protect_version=current_version, busy=False)
        freed += int(res.get("freed") or 0)
        names = res.get("removed") or []
        removed_count += len(names)
        removed_items.extend(names)

    _emit("storage: cleanup(%s) 回收 %.1f MB，%d 项"
          % (target, freed / 1024.0 / 1024.0, removed_count))
    return {
        "ok": True,
        "busy": False,
        "target": target,
        "freed": int(freed),
        "removed": int(removed_count),
        # detail 只放处理失败的路径：界面自己拼提示语，后端不产出半句中文句子
        # 让英文界面去替换。
        "detail": "；".join(str(item) for item in failures if item),
        "removedItems": removed_items,
        "kept": int(keep_n),
        "snapshot_keep": SNAPSHOT_KEEP,
        "updates_keep": UPDATES_KEEP,
        "backup_keep": int(keep_n),
    }


# ---------------------------------------------------------------------------
# 启动自动清理
# ---------------------------------------------------------------------------

def startup_purge(var_dir, current_version="", keep=UPDATES_KEEP):
    """启动时的自动清理：裁掉多余的安装包与半截下载。

    由后台线程调用，永不抛异常、永不阻塞启动。归档备份与配置快照各自有
    自己的轮转（备份创建时 prune、jailconf 每次写前 prune），这里不动它们：
    用户的历史备份不该被一次静默的启动顺手删掉。
    """
    try:
        return purge_update_cache(var_dir, keep=keep,
                                  protect_version=current_version)
    except Exception as exc:  # noqa: BLE001 - 启动流程必须扛住任何异常
        _emit("storage: 启动清理失败：%r" % (exc,))
        return {"ok": False, "error": repr(exc), "removed": [], "count": 0,
                "freed": 0, "kept": []}
