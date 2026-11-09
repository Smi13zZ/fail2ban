"""从 fail2ban 日志推断封禁时间，并换算 bantime 时长。

fail2ban-client status <jail> 只列出当前被封禁的 IP，不含封禁时刻，所以
「封禁时间 / 到期时间」只能从 /var/log/fail2ban.log 里自己解析：

* 识别 Ban / Unban / Restore Ban 三类事件行；
* 结果形如 {jail: {ip: {"bannedAt": 秒, "lastSeen": 秒}}}（epoch 秒）；
* 以 (inode, mtime, size) 作缓存键，文件只是追加时仅解析新增的尾部；
* 日志缺失、不可读、内容异常一律按「没有数据」处理，绝不抛异常 —— 时间只是
  附加信息，不能因为它出问题而把整个面板拖垮。

仅使用 Python 标准库。
"""

from __future__ import annotations

import os
import re
import threading
import time

# 日志基准路径（与 f2b.LOG_CANDIDATES 指向同一批位置，这里只列未轮转的基准名，
# 轮转文件由 _rotations() 在同一目录里按命名约定找出来）
LOG_CANDIDATES = (
    "/var/log/fail2ban.log",
    "/var/log/fail2ban/fail2ban.log",
)

MAX_FILE_BYTES = 32 * 1024 * 1024   # 单个文件最多解析的字节数，超出时只读尾部
MAX_EVENTS_PER_FILE = 200000        # 单个文件保留的事件上限，避免异常大的日志吃内存
MAX_ROTATED_FILES = 6               # 轮转文件最多往回追溯几个
ANCHOR_BYTES = 64                   # 续读前回看的字节数，用来确认文件是「追加」而非「重写」

# fail2ban 1.x: "... fail2ban.actions  [1234]: NOTICE  [sshd] Ban 1.2.3.4"
# fail2ban 0.9: "... fail2ban.actions[1234]: NOTICE [sshd] Ban 1.2.3.4"
# "Restore Ban" 必须排在 "Ban" 之前，否则会被拆成 "Ban"
_EVENT_RE = re.compile(
    r"\[(?P<jail>[^\[\]]{1,64})\]\s+"
    r"(?P<action>Restore Ban|Unban|Ban)\s+"
    r"(?P<ip>[0-9A-Fa-f:.]+)\s*$"
)
_STAMP_RE = re.compile(r"(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})")
_ROTATE_RE = re.compile(r"^\.(?P<index>\d+)$|^-(?P<date>\d{8})$")

# bantime 的写法：1h / 30m / 1d / 1w / 600 / "1d 12h" / -1（永久）
_DURATION_RE = re.compile(r"\s*(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>[a-z]*)")
_TIME_UNITS = {
    "": 1, "s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1,
    "m": 60, "min": 60, "mins": 60, "minute": 60, "minutes": 60,
    "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600,
    "d": 86400, "day": 86400, "days": 86400,
    "w": 604800, "week": 604800, "weeks": 604800,
    "mo": 2592000, "mon": 2592000, "month": 2592000, "months": 2592000,
    "y": 31536000, "year": 31536000, "years": 31536000,
}

_LOCK = threading.Lock()
_FILES: dict[str, dict] = {}                 # 单文件解析缓存：path -> 状态
_MERGED: dict = {"key": None, "value": {}}   # 合并结果缓存，键是全部文件的签名


# ---------------------------------------------------------------------------
# bantime 时长
# ---------------------------------------------------------------------------

