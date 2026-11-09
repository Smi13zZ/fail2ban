#!/usr/bin/env python3
"""fnOS「Fail2ban」管理面板后端。

仅依赖 Python 标准库（http.server），无需 pip 安装任何包。
"""

from __future__ import annotations

import glob
import ipaddress
import json
import mimetypes
import os
import platform
import re
import socket
import socketserver
import sys
import threading
import time
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import backup  # noqa: E402
import banlog  # noqa: E402
import delivery  # noqa: E402
import f2b  # noqa: E402
import filter_templates  # noqa: E402
import fnknock  # noqa: E402
import installer  # noqa: E402
import jailconf  # noqa: E402
import notify  # noqa: E402
import storage  # noqa: E402
import store  # noqa: E402
import update  # noqa: E402

APP_NAME = os.environ.get("TRIM_APPNAME") or "fn-fail2ban"
APP_VERSION = "26.11.9"
PORT = int(os.environ.get("TRIM_SERVICE_PORT") or os.environ.get("F2B_PORT") or 18752)
BIND_ADDR = os.environ.get("F2B_BIND") or "0.0.0.0"
READY_FILE = os.environ.get("F2B_READYFILE") or os.path.join(store.VAR_DIR, "server.ready")

# ---- 飞牛统一网关 ---------------------------------------------------------
# 网关把 /app/<appname>/* 的请求转发到应用 target 目录下的 Unix Socket，
# 因此后端必须同时监听该 Socket（TCP 端口保留作为直连/排查入口）。
GATEWAY_PREFIX = (os.environ.get("FNNAS_GATEWAY_PREFIX") or f"/app/{APP_NAME}").rstrip("/")

# 显式传入空字符串表示禁用网关 Socket（仅在不需要时使用；cmd/main 总会传入真实路径）
_gateway_socket_env = os.environ.get("FNNAS_GATEWAY_SOCKET")
GATEWAY_SOCKET = (_gateway_socket_env if _gateway_socket_env is not None
                  else os.path.join(store.APP_DIR, "app.sock"))
COOKIE_NAME = "f2b_session"
MAX_BODY = 1024 * 1024
SESSION_TTL = store.SESSION_TTL

START_TIME = time.time()

# ---- 访问控制 -------------------------------------------------------------
# 面板不再有登录页，因此不能只靠「谁知道地址」来保护：这个应用能写
# /etc/fail2ban、能封禁/解封 IP、能改防火墙动作，必须限制可访问的网段。
#
# 默认只允许回环 + 私有网段（RFC1918）访问，命中范围外的请求一律 403。
# 可以用环境变量 F2B_ALLOW_CIDRS 覆盖（空格分隔），例如只放行单个管理机：
#   F2B_ALLOW_CIDRS="192.168.1.50/32"
# 设为 * 表示完全放开（不推荐；仅在面板前面另有反向代理做鉴权时才合理）。
DEFAULT_ALLOW_CIDRS = "127.0.0.0/8 ::1 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12"
_ALLOW_SPEC = (os.environ.get("F2B_ALLOW_CIDRS") or DEFAULT_ALLOW_CIDRS).strip()
ALLOW_ANY = _ALLOW_SPEC in ("*", "all", "any")
ALLOW_NETS = []
for _item in re.split(r"[\s,]+", _ALLOW_SPEC):
    _item = _item.strip()
    if not _item or ALLOW_ANY:
        continue
    try:
        ALLOW_NETS.append(ipaddress.ip_network(_item, strict=False))
    except ValueError:
        # 写错的网段不能让整个面板起不来，跳过并在 self-check 里暴露
        pass


