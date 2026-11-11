"""配置自动备份。

把本应用托管的配置快照打包成 tar.gz，可写入 fnOS 存储空间（/vol1、/vol2 …），
便于用户直接通过「文件管理」取走或归档。仅使用标准库。
"""

from __future__ import annotations

import glob
import io
import json
import os
import socket
import tarfile
import threading
import time

import f2b
import jailconf
import notify
import store

SETTINGS_FILE = os.path.join(store.VAR_DIR, "backup.json")
APP_FOLDER_NAME = "fn-fail2ban"
BACKUP_SUBDIR = "backup"
ARCHIVE_PREFIX = APP_FOLDER_NAME
APPSHARE_NAME = "@appshare"
CHECK_INTERVAL = 600          # 调度线程轮询间隔（秒）
DEFAULT_KEEP = 10

DEFAULT_SETTINGS = {
    "enabled": False,
    "directory": "",          # 空 = 应用数据目录
    "intervalHours": 24,
    "keep": DEFAULT_KEEP,
    "includeNotifications": True,
    "lastAttemptAt": 0,
    "lastSuccessAt": 0,
    "lastStatus": None,       # {"ok": bool, "message": str, "time": int, "name": str}
}

# 不允许作为备份目标的系统目录（防止误覆盖系统文件）
FORBIDDEN_PREFIXES = ("/etc", "/usr", "/bin", "/sbin", "/lib", "/boot",
                      "/dev", "/proc", "/sys", "/run", "/var/lib")


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    stored = store.read_json(SETTINGS_FILE)
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in settings:
                settings[key] = value
    try:
        settings["intervalHours"] = max(1, min(int(settings.get("intervalHours") or 24), 720))
    except (TypeError, ValueError):
        settings["intervalHours"] = 24
    try:
        settings["keep"] = max(1, min(int(settings.get("keep") or DEFAULT_KEEP), 200))
    except (TypeError, ValueError):
        settings["keep"] = DEFAULT_KEEP
    return settings


def _normalize_for_check(path: str) -> str:
    """统一分隔符后再比较，便于在非 POSIX 平台上验证同一套规则。"""
    return os.path.normpath(path).replace("\\", "/")


def validate_directory(raw: str) -> tuple[str, str | None]:
    """校验备份目录，返回 (规范化路径, 错误信息)。"""
    text = (raw or "").strip()
    if not text:
        return "", None
    if not os.path.isabs(text):
        return text, "备份目录必须是绝对路径"

    resolved = os.path.normpath(os.path.abspath(text))
    if "/" in (_normalize_for_check(text), _normalize_for_check(resolved)):
        return resolved, "不能直接使用根目录作为备份目录"

    for candidate in (text, resolved):
        normalized = _normalize_for_check(candidate)
        for prefix in FORBIDDEN_PREFIXES:
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return resolved, f"不允许使用系统目录 {prefix} 作为备份目标"
    return resolved, None


def save_settings(updates: dict) -> dict:
    settings = load_settings()
    if "enabled" in updates:
        settings["enabled"] = bool(updates["enabled"])
    if "includeNotifications" in updates:
        settings["includeNotifications"] = bool(updates["includeNotifications"])
    if "directory" in updates:
        resolved, error = validate_directory(str(updates.get("directory") or ""))
        if error:
            raise ValueError(error)
        # 与当前默认位置一致时存空串，保持“跟随默认”的语义。
        # 面板会把已解析的路径预填进输入框，用户点一次保存就把它固化下来，
        # 之后调整默认目录（例如应用改名）也带不动这些用户了。
        settings["directory"] = "" if resolved == default_directory() else resolved
    for key, low, high in (("intervalHours", 1, 720), ("keep", 1, 200)):
        if key in updates:
            try:
                settings[key] = max(low, min(int(updates[key]), high))
            except (TypeError, ValueError):
                pass
    store.ensure_dirs()
    store.atomic_write_json(SETTINGS_FILE, settings)
    return settings


