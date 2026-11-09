"""fail2ban 配置文件的解析与写入。

设计原则
--------
* 只读系统自带的 ``jail.conf`` / ``jail.d/*.conf`` / ``jail.local``；
* 所有修改统一写入 ``jail.d/50-fn-fail2ban.local``（fail2ban 的加载顺序中
  该文件优先级最高），因此绝不会破坏发行版或用户既有配置；
* 每次写入前自动备份到应用数据目录，便于回滚。
"""

from __future__ import annotations

import glob
import os
import re
import shutil
import time

import store

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

SYS_ETC = os.environ.get("F2B_SYS_ETC") or "/etc/fail2ban"
JAIL_CONF = os.path.join(SYS_ETC, "jail.conf")
JAIL_LOCAL = os.path.join(SYS_ETC, "jail.local")
JAIL_D = os.path.join(SYS_ETC, "jail.d")
FILTER_D = os.path.join(SYS_ETC, "filter.d")
ACTION_D = os.environ.get("F2B_ACTION_D") or os.path.join(SYS_ETC, "action.d")
MANAGED_FILE = os.environ.get("F2B_MANAGED") or os.path.join(JAIL_D, "50-fn-fail2ban.local")

MANAGED_HEADER = """# 本文件由 fnOS「Fail2ban」应用自动生成，请勿手动编辑。
# 手动修改会在应用下次保存配置时被覆盖。
# 如需完全自定义，请在应用面板中关闭“接管配置”，或改用其他文件名。
"""

CUSTOM_FILTER_HEADER = """# 本文件由 fnOS「Fail2ban」应用生成。
"""

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]\s*$")
_INLINE_COMMENT = re.compile(r"\s[#;].*$")

# 需要剥离行内注释的“简单键”（避免误伤 failregex / action 等含特殊字符的值）
SIMPLE_KEYS = {
    "enabled", "filter", "logpath", "port", "backend", "maxretry", "findtime",
    "bantime", "bantime.increment", "bantime.factor", "bantime.maxtime",
    "bantime.rndtime", "ignoreip", "banaction", "banaction_allports", "usedns",
    "maxmatches", "action", "ignoreself", "allowipv6", "ignorecommand", "mode",
    "jail", "protocol", "chain", "loglevel", "logtarget", "dbpurgeage",
    "syslogsocket", "dbfile", "filter.d", "maxlines", "datepattern",
    "journalmatch", "logencoding",
}

BANACTIONS = [
    "iptables-multiport", "iptables-allports", "iptables", "iptables-ipset-proto6",
    "nftables-multiport", "nftables-allports", "nftables",
    "firewallcmd-multiport", "firewallcmd-allports", "firewallcmd-ipset",
    "ufw", "route", "ipset", "hostsdeny", "dummy",
]

JAIL_FIELDS = [
    {"key": "enabled", "label": "启用该 Jail", "type": "bool",
     "help": "关闭后该 Jail 不会在 fail2ban 中生效"},
    {"key": "filter", "label": "过滤器 (filter)", "type": "text", "placeholder": "sshd",
     "help": "对应 /etc/fail2ban/filter.d 下的过滤器名，不含 .conf"},
    {"key": "logpath", "label": "日志文件 (logpath)", "type": "text",
     "placeholder": "/var/log/auth.log", "help": "可以是文件或目录；多个用空格分隔"},
    {"key": "port", "label": "端口 / 服务 (port)", "type": "text", "placeholder": "ssh",
     "help": "如 ssh、http、22；支持逗号分隔的多端口"},
    {"key": "backend", "label": "日志读取后端", "type": "select",
     "options": ["auto", "systemd", "polling", "pyinotify", "gamin"]},
    {"key": "maxretry", "label": "最大重试次数", "type": "int", "placeholder": "5",
     "help": "在 findtime 内失败达到该次数即封禁"},
    {"key": "findtime", "label": "检测时间窗口", "type": "duration", "placeholder": "10m",
     "help": "如 30s / 10m / 1h"},
    {"key": "bantime", "label": "封禁时长", "type": "duration", "placeholder": "1h",
     "help": "-1 表示永久封禁"},
    {"key": "bantime.increment", "label": "递增封禁", "type": "bool",
     "help": "重复违规时按系数成倍延长封禁时间"},
    {"key": "bantime.factor", "label": "递增系数", "type": "int", "placeholder": "1"},
    {"key": "bantime.maxtime", "label": "封禁时长上限", "type": "duration", "placeholder": "1w"},
    {"key": "bantime.rndtime", "label": "封禁随机抖动", "type": "duration", "placeholder": "5m"},
    {"key": "ignoreip", "label": "白名单 IP / 网段", "type": "text",
     "placeholder": "127.0.0.1/8 ::1", "help": "多个条目用空格分隔"},
    {"key": "banaction", "label": "封禁动作", "type": "text", "options": BANACTIONS,
     "placeholder": "nftables-multiport"},
    {"key": "action", "label": "自定义 Action", "type": "text",
     "help": "留空则使用 banaction；填写后覆盖 banaction"},
    {"key": "usedns", "label": "DNS 反查", "type": "select",
     "options": ["yes", "no", "warn", "raw"]},
    {"key": "maxmatches", "label": "单个 IP 最大匹配数", "type": "int", "placeholder": "5"},
    {"key": "mode", "label": "过滤模式", "type": "select",
     "options": ["normal", "aggressive", "ddos", "extra"]},
    {"key": "ignorecommand", "label": "忽略命令", "type": "text",
     "help": "返回 0 时忽略该条日志"},
]

