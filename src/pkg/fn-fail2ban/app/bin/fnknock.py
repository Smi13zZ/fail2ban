"""与 fn-knock（敲门）联动。

fail2ban 产生封禁 / 解封时，把 IP 同步到 fn-knock 的「通用黑名单」
（`GET/POST/DELETE /api/admin/general-blacklist`），让经过敲门网关的流量
在应用层也被拒之门外；面板中还能查看对端名单与同步记录。

fn-knock 侧的接口约定（摘自 apps/server-admin-rs/src/security/general_blacklist.rs）：

    GET    /api/admin/general-blacklist?page=&limit=&search=
    POST   /api/admin/general-blacklist/status   {"ips": [...]}
    POST   /api/admin/general-blacklist          {"ips": [...], "source": "...", "comment": "..."}
    DELETE /api/admin/general-blacklist          {"ips": [...]}
    DELETE /api/admin/general-blacklist/{ip}

source 取值：manual / request_log / active_ip / waf_log / waf_rate_limit，
响应为 `{success, code, message, data}` 信封。

除「后台巡检同步」外，还可以把**封禁动作本身**交给敲门：写入
`/etc/fail2ban/action.d/fnknock.local` 并把托管配置 `[DEFAULT] banaction` 指向它，
fail2ban 封禁时会立即调用 `server.py --fnknock ban <ip> <jail>`；关闭开关则移除该键，
恢复系统原本的封禁方式。

仅使用标准库，保证在 fnOS 上零依赖运行。
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import filter_templates
import jailconf
import store

SETTINGS_FILE = os.path.join(store.VAR_DIR, "fnknock.json")
LOG_FILE = os.path.join(store.VAR_DIR, "fnknock-log.json")
MAX_LOG = 200

# fn-knock 的运行时常量（见 apps/fn-knock/cmd/main）
DEFAULT_BACKEND_PORT = 7998
PORTS_FILE = "runtime-ports.env"
SECRET_FILE = "admin_proxy_secret"
ADMIN_AUTH_HEADER = "x-fn-knock-admin-auth"

# 由本应用安装的 fail2ban action：把封禁/解封动作直接交给敲门执行。
ACTION_NAME = "fnknock"
ACTION_FILE = os.path.join(jailconf.ACTION_D, f"{ACTION_NAME}.local")
DEFAULT_BANACTION = "iptables-multiport"
ACTION_HEADER = """# 本文件由 fnOS「Fail2ban」应用的 fnknock 联动生成，请勿手动编辑。
# 关闭面板中的「由 fn-knock 执行封禁」后，本文件会被自动删除。"""

VAR_CANDIDATES = (
    os.environ.get("FNKNOCK_VAR_DIR") or "",
    "/var/apps/fn-knock/var",
    "/var/apps/fn-knock-lite/var",
)

SOURCES = [
    {"value": "manual", "label": "手动（面板 / fail2ban）"},
    {"value": "request_log", "label": "请求日志"},
    {"value": "active_ip", "label": "活跃 IP"},
    {"value": "waf_log", "label": "WAF 日志"},
    {"value": "waf_rate_limit", "label": "WAF 限流"},
]

AUTH_MODES = [
    {"value": "auto", "label": "自动（读取 fn-knock 本地代理密钥）"},
    {"value": "none", "label": "不需要鉴权"},
    {"value": "header", "label": "自定义请求头"},
    {"value": "cookie", "label": "Cookie"},
    {"value": "bearer", "label": "Bearer 令牌"},
]

