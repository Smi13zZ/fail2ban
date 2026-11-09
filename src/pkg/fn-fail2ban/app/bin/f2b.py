"""fail2ban 交互封装（fail2ban-client / systemctl）。"""

from __future__ import annotations

import glob
import ipaddress
import os
import platform
import re
import shutil
import subprocess
import time

CLIENT_CANDIDATES = (
    "/usr/bin/fail2ban-client",
    "/usr/local/bin/fail2ban-client",
    "/bin/fail2ban-client",
)

SOCKET_CANDIDATES = (
    "/run/fail2ban/fail2ban.sock",
    "/var/run/fail2ban/fail2ban.sock",
)

LOG_CANDIDATES = (
    "/var/log/fail2ban.log",
    "/var/log/fail2ban/fail2ban.log",
    "/var/log/fail2ban.log.1",
)

SERVICE_NAME = "fail2ban"

_STATUS_KEYS = {
    "currently failed": "currentlyFailed",
    "total failed": "totalFailed",
    "currently banned": "currentlyBanned",
    "total banned": "totalBanned",
    "banned ip list": "bannedIps",
    "file list": "fileList",
    "journal match": "journalMatch",
}


class Fail2banError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 基础执行
# ---------------------------------------------------------------------------

def client_path() -> str | None:
    for candidate in CLIENT_CANDIDATES:
        if os.access(candidate, os.X_OK):
            return candidate
    found = shutil.which("fail2ban-client")
    return found


def installed() -> bool:
    return client_path() is not None