def volume_roots() -> list[str]:
    """fnOS 的存储空间挂载点，形如 /vol1、/vol2。"""
    return sorted(path for path in glob.glob("/vol[0-9]*") if os.path.isdir(path))


def default_directory() -> str:
    """默认备份目录：fnOS 共享目录下应用自己的 ``backup`` 子目录。

    优先 ``/vol1/@appshare/fn-fail2ban/backup``，这样在「文件管理」里能直接看到；
    没有 /vol1 时退到其它存储空间；一个存储空间都没有（例如本地开发环境）
    才退回应用数据目录，避免在只读的根文件系统上凭空建目录。
    """
    volumes = volume_roots()
    root = "/vol1" if "/vol1" in volumes else (volumes[0] if volumes else "")
    if not root:
        return os.path.join(store.VAR_DIR, BACKUP_SUBDIR)
    return os.path.join(root, APPSHARE_NAME, APP_FOLDER_NAME, BACKUP_SUBDIR)


def resolve_directory(settings: dict | None = None) -> str:
    settings = settings or load_settings()
    raw = (settings.get("directory") or "").strip()
    return raw if raw else default_directory()


def list_locations(roots: list[str] | None = None) -> list[dict]:
    """列出可选的备份位置：默认位置 + fnOS 存储空间及其共享文件夹。"""
    items = [{
        "path": "",
        "kind": "default",
        "label": f"默认（{default_directory()}）",
        "defaultDir": default_directory(),
    }]
    for volume in (volume_roots() if roots is None else roots):
        if not os.path.isdir(volume):
            continue
        items.append({
            "path": volume,
            "kind": "volume",
            "label": f"{volume}（存储空间根目录）",
            "volume": volume,
        })
        try:
            entries = sorted(os.listdir(volume))
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(volume, entry)
            if entry.startswith(".") or not os.path.isdir(full):
                continue
            items.append({
                "path": full,
                "kind": "share",
                "label": f"{entry}  （位于 {volume}）",
                "name": entry,
                "volume": volume,
            })
    return items


# ---------------------------------------------------------------------------
# 备份
# ---------------------------------------------------------------------------