def parse_bantime(value) -> int | None:
    """把 bantime / findtime 这类写法换算成秒。

    返回秒数；-1（永久封禁）原样返回 -1；空值或认不出来返回 None。
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text in ("-1", "permanent", "forever", "never", "inf"):
        return -1
    negative = text.startswith("-")
    if negative:
        text = text.lstrip("-").strip()
        if not text:
            return -1
    total = 0.0
    matched = False
    # 允许 "1h30m"、"1d 12h"、"2 hours" 这些写法：按「数字 + 单位」整段扫描，
    # 并且要求整串都被消费掉，"1hzz" 这类拼写错误判为认不出来。
    cursor = 0
    for piece in _DURATION_RE.finditer(text):
        if text[cursor:piece.start()].strip():
            return None
        factor = _TIME_UNITS.get(piece.group("unit"))
        if factor is None:
            return None
        total += float(piece.group("value")) * factor
        matched = True
        cursor = piece.end()
    if not matched or text[cursor:].strip():
        return None
    seconds = int(round(total))
    if negative:
        return -1 if seconds <= 1 else -seconds
    return seconds


# ---------------------------------------------------------------------------
# 单行解析
# ---------------------------------------------------------------------------

def _parse_stamp(line: str) -> int | None:
    """取行首的时间戳（日志里是本地时间）并换算成 epoch 秒。"""
    match = _STAMP_RE.match(line)
    if match is None:
        return None
    try:
        year, month, day = (int(part) for part in match.group("date").split("-"))
        hour, minute, second = (int(part) for part in match.group("time").split(":"))
        return int(time.mktime((year, month, day, hour, minute, second, 0, 0, -1)))
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_line(line: str, fallback: int | None) -> tuple[str, str, str, int] | None:
    """解析一行日志，返回 (kind, jail, ip, 秒)；不是封禁/解封行返回 None。"""
    match = _EVENT_RE.search(line)
    if match is None:
        return None
    stamp = _parse_stamp(line)
    if stamp is None:
        stamp = fallback
    if stamp is None:
        # 没有时间戳的事件无法用来算到期时间，直接跳过
        return None
    action = match.group("action")
    # Restore Ban 是 fail2ban 重启后回放数据库里的既有封禁：它不改变最初封禁的时刻，
    # 单独标成 restore，让合并时保留更早的 bannedAt。
    kind = {"Unban": "unban", "Restore Ban": "restore"}.get(action, "ban")
    return (kind, match.group("jail"), match.group("ip"), stamp)


# ---------------------------------------------------------------------------
# 日志文件定位
# ---------------------------------------------------------------------------

def _rotations(base: str) -> list[str]:
    """返回基准日志及其轮转文件，按时间从旧到新排列。"""
    directory = os.path.dirname(base) or "."
    naming = os.path.basename(base)
    try:
        names = os.listdir(directory)
    except OSError:
        return [base] if os.path.isfile(base) else []
    rotated: list[tuple[tuple[int, int], str]] = []
    for name in names:
        if not name.startswith(naming):
            continue
        suffix = name[len(naming):]
        if not suffix or suffix.endswith(".gz"):
            # 同名的其它文件不管；压缩包是更早的历史，不解析
            continue
        match = _ROTATE_RE.match(suffix)
        if match is None:
            continue
        if match.group("index"):
            rotated.append(((0, -int(match.group("index"))), name))   # 编号越大越旧
        else:
            rotated.append(((1, int(match.group("date"))), name))      # 日期越早越旧
    rotated.sort()
    ordered = [os.path.join(directory, name) for _, name in rotated[-MAX_ROTATED_FILES:]]
    ordered.append(base)
    return [path for path in ordered if os.path.isfile(path)]


def _log_files() -> list[str]:
    """按时间顺序（旧 → 新）返回要解析的日志文件列表。"""
    for base in LOG_CANDIDATES:
        if os.path.isfile(base):
            return _rotations(base)
    found: list[str] = []
    for base in LOG_CANDIDATES:
        directory = os.path.dirname(base) or "."
        try:
            names = sorted(os.listdir(directory))
        except OSError:
            continue
        for name in names:
            if name.startswith("fail2ban") and name.endswith(".log"):
                found.append(os.path.join(directory, name))
    return found


# ---------------------------------------------------------------------------
# 解析与缓存
# ---------------------------------------------------------------------------

def _anchor(handle, offset: int) -> bytes:
    """取 offset 之前的一小段字节，作为「续读位置」的指纹。"""
    if offset <= 0:
        return b""
    try:
        handle.seek(max(0, offset - ANCHOR_BYTES))
        return handle.read(min(ANCHOR_BYTES, offset))
    except OSError:
        return b""


def _read_lines(handle, start: int, skip_first: bool) -> tuple[list[str], int]:
    """从 start 开始读到文件末尾，返回 (行列表, 新的安全偏移)。

    按 MAX_FILE_BYTES 分块循环，保证一次就把新增内容读完 —— 否则一次写入
    超过单块上限时，剩下的尾部要等到下次文件再变化才会被解析。
    """
    handle.seek(0, os.SEEK_END)
    end = handle.tell()
    found: list[str] = []
    offset = start
    first = True
    while offset < end:
        handle.seek(offset)
        raw = handle.read(min(MAX_FILE_BYTES, end - offset))
        if not raw:
            break
        cut = raw.rfind(b"\n")
        # 只消费完整的行，末尾的半行留给下次，避免把一行截成两段
        chunk = raw[: cut + 1] if cut >= 0 else b""
        if not chunk:
            break   # 连一个换行都没有：等它写完再来
        offset += len(chunk)
        lines = chunk.decode("utf-8", errors="replace").splitlines()
        if first and skip_first and lines:
            lines = lines[1:]   # 从文件中间读起时，第一行可能是半行
        first = False
        found.extend(lines)
        if offset >= end:
            break
    return found, offset


def _load_file(path: str) -> list[tuple]:
    """解析单个日志文件（带缓存），返回事件列表；出错返回空列表。"""
    try:
        stat = os.stat(path)
    except OSError:
        return []
    signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
    cached = _FILES.get(path)
    if cached and cached["signature"] == signature:
        return cached["events"]

    events: list[tuple] = []
    start = 0
    skip_first = False
    cached_offset = int(cached.get("offset", 0)) if cached else 0
    try:
        handle = open(path, "rb")
    except OSError:
        _FILES.pop(path, None)
        return []
    try:
        # 只有「同一个 inode + 位置还在 + 位置前面那 64 字节没变」才能续读。
        # 少了最后一条检查，logrotate 的 copytruncate 会把新内容当成旧内容跳过。
        if (cached is not None
                and cached.get("inode") == stat.st_ino
                and 0 < cached_offset <= stat.st_size
                and _anchor(handle, cached_offset) == cached.get("anchor", b"")):
            start = cached_offset
            events = list(cached["events"])
        elif stat.st_size > MAX_FILE_BYTES:
            # 超大文件只保留尾部，足够覆盖最近一段时间的封禁
            start = max(0, stat.st_size - MAX_FILE_BYTES)
            skip_first = True
        lines, offset = _read_lines(handle, start, skip_first)
        anchor = _anchor(handle, offset)
    except OSError:
        handle.close()
        _FILES.pop(path, None)
        return []
    handle.close()

    stamp: int | None = None
    for line in lines:
        event = _parse_line(line, stamp)
        if event is None:
            continue
        stamp = event[3]
        events.append(event)

    if len(events) > MAX_EVENTS_PER_FILE:
        events = events[-MAX_EVENTS_PER_FILE:]

    _FILES[path] = {"signature": signature, "inode": stat.st_ino,
                    "offset": offset, "anchor": anchor, "events": events}
    return events


def collect(force: bool = False) -> dict[str, dict[str, dict]]:
    """返回 {jail: {ip: {"bannedAt": 秒, "lastSeen": 秒}}}。

    只包含「最后一次事件是封禁」的 IP：解封过的 IP 会被移出结果。返回值来自
    缓存，调用方只读、不要原地修改。任何异常都返回空结构。
    """
    try:
        with _LOCK:
            stamps: list[tuple[str, int, int, int]] = []
            for path in _log_files():
                try:
                    stat = os.stat(path)
                except OSError:
                    continue
                stamps.append((path, stat.st_ino, stat.st_mtime_ns, stat.st_size))
            key = tuple(stamps)
            if not force and _MERGED["key"] == key:
                return _MERGED["value"]

            state: dict[str, dict[str, dict]] = {}
            for path, _ino, _mtime, _size in stamps:
                for kind, jail, ip, stamp in _load_file(path):
                    bucket = state.get(jail)
                    if kind == "unban":
                        # 解封过就整条移出；不能只是留在空桶里
                        if bucket:
                            bucket.pop(ip, None)
                        continue
                    bucket = state.setdefault(jail, {})
                    previous = bucket.get(ip)
                    if kind == "restore" and previous is not None:
                        # 重启回放：保留最初封禁时刻，只把 lastSeen 往后推
                        previous["lastSeen"] = stamp
                        continue
                    bucket[ip] = {"bannedAt": stamp, "lastSeen": stamp}
            state = {jail: ips for jail, ips in state.items() if ips}

            _MERGED["key"] = key
            _MERGED["value"] = state
            return state
    except Exception:  # noqa: BLE001 - 解析失败不能影响面板其它功能
        return {}


def reset_cache() -> None:
    """清空全部缓存（自检 / 测试用）。"""
    with _LOCK:
        _FILES.clear()
        _MERGED["key"] = None
        _MERGED["value"] = {}