def socket_path() -> str | None:
    for candidate in SOCKET_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def run(args: list[str], timeout: int = 25) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", f"未找到可执行文件：{args[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"命令执行超时（>{timeout}s）：{' '.join(args)}"
    except OSError as exc:
        return 126, "", f"命令执行失败：{exc}"
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()


def client(*args: str, timeout: int = 25) -> str:
    binary = client_path()
    if not binary:
        raise Fail2banError("系统未安装 fail2ban，请先执行 apt install -y fail2ban")
    code, out, err = run([binary, *args], timeout=timeout)
    if code != 0:
        detail = err or out or f"退出码 {code}"
        raise Fail2banError(detail.strip())
    return out


# ---------------------------------------------------------------------------
# 状态查询
# ---------------------------------------------------------------------------

def version() -> str:
    binary = client_path()
    if binary:
        code, out, err = run([binary, "--version"], timeout=10)
        text = out or err
        match = re.search(r"v?(\d+\.\d+\.\d+(?:[.\-][\w.]+)?)", text or "")
        if code == 0 and match:
            return match.group(1)
    code, out, _ = run(["dpkg-query", "-W", "-f=${Version}", "fail2ban"], timeout=10)
    if code == 0 and out:
        return out.strip()
    return "未知"


def ping() -> bool:
    if not installed():
        return False
    code, out, _ = run([client_path(), "ping"], timeout=10)
    return code == 0 and "pong" in out.lower()


def service_state() -> dict:
    info = {"active": False, "enabled": False, "raw": "unknown"}
    code, out, _ = run(["systemctl", "is-active", SERVICE_NAME], timeout=10)
    if code == 0:
        info["active"] = out.strip() == "active"
        info["raw"] = out.strip() or "inactive"
    code, out, _ = run(["systemctl", "is-enabled", SERVICE_NAME], timeout=10)
    if code == 0:
        info["enabled"] = out.strip() in ("enabled", "enabled-runtime", "static")
    return info


def info() -> dict:
    is_installed = installed()
    is_running = ping() if is_installed else False
    state = service_state() if is_installed else {"active": False, "enabled": False, "raw": "not-installed"}
    return {
        "installed": is_installed,
        "running": is_running,
        "version": version() if is_installed else None,
        "service": state,
        "socket": socket_path(),
        "client": client_path(),
    }


def jail_names() -> list[str]:
    if not ping():
        return []
    try:
        output = client("status", timeout=20)
    except Fail2banError:
        return []
    match = re.search(r"Jail list:\s*(.*)", output)
    if not match:
        return []
    return [name.strip() for name in match.group(1).split(",") if name.strip()]


def _parse_status(output: str) -> dict:
    result: dict = {}
    for raw in output.splitlines():
        line = raw.strip().lstrip("|`- ").strip()
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = _STATUS_KEYS.get(label.strip().lower())
        if not key:
            continue
        value = value.strip()
        if key in ("currentlyFailed", "totalFailed", "currentlyBanned", "totalBanned"):
            try:
                result[key] = int(value)
            except ValueError:
                result[key] = 0
        elif key in ("bannedIps", "fileList"):
            # 封禁 IP 用空格分隔（fail2ban status 输出），文件列表用逗号分隔：
            # 只按逗号切会把一整串 IP 当成一个条目，解封时也会把整串当参数发过去。
            pattern = r"[\s,]+" if key == "bannedIps" else ","
            result[key] = [item.strip() for item in re.split(pattern, value) if item.strip()] if value else []
        else:
            result[key] = value
    return result


def jail_status(name: str) -> dict:
    try:
        output = client("status", name, timeout=20)
    except Fail2banError as exc:
        return {
            "name": name,
            "ok": False,
            "error": str(exc),
            "currentlyFailed": 0,
            "totalFailed": 0,
            "currentlyBanned": 0,
            "totalBanned": 0,
            "bannedIps": [],
            "fileList": [],
        }
    data = _parse_status(output)
    data.update({"name": name, "ok": True, "error": None})
    data.setdefault("currentlyFailed", 0)
    data.setdefault("totalFailed", 0)
    data.setdefault("currentlyBanned", 0)
    data.setdefault("totalBanned", 0)
    data.setdefault("bannedIps", [])
    data.setdefault("fileList", [])
    return data


# ---------------------------------------------------------------------------
# 操作
# ---------------------------------------------------------------------------

def validate_ip(value: str) -> str:
    text = (value or "").strip()
    try:
        return str(ipaddress.ip_address(text))
    except ValueError:
        pass
    try:
        return str(ipaddress.ip_network(text, strict=False))
    except ValueError:
        raise Fail2banError(f"“{text}” 不是合法的 IP 地址或网段") from None


def ban(name: str, ip: str) -> str:
    return client("set", name, "banip", validate_ip(ip))


def unban(name: str, ip: str) -> str:
    return client("set", name, "unbanip", validate_ip(ip))


def unban_all(name: str) -> list[str]:
    status = jail_status(name)
    removed: list[str] = []
    for ip in status.get("bannedIps", []):
        try:
            client("set", name, "unbanip", ip, timeout=20)
            removed.append(ip)
        except Fail2banError:
            continue
    return removed


def start_jail(name: str) -> str:
    try:
        return client("start", name, timeout=30)
    except Fail2banError:
        return client("reload", name, timeout=60)


def stop_jail(name: str) -> str:
    return client("stop", name, timeout=30)


def reload() -> str:
    return client("reload", timeout=90)


def restart() -> str:
    binary = client_path()
    if binary:
        code, out, err = run([binary, "restart"], timeout=120)
        if code == 0:
            return out or "已重启"
    return service_action("restart")


def service_action(action: str) -> str:
    if action not in ("start", "stop", "restart", "enable", "disable"):
        raise Fail2banError(f"不支持的服务操作：{action}")
    if not installed():
        raise Fail2banError("系统未安装 fail2ban")
    code, out, err = run(["systemctl", action, SERVICE_NAME], timeout=120)
    if code != 0:
        raise Fail2banError((err or out or "systemctl 执行失败").strip())
    return out or f"服务已{action}"


def wait_for_service(timeout: int = 20) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ping():
            return True
        time.sleep(1)
    return False


def install_package() -> dict:
    """通过 apt 安装 fail2ban（需要联网与 root 权限）。"""
    if installed():
        return {"ok": True, "message": "fail2ban 已安装"}
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    steps = [
        ["apt-get", "update", "-qq"],
        ["apt-get", "install", "-y", "-qq", "fail2ban"],
    ]
    logs: list[str] = []
    for step in steps:
        try:
            proc = subprocess.run(
                step, capture_output=True, text=True, timeout=600, env=env, errors="replace"
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "message": f"执行 {' '.join(step)} 失败：{exc}", "log": "\n".join(logs)}
        logs.append((proc.stdout or "")[-4000:])
        logs.append((proc.stderr or "")[-4000:])
        if proc.returncode != 0 and step[1] != "update":
            return {
                "ok": False,
                "message": "fail2ban 安装失败，请检查网络或软件源。",
                "log": "\n".join(logs)[-8000:],
            }
    if not installed():
        return {"ok": False, "message": "安装命令已执行，但仍未找到 fail2ban-client。", "log": "\n".join(logs)[-8000:]}
    run(["systemctl", "enable", SERVICE_NAME], timeout=60)
    run(["systemctl", "start", SERVICE_NAME], timeout=60)
    return {"ok": True, "message": "fail2ban 安装完成"}


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def log_file() -> str | None:
    for candidate in LOG_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    matches = sorted(glob.glob("/var/log/fail2ban*.log"))
    return matches[0] if matches else None


def log_tail(lines: int = 400, keyword: str = "") -> dict:
    path = log_file()
    if not path:
        return {"path": None, "lines": [], "error": "未找到 fail2ban 日志文件（/var/log/fail2ban.log）"}
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            block = min(size, 512 * 1024)
            handle.seek(size - block)
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"path": path, "lines": [], "error": f"读取日志失败：{exc}"}

    collected = raw.splitlines()
    if len(collected) > lines:
        collected = collected[-lines:]
    if keyword:
        needle = keyword.lower()
        collected = [line for line in collected if needle in line.lower()]
    return {"path": path, "lines": collected, "error": None}


def system_info() -> dict:
    os_name, os_version = "未知", ""
    try:
        with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
            data = {}
            for line in handle:
                if "=" in line:
                    key, _, value = line.partition("=")
                    data[key.strip()] = value.strip().strip('"')
        os_name = data.get("PRETTY_NAME") or data.get("NAME") or "未知"
        os_version = data.get("VERSION_ID", "")
    except OSError:
        pass
    return {
        "os": os_name,
        "osVersion": os_version,
        "kernel": platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
        "uptime": _uptime(),
        # language-neutral form; the panel formats it for the active locale
        "uptimeParts": _uptime_parts(),
    }


def _uptime_parts() -> dict:
    """Uptime as numbers, so the panel can format it in the active language.

    The old behaviour baked Chinese into the string, which meant the English
    UI could never render it correctly.
    """
    try:
        with open("/proc/uptime", "r", encoding="ascii") as handle:
            seconds = float(handle.read().split()[0])
    except (OSError, ValueError, IndexError):
        return {}
    days, rest = divmod(int(seconds), 86400)
    hours, rest = divmod(rest, 3600)
    minutes = rest // 60
    return {"days": days, "hours": hours, "minutes": minutes}


def _uptime() -> str:
    """Legacy Chinese string, kept for callers that do not localise."""
    parts = _uptime_parts()
    if not parts:
        return ""
    days, hours, minutes = parts["days"], parts["hours"], parts["minutes"]
    if days:
        return f"{days} 天 {hours} 小时"
    if hours:
        return f"{hours} 小时 {minutes} 分钟"
    return f"{minutes} 分钟"