GLOBAL_FIELDS = [
    {"key": "bantime", "label": "默认封禁时长", "type": "duration", "placeholder": "1h"},
    {"key": "findtime", "label": "默认检测窗口", "type": "duration", "placeholder": "10m"},
    {"key": "maxretry", "label": "默认最大重试", "type": "int", "placeholder": "5"},
    {"key": "ignoreip", "label": "全局白名单", "type": "text", "placeholder": "127.0.0.1/8 ::1"},
    {"key": "banaction", "label": "默认封禁动作", "type": "text", "options": BANACTIONS,
     "placeholder": "nftables-multiport"},
    {"key": "backend", "label": "默认日志后端", "type": "select",
     "options": ["auto", "systemd", "polling", "pyinotify", "gamin"]},
    {"key": "bantime.increment", "label": "递增封禁", "type": "bool"},
    {"key": "bantime.factor", "label": "递增系数", "type": "int", "placeholder": "1"},
    {"key": "bantime.maxtime", "label": "递增封禁上限", "type": "duration", "placeholder": "1w"},
    {"key": "ignoreself", "label": "忽略本机 IP", "type": "bool"},
    {"key": "allowipv6", "label": "允许 IPv6", "type": "select", "options": ["auto", "yes", "no"]},
    {"key": "dbpurgeage", "label": "数据库保留时长", "type": "duration", "placeholder": "1d"},
]

_ALL_FIELD_KEYS = [item["key"] for item in JAIL_FIELDS]


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse_file(path: str) -> list[tuple[str, str, str, int]]:
    """解析 ini 风格配置文件，返回 (section, key, value, lineno)。"""
    entries: list[tuple[str, str, str, int]] = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            section = None
            for lineno, raw in enumerate(handle, 1):
                line = raw.rstrip("\r\n").strip()
                if not line or line[0] in "#;":
                    continue
                match = _SECTION_RE.match(line)
                if match:
                    section = match.group("name").strip()
                    continue
                if section is None or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.strip()
                value = value.strip()
                if key.lower() in SIMPLE_KEYS:
                    value = _INLINE_COMMENT.sub("", value).strip()
                entries.append((section, key, value, lineno))
    except OSError:
        return entries
    return entries


def config_files() -> list[str]:
    """按 fail2ban 实际加载顺序返回配置文件列表。"""
    files: list[str] = []
    if os.path.isfile(JAIL_CONF):
        files.append(JAIL_CONF)
    files.extend(sorted(glob.glob(os.path.join(JAIL_D, "*.conf"))))
    if os.path.isfile(JAIL_LOCAL):
        files.append(JAIL_LOCAL)
    files.extend(sorted(glob.glob(os.path.join(JAIL_D, "*.local"))))
    return files


def load_merged(exclude_managed: bool = False) -> tuple[dict, list[str]]:
    """合并全部配置文件，保留每个键的来源文件。

    ``exclude_managed=True`` 时忽略本应用接管的那份文件，用于求出“原始默认值”。
    """
    sources = config_files()
    if exclude_managed:
        managed = os.path.realpath(MANAGED_FILE)
        sources = [path for path in sources if os.path.realpath(path) != managed]
    merged: dict[str, dict[str, dict]] = {}
    for path in sources:
        for section, key, value, _ in parse_file(path):
            merged.setdefault(section, {})[key] = {"value": value, "source": path}
    return merged, sources


def managed_sections() -> dict[str, dict[str, str]]:
    """仅读取本应用接管的那份配置。"""
    result: dict[str, dict[str, str]] = {}
    for section, key, value, _ in parse_file(MANAGED_FILE):
        result.setdefault(section, {})[key] = value
    return result


def preset_jail_names(merged: dict | None = None) -> set[str]:
    """系统自带（非本应用托管）的 Jail 名称。"""
    names: set[str] = set()
    if merged is None:
        merged, _ = load_merged()
    managed = os.path.realpath(MANAGED_FILE) if os.path.exists(MANAGED_FILE) else None
    for section, options in merged.items():
        if section == "DEFAULT":
            continue
        for info in options.values():
            source = os.path.realpath(info.get("source", ""))
            if managed and source == managed:
                continue
            if source.endswith(".local"):
                continue
            names.add(section)
            break
    return names