def _add_text(tar: tarfile.TarFile, arcname: str, text: str) -> None:
    data = text.encode("utf-8")
    info = tarfile.TarInfo(arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    info.mode = 0o600
    tar.addfile(info, io.BytesIO(data))


def _our_filters() -> list[str]:
    paths = []
    for name in store.read_custom_filters():
        _, local = jailconf.filter_paths(name)
        if os.path.isfile(local):
            paths.append(local)
    return paths


def create_backup(reason: str = "manual") -> dict:
    """生成一份备份快照并做轮转，返回结果描述。"""
    settings = load_settings()
    directory = resolve_directory(settings)
    os.makedirs(directory, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{ARCHIVE_PREFIX}-{reason}-{stamp}.tar.gz"
    path = os.path.join(directory, name)
    partial = path + ".part"

    info = f2b.info()
    meta = {
        "app": APP_FOLDER_NAME,
        "appVersion": _app_version(),
        "fail2banVersion": info.get("version"),
        "reason": reason,
        "createdAt": int(time.time()),
        "hostname": socket.gethostname(),
    }

    try:
        with tarfile.open(partial, "w:gz") as tar:
            _add_text(tar, "manifest.json",
                      json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
            if os.path.isfile(jailconf.MANAGED_FILE):
                tar.add(jailconf.MANAGED_FILE,
                        arcname=f"jail.d/{os.path.basename(jailconf.MANAGED_FILE)}")
            for filter_path in _our_filters():
                tar.add(filter_path, arcname=f"filter.d/{os.path.basename(filter_path)}")
            if os.path.isfile(store.PREFS_FILE):
                tar.add(store.PREFS_FILE, arcname="prefs.json")
            if settings.get("includeNotifications") and os.path.isfile(notify.SETTINGS_FILE):
                tar.add(notify.SETTINGS_FILE, arcname="notifications.json")
        os.replace(partial, path)
    except BaseException:
        try:
            os.unlink(partial)
        except OSError:
            pass
        raise

    removed = prune(settings)
    size = os.path.getsize(path)
    result = {
        "ok": True,
        "name": name,
        "path": path,
        "size": size,
        "removed": removed,
        "message": f"已备份到 {directory}",
        # language-neutral result fields; the panel renders these itself so the
        # English UI never shows the Chinese message above
        "resultKind": "backedUp",
        "directory": directory,
        "time": int(time.time()),
    }
    _record(result)
    return result


def _app_version() -> str:
    manifest = os.path.join(os.path.dirname(store.APP_DIR.rstrip("/")), "manifest")
    try:
        with open(manifest, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.startswith("version"):
                    return line.partition("=")[2].strip()
    except OSError:
        pass
    return "unknown"


def _record(result: dict) -> None:
    settings = load_settings()
    settings["lastAttemptAt"] = int(time.time())
    settings["lastStatus"] = {
        "ok": bool(result.get("ok")),
        "message": result.get("message") or "",
        "name": result.get("name") or "",
        "time": int(time.time()),
    }
    if result.get("ok"):
        settings["lastSuccessAt"] = int(time.time())
    store.atomic_write_json(SETTINGS_FILE, settings)


def record_failure(message: str) -> None:
    _record({"ok": False, "message": message})


def list_backups(settings: dict | None = None) -> list[dict]:
    directory = resolve_directory(settings)
    items = []
    for path in glob.glob(os.path.join(directory, f"{ARCHIVE_PREFIX}-*.tar.gz")):
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append({
            "name": os.path.basename(path),
            "size": stat.st_size,
            "mtime": int(stat.st_mtime),
        })
    # 同一秒内生成的多个归档 mtime 相同，只按 mtime 排会让顺序取决于目录遍历顺序，
    # 于是 prune() 有可能把「刚生成的那一个」当成最旧的删掉。名字里带时间戳，
    # 拿它当第二排序键，同一秒内的先后也稳定。
    items.sort(key=lambda item: (item["mtime"], item["name"]), reverse=True)
    return items


def find_backup(name: str) -> str | None:
    """安全地按文件名定位备份，拒绝任何路径穿越。"""
    safe = (name or "").strip()
    # 只接受纯文件名：带分隔符或 .. 的输入一律拒绝，不做静默修正
    if not safe or safe != os.path.basename(safe) or safe in (".", ".."):
        return None
    if not safe.startswith(f"{ARCHIVE_PREFIX}-") or not safe.endswith(".tar.gz"):
        return None
    path = os.path.join(resolve_directory(), safe)
    return path if os.path.isfile(path) else None


def delete_backup(name: str) -> bool:
    path = find_backup(name)
    if not path:
        return False
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# 恢复
# ---------------------------------------------------------------------------
# 只认下面这几类成员，其余一律忽略。恢复**不**调用 tarfile.extractall()，而是自己
# 读内容、自己决定写到哪里：归档里的路径永远不会成为写盘目标，符号链接 / 设备文件
# 也不会被落成设备上的文件。

MESSAGE_NOT_FOUND = "找不到该备份文件"
MESSAGE_BROKEN = "备份文件不完整或已损坏"
MESSAGE_FOREIGN = "不是本应用的备份文件"

RESTORE_MANIFEST = "manifest.json"
RESTORE_PREFS = "prefs.json"
RESTORE_NOTIFICATIONS = "notifications.json"


class BackupNotFound(ValueError):
    """找不到指定的备份文件。handler 据此回 404，与其它 400 分支区分开。"""


def _safe_member_name(member: tarfile.TarInfo) -> str:
    """取成员归一化后的相对路径；绝对路径或含 .. 段的一律判定为损坏。

    这里刻意不做「跳过可疑成员」：带着越界路径的归档本身就可疑，静默跳过等于
    把它当成正常归档继续处理，出问题的人也就看不到任何线索。
    """
    text = (member.name or "").replace("\\", "/")
    # tar 里常见 "./x" 前缀，去掉再判，避免把正常打包的归档误判成损坏
    while text.startswith("./"):
        text = text[2:]
    if not text:
        # "." / "./" 是归档根目录条目，本身没有可写目标，不算越界
        return ""
    if text.startswith("/"):
        raise ValueError(MESSAGE_BROKEN)
    parts = [part for part in text.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError(MESSAGE_BROKEN)
    # Windows 盘符形式的绝对路径，换到 Windows 上运行时同样越界
    if parts and len(parts[0]) == 2 and parts[0][1] == ":" and parts[0][0].isalpha():
        raise ValueError(MESSAGE_BROKEN)
    return "/".join(parts)


def _classify_member(name: str) -> tuple[str, str]:
    """把成员归入可恢复的几类，返回 (类型, 附加名)；不恢复的返回 ("", "")。

    jail.d 与 filter.d 都只接受「一层」成员：jail.d/<文件>、
    filter.d/<名字>.local。过滤器名还要过 NAME_RE，写出去的文件名不可能带路径。
    """
    if name in (RESTORE_MANIFEST, RESTORE_PREFS, RESTORE_NOTIFICATIONS):
        return "config", name
    parts = name.split("/")
    if len(parts) == 2 and parts[0] == "jail.d" and parts[1]:
        return "jail", parts[1]
    if len(parts) == 2 and parts[0] == "filter.d" and parts[1].endswith(".local"):
        filter_name = parts[1][:-len(".local")]
        if jailconf.NAME_RE.match(filter_name):
            return "filter", filter_name
    return "", ""


def _parse_json_text(text: str):
    """解析归档里的 JSON；解析不了就是归档损坏，不要当成「内容为空」。"""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(MESSAGE_BROKEN) from exc


def _parse_manifest(text: str) -> dict:
    """校验归档来路：必须是本应用写出的备份。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(MESSAGE_FOREIGN) from exc
    if not isinstance(data, dict) or data.get("app") != APP_FOLDER_NAME:
        raise ValueError(MESSAGE_FOREIGN)
    return data


def restore_backup(name: str) -> dict:
    """把一份归档写回当前配置，返回恢复结果。

    读取与校验全部在内存里做完，只有整份归档都通过（路径、manifest、JSON 结构）才
    开始写盘 —— 「不是本应用的备份」不应该先改掉一半配置再说不行。
    重载 fail2ban 放在最后，结果只记录不抛出：配置已经写下去了，这时报错会让用户
    以为恢复没生效。
    """
    path = find_backup(name)
    if not path:
        raise BackupNotFound(MESSAGE_NOT_FOUND)

    manifest = None
    jail_text = None
    jail_is_managed = False
    prefs_data = None
    notifications_data = None
    filters: list[tuple[str, str]] = []

    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                # 路径校验对全部成员生效（越界就是可疑归档），只有普通文件才会被读
                normalized = _safe_member_name(member)
                # 目录、符号链接、设备文件都不是本应用写出的内容，直接忽略
                if not normalized or not member.isfile():
                    continue
                kind, extra = _classify_member(normalized)
                if not kind:
                    continue
                handle = archive.extractfile(member)
                if handle is None:
                    raise ValueError(MESSAGE_BROKEN)
                text = handle.read().decode("utf-8", errors="replace")
                if kind == "config" and extra == RESTORE_MANIFEST:
                    manifest = _parse_manifest(text)
                elif kind == "jail":
                    # 正常归档里只有一份托管配置。万一有多份，优先取文件名与当前
                    # 托管文件同名的那一份，其余按出现顺序覆盖，不留下"看运气"的结果。
                    managed = extra == os.path.basename(jailconf.MANAGED_FILE)
                    if jail_text is None or managed or not jail_is_managed:
                        jail_text = text
                        jail_is_managed = managed
                elif kind == "filter":
                    filters.append((extra, text))
                elif kind == "config" and extra == RESTORE_PREFS:
                    prefs_data = _parse_json_text(text)
                elif kind == "config" and extra == RESTORE_NOTIFICATIONS:
                    data = _parse_json_text(text)
                    # 结构不对就跳过：宁可少恢复一块，也不要把半份通知配置写进去
                    if isinstance(data, dict) and isinstance(data.get("channels"), list):
                        notifications_data = data
    except (tarfile.TarError, EOFError, OSError) as exc:
        # 截断、非 tar/gz、崩掉的 gzip 头、读到一半的 I/O 错误都算归档不可用。
        # 这一段只读不写，所以这里的 OSError 与「写盘失败」不是一回事。
        raise ValueError(MESSAGE_BROKEN) from exc

    if manifest is None:
        raise ValueError(MESSAGE_FOREIGN)

    restored = {"jail": False, "filters": 0, "prefs": False, "notifications": False}

    if jail_text is not None or filters:
        # 只在真要写 fail2ban 配置时才建目录：系统没装 fail2ban 而只恢复界面偏好时，
        # 不该凭空在 /etc/fail2ban 下造出目录。
        jailconf.ensure_dirs()

    if jail_text is not None:
        # 覆盖托管配置前先留底：恢复错了还能从「维护 → 备份文件」里找回上一份。
        jailconf.backup_file(jailconf.MANAGED_FILE)
        store.atomic_write(jailconf.MANAGED_FILE, jail_text, 0o644)
        restored["jail"] = True

    for filter_name, text in filters:
        _, local_path = jailconf.filter_paths(filter_name)
        store.atomic_write(local_path, text, 0o644)
        # 记进自定义过滤器清单，面板的过滤器列表才会把它当成本应用管理的那一份
        store.record_custom_filter(filter_name, True)
        restored["filters"] += 1

    if isinstance(prefs_data, dict):
        store.save_prefs(prefs_data)
        restored["prefs"] = True

    if notifications_data is not None:
        store.atomic_write_json(notify.SETTINGS_FILE, notifications_data)
        restored["notifications"] = True

    reload_state = {"ok": False, "error": None}
    try:
        f2b.reload()
    except Exception as exc:  # noqa: BLE001 - 重载失败只记录，不能让恢复本身失败
        reload_state = {"ok": False, "error": str(exc)}
    else:
        reload_state = {"ok": True, "error": None}

    return {
        "ok": True,
        "name": os.path.basename(path),
        "restored": restored,
        "reload": reload_state,
    }


def prune(settings: dict | None = None) -> int:
    settings = settings or load_settings()
    try:
        keep = max(1, min(int(settings.get("keep") or DEFAULT_KEEP), 200))
    except (TypeError, ValueError):
        keep = DEFAULT_KEEP
    removed = 0
    for item in list_backups(settings)[keep:]:
        path = find_backup(item["name"])
        if not path:
            continue
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


# ---------------------------------------------------------------------------
# 调度
# ---------------------------------------------------------------------------

class Scheduler(threading.Thread):
    """按设定间隔触发自动备份；关闭时只休眠，不做任何事。"""

    daemon = True

    def __init__(self):
        super().__init__(name="f2b-backup-scheduler")
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        # 启动后先等一轮，避免服务重启时立刻产生备份
        if self._stop.wait(60):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程不应因异常退出
                pass
            self._stop.wait(CHECK_INTERVAL)

    def tick(self) -> None:
        settings = load_settings()
        if not settings.get("enabled"):
            return
        try:
            interval = max(1, int(settings.get("intervalHours") or 24)) * 3600
        except (TypeError, ValueError):
            interval = 24 * 3600
        last = max(int(settings.get("lastAttemptAt") or 0),
                   int(settings.get("lastSuccessAt") or 0))
        if time.time() - last < interval:
            return
        try:
            create_backup("auto")
        except (OSError, ValueError) as exc:
            record_failure(f"自动备份失败：{exc}")
