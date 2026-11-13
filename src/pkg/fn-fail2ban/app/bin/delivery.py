"""通知投递记录（最近 300 条，最新在前）。

每次真正发出 HTTP 请求的推送都留一条记录，成功失败都记 —— 只记成功的记录等于
没有记录，静默失败的 Webhook 在界面上必须看得见原因。

三条固定要求：

1. 记录里绝不含渠道密钥。这里只保存标题、正文、结果与原因；通道配置里的
   token / secret 从不进入本模块，也就没有泄漏路径。
2. 环形缓冲 + 原子写。写临时文件再 os.replace，进程被杀不会留下半截 JSON；
   文件损坏或不存在一律当成空列表，不能让通知页整页报错。
3. 加锁。投递由后台巡检线程与 HTTP 请求线程同时触发，读改写必须串行。

字段固定为 id / at / event / channel / channel_id / type / ok / detail /
title / body，顺序由 FIELD_ORDER 决定，前端按这个顺序取用，不做猜测。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import store

#: 保留多少条记录。超出后丢弃最旧的。
MAX_KEEP = 300

#: 单条记录里各文本字段的上限（字符）。正文保留整段推送内容才有排查价值，
#: 但不能让一条带长日志的推送把文件撑大。
TITLE_LIMIT = 200
BODY_LIMIT = 600
DETAIL_LIMIT = 300

#: 记录字段的固定顺序。
FIELD_ORDER = ("id", "at", "event", "channel", "channel_id", "type", "ok",
               "detail", "title", "body")

_LOCK = threading.Lock()

#: 最近一次落盘失败的原因，供排查用；正常情况下为空串。
LAST_ERROR = ""


def path() -> str:
    """投递记录文件位置（数据目录随 store 走，测试可覆盖 TRIM_PKGVAR）。"""
    return os.path.join(store.VAR_DIR, "deliveries.json")


def _clip(value, limit: int) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:limit]


def _clean(item) -> dict:
    """把一条外部数据规整成固定字段与固定顺序的记录。"""
    item = item if isinstance(item, dict) else {}
    try:
        at = int(item.get("at") or 0)
    except (TypeError, ValueError):
        at = 0
    return {
        "id": _clip(item.get("id") or "", 40) or uuid.uuid4().hex[:12],
        "at": at,
        "event": _clip(item.get("event") or "manual", 40),
        "channel": _clip(item.get("channel") or "", 80),
        "channel_id": _clip(item.get("channel_id") or "", 40),
        "type": _clip(item.get("type") or "", 40),
        "ok": bool(item.get("ok")),
        "detail": _clip(item.get("detail") or "", DETAIL_LIMIT),
        "title": _clip(item.get("title") or "", TITLE_LIMIT),
        "body": _clip(item.get("body") or "", BODY_LIMIT),
    }


def load() -> list:
    """全部记录，最新在前。文件缺失或损坏都返回空列表。"""
    try:
        with open(path(), "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    records = data.get("records") if isinstance(data, dict) else data
    if not isinstance(records, list):
        return []
    return [_clean(item) for item in records if isinstance(item, dict)]


def _save(records) -> bool:
    """原子写盘：先写同目录临时文件，fsync 后 os.replace 覆盖正式文件。"""
    global LAST_ERROR
    target = path()
    directory = os.path.dirname(target) or "."
    tmp = target + ".tmp"
    payload = json.dumps({"records": records}, ensure_ascii=False, indent=1) + "\n"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
    except OSError as exc:
        LAST_ERROR = str(exc)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
    LAST_ERROR = ""
    return True


def record(*, event, channel, channel_id, type, ok, detail="", title="", body=""):
    """追加一条投递记录并返回它。

    调用方是「刚刚发过一次请求」的代码路径，所以这里不抛异常：记录失败也要让
    推送本身走完，最多影响历史列表。
    """
    item = _clean({
        "id": uuid.uuid4().hex[:12],
        "at": int(time.time()),
        "event": event,
        "channel": channel,
        "channel_id": channel_id,
        "type": type,
        "ok": ok,
        "detail": detail,
        "title": title,
        "body": body,
    })
    with _LOCK:
        records = load()
        records.insert(0, item)
        del records[MAX_KEEP:]
        _save(records)
    return item


def list_items(status: str = "all", limit: int = 60) -> dict:
    """按结果筛选的分页读取。

    items 是筛选后的前 limit 条；total 是符合筛选条件的总条数；kept 是当前
    实际保留的总条数，max 是环形缓冲的上限 —— 两个都带上，界面才能既说清
    「现在存了几条」也说清「最多能存几条」。
    """
    try:
        limit = max(1, min(int(limit), MAX_KEEP))
    except (TypeError, ValueError):
        limit = 60
    records = load()
    wanted = str(status or "all").strip().lower()
    if wanted == "ok":
        picked = [item for item in records if item.get("ok")]
    elif wanted in ("fail", "failed", "error"):
        picked = [item for item in records if not item.get("ok")]
    else:
        picked = list(records)
    # fail 是「全部记录里失败的条数」，与当前筛选无关：界面上的失败徽标
    # 挂在「失败」那个筛选按钮上，切到「成功」时它也必须仍然说真话。
    failed_total = sum(1 for item in records if not item.get("ok"))
    return {"items": picked[:limit], "total": len(picked), "kept": len(records),
            "max": MAX_KEEP, "fail": failed_total}


def clear() -> None:
    """清空全部记录（保留文件本身，避免下次读到缺失文件）。"""
    with _LOCK:
        _save([])