def all_jail_names(merged: dict | None = None) -> list[str]:
    if merged is None:
        merged, _ = load_merged()
    return sorted(name for name in merged if name != "DEFAULT")


def effective_jail(name: str, merged: dict) -> dict[str, dict]:
    options: dict[str, dict] = {}
    options.update(merged.get("DEFAULT", {}))
    options.update(merged.get(name, {}))
    return options


def options_to_values(options: dict[str, dict]) -> dict[str, str]:
    return {key: info.get("value", "") for key, info in options.items()}


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def ensure_dirs() -> None:
    os.makedirs(JAIL_D, exist_ok=True)
    os.makedirs(FILTER_D, exist_ok=True)
    store.ensure_dirs()


def backup_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    store.ensure_dirs()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = os.path.join(store.BACKUP_DIR, f"{os.path.basename(path)}.{stamp}.bak")
    try:
        shutil.copy2(path, target)
    except OSError:
        return None
    _prune_backups()
    return target


def _prune_backups(keep: int = 50) -> None:
    try:
        items = sorted(
            glob.glob(os.path.join(store.BACKUP_DIR, "*.bak")),
            key=os.path.getmtime,
            reverse=True,
        )
    except OSError:
        return
    for stale in items[keep:]:
        try:
            os.unlink(stale)
        except OSError:
            pass


def sanitize_options(options: dict) -> dict[str, str]:
    """过滤非法键名与值，避免把内容注入到 ini 文件里。"""
    clean: dict[str, str] = {}
    for key, value in (options or {}).items():
        key = str(key).strip()
        if not KEY_RE.match(key):
            continue
        if value is None:
            continue
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        if len(text) > 4096:
            text = text[:4096]
        if "[" in text[:1]:
            continue
        clean[key] = text
    return clean


def _sorted_keys(options: dict[str, str]) -> list[str]:
    def sort_key(key: str):
        try:
            return (0, _ALL_FIELD_KEYS.index(key), key)
        except ValueError:
            return (1, 0, key)

    return sorted(options.keys(), key=sort_key)


def write_managed(sections: dict[str, dict[str, str]]) -> None:
    ensure_dirs()
    backup_file(MANAGED_FILE)
    names = [name for name in sections if name != "DEFAULT"]
    ordered = (["DEFAULT"] if "DEFAULT" in sections else []) + sorted(names)

    lines = [MANAGED_HEADER.rstrip("\n")]
    for name in ordered:
        options = {k: v for k, v in (sections.get(name) or {}).items() if str(v).strip() != ""}
        if not options:
            continue
        lines.append("")
        lines.append(f"[{name}]")
        for key in _sorted_keys(options):
            lines.append(f"{key} = {options[key]}")
    store.atomic_write(MANAGED_FILE, "\n".join(lines) + "\n", 0o644)


def update_section(name: str, options: dict, remove=()) -> None:
    """新增/更新一个 section（jail 或 DEFAULT），空值表示删除该键。"""
    ensure_dirs()
    sections = managed_sections()
    current = sections.setdefault(name, {})
    for key, value in sanitize_options(options).items():
        if str(value).strip() == "":
            current.pop(key, None)
        else:
            current[key] = str(value).strip()
    for key in remove or ():
        current.pop(str(key), None)
    if name != "DEFAULT" and not current:
        sections.pop(name, None)
    if "DEFAULT" in sections and not sections["DEFAULT"]:
        sections.pop("DEFAULT", None)
    write_managed(sections)


def delete_section(name: str) -> None:
    sections = managed_sections()
    sections.pop(name, None)
    write_managed(sections)


def set_enabled(name: str, enabled: bool) -> None:
    update_section(name, {"enabled": "true" if enabled else "false"})


# ---------------------------------------------------------------------------
# 过滤器
# ---------------------------------------------------------------------------

def filter_paths(name: str) -> tuple[str, str]:
    return (
        os.path.join(FILTER_D, f"{name}.conf"),
        os.path.join(FILTER_D, f"{name}.local"),
    )


def _read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return ""


def list_filters() -> list[dict]:
    """列出 filter.d 下的全部过滤器，并标注是否被「已启用的 Jail」引用。"""
    seen: dict[str, dict] = {}
    for path in glob.glob(os.path.join(FILTER_D, "*.conf")):
        name = os.path.basename(path)[:-5]
        if name in ("common",):
            continue
        seen[name] = {"name": name, "custom": False, "preset": True}
    for path in glob.glob(os.path.join(FILTER_D, "*.local")):
        name = os.path.basename(path)[:-6]
        entry = seen.setdefault(name, {"name": name, "preset": False})
        entry["custom"] = True

    used = enabled_jail_filters()
    for name, entry in seen.items():
        owners = used.get(name, [])
        entry["usedBy"] = owners
        entry["enabled"] = bool(owners)
    return sorted(seen.values(), key=lambda item: item["name"])


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on", "enabled")


