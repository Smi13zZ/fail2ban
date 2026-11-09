"""应用运行时存储：登录凭证、会话签名与界面偏好。

仅使用 Python 标准库，保证在 fnOS 上零依赖运行。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time

PBKDF2_ITERATIONS = 200_000
SESSION_TTL = 7 * 24 * 3600  # 7 天


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return value if value else default


ETC_DIR = _env("TRIM_PKGETC", "/var/apps/fn-fail2ban/etc")
VAR_DIR = _env("TRIM_PKGVAR", "/var/apps/fn-fail2ban/var")
APP_DIR = _env("TRIM_APPDEST", "/var/apps/fn-fail2ban/target")
WEB_DIR = os.path.join(APP_DIR, "web")

CREDENTIALS_FILE = os.path.join(ETC_DIR, "credentials.json")
PREFS_FILE = os.path.join(VAR_DIR, "prefs.json")
BACKUP_DIR = os.path.join(VAR_DIR, "backup")
CUSTOM_FILTER_LIST = os.path.join(VAR_DIR, "custom-filters.list")

# 界面偏好：theme / lang / sidebar / density 是「看」的，logLines / autoReload /
# confirmDanger 是「用」的。全部有默认值，旧版 prefs.json 缺键时自然回落。
DEFAULT_PREFS: dict = {
    "theme": "auto",
    # "" = 跟随浏览器语言；面板只提供中文与英文两套界面
    "lang": "",
    # auto = 记住上次的展开状态；expanded / collapsed = 每次打开都按这个来
    "sidebar": "auto",
    # comfortable = 默认行高；compact = 压缩表格行高，一屏多看几行
    "density": "comfortable",
    "logLines": 400,
    "autoReload": True,
    "confirmDanger": True,
}

# 每个偏好的合法取值：load / save 都过一遍，坏值回落默认，
# 免得前端写进一个拼错的值后整个界面停在奇怪状态。
PREFS_CHOICES: dict = {
    "theme": ("auto", "dark", "light"),
    "lang": ("", "zh", "en"),
    "sidebar": ("auto", "expanded", "collapsed"),
    "density": ("comfortable", "compact"),
}


def _clean_pref(key: str, value):
    """把一个偏好值收敛到合法范围；不合法返回默认值。"""
    default = DEFAULT_PREFS.get(key)
    choices = PREFS_CHOICES.get(key)
    if choices is not None:
        text = value if isinstance(value, str) else ""
        return text if text in choices else default
    if key == "logLines":
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(20, min(5000, number))
    if key in ("autoReload", "confirmDanger"):
        return bool(value)
    return value if value is not None else default


def ensure_dirs() -> None:
    for path in (ETC_DIR, VAR_DIR, BACKUP_DIR):
        os.makedirs(path, exist_ok=True)


# ---------------------------------------------------------------------------
# 通用读写
# ---------------------------------------------------------------------------

def read_json(path: str, default=None):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def atomic_write(path: str, text: str, mode: int = 0o600) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data, mode: int = 0o600) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode)


# ---------------------------------------------------------------------------
# 凭证
# ---------------------------------------------------------------------------

def _hash_password(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def load_credentials() -> dict | None:
    data = read_json(CREDENTIALS_FILE)
    if not isinstance(data, dict) or "password" not in data:
        return None
    return data


def save_credentials(username: str, password: str) -> dict:
    ensure_dirs()
    salt = secrets.token_bytes(16)
    iterations = PBKDF2_ITERATIONS
    digest = _hash_password(password, salt, iterations)
    data = {
        "username": username,
        "password": {
            "algo": "pbkdf2_sha256",
            "iterations": iterations,
            "salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(digest).decode("ascii"),
        },
        "sessionSecret": secrets.token_hex(32),
        "apiToken": secrets.token_urlsafe(32),
        "createdAt": int(time.time()),
        "updatedAt": int(time.time()),
    }
    existing = load_credentials()
    if existing and isinstance(existing.get("sessionSecret"), str):
        # 保留既有会话密钥，避免改密码时强制重新登录
        data["sessionSecret"] = existing["sessionSecret"]
    atomic_write_json(CREDENTIALS_FILE, data)
    return data


def verify_password(username: str, password: str) -> bool:
    data = load_credentials()
    if not data:
        return False
    if not hmac.compare_digest(str(data.get("username", "")), username):
        return False
    info = data.get("password") or {}
    try:
        salt = base64.b64decode(info.get("salt", ""))
        expected = base64.b64decode(info.get("hash", ""))
        iterations = int(info.get("iterations", PBKDF2_ITERATIONS))
    except (ValueError, TypeError, binascii.Error):
        return False
    if not salt or not expected:
        return False
    actual = _hash_password(password, salt, iterations)
    return hmac.compare_digest(actual, expected)


def change_password(username: str, old_password: str, new_password: str) -> bool:
    if not verify_password(username, old_password):
        return False
    save_credentials(username, new_password)
    return True


def session_secret() -> str | None:
    data = load_credentials()
    if not data:
        return None
    secret = data.get("sessionSecret")
    return secret if isinstance(secret, str) and secret else None


def api_token() -> str | None:
    data = load_credentials()
    if not data:
        return None
    token = data.get("apiToken")
    return token if isinstance(token, str) and token else None


# ---------------------------------------------------------------------------
# 会话（无状态签名令牌）
# ---------------------------------------------------------------------------

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def issue_session(username: str, ttl: int = SESSION_TTL) -> str | None:
    secret = session_secret()
    if not secret:
        return None
    payload = {"u": username, "iat": int(time.time()), "exp": int(time.time()) + ttl}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64e(signature)}"


def verify_session(token: str | None) -> str | None:
    if not token or "." not in token:
        return None
    secret = session_secret()
    if not secret:
        return None
    body, _, signature = token.partition(".")
    expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64d(signature)
    except (ValueError, binascii.Error):
        return None
    if not hmac.compare_digest(expected, provided):
        return None
    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    username = payload.get("u")
    return username if isinstance(username, str) and username else None


# ---------------------------------------------------------------------------
# 界面偏好
# ---------------------------------------------------------------------------

def load_prefs() -> dict:
    prefs = dict(DEFAULT_PREFS)
    stored = read_json(PREFS_FILE)
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key in DEFAULT_PREFS:
                prefs[key] = _clean_pref(key, value)
    return prefs


def save_prefs(updates: dict) -> dict:
    prefs = load_prefs()
    for key, value in (updates or {}).items():
        if key in DEFAULT_PREFS:
            prefs[key] = _clean_pref(key, value)
    ensure_dirs()
    atomic_write_json(PREFS_FILE, prefs)
    return prefs


def record_custom_filter(name: str, present: bool = True) -> None:
    names = set(read_custom_filters())
    if present:
        names.add(name)
    else:
        names.discard(name)
    ensure_dirs()
    atomic_write(CUSTOM_FILTER_LIST, "\n".join(sorted(names)) + ("\n" if names else ""))


def read_custom_filters() -> list[str]:
    try:
        with open(CUSTOM_FILTER_LIST, "r", encoding="utf-8") as handle:
            return [line.strip() for line in handle if line.strip()]
    except OSError:
        return []