DEFAULT_SETTINGS = {
    "enabled": False,
    # 留空表示自动探测（127.0.0.1:<BACKEND_PORT>）
    "baseUrl": "",
    "authMode": "auto",
    "authHeader": ADMIN_AUTH_HEADER,
    "authValue": "",
    "source": "manual",
    "comment": "fail2ban",
    # 解封时也从 fn-knock 黑名单移除（关闭则只增不减）
    "removeOnUnban": True,
    "timeout": 8,
    # 由 fn-knock 执行封禁：安装 action.d/fnknock.local 并把 banaction 指向它。
    # 打开 = 封禁只走敲门（不再执行 iptables / nftables）；
    # 关闭 = 移除该键，完全回到系统原本的封禁动作。
    "banViaFnknock": False,
    # 高级选项：在敲门之外额外保留系统防火墙封禁（banaction = fnknock <原动作>）。
    # 默认关闭，即「只走敲门」。
    "keepFirewall": False,
    # 内部状态：安装 action 之前托管配置里的 banaction，用于关闭时还原
    "savedBanaction": "",
}

_LOCK = threading.Lock()
_DISCOVER_CACHE: dict = {"at": 0.0, "data": None}


class FnknockError(RuntimeError):
    """调用 fn-knock 失败。"""


# ---------------------------------------------------------------------------
# 探测
# ---------------------------------------------------------------------------