def enabled_jail_filters(merged: dict | None = None) -> dict[str, list[str]]:
    """{过滤器名: [引用它的已启用 Jail…]}。

    与 fail2ban 的规则保持一致：Jail 没写 ``filter`` 时，默认用 Jail 名当过滤器名。
    """
    if merged is None:
        merged, _ = load_merged()
    result: dict[str, list[str]] = {}
    for name in all_jail_names(merged):
        values = options_to_values(effective_jail(name, merged))
        if not _truthy(values.get("enabled", "false")):
            continue
        target = str(values.get("filter") or "").strip() or name
        result.setdefault(target, []).append(name)
    return result


def list_actions() -> list[str]:
    names = set()
    for path in glob.glob(os.path.join(ACTION_D, "*.conf")):
        name = os.path.basename(path)[:-5]
        if name in ("helpers", "iptables-common", "nftables-common", "firewallcmd-common",
                    "iptables-ipset-common", "blocklist-common"):
            continue
        names.add(name)
    for path in glob.glob(os.path.join(ACTION_D, "*.local")):
        names.add(os.path.basename(path)[:-6])
    return sorted(names)


def read_filter(name: str) -> dict:
    base, local = filter_paths(name)
    base_content = _read_text(base)
    local_content = _read_text(local)
    source = local_content or base_content
    return {
        "name": name,
        "basePath": base if os.path.isfile(base) else None,
        "localPath": local if os.path.isfile(local) else None,
        "baseContent": base_content,
        "localContent": local_content,
        "custom": os.path.isfile(local),
        "failregex": _extract_regex(source, "failregex"),
        "ignoreregex": _extract_regex(source, "ignoreregex"),
    }


def _extract_regex(text: str, key: str) -> list[str]:
    lines: list[str] = []
    collecting = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if collecting:
            # 续行：缩进且不是新的 key
            if raw[:1].isspace() and "=" not in stripped.split(" ")[0]:
                lines.append(stripped)
                continue
            collecting = False
        if stripped.lower().startswith(f"{key}" + " "):
            _, _, value = stripped.partition("=")
            value = _INLINE_COMMENT.sub("", value).strip()
            if value:
                lines.append(value)
            collecting = True
        elif stripped.lower().startswith(f"{key}="):
            value = stripped[len(key) + 1:].strip()
            if value:
                lines.append(value)
            collecting = True
    return lines


def _emit_regex(lines_out: list[str], key: str, values) -> None:
    if isinstance(values, str):
        values = [line for line in values.splitlines()]
    values = [str(item).strip() for item in (values or []) if str(item).strip()]
    if not values:
        return
    prefix = f"{key} = "
    lines_out.append(prefix + values[0])
    pad = " " * len(prefix)
    for extra in values[1:]:
        lines_out.append(pad + extra)


def write_filter_local(name: str, failregex, ignoreregex=None, definition: dict | None = None) -> str:
    if not NAME_RE.match(name):
        raise ValueError("非法的过滤器名称")
    os.makedirs(FILTER_D, exist_ok=True)
    path = os.path.join(FILTER_D, f"{name}.local")
    backup_file(path)

    lines = [CUSTOM_FILTER_HEADER.rstrip("\n"), "", "[Definition]"]
    for key, value in sanitize_options(definition or {}).items():
        lines.append(f"{key} = {value}")
    _emit_regex(lines, "failregex", failregex)
    _emit_regex(lines, "ignoreregex", ignoreregex)
    store.atomic_write(path, "\n".join(lines) + "\n", 0o644)
    store.record_custom_filter(name, True)
    return path


def delete_filter_local(name: str) -> bool:
    _, path = filter_paths(name)
    if not os.path.isfile(path):
        return False
    backup_file(path)
    try:
        os.unlink(path)
    except OSError:
        return False
    store.record_custom_filter(name, False)
    return True


# ---------------------------------------------------------------------------
# 初始化
# ---------------------------------------------------------------------------

def bootstrap(defaults: dict, jails: dict[str, dict]) -> None:
    """首次安装时写入基线配置（若托管文件已存在则只补缺失项）。"""
    sections = managed_sections()
    default_section = sections.setdefault("DEFAULT", {})
    for key, value in sanitize_options(defaults).items():
        if str(value).strip():
            default_section.setdefault(key, value)
    for name, options in jails.items():
        section = sections.setdefault(name, {})
        for key, value in sanitize_options(options).items():
            if str(value).strip():
                section.setdefault(key, value)
    write_managed(sections)