def app_version() -> str:
    manifest = os.path.join(os.path.dirname(store.APP_DIR.rstrip("/")), "manifest")
    for candidate in (manifest, os.path.join(store.APP_DIR, "manifest")):
        try:
            with open(candidate, "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if line.startswith("version"):
                        return line.partition("=")[2].strip().strip('"')
        except OSError:
            continue
    return APP_VERSION


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


class RawResponse:
    """绕过 JSON 封装的响应（目前用于备份文件下载）。"""

    def __init__(self, status: int, content_type: str, body: bytes, headers: dict | None = None):
        self.status = status
        self.content_type = content_type
        self.body = body
        self.headers = headers or {}


# ---------------------------------------------------------------------------
# 业务处理
# ---------------------------------------------------------------------------

def _bool(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")


def client_allowed(addr: str) -> bool:
    """判断来源地址是否在允许访问的网段内。

    面板没有登录页，所以这一层是唯一的入口保护：只有回环和内网能访问，
    公网来的请求在看任何数据之前就被挡掉。Unix Socket（统一网关）的
    client_address 是空串，那种情况总是放行 —— 它只能由本机网关连上。
    """
    if ALLOW_ANY:
        return True
    host = (addr or "").strip()
    if not host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    for net in ALLOW_NETS:
        if ip.version == net.version and ip in net:
            return True
    return False


# ---------------------------------------------------------------------------
# 封禁时间 / 到期时间
# ---------------------------------------------------------------------------

def _ban_seconds(values: dict) -> int | None:
    """从 Jail 的有效配置里取出 bantime 秒数。

    返回 -1 表示永久封禁；没配置或认不出来返回 None（此时不给 expiresAt，
    宁可少一个字段也不编造到期时间）。
    """
    return banlog.parse_bantime((values or {}).get("bantime"))


def _ban_entries(name: str, ips: list[str], bantime: int | None,
                 bans: dict | None = None) -> tuple[dict, list]:
    """把「IP 列表」和日志里的封禁时刻合并成前端要的两种形状。

    返回 (banInfo, banList)：

    * banInfo: {ip: {bannedAt, expiresAt}} —— 只含能查到时间的 IP；
    * banList: [{ip, bannedAt?, expiresAt?}] —— 与传入的 ip 顺序一致。
    """
    if bans is None:
        bans = banlog.collect().get(name) or {}
    ban_info: dict[str, dict] = {}
    ban_list: list[dict] = []
    permanent = bantime is not None and bantime <= 0
    for ip in ips or []:
        item: dict = {"ip": ip}
        info = bans.get(ip) or {}
        banned_at = info.get("bannedAt")
        if isinstance(banned_at, int) and banned_at > 0:
            item["bannedAt"] = banned_at
            if bantime is not None and not permanent:
                item["expiresAt"] = banned_at + bantime
            ban_info[ip] = {key: item[key] for key in ("bannedAt", "expiresAt") if key in item}
        ban_list.append(item)
    return ban_info, ban_list


def _decorate_status(name: str, status: dict | None, values: dict,
                     bans: dict | None = None) -> dict | None:
    """给 fail2ban 的 status 补上封禁时间字段（只增不改，原有键一个都不动）。

    ``bans`` 是 banlog.collect() 的整份结果（{jail: {ip: ...}}），这里按 Jail 名
    取出自己那一段；多一次索引换来调用方只解析一次日志。
    """
    if not isinstance(status, dict):
        return status
    bantime = _ban_seconds(values)
    status["bantime"] = bantime
    if not status.get("ok"):
        return status
    jail_bans = None if bans is None else (bans.get(name) or {})
    ban_info, ban_list = _ban_entries(name, status.get("bannedIps") or [], bantime, jail_bans)
    status["banInfo"] = ban_info
    status["bans"] = ban_list
    return status


def _jail_view(name: str, merged: dict, managed: dict, preset: set[str],
               active: set[str], with_status: bool = False, plain: dict | None = None,
               bans: dict | None = None) -> dict:
    effective = jailconf.effective_jail(name, merged)
    values = jailconf.options_to_values(effective)
    sources = {key: info.get("source") for key, info in effective.items()}
    override = managed.get(name, {})
    inherited = {}
    if plain is not None:
        inherited = jailconf.options_to_values(jailconf.effective_jail(name, plain))
    item = {
        "name": name,
        "enabled": _bool(values.get("enabled", "false")),
        "active": name in active,
        "preset": name in preset,
        "managed": bool(override),
        "override": override,
        "values": values,
        "inherited": inherited,
        "sources": sources,
    }
    if with_status and name in active:
        item["status"] = _decorate_status(name, f2b.jail_status(name), values, bans)
    return item


def h_health(handler, params, body):
    return {
        "ok": True,
        "app": APP_NAME,
        "version": app_version(),
        "pid": os.getpid(),
        "uptime": int(time.time() - START_TIME),
        "loginRequired": False,
        "allowCidrs": _ALLOW_SPEC,
    }


def h_meta(handler, params, body):
    return {
        "app": {
            "name": APP_NAME,
            "version": app_version(),
            "port": PORT,
            "bind": BIND_ADDR,
        },
        "jailFields": jailconf.JAIL_FIELDS,
        "globalFields": jailconf.GLOBAL_FIELDS,
        "banactions": jailconf.BANACTIONS,
        "actions": jailconf.list_actions(),
        "managedFile": jailconf.MANAGED_FILE,
        "paths": {
            "jailConf": jailconf.JAIL_CONF,
            "jailLocal": jailconf.JAIL_LOCAL,
            "jailD": jailconf.JAIL_D,
            "filterD": jailconf.FILTER_D,
            "actionD": jailconf.ACTION_D,
            "backupDir": store.BACKUP_DIR,
            "logFile": f2b.log_file(),
            # 「关于」页要显示数据与配置目录，前端不拼接路径
            "dataDir": store.VAR_DIR,
            "etcDir": store.ETC_DIR,
            "webDir": store.WEB_DIR,
        },
        "prefs": store.load_prefs(),
    }


def h_session(handler, params, body):
    """面板没有登录页；保留该接口只为前端读取版本号与允许访问的网段。"""
    return {
        "authenticated": True,
        "loginRequired": False,
        "allowCidrs": _ALLOW_SPEC,
        "version": app_version(),
    }


def h_overview(handler, params, body):
    info = f2b.info()
    active = set(f2b.jail_names()) if info["running"] else set()
    merged, files = jailconf.load_merged()
    plain, _ = jailconf.load_merged(exclude_managed=True)
    managed = jailconf.managed_sections()
    preset = jailconf.preset_jail_names(merged)
    bans = banlog.collect() if active else {}

    jails = []
    summary = {"activeJails": 0, "enabledJails": 0, "configuredJails": 0,
               "bannedIps": 0, "totalBanned": 0, "currentlyFailed": 0, "totalFailed": 0}

    for name in jailconf.all_jail_names(merged):
        item = _jail_view(name, merged, managed, preset, active, with_status=True,
                          plain=plain, bans=bans)
        summary["configuredJails"] += 1
        if item["enabled"]:
            summary["enabledJails"] += 1
        if item["active"]:
            summary["activeJails"] += 1
            status = item.get("status") or {}
            summary["bannedIps"] += status.get("currentlyBanned", 0)
            summary["totalBanned"] += status.get("totalBanned", 0)
            summary["currentlyFailed"] += status.get("currentlyFailed", 0)
            summary["totalFailed"] += status.get("totalFailed", 0)
        if item["active"] or item["enabled"]:
            jails.append(item)

    jails.sort(key=lambda entry: (not entry["active"], not entry["enabled"], entry["name"]))

    return {
        "app": {"name": APP_NAME, "version": app_version()},
        "fail2ban": info,
        "system": f2b.system_info(),
        "summary": summary,
        "jails": jails,
        "config": {
            "managedFile": jailconf.MANAGED_FILE,
            "managedExists": os.path.isfile(jailconf.MANAGED_FILE),
            "files": files,
        },
        "logFile": f2b.log_file(),
    }


def _ip_sort_key(ip: str):
    parts = str(ip).split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return tuple(int(part) for part in parts)
    return (999, 999, 999, 999)


def h_bans(handler, params, body):
    """汇总所有运行中 Jail 当前封禁的 IP，供「全部 Jail」视图一处查看与解封。"""
    info = f2b.info()
    names = f2b.jail_names() if info.get("running") else []
    merged, _ = jailconf.load_merged()
    # 封禁时刻来自 fail2ban 日志：每个请求只解析一次，并由 (inode, mtime, size) 缓存
    log_bans = banlog.collect() if names else {}
    by_ip: dict[str, dict] = {}
    entries: list[dict] = []
    jails: list[dict] = []

    for name in names:
        values = jailconf.options_to_values(jailconf.effective_jail(name, merged))
        bantime = _ban_seconds(values)
        status = f2b.jail_status(name)
        if not status.get("ok"):
            jails.append({"name": name, "ok": False, "count": 0, "bantime": bantime,
                          "error": status.get("error") or "读取失败"})
            continue
        ips = status.get("bannedIps") or []
        jails.append({"name": name, "ok": True, "count": len(ips), "bantime": bantime})
        jail_bans = log_bans.get(name) or {}
        permanent = bantime is not None and bantime <= 0
        for ip in ips:
            item = by_ip.get(ip)
            if item is None:
                item = {"ip": ip, "jails": []}
                by_ip[ip] = item
                entries.append(item)
            if name not in item["jails"]:
                item["jails"].append(name)
            # 「永久」要求这个 IP 命中的每一个 Jail 的 bantime 都明确是 -1。
            # 只要有一个认不出来（None）或算得出到期时间，就不是永久。
            if bantime != -1:
                item["_permanent"] = False
            banned_at = (jail_bans.get(ip) or {}).get("bannedAt")
            if not isinstance(banned_at, int) or banned_at <= 0:
                continue
            # 「封禁时间」取最早的那次封禁
            if "bannedAt" not in item or banned_at < item["bannedAt"]:
                item["bannedAt"] = banned_at
            # 「到期时间」取最早会解封的那次（各 Jail 各算各的，再取最小值）
            if bantime is not None and not permanent:
                expires_at = banned_at + bantime
                if "expiresAt" not in item or expires_at < item["expiresAt"]:
                    item["expiresAt"] = expires_at

    for item in entries:
        # 只有「bantime 全明确为 -1、且确实算不出 expiresAt」才标永久；
        # 取不到封禁时间的 IP 不标 permanent —— 那是「不知道」，不是「永久」。
        if item.pop("_permanent", True) and "expiresAt" not in item:
            item["permanent"] = True

    entries.sort(key=lambda item: _ip_sort_key(item["ip"]))
    return {"running": bool(info.get("running")), "total": len(entries),
            "entries": entries, "jails": jails}


def h_jails(handler, params, body):
    scope = (params.get("scope") or "enabled").lower()
    merged, files = jailconf.load_merged()
    plain, _ = jailconf.load_merged(exclude_managed=True)
    managed = jailconf.managed_sections()
    preset = jailconf.preset_jail_names(merged)
    active = set(f2b.jail_names()) if f2b.ping() else set()
    with_status = _bool(params.get("withStatus", "0"))
    bans = banlog.collect() if (with_status and active) else None

    items = []
    for name in jailconf.all_jail_names(merged):
        item = _jail_view(name, merged, managed, preset, active, with_status=with_status,
                          plain=plain, bans=bans)
        if scope == "active" and not item["active"]:
            continue
        if scope == "managed" and not item["managed"]:
            continue
        if scope == "enabled" and not (item["enabled"] or item["active"]):
            continue
        items.append(item)

    items.sort(key=lambda entry: (not entry["active"], entry["name"]))
    return {"jails": items, "files": files, "managedFile": jailconf.MANAGED_FILE}


def h_jail_get(handler, params, body):
    name = params["name"]
    merged, _ = jailconf.load_merged()
    if name not in merged:
        raise ApiError(f"Jail “{name}” 不存在", 404)
    plain, _ = jailconf.load_merged(exclude_managed=True)
    managed = jailconf.managed_sections()
    preset = jailconf.preset_jail_names(merged)
    active = set(f2b.jail_names()) if f2b.ping() else set()
    bans = banlog.collect() if active else {}
    item = _jail_view(name, merged, managed, preset, active, with_status=True,
                      plain=plain, bans=bans)
    item["raw"] = override = managed.get(name, {})
    return item


def h_jail_create(handler, params, body):
    name = str(body.get("name", "")).strip()
    if not jailconf.NAME_RE.match(name):
        raise ApiError("Jail 名称只能包含字母、数字、点、下划线与短横线")
    merged, _ = jailconf.load_merged()
    if name in merged:
        raise ApiError(f"Jail “{name}” 已存在", 409)
    values = body.get("values") or {}
    values.setdefault("enabled", "true")
    jailconf.update_section(name, values)
    reloaded, error = _try_reload(body)
    return {"ok": True, "name": name, "reloaded": reloaded, "reloadError": error}


def h_jail_update(handler, params, body):
    name = params["name"]
    if not jailconf.NAME_RE.match(name):
        raise ApiError("非法的 Jail 名称")
    values = body.get("values") or {}
    jailconf.update_section(name, values, remove=body.get("remove") or [])
    reloaded, error = _try_reload(body)
    return {"ok": True, "name": name, "reloaded": reloaded, "reloadError": error}


def h_jail_delete(handler, params, body):
    name = params["name"]
    merged, _ = jailconf.load_merged()
    managed = jailconf.managed_sections()
    if name not in merged and name not in managed:
        raise ApiError(f"Jail “{name}” 不存在", 404)
    was_active = name in (f2b.jail_names() if f2b.ping() else [])
    if was_active:
        try:
            f2b.stop_jail(name)
        except f2b.Fail2banError:
            pass
    jailconf.delete_section(name)
    reloaded, error = _try_reload(body)
    return {"ok": True, "name": name, "reloaded": reloaded, "reloadError": error}


def h_jail_actions(handler, params, body):
    name = params["name"]
    action = str(body.get("action", "")).strip()
    ip = str(body.get("ip", "")).strip()

    if action == "start":
        f2b.start_jail(name)
        return {"ok": True, "message": f"Jail {name} 已启动"}
    if action == "stop":
        f2b.stop_jail(name)
        return {"ok": True, "message": f"Jail {name} 已停止"}
    if action == "ban":
        if not ip:
            raise ApiError("请填写要封禁的 IP")
        f2b.ban(name, ip)
        return {"ok": True, "message": f"已封禁 {ip}"}
    if action == "unban":
        if not ip:
            raise ApiError("请填写要解封的 IP")
        f2b.unban(name, ip)
        return {"ok": True, "message": f"已解封 {ip}"}
    if action == "unban-all":
        removed = f2b.unban_all(name)
        return {"ok": True, "message": f"已解封 {len(removed)} 个 IP", "removed": removed}
    if action == "status":
        return f2b.jail_status(name)
    raise ApiError(f"不支持的操作：{action}")


# ---------------------------------------------------------------------------
# 单个 Jail 的日志尾部
# ---------------------------------------------------------------------------

LOG_MAX_FILES = 8
LOG_MAX_BYTES = 32 * 1024 * 1024
LOG_MAX_LINES = 2000
LOG_READ_CHUNK = 256 * 1024
# 允许读取的目录白名单：模板里出现过、以及 fail2ban 自己的日志位置。
# 面板以 root 运行，所以即使调用方给了绝对路径也必须落在这些目录里。
LOG_ALLOWED_ROOTS = (
    "/var/log",
    "/var/lib/fail2ban",
    "/usr/local/etc/fn-knock/logs",  # fn-knock 网关日志
)

# Docker 容器日志单独按「具体文件」放行，不整棵放开 /var/lib/docker ——
# 那底下还有 overlay2、volumes 等，递归可读等于把宿主 docker 数据面暴露出去。
# 只认 <64 位 hex 容器ID>/<64 位 hex>-json.log 这一种形态。
_CONTAINER_ID = r"[0-9a-fA-F]{64}"
LOG_ALLOWED_FILE_PATTERNS = (
    re.compile("^/var/lib/docker/containers/" + _CONTAINER_ID + "/" + _CONTAINER_ID
               + r"-json\.log(?:\.[0-9]+)?$"),
)
# 通配符展开时允许「枚举」的目录：只到容器这一层，且最终仍要按上面的文件正则复核，
# 所以即使写 /var/lib/docker/containers/*/*-json.log 也不会读到容器日志以外的内容。
LOG_ALLOWED_DIR_PATTERNS = (
    re.compile(r"^/var/lib/docker/containers$"),
    re.compile(r"^/var/lib/docker/containers/" + _CONTAINER_ID + r"$"),
)


def _log_roots() -> tuple[str, ...]:
    """允许读取的根目录（realpath 归一化后比较）。

    面板以 root 运行，所以这里绝不能只靠调用方传进来的路径就开读：
    必须是这些根目录之一底下的普通文件。
    """
    roots = list(LOG_ALLOWED_ROOTS)
    extra = os.environ.get("F2B_LOG_ROOTS") or ""
    roots.extend(item for item in re.split(r"[,\s]+", extra) if item)
    return tuple(os.path.realpath(root) for root in roots)


def _split_logpaths(value) -> list[str]:
    """把 logpath 拆成多个路径：fail2ban 用空格分隔，也兼容逗号/分号。

    带空格的路径可以用引号包起来（"…" 或 ‘…’），此时整段算一个路径。
    """
    text = str(value or "").strip()
    if not text:
        return []
    pieces: list[str] = []
    buffer: list[str] = []
    quote = ""
    for char in text:
        if quote:
            if char == quote:
                quote = ""
            else:
                buffer.append(char)
            continue
        if char in ("\"", chr(0x201C), chr(0x2018)):
            quote = "\"" if char == "\"" else (chr(0x201D) if char == chr(0x201C) else chr(0x2019))
            continue
        if char.isspace() or char in ",;":
            if buffer:
                pieces.append("".join(buffer))
                buffer = []
            continue
        buffer.append(char)
    if buffer:
        pieces.append("".join(buffer))
    return [piece for piece in pieces if piece]


# ---------------------------------------------------------------------------
# logpath 的 %(name)s 插值
# ---------------------------------------------------------------------------
#
# fail2ban 的 Jail 极少把 logpath 写死，用的都是变量引用，例如：
#   jail.conf         [sshd]     logpath = %(sshd_log)s
#   paths-common.conf            sshd_log = %(syslog_authpriv)s
#   paths-common.conf            syslog_authpriv = /var/log/auth.log
# 不插值就只会拿到字面量 "%(sshd_log)s"，系统自带的 Jail 全都看不了日志，
# 所以必须在安全校验之前把变量还原成真实路径。

_LOG_VAR_RE = re.compile(r"%\((?P<name>[^)]{1,128})\)s")
LOG_VAR_MAX_DEPTH = 8             # 递归上限，防止 a=%(b)s / b=%(a)s 这类循环引用
LOG_VAR_MAX_LENGTH = 8192         # 插值结果的兜底长度，异常时退回原值
# 变量来源文件，按 fail2ban 的加载顺序排列（后者覆盖前者）
LOG_VAR_FILES = (
    "paths-common.conf",
    "paths-debian.conf",
    "paths-overrides.local",
)


def _log_variables(jail: str, merged: dict) -> dict:
    """收集可用于插值的变量，优先级由低到高。"""
    variables: dict[str, str] = {}
    for filename in LOG_VAR_FILES:
        path = os.path.join(jailconf.SYS_ETC, filename)
        if not os.path.isfile(path):
            continue
        for section, key, value, _ in jailconf.parse_file(path):
            if section == "DEFAULT":
                variables[key] = value
    # jail.conf 的 [DEFAULT] 覆盖系统路径变量，Jail 自己的段优先级最高
    for section in ("DEFAULT", jail):
        for key, info in (merged.get(section) or {}).items():
            value = info.get("value") if isinstance(info, dict) else None
            if isinstance(value, str):
                variables[key] = value
    return variables


def _resolve_variable(name: str, variables: dict, depth: int,
                      chain: tuple[str, ...] = ()) -> str | None:
    """递归解析单个变量，解不出来返回 None。

    ``chain`` 记录当前解析链上的变量名：a 引用了 b、b 又引用 a 时必须立刻判定
    为循环并放弃替换，否则每一层都会把字面量再替换一遍，字符串指数级膨胀
    （实测 a=%(b)s / b=%(a)s 会炸成成百上千个 %）。
    """
    if depth > LOG_VAR_MAX_DEPTH or name in chain:
        return None
    value = variables.get(name)
    if value is None:
        return None
    nested = chain + (name,)
    return _LOG_VAR_RE.sub(
        lambda match: _resolve_or_literal(match, variables, depth + 1, nested), value)


def _resolve_or_literal(match: re.Match, variables: dict, depth: int,
                        chain: tuple[str, ...] = ()) -> str:
    """替换回调：能解析就替换，解析不出来（含循环引用）就原样保留。"""
    resolved = _resolve_variable(match.group("name"), variables, depth, chain)
    if resolved is None:
        return match.group(0)
    return resolved


def _interpolate_logpath(raw: str, jail: str, merged: dict) -> str:
    """把 logpath 里的 %(name)s 展开成真实路径。

    只做还原，不做任何放行判断；展开后仍不是绝对路径的，交给
    ``_expand_logpath`` 按原有规则拒绝。解析不出来的变量保留原样。
    """
    text = str(raw or "")
    if "%(" not in text:
        return text
    variables = _log_variables(jail, merged)
    result = _LOG_VAR_RE.sub(
        lambda match: _resolve_or_literal(match, variables, 0, ()), text)
    # 兜底：真出现异常膨胀时退回原值，绝不把超长字符串带进后续处理
    if len(result) > LOG_VAR_MAX_LENGTH:
        return text
    return result


def _expand_logpath(candidate: str) -> tuple[list[str], list[str]]:
    """展开单个 logpath，返回 (可读文件, 不存在的路径)。

    安全校验**先做**、存在性判断后做：路径不在白名单里时直接拒绝，不能因为
    「文件恰好不存在」就放过 —— 那会让校验形同虚设。
    """
    if not candidate:
        return [], []
    if not os.path.isabs(candidate):
        raise ApiError(f"logpath 里出现了相对路径（{candidate}），出于安全考虑不予读取", 400)
    expanded = False
    if any(char in candidate for char in "*?["):
        # 通配符：允许枚举的父目录先过一遍，展开出来的每个文件再逐个复核
        expanded = True
        parent = os.path.realpath(os.path.dirname(candidate) or "/")
        if not _may_enter_dir(parent):
            raise ApiError(f"日志路径不在允许的目录范围内：{candidate}", 400)
        matched = sorted(glob.glob(candidate))
    elif os.path.isdir(candidate):
        # fail2ban 允许 logpath 指向目录，此时取目录下的文件
        expanded = True
        real_dir = os.path.realpath(candidate)
        if not _may_enter_dir(real_dir):
            raise ApiError(f"日志路径不在允许的目录范围内：{candidate}", 400)
        try:
            matched = sorted(os.path.join(real_dir, name) for name in os.listdir(real_dir))
        except OSError:
            matched = []
    else:
        matched = [candidate]

    files: list[str] = []
    missing: list[str] = []
    for path in matched:
        real = os.path.realpath(path)   # 先按原路径归一化，符号链接指向哪里一查便知
        if not _under_roots(real):
            if expanded:
                # 通配符/目录展开出来的、不在白名单里的条目不读就是了，
                # 不该因为目录里混了别的文件就让整个请求失败。
                continue
            raise ApiError(f"日志路径不在允许的目录范围内：{path}", 400)
        if not os.path.isfile(real):
            missing.append(path)
            continue
        try:
            size = os.stat(real).st_size
        except OSError:
            missing.append(path)
            continue
        if size > LOG_MAX_BYTES:
            raise ApiError(
                f"日志文件过大（{path}，{size // (1024 * 1024)}MB），超过 "
                f"{LOG_MAX_BYTES // (1024 * 1024)}MB 上限", 400)
        files.append(real)
    return files, missing


def _under_roots(real: str) -> bool:
    """real 是否允许读取。

    目录白名单用前缀判断；Docker 容器日志则必须**整条路径**匹配具体形态，
    不能靠前缀糊过去 —— /var/lib/docker 底下还有 overlay2、volumes 等。
    """
    normalized = str(real).replace(os.sep, "/")
    for root in _log_roots():
        root_norm = str(root).replace(os.sep, "/").rstrip("/")
        if normalized == root_norm or normalized.startswith(root_norm + "/"):
            return True
    for pattern in LOG_ALLOWED_FILE_PATTERNS:
        if pattern.match(normalized):
            return True
    return False


def _may_enter_dir(real: str) -> bool:
    """通配符 / 目录展开时，是否允许枚举这个目录里的内容。"""
    if _under_roots(real):
        return True
    normalized = str(real).replace(os.sep, "/").rstrip("/")
    for pattern in LOG_ALLOWED_DIR_PATTERNS:
        if pattern.match(normalized):
            return True
    return False


def _tail_lines(path: str, limit: int) -> list[str]:
    """读文件尾部最多 limit 行（只读尾部，不把整个文件读进内存）。"""
    if limit <= 0:
        return []
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, max(LOG_READ_CHUNK, limit * 512))
            handle.seek(size - block)
            raw = handle.read()
    except OSError as exc:
        raise ApiError(f"读取日志失败：{exc}", 400) from exc
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if block < size and lines:
        # 从文件中间读起时，第一行可能是半行
        lines = lines[1:]
    return lines[-limit:]


def h_jail_log(handler, params, body):
    """GET /api/jails/<name>/log?lines=200 —— 读该 Jail 的 logpath 尾部。

    只读：不打日志、不写文件、不改配置。任何越界或文件不存在都回 400/404，
    不把底层异常抛成 500。
    """
    name = params["name"]
    if not jailconf.NAME_RE.match(name):
        raise ApiError("非法的 Jail 名称", 400)
    try:
        lines = int(params.get("lines") or 200)
    except (TypeError, ValueError):
        lines = 200
    lines = max(1, min(lines, LOG_MAX_LINES))

    merged, _ = jailconf.load_merged()
    if name not in merged:
        raise ApiError(f"Jail “{name}” 不存在", 404)
    values = jailconf.options_to_values(jailconf.effective_jail(name, merged))
    raw_logpath = values.get("logpath")
    if not str(raw_logpath or "").strip():
        raise ApiError(f"Jail “{name}” 没有配置 logpath，无法查看日志", 400)
    # 先把 %(name)s 还原成真实路径，再按空格/逗号拆成多个文件；
    # 有的变量本身就展开成多条路径（例如 suhosin_log），所以顺序不能反。
    resolved = _interpolate_logpath(raw_logpath, name, merged)
    candidates = _split_logpaths(resolved)
    if not candidates:
        raise ApiError(f"Jail “{name}” 的 logpath 插值后为空，无法查看日志", 400)

    files: list[str] = []
    missing: list[str] = []
    for candidate in candidates:
        found, absent = _expand_logpath(candidate)
        for path in found:
            if path not in files:
                files.append(path)
        for path in absent:
            if path not in missing:
                missing.append(path)
    if not files:
        raise ApiError(f"Jail “{name}” 配置的日志文件都不存在：" + "、".join(missing), 404)
    if len(files) > LOG_MAX_FILES:
        raise ApiError(
            f"Jail “{name}” 的日志文件有 {len(files)} 个，超过 {LOG_MAX_FILES} 个上限", 400)

    # 多个文件时按条数均分（至少 20 行），保证每个文件都有内容且总量可控
    if len(files) == 1:
        budgets = [lines]
    else:
        per_file = max(20, lines // len(files))
        budgets = [per_file] * len(files)

    collected: list[str] = []
    truncated = False
    for path, budget in zip(files, budgets):
        got = _tail_lines(path, budget)
        if len(got) >= budget:
            truncated = True
        collected.extend(got)
    if len(collected) > lines:
        collected = collected[-lines:]
        truncated = True

    return {
        "jail": name,
        "files": files,
        "lines": collected,
        "truncated": truncated,
        "missing": missing,
    }


def h_filters(handler, params, body):
    return {
        "filters": jailconf.list_filters(),
        "filterDir": jailconf.FILTER_D,
        "templates": filter_templates.list_templates(),
    }


def h_filter_get(handler, params, body):
    name = params["name"]
    if not jailconf.NAME_RE.match(name):
        raise ApiError("非法的过滤器名称")
    data = jailconf.read_filter(name)
    if not data["basePath"] and not data["localPath"]:
        raise ApiError(f"过滤器 “{name}” 不存在", 404)
    return data


def h_filter_put(handler, params, body):
    name = params["name"]
    try:
        path = jailconf.write_filter_local(
            name,
            body.get("failregex") or [],
            body.get("ignoreregex") or [],
            body.get("definition") or {},
        )
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    reloaded, error = _try_reload(body)
    return {"ok": True, "name": name, "path": path, "reloaded": reloaded, "reloadError": error}


def h_filter_delete(handler, params, body):
    name = params["name"]
    if not jailconf.NAME_RE.match(name):
        raise ApiError("非法的过滤器名称")
    removed = jailconf.delete_filter_local(name)
    if not removed:
        raise ApiError(f"过滤器 “{name}” 没有由本应用创建的自定义版本", 404)
    reloaded, error = _try_reload(body)
    return {"ok": True, "name": name, "reloaded": reloaded, "reloadError": error}


def h_settings_get(handler, params, body):
    merged, files = jailconf.load_merged()
    plain, _ = jailconf.load_merged(exclude_managed=True)
    managed = jailconf.managed_sections()
    return {
        "values": jailconf.options_to_values(merged.get("DEFAULT", {})),
        "inherited": jailconf.options_to_values(plain.get("DEFAULT", {})),
        "override": managed.get("DEFAULT", {}),
        "fields": jailconf.GLOBAL_FIELDS,
        "files": files,
        "managedFile": jailconf.MANAGED_FILE,
    }


def h_settings_put(handler, params, body):
    values = body.get("values") or {}
    jailconf.update_section("DEFAULT", values, remove=body.get("remove") or [])
    reloaded, error = _try_reload(body)
    return {"ok": True, "reloaded": reloaded, "reloadError": error}


def h_service_get(handler, params, body):
    return f2b.info()


def h_service_post(handler, params, body):
    action = str(body.get("action", "")).strip()
    if action == "reload":
        message = f2b.reload()
    elif action == "restart":
        message = f2b.restart()
    else:
        message = f2b.service_action(action)
        if action in ("start", "restart"):
            f2b.wait_for_service()
    return {"ok": True, "message": message, "info": f2b.info()}


def h_install(handler, params, body):
    result = f2b.install_package()
    result["info"] = f2b.info()
    if not result.get("ok"):
        raise ApiError(result.get("message") or "安装失败", 500)
    return result


def h_logs(handler, params, body):
    try:
        lines = int(params.get("lines") or 400)
    except (TypeError, ValueError):
        lines = 400
    lines = max(20, min(lines, 5000))
    keyword = str(params.get("keyword") or "").strip()
    return f2b.log_tail(lines=lines, keyword=keyword)


def h_prefs_get(handler, params, body):
    return {"prefs": store.load_prefs()}


def h_prefs_put(handler, params, body):
    return {"prefs": store.save_prefs(body.get("prefs") or {})}


def h_fnknock_get(handler, params, body):
    return {
        "settings": fnknock.load_settings(),
        "detected": fnknock.public_info(),
        "authModes": fnknock.AUTH_MODES,
        "sources": fnknock.SOURCES,
        "banaction": fnknock.action_status(),
        "gateway": fnknock.gateway_status(),
        "log": fnknock.log_entries(limit=60),
        "fail2ban": {"installed": f2b.installed(), "running": f2b.ping()},
    }


def h_fnknock_put(handler, params, body):
    before = fnknock.load_settings()
    settings = fnknock.save_settings(body.get("settings") or {})

    turned_on = not before.get("banViaFnknock") and settings.get("banViaFnknock")
    turned_off = before.get("banViaFnknock") and not settings.get("banViaFnknock")
    switch_changed = bool(turned_on or turned_off or (
        settings.get("banViaFnknock")
        and bool(before.get("keepFirewall", False)) != bool(settings.get("keepFirewall", False))))

    applied = None
    gateway = None
    # 「启用 fn-knock 联动」开着时每次保存都重新落盘：
    # 既保证 action 文件与 banaction 一致，也保证网关过滤器与 Jail 齐全。
    if switch_changed or settings.get("banViaFnknock"):
        try:
            applied = fnknock.apply_ban_action(settings)
        except (OSError, ValueError) as exc:
            raise ApiError(f"写入 fail2ban 封禁动作失败：{exc}", 500) from exc
        try:
            gateway = fnknock.apply_gateway_jail(settings)
        except (OSError, ValueError) as exc:
            raise ApiError(f"写入敲门网关的过滤器 / Jail 失败：{exc}", 500) from exc
        settings = fnknock.load_settings()

    reloaded, error = (None, None)
    if switch_changed:
        reloaded, error = _try_reload(body)

    return {
        "ok": True,
        "settings": settings,
        "detected": fnknock.public_info(force=True),
        "banaction": fnknock.action_status(),
        "gateway": fnknock.gateway_status(),
        "applied": applied,
        "gatewayApplied": gateway,
        "reloaded": reloaded,
        "reloadError": error,
    }


def h_fnknock_test(handler, params, body):
    draft = body.get("settings")
    target = None
    if isinstance(draft, dict) and draft:
        target = fnknock.load_settings()
        for key in ("baseUrl", "authMode", "authHeader", "authValue", "source", "comment", "timeout"):
            if key in draft:
                target[key] = draft[key]
    try:
        result = fnknock.test(target)
    except fnknock.FnknockError as exc:
        raise ApiError(f"连接失败：{exc}", 502) from exc
    fnknock.record("test", "", [], True, f"连接正常：{result.get('baseUrl')}")
    return result


def h_fnknock_blacklist(handler, params, body):
    try:
        page = max(1, int(params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = max(1, min(int(params.get("limit") or 20), 100))
    except (TypeError, ValueError):
        limit = 20
    try:
        data = fnknock.list_blacklist(page=page, limit=limit,
                                      search=str(params.get("search") or "").strip())
    except fnknock.FnknockError as exc:
        raise ApiError(f"读取 fn-knock 黑名单失败：{exc}", 502) from exc
    return {"page": page, "limit": limit, "data": data}


def h_fnknock_sync(handler, params, body):
    action = str(body.get("action") or "").strip()
    settings = fnknock.load_settings()
    ips = fnknock.normalize_ips(body.get("ips") or [])
    comment = str(body.get("comment") or settings.get("comment") or "fail2ban").strip() or "fail2ban"

    try:
        if action == "push":
            if not ips:
                raise ApiError("请填写至少一个合法的 IP 地址")
            fnknock.add_ips(ips, comment=comment, settings=settings)
            fnknock.record("manual-push", "", ips, True, "手动加入 fn-knock 黑名单")
            return {"ok": True, "message": f"已把 {len(ips)} 个 IP 加入 fn-knock 黑名单"}

        if action == "remove":
            if not ips:
                raise ApiError("请填写至少一个合法的 IP 地址")
            fnknock.remove_ips(ips, settings=settings)
            fnknock.record("manual-remove", "", ips, True, "手动从 fn-knock 黑名单移除")
            return {"ok": True, "message": f"已从 fn-knock 黑名单移除 {len(ips)} 个 IP"}

        if action == "remove-one":
            if not ips:
                raise ApiError("请提供要移除的 IP")
            fnknock.remove_ip(ips[0], settings=settings)
            fnknock.record("manual-remove", "", ips[:1], True, "手动从 fn-knock 黑名单移除")
            return {"ok": True, "message": f"已从 fn-knock 黑名单移除 {ips[0]}"}

        if action == "push-all":
            collected = []
            for jail in f2b.jail_names():
                status = f2b.jail_status(jail)
                if status.get("ok"):
                    collected.extend(status.get("bannedIps") or [])
            all_ips = fnknock.normalize_ips(collected)
            if not all_ips:
                return {"ok": True, "message": "当前没有正在封禁的 IP，无需同步"}
            for index in range(0, len(all_ips), 100):
                fnknock.add_ips(all_ips[index:index + 100], comment=comment, settings=settings)
            fnknock.record("manual-push", "", all_ips, True, "全量推送当前封禁")
            return {"ok": True, "message": f"已同步 {len(all_ips)} 个当前封禁 IP 到 fn-knock"}

        raise ApiError(f"不支持的操作：{action}")
    except fnknock.FnknockError as exc:
        kind = "manual-remove" if action.startswith("remove") else "manual-push"
        fnknock.record(kind, "", ips, False, str(exc))
        raise ApiError(f"操作失败：{exc}", 502) from exc


def h_fnknock_log_clear(handler, params, body):
    fnknock.clear_log()
    return {"ok": True}


def h_backups(handler, params, body):
    import glob

    items = []
    for path in sorted(glob.glob(os.path.join(store.BACKUP_DIR, "*.bak")), reverse=True)[:100]:
        try:
            stat = os.stat(path)
        except OSError:
            continue
        items.append({"name": os.path.basename(path), "size": stat.st_size,
                      "mtime": int(stat.st_mtime)})
    return {"backups": items, "dir": store.BACKUP_DIR}


def h_notifications_get(handler, params, body):
    # 密钥字段一律打码成 "__SET__"：浏览器永远拿不到真实凭证，前端据此显示
    # 「已保存，留空保留原值」，提交时原样回传也不会覆盖磁盘上的真值。
    return {
        "settings": notify.masked_settings(),
        "channelTypes": notify.CHANNEL_TYPES,
        "eventTypes": notify.EVENT_TYPES,
        "events": notify.list_events(limit=60),
        "deliveries": delivery.list_items(status="all", limit=60),
        "fail2ban": {
            "installed": f2b.installed(),
            "running": f2b.ping(),
        },
    }


def h_notifications_put(handler, params, body):
    # save_settings 的返回值本身已经是打码后的配置（见 notify 模块说明），
    # 直接回给前端即可；再套一层 mask 是多余的，漏套一次反而会泄密。
    settings = notify.save_settings(body.get("settings") or {})
    return {"ok": True, "settings": settings}


def h_notifications_test(handler, params, body):
    # 允许直接测试尚未保存的草稿渠道（前端传 channel），否则按 channelId 取已保存的。
    # 走 test_channel：编辑已保存渠道时前端拿到的是打码草稿，只有它能按 id 把密钥补齐。
    channel = body.get("channel")
    if not isinstance(channel, dict) or not channel.get("type"):
        channel_id = body.get("channelId")
        saved = notify.load_settings()
        channel = next((item for item in saved["channels"] if item["id"] == channel_id), None)
    if not channel:
        raise ApiError("找不到该通知渠道", 404)
    if not notify.channel_ready(channel):
        raise ApiError("渠道信息不完整，请先填写必填项")
    ok, detail, record = notify.test_channel(channel)
    notify.record_event(
        "test",
        "测试通知" + ("发送成功" if ok else "发送失败"),
        f"渠道：{channel.get('name') or channel.get('type')}｜{detail}",
    )
    if not ok:
        raise ApiError(f"发送失败：{detail}", 502)
    return {"ok": True, "detail": detail, "delivery": record}


def h_notifications_events(handler, params, body):
    try:
        since = int(params.get("since") or 0)
    except (TypeError, ValueError):
        since = 0
    return {"events": notify.list_events(since=since, limit=50)}


def h_notifications_clear(handler, params, body):
    notify.clear_events()
    return {"ok": True}


def h_backup_get(handler, params, body):
    settings = backup.load_settings()
    return {
        "settings": settings,
        "locations": backup.list_locations(),
        "directory": backup.resolve_directory(settings),
        "defaultDirectory": backup.resolve_directory({"directory": ""}),
        "backups": backup.list_backups(settings),
    }


def h_backup_put(handler, params, body):
    try:
        settings = backup.save_settings(body.get("settings") or {})
    except ValueError as exc:
        raise ApiError(str(exc)) from exc
    return {
        "ok": True,
        "settings": settings,
        "directory": backup.resolve_directory(settings),
        "backups": backup.list_backups(settings),
    }


def h_backup_run(handler, params, body):
    try:
        result = backup.create_backup("manual")
    except (OSError, ValueError) as exc:
        backup.record_failure(f"备份失败：{exc}")
        raise ApiError(f"备份失败：{exc}", 500) from exc
    result["backups"] = backup.list_backups()
    return result


def h_backup_delete(handler, params, body):
    name = str(body.get("name") or "")
    if not backup.delete_backup(name):
        raise ApiError("找不到该备份文件", 404)
    return {"ok": True, "backups": backup.list_backups()}


def h_backup_download(handler, params, body):
    name = str(params.get("name") or "")
    path = backup.find_backup(name)
    if not path:
        raise ApiError("找不到该备份文件", 404)
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise ApiError(f"读取备份失败：{exc}", 500) from exc
    filename = os.path.basename(path)
    return RawResponse(200, "application/gzip", data, {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Cache-Control": "no-store",
    })


# ---------------------------------------------------------------------------
# 投递记录 / 存储 / 更新（26.11.5 新增）
# ---------------------------------------------------------------------------

def h_deliveries(handler, params, body):
    status = str(params.get("status") or "all")
    try:
        limit = int(params.get("limit") or 60)
    except (TypeError, ValueError):
        limit = 60
    return delivery.list_items(status=status, limit=limit)


def h_deliveries_clear(handler, params, body):
    delivery.clear()
    return {"ok": True}


def h_storage(handler, params, body):
    import backup as backup_mod

    return storage.report(
        store.VAR_DIR,
        current_version=app_version(),
        backups=backup_mod.list_backups(),
        keep=backup_mod.load_settings().get("keep"),
    )


def h_storage_cleanup(handler, params, body):
    target = str(body.get("target") or "all").strip().lower()
    if target not in ("logs", "backups", "all"):
        raise ApiError("未知的清理目标")
    keep = body.get("keep")
    try:
        keep = int(keep) if keep is not None and str(keep).strip() != "" else None
    except (TypeError, ValueError):
        keep = None
    result = storage.cleanup(store.VAR_DIR, target=target, keep=keep,
                             current_version=app_version())
    if not result.get("ok") and result.get("busy"):
        # 升级正在下载或安装：这时删文件有可能删到正在写的那一个，整体拒绝。
        # 用 409 而不是 200 + 失败标记，前端才能把"忙"和其它失败分开处理。
        return RawResponse(409, "application/json; charset=utf-8",
                           json.dumps({"error": "升级正在进行，暂时不能清理",
                                       "busy": True, "target": target},
                                      ensure_ascii=False).encode("utf-8"),
                           {"Cache-Control": "no-store"})
    if not result.get("ok"):
        raise ApiError(result.get("error") or "清理失败")
    result["storage"] = h_storage(handler, params, body)
    return result


def h_update(handler, params, body):
    force = _bool(params.get("force"))
    current = app_version()
    if force:
        payload = update.check(current, force=True)
    else:
        payload = update.cached(current)
        if not payload or payload.get("reason") == "not_checked":
            payload = update.check(current)
    return payload


def h_update_install(handler, params, body):
    info = update.cached(app_version())
    if not info.get("has_update"):
        info = update.check(app_version(), force=True)
    if not info.get("has_update"):
        raise ApiError("当前没有可用更新")
    # 资产挑选交给 installer：它知道平台后缀与"绝不下载非 .fpk"这两条规则，
    # 在处理器里重写一遍等于给"卸载完才发现包不对"留了个口子。
    asset = installer.pick_asset(info)
    if not asset:
        raise ApiError("该版本没有可下载的安装包", 404)
    # 带上当前生效的 DEFAULT 策略：向导字段要用它回填，否则一次升级会把
    # bantime / maxretry / ignoreip 退回内置默认值。
    policy = installer.current_policy()
    result = installer.install(
        version=str(info.get("latest") or ""),
        url=asset.get("url") or "",
        var_dir=store.VAR_DIR,
        current=app_version(),
        settings=policy,
        log=lambda msg: None,
    )
    if not result.get("ok"):
        raise ApiError(result.get("error") or "无法开始升级", 409)
    return result


def h_update_progress(handler, params, body):
    return installer.progress()

def _try_reload(body) -> tuple[bool, str | None]:
    if body is not None and body.get("reload") is False:
        return False, None
    if not f2b.installed():
        return False, "系统未安装 fail2ban"
    try:
        f2b.reload()
        return True, None
    except f2b.Fail2banError as exc:
        return False, str(exc)


ROUTES = [
    ("GET", re.compile(r"^/api/health$"), h_health, False),
    ("GET", re.compile(r"^/api/meta$"), h_meta, False),
    ("GET", re.compile(r"^/api/session$"), h_session, False),

    ("GET", re.compile(r"^/api/overview$"), h_overview, False),
    ("GET", re.compile(r"^/api/service$"), h_service_get, False),
    ("POST", re.compile(r"^/api/service$"), h_service_post, False),
    ("POST", re.compile(r"^/api/install$"), h_install, False),

    ("GET", re.compile(r"^/api/bans$"), h_bans, False),
    ("GET", re.compile(r"^/api/jails$"), h_jails, False),
    ("POST", re.compile(r"^/api/jails$"), h_jail_create, False),
    ("GET", re.compile(r"^/api/jails/(?P<name>[A-Za-z0-9._-]+)/log$"), h_jail_log, False),
    ("GET", re.compile(r"^/api/jails/(?P<name>[A-Za-z0-9._-]+)$"), h_jail_get, False),
    ("PUT", re.compile(r"^/api/jails/(?P<name>[A-Za-z0-9._-]+)$"), h_jail_update, False),
    ("DELETE", re.compile(r"^/api/jails/(?P<name>[A-Za-z0-9._-]+)$"), h_jail_delete, False),
    ("POST", re.compile(r"^/api/jails/(?P<name>[A-Za-z0-9._-]+)/actions$"), h_jail_actions, False),

    ("GET", re.compile(r"^/api/filters$"), h_filters, False),
    ("GET", re.compile(r"^/api/filters/(?P<name>[A-Za-z0-9._-]+)$"), h_filter_get, False),
    ("PUT", re.compile(r"^/api/filters/(?P<name>[A-Za-z0-9._-]+)$"), h_filter_put, False),
    ("DELETE", re.compile(r"^/api/filters/(?P<name>[A-Za-z0-9._-]+)$"), h_filter_delete, False),

    ("GET", re.compile(r"^/api/settings$"), h_settings_get, False),
    ("PUT", re.compile(r"^/api/settings$"), h_settings_put, False),
    ("GET", re.compile(r"^/api/logs$"), h_logs, False),
    ("GET", re.compile(r"^/api/backups$"), h_backups, False),
    ("GET", re.compile(r"^/api/notifications$"), h_notifications_get, False),
    ("PUT", re.compile(r"^/api/notifications$"), h_notifications_put, False),
    ("POST", re.compile(r"^/api/notifications/test$"), h_notifications_test, False),
    ("GET", re.compile(r"^/api/notifications/events$"), h_notifications_events, False),
    ("GET", re.compile(r"^/api/notifications/deliveries$"), h_deliveries, False),
    ("POST", re.compile(r"^/api/notifications/deliveries/clear$"), h_deliveries_clear, False),
    ("POST", re.compile(r"^/api/notifications/events/clear$"), h_notifications_clear, False),

    ("GET", re.compile(r"^/api/backup$"), h_backup_get, False),
    ("PUT", re.compile(r"^/api/backup$"), h_backup_put, False),
    ("POST", re.compile(r"^/api/backup/run$"), h_backup_run, False),
    ("POST", re.compile(r"^/api/backup/delete$"), h_backup_delete, False),
    ("GET", re.compile(r"^/api/backup/download$"), h_backup_download, False),

    ("GET", re.compile(r"^/api/fnknock$"), h_fnknock_get, False),
    ("PUT", re.compile(r"^/api/fnknock$"), h_fnknock_put, False),
    ("POST", re.compile(r"^/api/fnknock/test$"), h_fnknock_test, False),
    ("GET", re.compile(r"^/api/fnknock/blacklist$"), h_fnknock_blacklist, False),
    ("POST", re.compile(r"^/api/fnknock/sync$"), h_fnknock_sync, False),
    ("POST", re.compile(r"^/api/fnknock/log/clear$"), h_fnknock_log_clear, False),

    ("GET", re.compile(r"^/api/storage$"), h_storage, False),
    ("POST", re.compile(r"^/api/storage/cleanup$"), h_storage_cleanup, False),

    ("GET", re.compile(r"^/api/update$"), h_update, False),
    ("POST", re.compile(r"^/api/update/install$"), h_update_install, False),
    ("GET", re.compile(r"^/api/update/progress$"), h_update_progress, False),

    ("GET", re.compile(r"^/api/prefs$"), h_prefs_get, False),
    ("PUT", re.compile(r"^/api/prefs$"), h_prefs_put, False),
]

STRIP_PREFIXES = tuple(dict.fromkeys(
    prefix for prefix in (GATEWAY_PREFIX, f"/app/{APP_NAME}", f"/apps/{APP_NAME}") if prefix
))


# ---------------------------------------------------------------------------
# HTTP 处理
# ---------------------------------------------------------------------------

class PanelHandler(BaseHTTPRequestHandler):
    server_version = f"Fail2banPanel/{APP_VERSION}"
    protocol_version = "HTTP/1.1"

    session_user: str | None = None
    base_path: str = ""

    # -- 工具 ---------------------------------------------------------------

    def log_message(self, fmt, *args):  # noqa: A003
        sys.stderr.write("[%s] %s - %s\n" % (time.strftime("%F %T"), self.address_string(), fmt % args))

    def _normalize(self, path: str) -> str:
        self.base_path = ""
        if not path:
            return "/"
        for prefix in STRIP_PREFIXES:
            if path == prefix:
                self.base_path = prefix
                return "/"
            if path.startswith(prefix + "/"):
                self.base_path = prefix
                return path[len(prefix):]
        return path

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return {}
        if length > MAX_BODY:
            raise ApiError("请求体过大", 413)
        raw = self.rfile.read(length)
        if not raw:
            return {}
        try:
            data = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise ApiError(f"请求体不是合法 JSON：{exc}") from exc
        return data if isinstance(data, dict) else {}

    def _cookies(self) -> SimpleCookie:
        cookie = SimpleCookie()
        raw = self.headers.get("Cookie")
        if raw:
            try:
                cookie.load(raw)
            except Exception:  # noqa: BLE001 - 容忍畸形 Cookie
                pass
        return cookie

    def set_session_cookie(self, token: str, max_age: int = SESSION_TTL) -> None:
        path = (self.base_path or "") + "/"
        parts = [
            f"{COOKIE_NAME}={token}",
            f"Path={path}",
            "HttpOnly",
            "SameSite=Lax",
            f"Max-Age={max_age}",
        ]
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https":
            parts.append("Secure")
        self._extra_headers.append(("Set-Cookie", "; ".join(parts)))

    def _authenticate(self) -> None:
        token = self.headers.get("X-Auth-Token")
        expected = store.api_token()
        if token and expected and token == expected:
            credentials = store.load_credentials() or {}
            self.session_user = credentials.get("username", "api")
            return
        cookie = self._cookies()
        morsel = cookie.get(COOKIE_NAME)
        if morsel:
            self.session_user = store.verify_session(morsel.value)

    def _redirect(self, location: str) -> None:
        self.send_response(301)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()

    def _json(self, status: int, payload) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _send_raw(self, response: "RawResponse") -> None:
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(response.body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in response.headers.items():
            self.send_header(key, value)
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(response.body)

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        relative = urllib.parse.unquote(path.lstrip("/"))
        web_root = os.path.realpath(store.WEB_DIR)
        candidate = os.path.realpath(os.path.join(web_root, relative))
        if candidate != web_root and not candidate.startswith(web_root + os.sep):
            self._json(403, {"error": "非法路径"})
            return
        if not os.path.isfile(candidate):
            # 前端使用 hash 路由，未知路径统一回落到入口页面
            if "." not in os.path.basename(relative):
                candidate = os.path.join(store.WEB_DIR, "index.html")
            else:
                self._json(404, {"error": "文件不存在"})
                return
        try:
            with open(candidate, "rb") as handle:
                data = handle.read()
        except OSError:
            self._json(404, {"error": "文件不存在"})
            return
        content_type, _ = mimetypes.guess_type(candidate)
        if candidate.endswith(".js"):
            content_type = "application/javascript; charset=utf-8"
        elif candidate.endswith(".css"):
            content_type = "text/css; charset=utf-8"
        elif candidate.endswith(".html"):
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        for key, value in self._extra_headers:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _dispatch(self, method: str) -> None:
        self._extra_headers: list[tuple[str, str]] = []
        try:
            # 入口门禁：面板没有登录页，网段校验必须在解析任何请求之前完成
            if not client_allowed(self.client_address[0] if self.client_address else ""):
                self._json(403, {"error": "当前来源地址不在允许访问的网段内"})
                return

            parsed = urllib.parse.urlsplit(self.path)

            # 经统一网关访问 /app/<appname>（缺尾斜杠）时，index.html 中的相对资源
            # 会被解析到上级目录，这里先补一次重定向再提供服务。
            if parsed.path in STRIP_PREFIXES:
                self._redirect(parsed.path + "/" + (("?" + parsed.query) if parsed.query else ""))
                return

            path = self._normalize(parsed.path)
            query = {}
            for key, values in urllib.parse.parse_qs(parsed.query).items():
                query[key] = values[-1] if values else ""

            if path.startswith("/api/"):
                self._authenticate()
            elif path.startswith("/api"):
                path = "/api/" + path[4:].lstrip("/")

            for route_method, pattern, func, need_auth in ROUTES:
                if route_method != method:
                    continue
                match = pattern.match(path)
                if not match:
                    continue
                if need_auth and not self.session_user:
                    self._json(401, {"error": "未登录或会话已过期"})
                    return
                params = dict(match.groupdict())
                params.update(query)
                body = self._read_body() if method in ("POST", "PUT", "PATCH") else {}
                result = func(self, params, body)
                if isinstance(result, RawResponse):
                    self._send_raw(result)
                else:
                    self._json(200, result)
                return

            if path.startswith("/api/"):
                self._json(404, {"error": "接口不存在"})
                return

            if method == "GET":
                self._static(path)
            else:
                self._json(405, {"error": "方法不被允许"})

        except ApiError as exc:
            self._json(exc.status, {"error": exc.message})
        except f2b.Fail2banError as exc:
            self._json(500, {"error": str(exc)})
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - 兜底，避免线程崩溃
            self.log_message("处理 %s 时出错: %r", self.path, exc)
            self._json(500, {"error": f"服务器内部错误：{exc}"})

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self):  # noqa: N802
        self._dispatch("DELETE")


class PanelServer(ThreadingHTTPServer):
    """对外/局域网直连入口（TCP 端口）。"""

    daemon_threads = True
    allow_reuse_address = True


_UnixBase = getattr(socketserver, "ThreadingUnixStreamServer", None)


if _UnixBase is not None:
    class UnixPanelServer(_UnixBase):
        """飞牛统一网关入口：网关把 /app/<appname>/* 转发到该 Unix Socket。"""

        daemon_threads = True
        allow_reuse_address = False
        server_name = "localhost"
        server_port = 0

        def __init__(self, socket_path: str, handler):
            self.socket_path = socket_path
            super().__init__(socket_path, handler)

        def server_bind(self):
            os.makedirs(os.path.dirname(self.socket_path) or ".", exist_ok=True)
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
            super().server_bind()
            try:
                # 网关进程需要以自身身份连接该 Socket
                os.chmod(self.socket_path, 0o666)
            except OSError:
                pass

        def get_request(self):
            request, _ = super().get_request()
            # Unix 连接的 client_address 是空串，补成合法元组避免日志/取值出错
            return request, ("127.0.0.1", 0)

        def server_close(self):
            super().server_close()
            try:
                os.unlink(self.socket_path)
            except OSError:
                pass
else:
    class UnixPanelServer:  # type: ignore[no-redef]
        """运行时不支持 AF_UNIX 时的占位实现（如部分 Windows 构建）。"""

        def __init__(self, socket_path: str, handler):
            raise OSError("当前 Python 运行时不支持 AF_UNIX，无法提供统一网关入口")


# ---------------------------------------------------------------------------
# 命令行
# ---------------------------------------------------------------------------

def cmd_set_credentials(argv: list[str]) -> int:
    if not argv:
        print("用法: server.py --set-credentials <用户名> <密码>", file=sys.stderr)
        return 2
    username = argv[0].strip() or "admin"
    password = argv[1] if len(argv) > 1 else ""
    if len(password) < 6:
        print("密码至少需要 6 个字符", file=sys.stderr)
        return 2
    store.ensure_dirs()
    store.save_credentials(username, password)
    print(f"已写入管理面板凭证：{store.CREDENTIALS_FILE}")
    return 0


def cmd_bootstrap() -> int:
    ignoreip = os.environ.get("F2B_BOOTSTRAP_IGNOREIP", "127.0.0.1/8 ::1")
    enable_sshd = _bool(os.environ.get("F2B_BOOTSTRAP_SSHD", "true"))
    bantime = os.environ.get("F2B_BOOTSTRAP_BANTIME", "1h")
    maxretry = os.environ.get("F2B_BOOTSTRAP_MAXRETRY", "5")

    defaults = {
        "bantime": bantime,
        "findtime": "10m",
        "maxretry": maxretry,
        "ignoreip": ignoreip,
        "bantime.increment": "true",
        "bantime.factor": "1",
        "bantime.maxtime": "1w",
    }
    jailconf.ensure_dirs()
    jailconf.bootstrap(defaults, {})
    if enable_sshd:
        try:
            jailconf.update_section("sshd", {"enabled": "true", "maxretry": maxretry})
        except OSError as exc:
            print(f"写入 sshd jail 失败：{exc}", file=sys.stderr)
    if f2b.installed():
        try:
            f2b.reload()
        except f2b.Fail2banError:
            pass
    print(f"基线配置已写入：{jailconf.MANAGED_FILE}")
    return 0


def cmd_fnknock(argv: list[str]) -> int:
    """fail2ban action 的调用入口：--fnknock {ban|unban} <ip> [jail]。"""
    if len(argv) < 2:
        print("用法: server.py --fnknock {ban|unban} <ip> [jail]", file=sys.stderr)
        return 2
    return fnknock.run_action(argv[0], argv[1], argv[2] if len(argv) > 2 else "")


def cmd_self_check() -> int:
    """输出运行环境自检信息，便于在 NAS 上排查问题。"""
    def line(label: str, value) -> None:
        print(f"{label:<16}: {value}")

    print("=== Fail2ban · 运行环境自检 ===")
    line("应用版本", app_version())
    line("Python", f"{platform.python_version()} ({sys.executable})")
    if hasattr(os, "getuid"):
        line("运行身份", f"uid={os.getuid()} gid={os.getgid()}")
    else:
        line("运行身份", "非 POSIX 平台")

    print("\n--- 统一网关 ---")
    line("AF_UNIX 支持", "是" if hasattr(socket, "AF_UNIX") else "否（无法使用统一网关）")
    line("网关前缀", GATEWAY_PREFIX)
    line("Socket 路径", GATEWAY_SOCKET or "（已禁用）")
    if GATEWAY_SOCKET:
        exists = os.path.exists(GATEWAY_SOCKET)
        line("Socket 文件", "存在" if exists else "不存在")
        if exists:
            try:
                stat = os.stat(GATEWAY_SOCKET)
                line("Socket 权限", oct(stat.st_mode & 0o777))
            except OSError as exc:
                line("Socket 权限", f"读取失败：{exc}")
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            probe.settimeout(3)
            try:
                probe.connect(GATEWAY_SOCKET)
                line("Socket 连通", "正常")
            except OSError as exc:
                line("Socket 连通", f"失败：{exc}")
            finally:
                probe.close()

    print("\n--- 网络监听 ---")
    line("TCP 监听", f"{BIND_ADDR}:{PORT}")
    line("端口可用", "是" if _port_available() else "否（可能被占用）")

    print("\n--- fail2ban ---")
    info = f2b.info()
    line("已安装", "是" if info["installed"] else "否")
    line("客户端路径", info["client"] or "未找到")
    line("控制套接字", info["socket"] or "未找到")
    line("服务状态", info["service"].get("raw"))
    line("守护进程响应", "正常" if info["running"] else "无响应")
    line("版本", info["version"] or "未知")

    print("\n--- 配置路径 ---")
    line("系统配置目录", jailconf.SYS_ETC)
    line("可写", "是" if os.access(jailconf.SYS_ETC, os.W_OK) else "否")
    line("托管配置", jailconf.MANAGED_FILE)
    line("日志文件", f2b.log_file() or "未找到")

    print("\n--- fn-knock 联动 ---")
    fnk = fnknock.public_info(force=True)
    line("安装目录", fnk["varDir"] or "未检测到")
    line("接口地址", fnk["baseUrl"] or "未探测到")
    line("后端监听", "正常" if fnk["running"] else "无响应")
    line("本地密钥", "可读" if fnk["secretFound"] else "未找到（可能需要手动填写鉴权）")
    fnk_settings = fnknock.load_settings()
    line("黑名单同步", "已启用" if fnk_settings.get("enabled") else "未启用")
    line("封禁动作", "由敲门执行" if fnk_settings.get("banViaFnknock") else "系统默认")
    fnk_action = fnknock.action_status()
    line("生效 banaction", fnk_action["effectiveBanaction"] or "（继承系统默认）")
    line("action 文件", "已安装" if fnk_action["actionInstalled"] else "未安装")

    print("\n--- 应用数据 ---")
    line("配置目录", store.ETC_DIR)
    line("数据目录", store.VAR_DIR)
    line("静态资源", f"存在 {len(os.listdir(store.WEB_DIR))} 个文件"
         if os.path.isdir(store.WEB_DIR) else "缺失")
    return 0


def _port_available() -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((BIND_ADDR, PORT))
        return True
    except OSError:
        return False
    finally:
        probe.close()


def main() -> int:
    if "--self-check" in sys.argv:
        return cmd_self_check()
    if "--set-credentials" in sys.argv:
        index = sys.argv.index("--set-credentials")
        return cmd_set_credentials(sys.argv[index + 1:])
    if "--bootstrap" in sys.argv:
        return cmd_bootstrap()
    if "--fnknock" in sys.argv:
        index = sys.argv.index("--fnknock")
        return cmd_fnknock(sys.argv[index + 1:])
    if "--print-token" in sys.argv:
        print(store.api_token() or "")
        return 0
    if "--version" in sys.argv:
        print(app_version())
        return 0

    store.ensure_dirs()
    jailconf.ensure_dirs()
    try:
        os.chmod(store.CREDENTIALS_FILE, 0o600)
    except OSError:
        pass

    listeners = []

    # 1) TCP：局域网直连与排查入口
    try:
        listeners.append((f"http://{BIND_ADDR}:{PORT}/",
                          PanelServer((BIND_ADDR, PORT), PanelHandler)))
    except OSError as exc:
        print(f"TCP 端口监听失败 {BIND_ADDR}:{PORT} -> {exc}", file=sys.stderr)

    # 2) Unix Socket：飞牛统一网关（/app/<appname>）
    if GATEWAY_SOCKET:
        try:
            listeners.append((f"unix:{GATEWAY_SOCKET}  ->  网关前缀 {GATEWAY_PREFIX}",
                              UnixPanelServer(GATEWAY_SOCKET, PanelHandler)))
        except OSError as exc:
            print(f"统一网关 Socket 创建失败 {GATEWAY_SOCKET} -> {exc}", file=sys.stderr)

    if not listeners:
        print("没有可用的监听方式，服务退出。", file=sys.stderr)
        return 1

    try:
        with open(READY_FILE, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
    except OSError:
        pass

    print(f"[{time.strftime('%F %T')}] Fail2ban 面板已启动 "
          f"(版本 {app_version()}, PID {os.getpid()})", flush=True)
    for target, _ in listeners:
        print(f"    - {target}", flush=True)

    for _, server in listeners:
        threading.Thread(target=server.serve_forever,
                         kwargs={"poll_interval": 0.5}, daemon=True).start()

    watcher = notify.Watcher()
    watcher.start()

    scheduler = backup.Scheduler()
    scheduler.start()

    # 启动后清理一次历史遗留（安装包缓存、半截下载、超过保留份数的备份），
    # 放在新进程启动时做而不是下载完成时：升级会替换面板进程，旧进程里删文件
    # 有可能删到正在安装的那一个。
    def _startup_housekeeping():
        try:
            result = storage.startup_purge(store.VAR_DIR, current_version=app_version())
            if result.get("freed"):
                print("[%s] 启动清理回收 %d 字节" % (time.strftime("%F %T"), result["freed"]),
                      flush=True)
        except Exception as exc:  # noqa: BLE001 - 清理失败不能影响启动
            print("[%s] 启动清理失败：%r" % (time.strftime("%F %T"), exc), flush=True)

    threading.Thread(target=_startup_housekeeping, daemon=True).start()

    # 版本自检：与浏览器开没开无关，启动 60 秒后查第一次、之后每小时一次。
    try:
        threading.Thread(
            target=update.poll_loop,
            args=(lambda: {"update": {"check": True, "repo": ""}},
                  lambda msg: print("[%s] %s" % (time.strftime("%F %T"), msg), flush=True),
                  threading.Event()),
            kwargs={"initial_delay": 60, "interval": 3600},
            daemon=True,
        ).start()
    except Exception as exc:  # noqa: BLE001 - 版本自检起不来不能挡住面板
        print("[%s] 版本自检线程启动失败：%r" % (time.strftime("%F %T"), exc), flush=True)

    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for _, server in listeners:
            try:
                server.server_close()
            except OSError:
                pass
        try:
            os.unlink(READY_FILE)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