def _read_env_file(path: str) -> dict:
    data: dict[str, str] = {}
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                data[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        return {}
    return data


def _read_secret(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def discover(force: bool = False) -> dict:
    """探测本机是否安装了 fn-knock，并给出可用的接口地址与鉴权材料。"""
    now = time.time()
    cached = _DISCOVER_CACHE.get("data")
    if not force and cached and now - float(_DISCOVER_CACHE.get("at") or 0) < 10:
        return cached

    var_dir = ""
    for candidate in VAR_CANDIDATES:
        if candidate and os.path.isdir(candidate):
            var_dir = candidate
            break

    port = DEFAULT_BACKEND_PORT
    if var_dir:
        ports = _read_env_file(os.path.join(var_dir, PORTS_FILE))
        try:
            port = int(ports.get("BACKEND_PORT") or DEFAULT_BACKEND_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_BACKEND_PORT

    secret = _read_secret(os.path.join(var_dir, SECRET_FILE)) if var_dir else ""
    running = _port_open("127.0.0.1", port) if port else False

    info = {
        "installed": bool(var_dir),
        "varDir": var_dir,
        "port": port,
        "secretFound": bool(secret),
        "secret": secret,
        "running": running,
        "baseUrl": f"http://127.0.0.1:{port}" if (var_dir or running) else "",
        "checkedAt": int(now),
    }
    _DISCOVER_CACHE.update({"at": now, "data": info})
    return info


def public_info(force: bool = False) -> dict:
    """探测结果的安全视图（不含本地密钥明文）。"""
    info = dict(discover(force=force))
    info.pop("secret", None)
    return info


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------

def load_settings() -> dict:
    stored = store.read_json(SETTINGS_FILE)
    settings = dict(DEFAULT_SETTINGS)
    if isinstance(stored, dict):
        for key in DEFAULT_SETTINGS:
            if key in stored:
                settings[key] = stored[key]
        if str(settings.get("authHeader") or "").strip() == "":
            settings["authHeader"] = ADMIN_AUTH_HEADER
        if str(settings.get("source") or "") not in [item["value"] for item in SOURCES]:
            settings["source"] = DEFAULT_SETTINGS["source"]
        if str(settings.get("authMode") or "") not in [item["value"] for item in AUTH_MODES]:
            settings["authMode"] = DEFAULT_SETTINGS["authMode"]
        settings["enabled"] = bool(settings.get("enabled"))
        settings["removeOnUnban"] = bool(settings.get("removeOnUnban"))
        settings["banViaFnknock"] = bool(settings.get("banViaFnknock"))
        settings["keepFirewall"] = bool(settings.get("keepFirewall", False))
        settings["savedBanaction"] = str(settings.get("savedBanaction") or "")
        try:
            settings["timeout"] = max(2, min(int(settings.get("timeout") or 8), 60))
        except (TypeError, ValueError):
            settings["timeout"] = DEFAULT_SETTINGS["timeout"]
    return settings


def save_settings(updates: dict) -> dict:
    settings = load_settings()
    for key in ("enabled", "removeOnUnban", "banViaFnknock", "keepFirewall"):
        if key in updates:
            settings[key] = bool(updates[key])
    for key in ("baseUrl", "authMode", "authHeader", "authValue", "source", "comment"):
        if key in updates:
            settings[key] = str(updates[key] or "").strip()[:2048]
    if "timeout" in updates:
        try:
            settings["timeout"] = max(2, min(int(updates["timeout"]), 60))
        except (TypeError, ValueError):
            pass
    if not settings.get("authHeader"):
        settings["authHeader"] = ADMIN_AUTH_HEADER
    if settings.get("source") not in [item["value"] for item in SOURCES]:
        settings["source"] = DEFAULT_SETTINGS["source"]
    if settings.get("authMode") not in [item["value"] for item in AUTH_MODES]:
        settings["authMode"] = DEFAULT_SETTINGS["authMode"]
    store.ensure_dirs()
    store.atomic_write_json(SETTINGS_FILE, settings)
    return settings


def enabled() -> bool:
    try:
        return bool(load_settings().get("enabled"))
    except (OSError, ValueError):
        return False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _endpoint(settings: dict | None = None) -> tuple[str, dict]:
    settings = settings or load_settings()
    base = str(settings.get("baseUrl") or "").strip().rstrip("/")
    if not base:
        base = str(discover().get("baseUrl") or "")
    if base and not base.startswith(("http://", "https://")):
        base = "http://" + base

    headers = {"Accept": "application/json"}
    mode = str(settings.get("authMode") or "auto")
    value = str(settings.get("authValue") or "").strip()
    if mode == "none":
        pass
    elif mode == "header":
        name = str(settings.get("authHeader") or ADMIN_AUTH_HEADER).strip()
        if name and value:
            headers[name] = value
    elif mode == "cookie":
        if value:
            headers["Cookie"] = value
    elif mode == "bearer":
        if value:
            headers["Authorization"] = f"Bearer {value}"
    else:  # auto：优先用从数据目录读到的本地代理密钥
        secret = str(discover().get("secret") or "").strip() or value
        if secret:
            headers[ADMIN_AUTH_HEADER] = secret
    return base, headers


def _message(data) -> str:
    if isinstance(data, dict):
        for key in ("message", "error", "msg", "detail"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if data.get("success") is False:
            return f"fn-knock 返回失败（code={data.get('code')}）"
    if isinstance(data, str) and data.strip():
        return data.strip()[:200]
    return ""


def _parse(raw: str):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:500]}


def request(method: str, path: str, payload=None,
            settings: dict | None = None, timeout: float | None = None):
    """调用 fn-knock 的管理接口，返回 `data` 字段；失败时抛 FnknockError。"""
    settings = settings or load_settings()
    base, headers = _endpoint(settings)
    if not base:
        raise FnknockError("未探测到 fn-knock 的运行地址，请先安装 fn-knock 或手动填写接口地址")

    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"

    request_obj = urllib.request.Request(base + path, data=body, method=method, headers=headers)
    seconds = timeout or float(settings.get("timeout") or 8)
    try:
        with urllib.request.urlopen(request_obj, timeout=seconds) as response:
            status = response.status
            raw = response.read(256 * 1024).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = ""
        try:
            raw = exc.read(8192).decode("utf-8", errors="replace")
        except OSError:
            pass
        data = _parse(raw)
        detail = _message(data)
        if exc.code == 401:
            detail = detail or "fn-knock 拒绝了请求（401）"
            raise FnknockError(
                f"{detail}；请在「鉴权方式」中选择合适的凭据，或确认本地代理密钥可读") from None
        if exc.code == 404:
            raise FnknockError("fn-knock 上不存在该接口（404），请确认版本是否支持通用黑名单") from None
        raise FnknockError(detail or f"fn-knock 返回 HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise FnknockError(f"无法连接 fn-knock（{exc.reason}）") from None
    except OSError as exc:
        raise FnknockError(f"请求 fn-knock 失败：{exc}") from None
    except ValueError as exc:
        raise FnknockError(f"fn-knock 响应解析失败：{exc}") from None

    data = _parse(raw)
    if isinstance(data, dict):
        if data.get("success") is False:
            raise FnknockError(_message(data) or "fn-knock 返回失败")
        if "data" in data:
            return data.get("data")
    return data


# ---------------------------------------------------------------------------
# 通用黑名单接口
# ---------------------------------------------------------------------------

def normalize_ips(values) -> list[str]:
    """只保留合法的单个 IP（CIDR 会被 fn-knock 拒绝，这里直接过滤掉）。"""
    out: list[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        try:
            address = str(ipaddress.ip_address(text))
        except ValueError:
            continue
        if address not in seen:
            seen.add(address)
            out.append(address)
    return out


def list_blacklist(page: int = 1, limit: int = 20, search: str = "",
                   settings: dict | None = None) -> dict:
    query = f"?page={int(page)}&limit={int(limit)}"
    if search:
        query += "&search=" + urllib.parse.quote(str(search))
    return request("GET", "/api/admin/general-blacklist" + query, settings=settings)


def blacklist_status(ips, settings: dict | None = None) -> dict:
    return request("POST", "/api/admin/general-blacklist/status",
                   {"ips": normalize_ips(ips)}, settings=settings)


def add_ips(ips, comment: str = "", source: str = "",
            settings: dict | None = None) -> dict:
    settings = settings or load_settings()
    payload = {
        "ips": normalize_ips(ips),
        "source": source or settings.get("source") or "manual",
        "comment": comment if comment is not None else str(settings.get("comment") or ""),
    }
    return request("POST", "/api/admin/general-blacklist", payload, settings=settings)


def remove_ips(ips, settings: dict | None = None) -> dict:
    return request("DELETE", "/api/admin/general-blacklist",
                   {"ips": normalize_ips(ips)}, settings=settings)


def remove_ip(ip: str, settings: dict | None = None) -> dict:
    address = normalize_ips([ip])
    if not address:
        raise FnknockError(f"“{ip}” 不是合法的 IP 地址")
    return request("DELETE", "/api/admin/general-blacklist/" + address[0], settings=settings)


def test(settings: dict | None = None) -> dict:
    """连通性与鉴权自检：先探端口，再读一页黑名单。"""
    settings = settings or load_settings()
    base, headers = _endpoint(settings)
    if not base:
        raise FnknockError("未探测到 fn-knock 的运行地址，请先安装 fn-knock 或手动填写接口地址")
    data = list_blacklist(page=1, limit=1, settings=settings)
    total = 0
    if isinstance(data, dict):
        for key in ("total", "totalCount", "count"):
            value = data.get(key)
            if isinstance(value, int):
                total = value
                break
        if not total and isinstance(data.get("items"), list):
            total = len(data["items"])
    return {
        "baseUrl": base,
        "authHeader": next((key for key in headers if key.lower() != "accept"), "（无）"),
        "total": total,
    }


# ---------------------------------------------------------------------------
# fail2ban 封禁动作（banaction 切换）
#
# 打开「由 fn-knock 执行封禁」时：
#   1. 在 /etc/fail2ban/action.d/ 写入 fnknock.local，它的 actionban / actionunban
#      调用 `server.py --fnknock {ban|unban} <ip> <jail>`；
#   2. 把托管配置 [DEFAULT] 的 banaction 指向该 action，封禁时 fail2ban 会立即调用
#      敲门接口（不依赖后台巡检，即时生效）。
# 关闭时移除 banaction 键（或还原成安装前用户自定义的值），重新回到系统原本的封禁方式。
# ---------------------------------------------------------------------------

def action_command() -> str:
    """action.d 中调用的命令行（绝对路径，含空格时由 fail2ban 的 shlex 解析）。"""
    python = sys.executable or "/usr/bin/python3"
    script = os.path.join(store.APP_DIR, "bin", "server.py")
    return f'"{python}" "{script}" --fnknock'


def managed_banaction() -> str:
    """托管配置里当前写入的 banaction（未接管时为空）。"""
    section = jailconf.managed_sections().get("DEFAULT", {}) or {}
    return str(section.get("banaction") or "").strip()


def base_banaction() -> str:
    """系统原本的默认封禁动作（忽略本应用托管的那份配置）。"""
    merged, _ = jailconf.load_merged(exclude_managed=True)
    value = str(((merged.get("DEFAULT", {}) or {}).get("banaction") or {}).get("value") or "")
    tokens = [item for item in value.split() if item and item != ACTION_NAME]
    return " ".join(tokens) or DEFAULT_BANACTION


def effective_banaction() -> str:
    """最终生效的默认封禁动作（含托管配置）。"""
    merged, _ = jailconf.load_merged()
    value = str(((merged.get("DEFAULT", {}) or {}).get("banaction") or {}).get("value") or "")
    return value.strip()


def action_status() -> dict:
    return {
        "actionName": ACTION_NAME,
        "actionFile": ACTION_FILE,
        "actionInstalled": os.path.isfile(ACTION_FILE),
        "command": action_command(),
        "managedBanaction": managed_banaction(),
        "inheritedBanaction": base_banaction(),
        "effectiveBanaction": effective_banaction(),
    }


def write_action_file() -> str:
    jailconf.ensure_dirs()
    os.makedirs(jailconf.ACTION_D, exist_ok=True)
    jailconf.backup_file(ACTION_FILE)
    command = action_command()
    lines = [
        ACTION_HEADER,
        "",
        "[Definition]",
        "",
        "actionstart = /bin/true",
        "actionstop = /bin/true",
        "actioncheck = /bin/true",
        f"actionban = {command} ban <ip> <name>",
        f"actionunban = {command} unban <ip> <name>",
        "",
    ]
    store.atomic_write(ACTION_FILE, "\n".join(lines), 0o644)
    return ACTION_FILE


def remove_action_file() -> bool:
    if not os.path.isfile(ACTION_FILE):
        return False
    jailconf.backup_file(ACTION_FILE)
    try:
        os.unlink(ACTION_FILE)
    except OSError:
        return False
    return True


def apply_ban_action(settings: dict | None = None, persist: bool = True) -> dict:
    """按开关状态安装 / 卸载 action，并同步托管配置里的 banaction。"""
    settings = dict(settings if settings is not None else load_settings())
    current = managed_banaction()
    ours = ACTION_NAME in current.split()

    if settings.get("banViaFnknock"):
        if not ours:
            # 首次接管时记住用户原本写在托管配置里的值，便于关闭时还原
            settings["savedBanaction"] = current
        path = write_action_file()
        base = str(settings.get("savedBanaction") or "").strip() or base_banaction()
        tokens = [item for item in base.split() if item and item != ACTION_NAME]
        base = " ".join(tokens) or DEFAULT_BANACTION
        value = f"{ACTION_NAME} {base}" if settings.get("keepFirewall", False) else ACTION_NAME
        jailconf.update_section("DEFAULT", {"banaction": value})
        result = {"enabled": True, "banaction": value, "baseBanaction": base,
                  "actionFile": path, "keptFirewall": bool(settings.get("keepFirewall", False))}
    else:
        saved = str(settings.get("savedBanaction") or "").strip()
        if ours:
            if saved:
                jailconf.update_section("DEFAULT", {"banaction": saved})
            else:
                jailconf.update_section("DEFAULT", {}, remove=["banaction"])
        settings["savedBanaction"] = ""
        result = {"enabled": False, "removed": remove_action_file(),
                  "banaction": effective_banaction(), "actionFile": ACTION_FILE}

    if persist:
        store.ensure_dirs()
        store.atomic_write_json(SETTINGS_FILE, settings)
    return result


def run_action(action: str, ip: str, jail: str = "") -> int:
    """fail2ban action 的 CLI 入口：返回进程退出码。"""
    action = str(action or "").strip().lower()
    if action not in ("ban", "unban"):
        print(f"不支持的操作：{action or '(空)'}", file=sys.stderr)
        return 2
    ips = normalize_ips([ip])
    if not ips:
        print(f"不是合法的 IP 地址：{ip}", file=sys.stderr)
        return 2

    settings = load_settings()
    prefix = str(settings.get("comment") or "fail2ban").strip() or "fail2ban"
    comment = f"{prefix} · {jail}" if jail else prefix
    try:
        if action == "ban":
            add_ips(ips, comment=comment, settings=settings)
        else:
            remove_ips(ips, settings=settings)
    except FnknockError as exc:
        record(action, jail, ips, False, str(exc))
        print(f"fn-knock {action} 失败：{exc}", file=sys.stderr)
        return 1
    record(action, jail, ips, True, "fail2ban action 已同步")
    return 0


# ---------------------------------------------------------------------------
# 敲门网关的过滤器与 Jail
#
# 「启用 fn-knock 联动」打开时自动装好这两样，让 fail2ban 直接盯敲门的请求日志：
# 网关侧记下的认证失败 / WAF 拦截会被自动封禁，不必再去拼各个服务的系统日志。
# 关闭开关时撤掉，回到只看系统日志的状态。
# 过滤器正则与「过滤器 → 从模板新建 → 敲门网关请求日志」共用同一份定义，两边永远一致。
# ---------------------------------------------------------------------------

GATEWAY_FILTER_NAME = "fnknock-gateway"
GATEWAY_JAIL_NAME = "fnknock"
GATEWAY_LOG_PATH = "/usr/local/etc/fn-knock/logs/*.log"
GATEWAY_PORT = "http,https"
# 回环与 192.168.0.0/16 一律不封：网关日志里的 remote_ip 在很多部署下是内网客户端的地址，
# 一旦把 192.168.x.x 封掉，会把整个局域网（包括自己）挡在门外。
# 如果你的局域网用的是 10.x 或 172.16-31.x，请到「Jail 管理 → fnknock」里按需补上。
GATEWAY_IGNOREIP = "127.0.0.1/8 ::1 192.168.0.0/16"


def gateway_template() -> dict:
    for item in filter_templates.TEMPLATES:
        if item.get("id") == GATEWAY_FILTER_NAME:
            return item
    return {}


def gateway_filter_path() -> str:
    return jailconf.filter_paths(GATEWAY_FILTER_NAME)[1]


def gateway_log_dir() -> str:
    return os.path.dirname(GATEWAY_LOG_PATH)


def gateway_jail_config() -> dict:
    """托管配置里由本联动写入的那份 Jail；不是我们写的则返回空。"""
    section = dict(jailconf.managed_sections().get(GATEWAY_JAIL_NAME) or {})
    if str(section.get("filter") or "").strip() != GATEWAY_FILTER_NAME:
        return {}
    return section


def gateway_status() -> dict:
    section = gateway_jail_config()
    path = gateway_filter_path()
    log_dir = gateway_log_dir()
    return {
        "filterName": GATEWAY_FILTER_NAME,
        "filterPath": path,
        "filterInstalled": os.path.isfile(path),
        "jailName": GATEWAY_JAIL_NAME,
        "jailInstalled": bool(section),
        "logPath": section.get("logpath") or GATEWAY_LOG_PATH,
        "logDir": log_dir,
        "logDirExists": os.path.isdir(log_dir),
        "port": section.get("port") or GATEWAY_PORT,
        "ignoreip": section.get("ignoreip") or GATEWAY_IGNOREIP,
    }


def apply_gateway_jail(settings: dict | None = None) -> dict:
    """按「启用 fn-knock 联动」开关安装 / 卸载敲门网关的过滤器与 Jail。

    只碰本应用托管的那份配置与 ``filter.d/fnknock-gateway.local``：
    Jail 仅在 ``filter`` 指向我们的过滤器时才删除，避免误删用户自己写的同名 Jail。
    """
    settings = dict(settings if settings is not None else load_settings())

    if settings.get("banViaFnknock"):
        template = gateway_template()
        if not template:
            raise ValueError(f"缺少内置过滤器模板：{GATEWAY_FILTER_NAME}")
        jailconf.write_filter_local(GATEWAY_FILTER_NAME,
                                    template.get("failregex") or [],
                                    template.get("ignoreregex") or [])
        jailconf.update_section(GATEWAY_JAIL_NAME, {
            "enabled": "true",
            "filter": GATEWAY_FILTER_NAME,
            "logpath": GATEWAY_LOG_PATH,
            "port": GATEWAY_PORT,
            "ignoreip": GATEWAY_IGNOREIP,
        })
        return {"installed": True, "jail": GATEWAY_JAIL_NAME,
                "filter": GATEWAY_FILTER_NAME, "logPath": GATEWAY_LOG_PATH}

    removed_jail = False
    if gateway_jail_config():
        jailconf.delete_section(GATEWAY_JAIL_NAME)
        removed_jail = True
    removed_filter = (jailconf.delete_filter_local(GATEWAY_FILTER_NAME)
                      if os.path.isfile(gateway_filter_path()) else False)
    return {"installed": False, "jail": GATEWAY_JAIL_NAME,
            "filter": GATEWAY_FILTER_NAME, "logPath": GATEWAY_LOG_PATH,
            "removedJail": removed_jail, "removedFilter": removed_filter}


# ---------------------------------------------------------------------------
# 同步与记录
# ---------------------------------------------------------------------------

def sync(jail: str, added, removed) -> list[dict]:
    """巡检发现新增 / 解封时调用。返回本次产生的记录（未启用时为空）。"""
    settings = load_settings()
    if not settings.get("enabled"):
        return []

    results = []
    added_ips = normalize_ips(added)
    removed_ips = normalize_ips(removed) if settings.get("removeOnUnban", True) else []
    comment = str(settings.get("comment") or "fail2ban").strip() or "fail2ban"

    if added_ips:
        try:
            add_ips(added_ips, comment=f"{comment} · {jail}", settings=settings)
            results.append(record("ban", jail, added_ips, True, "已同步到 fn-knock 黑名单"))
        except FnknockError as exc:
            results.append(record("ban", jail, added_ips, False, str(exc)))
    if removed_ips:
        try:
            remove_ips(removed_ips, settings=settings)
            results.append(record("unban", jail, removed_ips, True, "已从 fn-knock 黑名单移除"))
        except FnknockError as exc:
            results.append(record("unban", jail, removed_ips, False, str(exc)))
    return results


def record(action: str, jail: str, ips, ok: bool, detail: str) -> dict:
    with _LOCK:
        data = store.read_json(LOG_FILE)
        entries = data.get("entries") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            entries = []
        entry = {
            "time": int(time.time()),
            "action": action,
            "jail": jail or "",
            "ips": normalize_ips(ips),
            "ok": bool(ok),
            "detail": str(detail or "")[:500],
        }
        entries.append(entry)
        store.ensure_dirs()
        store.atomic_write_json(LOG_FILE, {"entries": entries[-MAX_LOG:]})
        return entry


def log_entries(limit: int = 60) -> list[dict]:
    with _LOCK:
        data = store.read_json(LOG_FILE)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return []
    return entries[-limit:]


def clear_log() -> None:
    with _LOCK:
        store.ensure_dirs()
        store.atomic_write_json(LOG_FILE, {"entries": []})
