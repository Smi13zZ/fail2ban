"""通知渠道（11 种）、双语推送、密钥掩码与事件流。

设计要点：

1. **渠道元数据驱动**。CHANNEL_TYPES 同时给出字段顺序、字段类型、是否必填、
   zh 源文与 zh/en 双语标签和说明；前端据此渲染表单，新增渠道只改这一处，
   界面不用内置字段表。
2. **密钥掩码在服务端做**。GET 一律走 masked_settings()，密钥字段换成
   SECRET_MARK（"__SET__"）；PUT 走 save_settings()，收到 "__SET__"、空串或
   干脆没带这个字段时按渠道 id 沿用磁盘上的原值。整份配置回传时 channels 是
   list、会被整体替换，所以这道防线只能写在服务端 —— 前端收窄是第二道。
3. **投递结果不能只看 HTTP 200**。企业微信/钉钉 200 里带 errcode，Telegram 200
   里带 "ok": false，Server 酱/PushPlus/WxPusher 各有自己的 code；只判状态码
   会把静默失败当成功，那是最坏的一种通知故障。
4. **推送正文中英双语**：标题「中文 / English」，正文「中文段 + 空行 + 英文段」。
   收件人手机上的语言与面板界面语言并不总是一致，双语才能保证两边都读得懂。

仅使用标准库。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import delivery
import f2b
import fnknock
import store

SETTINGS_FILE = os.path.join(store.VAR_DIR, "notifications.json")
EVENTS_FILE = os.path.join(store.VAR_DIR, "events.json")
MAX_EVENTS = 200

#: 浏览器能看到的密钥占位符。它本身不是密钥，也永远不该被写回磁盘。
SECRET_MARK = "__SET__"

# 各服务端点。做成模块级常量，验证脚本把它们指向本地 sink 就能检查真实请求
# 构造（路径、查询串、正文、请求头），不需要任何外网访问。
SERVERCHAN_API = "https://sctapi.ftqq.com"
PUSHPLUS_API = "https://www.pushplus.plus/send"
WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"
DEFAULT_TELEGRAM_API = "https://api.telegram.org"
DEFAULT_WECOM_API = "https://qyapi.weixin.qq.com"

#: 单条消息的 UTF-8 字节预算。企业微信的硬上限是 2048 字节，而一个汉字占三字节，
#: 超了会被整条拒收且不说明原因，所以按字节裁而不是按字符。
MAX_TEXT_BYTES = 1800
MAX_TITLE_BYTES = 240

_LOCK = threading.Lock()
#: 设置的读改写要串行：两个 PUT 交错会让后一个覆盖掉前一个的改动。
_SETTINGS_LOCK = threading.Lock()
_event_seq = 0


# ---------------------------------------------------------------------------
# 渠道元数据
# ---------------------------------------------------------------------------

#: 前端的字段文案靠「中文源文反查字典」翻译，所以同名的字段在不同渠道里
#: 必须给出**各自那句话**，否则会串成别的渠道的说明。key 与中文源文一起做键：
#: 中文源文相同就直接命中字典，不同就各自独立成句。
_FIELD_HELP = {
    ("url", "Webhook 地址"): ("群机器人的完整 Webhook 地址",
                            "Full webhook URL of the group bot"),
    ("secret", "加签密钥（可选）"): ("机器人开启「加签」时必填，未开启留空",
                                  "Required when the bot has signing enabled, otherwise leave empty"),
    ("secret", "签名密钥（可选）"): ("机器人开启「签名校验」时必填，未开启留空",
                                  "Required when signature verification is on, otherwise leave empty"),
    ("apiBase", "API 地址（可选）"): ("留空使用 https://qyapi.weixin.qq.com",
                                   "Empty means https://qyapi.weixin.qq.com"),
}


def _field(key, zh, en, hint_zh="", hint_en="", type="text", required=False,
           placeholder="", default="", options=None):
    """一个字段的完整描述。所有键都补齐，前端不用做存在性判断。"""
    override = _FIELD_HELP.get((key, zh))
    if override:
        hint_zh, hint_en = override
    return {
        "key": key,
        "label": zh,
        "hint": hint_zh,
        "type": type,
        "required": bool(required),
        "placeholder": placeholder,
        "default": default,
        "options": list(options or []),
        "labels_i18n": {"zh": zh, "en": en or zh},
        "hints_i18n": {"zh": hint_zh, "en": hint_en or hint_zh},
    }


def _channel(value, label_zh, label_en, hint_zh, hint_en, fields, secret_fields):
    """一个渠道类型的完整描述（含类型级的双语标签与说明）。"""
    return {
        "value": value,
        "label": label_zh,
        "hint": hint_zh,
        "label_en": label_en,
        "hint_en": hint_en,
        "fields": fields,
        "secretFields": list(secret_fields),
        # 字段级的双语表在 fields[].labels_i18n/hints_i18n 里；这里额外给两个
        # 类型级键，前端取类型名与类型说明时不必再回退到中文源文反查。
        "labels_i18n": {
            "__label__": {"zh": label_zh, "en": label_en or label_zh},
        },
        "hints_i18n": {
            "__hint__": {"zh": hint_zh, "en": hint_en or hint_zh},
        },
    }


CHANNEL_TYPES = [
    _channel(
        "dingtalk", "钉钉机器人", "DingTalk bot",
        "群机器人 Webhook 地址；填写加签密钥后会自动计算签名。",
        "Group bot webhook URL; a signing secret is applied automatically when set.",
        [
            _field("url", "Webhook 地址", "Webhook URL",
                   "群机器人的完整 Webhook 地址",
                   "Full webhook URL of the group bot",
                   required=True,
                   placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."),
            _field("secret", "加签密钥（可选）", "Signing secret (optional)",
                   "机器人开启「加签」时必填，未开启留空",
                   "Required when the bot has signing enabled, otherwise leave empty",
                   type="password", placeholder="SEC 开头的密钥"),
        ],
        ["url", "secret"],
    ),
    _channel(
        "wecom", "企业微信群机器人", "WeCom group bot",
        "群机器人 Webhook 地址，消息发送到该群。",
        "Group bot webhook URL; messages are posted to that group chat.",
        [
            _field("url", "Webhook 地址", "Webhook URL",
                   "群机器人的完整 Webhook 地址",
                   "Full webhook URL of the group bot",
                   required=True,
                   placeholder="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=..."),
        ],
        ["url"],
    ),
    _channel(
        "wecom_app", "企业微信应用消息", "WeCom app message",
        "通过自建应用推送，可直接发给指定成员。需在企业微信后台把运行 "
        "fail2ban 的设备公网 IP 加入该应用的「企业可信 IP」，否则会返回 "
        "errcode 60020。",
        "Pushes through a self-built app and can target specific members. Add "
        "this device's public IP to the app's trusted-IP list, otherwise the "
        "call returns errcode 60020.",
        [
            _field("corpid", "企业 ID (corpid)", "Company ID (corpid)",
                   "企业微信「我的企业」页里的企业 ID",
                   "The company ID on the WeCom profile page",
                   required=True, placeholder="ww1234567890abcdef"),
            _field("corpsecret", "应用 Secret", "App secret",
                   "应用管理页里该应用的 Secret",
                   "The secret shown on the app's management page",
                   type="password", required=True, placeholder="应用管理页中的 Secret"),
            _field("agentid", "应用 AgentId", "App AgentId",
                   "应用详情页里的 AgentId，纯数字",
                   "Numeric AgentId of the app",
                   required=True, placeholder="1000002"),
            _field("touser", "接收成员", "Recipients",
                   "多个成员用 | 分隔，@all 表示全员",
                   "Separate multiple members with |; @all means everyone",
                   required=True, placeholder="@all 或 zhangsan|lisi", default="@all"),
            _field("apiBase", "API 地址（可选）", "API base (optional)",
                   "留空使用官方地址 https://qyapi.weixin.qq.com",
                   "Empty means https://qyapi.weixin.qq.com",
                   placeholder="默认 https://qyapi.weixin.qq.com"),
        ],
        ["corpsecret"],
    ),
    _channel(
        "feishu", "飞书自定义机器人", "Feishu custom bot",
        "Webhook 地址；填写签名密钥后会自动带上 timestamp 与 sign。",
        "Webhook URL; timestamp and sign are added automatically when a secret is set.",
        [
            _field("url", "Webhook 地址", "Webhook URL",
                   "群机器人的完整 Webhook 地址",
                   "Full webhook URL of the group bot",
                   required=True,
                   placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."),
            _field("secret", "签名密钥（可选）", "Signing secret (optional)",
                   "机器人开启「签名校验」时必填，未开启留空",
                   "Required when signature verification is on, otherwise leave empty",
                   type="password"),
        ],
        ["url", "secret"],
    ),
    _channel(
        "bark", "Bark (iOS)", "Bark (iOS)",
        "填写 Bark 服务器地址，消息以 POST 发送到该地址。",
        "Bark server address; messages are posted to it.",
        [
            _field("url", "Bark 地址", "Bark URL",
                   "Bark App 里显示的服务器地址加设备密钥",
                   "Server address plus device key as shown in the Bark app",
                   required=True, placeholder="https://api.day.app/你的Key"),
        ],
        ["url"],
    ),
    _channel(
        "slack", "Slack", "Slack",
        "Incoming Webhook 地址。",
        "Incoming webhook URL.",
        [
            _field("url", "Webhook 地址", "Webhook URL",
                   "Slack App 的 Incoming Webhook 地址",
                   "Incoming webhook URL of your Slack app",
                   required=True,
                   placeholder="https://hooks.slack.com/services/..."),
        ],
        ["url"],
    ),
    _channel(
        "serverchan", "Server 酱", "ServerChan",
        "通过 SendKey 推送到微信服务号。",
        "Pushes to the WeChat service account through a SendKey.",
        [
            _field("sendkey", "SendKey", "SendKey",
                   "Server 酱后台的 SendKey，通常以 SCT 开头",
                   "The SendKey from your ServerChan account, usually starting with SCT",
                   type="password", required=True, placeholder="SCT..."),
        ],
        ["sendkey"],
    ),
    _channel(
        "pushplus", "PushPlus", "PushPlus",
        "通过 token 推送到微信。",
        "Pushes to WeChat through a token.",
        [
            _field("token", "Token", "Token",
                   "PushPlus 账号页给出的推送 token",
                   "The push token shown on your PushPlus account page",
                   type="password", required=True, placeholder="32 位 token"),
        ],
        ["token"],
    ),
    _channel(
        "wxpusher", "WxPusher", "WxPusher",
        "通过应用 AppToken 推送到微信；UID 与主题 ID 至少填一个。",
        "Pushes to WeChat through an application AppToken; fill UIDs or topic IDs.",
        [
            _field("appToken", "AppToken", "AppToken",
                   "WxPusher 应用的 AppToken",
                   "AppToken of your WxPusher application",
                   type="password", required=True, placeholder="AT_..."),
            _field("uids", "UID 列表（可选）", "UID list (optional)",
                   "多个 UID 用英文逗号分隔",
                   "Separate multiple UIDs with commas",
                   placeholder="UID_xxx,UID_yyy"),
            _field("topics", "主题 ID 列表（可选）", "Topic ID list (optional)",
                   "多个主题 ID 用英文逗号分隔",
                   "Separate multiple topic IDs with commas",
                   placeholder="1234,5678"),
        ],
        ["appToken"],
    ),
    _channel(
        "telegram", "Telegram", "Telegram",
        "通过 Bot 推送；Bot Token 从 @BotFather 获取。",
        "Pushes through a bot; get the Bot Token from @BotFather.",
        [
            _field("botToken", "Bot Token", "Bot token",
                   "向 @BotFather 申请机器人时拿到的 token",
                   "The token @BotFather gives you for the bot",
                   type="password", required=True,
                   placeholder="123456789:AA..."),
            _field("chatId", "Chat ID", "Chat ID",
                   "私聊填用户 ID，群组填负数 ID",
                   "A user ID for private chats, a negative ID for groups",
                   required=True, placeholder="123456789 或 -1001234567890"),
            _field("apiBase", "API 地址（可选）", "API base (optional)",
                   "留空使用 https://api.telegram.org，走自建中转时填这里",
                   "Empty means https://api.telegram.org; set it when using a relay",
                   placeholder="默认 https://api.telegram.org"),
        ],
        ["botToken"],
    ),
    _channel(
        "generic", "自定义 Webhook", "Custom webhook",
        "以 POST 发送 JSON：app / event / title / text / jail / ips / time。",
        "POSTs JSON with app / event / title / text / jail / ips / time.",
        [
            _field("url", "目标地址", "Target URL",
                   "接收 POST 请求的完整地址，返回 2xx 即视为成功",
                   "Full address that receives the POST request; any 2xx counts as success",
                   required=True, placeholder="https://example.com/hook"),
            _field("secret", "Bearer 令牌（可选）", "Bearer token (optional)",
                   "填写后会作为 Authorization: Bearer 发送",
                   "Sent as Authorization: Bearer when filled in",
                   type="password",
                   placeholder="填写后会作为 Authorization: Bearer 发送"),
        ],
        # 目标地址是用户自己的接口，不是凭证；只有 Bearer 令牌算密钥
        ["secret"],
    ),
]

#: 下拉框里的展示顺序：先列原有七种（用户已经配好的渠道不会被排到后面），
#: 再接新增的四种。排序放在定义之后，定义区就不用为了顺序挪来挪去。
_CHANNEL_ORDER = ("dingtalk", "wecom", "wecom_app", "feishu", "bark", "slack",
                  "serverchan", "pushplus", "wxpusher", "telegram", "generic")
CHANNEL_TYPES.sort(key=lambda item: _CHANNEL_ORDER.index(item["value"]))

#: 渠道类型定义表，按 value 索引（内部用）。
CHANNEL_BY_TYPE = {item["value"]: item for item in CHANNEL_TYPES}

#: 各类型的必填字段，用来判断「配没配齐」；由元数据推导，不另外维护一份。
REQUIRED_FIELDS = {
    item["value"]: tuple(f["key"] for f in item["fields"] if f["required"])
    for item in CHANNEL_TYPES
}

#: 所有可能出现过的字段名，用于清洗渠道配置（拒绝任意键）。
CHANNEL_FIELD_KEYS = set()
for _item in CHANNEL_TYPES:
    for _f in _item["fields"]:
        CHANNEL_FIELD_KEYS.add(_f["key"])
del _item, _f

EVENT_TYPES = [
    {"key": "ban", "label": "新封禁 IP", "help": "有 IP 被封禁时推送"},
    {"key": "unban", "label": "解封 IP", "help": "封禁被解除时推送（较嘈杂）"},
    {"key": "serviceDown", "label": "服务异常", "help": "fail2ban 失去响应时推送"},
    {"key": "serviceRecover", "label": "服务恢复", "help": "fail2ban 恢复响应时推送"},
    {"key": "update", "label": "面板新版本", "help": "发现面板自身有新版本时推送"},
]

DEFAULT_SETTINGS = {
    "enabled": False,
    "browserNotify": False,
    "pollInterval": 30,
    "events": {"ban": True, "unban": False, "serviceDown": True,
               "serviceRecover": True, "update": True},
    "channels": [],
}


# ---------------------------------------------------------------------------
# 设置：读取 / 合并 / 掩码
# ---------------------------------------------------------------------------

def _channel_keys() -> set:
    return {"id", "type", "name", "enabled"} | CHANNEL_FIELD_KEYS


def _clean_channel(item: dict) -> dict:
    """只保留已知字段，避免渠道配置里塞进任意键或非字符串值。"""
    allowed = _channel_keys()
    channel = {
        "id": str(item.get("id") or os.urandom(6).hex()),
        "type": str(item.get("type") or "generic"),
        "name": str(item.get("name") or ""),
        "enabled": bool(item.get("enabled", True)),
    }
    for key in sorted(allowed):
        if key in channel:
            continue
        value = item.get(key)
        if value is None:
            continue
        channel[key] = str(value).strip()[:2048]
    return channel


def load_settings() -> dict:
    """读磁盘上的配置，密钥是**真实值**。

    只给后端内部使用（发送、合并）。任何要回给浏览器的路径都必须走
    masked_settings()，否则密钥会直接泄漏。
    """
    stored = store.read_json(SETTINGS_FILE)
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if isinstance(stored, dict):
        for key, value in stored.items():
            if key == "events" and isinstance(value, dict):
                settings["events"].update({k: bool(v) for k, v in value.items()
                                           if k in settings["events"]})
            elif key == "channels" and isinstance(value, list):
                settings["channels"] = [_clean_channel(item) for item in value
                                        if isinstance(item, dict)]
            elif key in settings:
                settings[key] = value
    try:
        settings["pollInterval"] = max(10, min(int(settings["pollInterval"]), 3600))
    except (TypeError, ValueError):
        settings["pollInterval"] = 30
    return settings


def _is_secret(channel_type: str, key: str) -> bool:
    meta = CHANNEL_BY_TYPE.get(channel_type) or {}
    return key in (meta.get("secretFields") or ())


def _merge_field(old_value: str, new_value, *, secret: bool) -> str:
    """一个字段的新旧合并规则。

    - 密钥字段："__SET__" / 空串 / 未提交 都表示「保持原值」，
      只有真填了新值才替换。"" 与 "__SET__" 等价，因为前端拿到的打码值就是
      未知的，留空是用户表达「不改」的正常方式。
    - 普通字段：空串表示清空；但 "__SET__" 是打码值误入，一律保留旧值 ——
      透传等于把字面量写进磁盘，这条兜底挡过真实事故。
    """
    old_text = "" if old_value is None else str(old_value)
    if new_value is None:
        return old_text
    text = str(new_value).strip()
    if text == SECRET_MARK:
        return old_text
    if not text:
        return old_text if secret else ""
    return text


def _merge_channel(old: dict | None, new: dict) -> dict:
    """按 id 合并单个渠道：未知字段丢弃，密钥留空沿用原值。"""
    new = _clean_channel(new)
    old = _clean_channel(old) if isinstance(old, dict) else None
    # 没有旧配置、或类型换了，都以新提交为准（旧字段对新类型没有意义）
    if not old or old.get("type") != new.get("type"):
        return new
    merged = dict(new)
    ctype = new["type"]
    for field in (CHANNEL_BY_TYPE.get(ctype) or {}).get("fields", []):
        key = field["key"]
        merged[key] = _merge_field(old.get(key), new.get(key),
                                   secret=_is_secret(ctype, key))
    return merged


def _merge_channels(old_list, new_list) -> list:
    """列表按 id 合并。

    这是整份配置回传时最关键的一步：channels 是 list，store 的深合并对它
    是整体替换，所以必须在这里逐个 id 跟旧配置对齐，否则打码值会被当成新密钥。
    """
    by_id = {}
    for item in (old_list or []):
        if isinstance(item, dict) and str(item.get("id") or ""):
            by_id[str(item["id"])] = item

    result = []
    for item in (new_list or []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "")
        # 提交里没提到的老渠道直接丢弃：整份配置保存的语义是「以提交的为准」，
        # 保留未提交的条目会让删除操作失效。密钥只在 id 对得上时继承。
        result.append(_merge_channel(by_id.get(cid), item) if cid else _clean_channel(item))
    return result


def save_settings(updates: dict) -> dict:
    """保存配置补丁，返回**打码后**的配置。

    返回值直接进 HTTP 响应体，所以这里就打好码，调用方不必记得再转一次 ——
    忘记转一次就等于把密钥发给浏览器。需要真实密钥的只有发送逻辑，它读
    load_settings()。
    """
    updates = updates if isinstance(updates, dict) else {}
    with _SETTINGS_LOCK:
        return _apply_and_store(updates)


def _apply_and_store(updates: dict) -> dict:
    """在设置锁内做「读 → 合并 → 原子写 → 打码」，调用方负责加锁。"""
    current = load_settings()
    settings = json.loads(json.dumps(current))

    if "enabled" in updates:
        settings["enabled"] = bool(updates["enabled"])
    if "browserNotify" in updates:
        settings["browserNotify"] = bool(updates["browserNotify"])
    if "pollInterval" in updates:
        try:
            settings["pollInterval"] = max(10, min(int(updates["pollInterval"]), 3600))
        except (TypeError, ValueError):
            pass
    if isinstance(updates.get("events"), dict):
        for key in settings["events"]:
            if key in updates["events"]:
                settings["events"][key] = bool(updates["events"][key])
    if isinstance(updates.get("channels"), list):
        settings["channels"] = _merge_channels(current.get("channels"), updates["channels"])

    store.ensure_dirs()
    store.atomic_write_json(SETTINGS_FILE, settings)
    return _mask(settings)


def _mask_channel(channel: dict) -> dict:
    """一个渠道给浏览器看的形态：密钥字段只留占位符。"""
    channel = _clean_channel(channel)
    ctype = channel.get("type") or ""
    out = dict(channel)
    for key in CHANNEL_FIELD_KEYS:
        if not _is_secret(ctype, key):
            continue
        out[key] = SECRET_MARK if str(channel.get(key) or "").strip() else ""
    return out


def _mask(settings: dict) -> dict:
    """整份配置的打码副本：密钥字段换成 SECRET_MARK。

    非密钥字段原样返回，前端才能正常回显可读配置（AgentId、接收成员、API 地址）。
    """
    out = dict(settings)
    out["channels"] = [_mask_channel(item) for item in settings.get("channels", [])]
    return out


def masked_settings() -> dict:
    """读磁盘上的配置并打码，给前端用。"""
    return _mask(load_settings())


def channel_ready(channel: dict) -> bool:
    """渠道是否已填齐该类型的必填项（没填齐的不推送，也不报错）。"""
    channel = channel if isinstance(channel, dict) else {}
    ctype = str(channel.get("type") or "")
    for key in REQUIRED_FIELDS.get(ctype, ()):
        if not str(channel.get(key) or "").strip():
            return False
    return bool(ctype and CHANNEL_BY_TYPE.get(ctype))


# ---------------------------------------------------------------------------
# 事件流（浏览器通知轮询仍依赖这段语义，键名与顺序保持不变）
# ---------------------------------------------------------------------------

def _load_events() -> list[dict]:
    global _event_seq
    data = store.read_json(EVENTS_FILE)
    events = data.get("events") if isinstance(data, dict) else None
    if not isinstance(events, list):
        events = []
    try:
        _event_seq = int(data.get("seq", 0)) if isinstance(data, dict) else 0
    except (TypeError, ValueError):
        _event_seq = 0
    if _event_seq < len(events):
        _event_seq = max([int(e.get("id", 0)) for e in events] or [0])
    return events


def record_event(kind: str, title: str, text: str, jail: str | None = None,
                 ips: list | None = None) -> dict:
    global _event_seq
    with _LOCK:
        events = _load_events()
        _event_seq += 1
        event = {
            "id": _event_seq,
            "type": kind,
            "title": title,
            "text": text,
            "jail": jail,
            "ips": list(ips or []),
            "time": int(time.time()),
        }
        events.append(event)
        events = events[-MAX_EVENTS:]
        store.ensure_dirs()
        store.atomic_write_json(EVENTS_FILE, {"seq": _event_seq, "events": events})
        return event


def list_events(since: int = 0, limit: int = 50) -> list[dict]:
    with _LOCK:
        events = _load_events()
    if since:
        events = [e for e in events if int(e.get("id", 0)) > since]
        return events[:limit]
    return events[-limit:]


def clear_events() -> None:
    with _LOCK:
        store.ensure_dirs()
        store.atomic_write_json(EVENTS_FILE, {"seq": _event_seq, "events": []})


# ---------------------------------------------------------------------------
# 双语消息
# ---------------------------------------------------------------------------

def bilingual_title(zh: str, en: str) -> str:
    """标题统一是「中文 / English」。"""
    return "%s / %s" % (zh, en)


def bilingual_text(zh_lines, en_lines) -> str:
    """正文是「中文段 + 空行 + 英文段」。

    空行不能省：两段紧挨着时，中文段一换行读者就分不清边界在哪。
    """
    zh = [line for line in (zh_lines or []) if line]
    en = [line for line in (en_lines or []) if line]
    text = "\n".join(zh)
    if en:
        text = text + ("\n\n" if text else "") + "\n".join(en)
    return text


def _clip(text, max_bytes: int) -> str:
    """按 UTF-8 字节裁剪，并显式说明被截断了。"""
    text = str(text or "")
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    suffix = "…(过长已截断)"
    keep = max(0, max_bytes - len(suffix.encode("utf-8")))
    return raw[:keep].decode("utf-8", "ignore") + suffix


def _stamp(ts=None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts or time.time()))


def _shorten_ips(ips, limit: int = 10) -> tuple[str, int]:
    """把 IP 列表压成一行，返回 (文本, 被省略的个数)。"""
    items = [str(ip) for ip in (ips or [])]
    shown = items[:limit]
    return ", ".join(shown), max(0, len(items) - len(shown))


def format_test(channel_name: str = "") -> tuple[str, str]:
    """测试通知的标题与正文，与新版本推送走同一套双语排版。"""
    name_zh = str(channel_name or "").strip() or "该"
    name_en = str(channel_name or "").strip() or "this"
    title = bilingual_title("Fail2ban 测试通知", "Notification test")
    now = _stamp()
    zh = [
        "收到这条消息说明「%s」渠道配置正确。" % name_zh,
        "封禁、解封与服务异常都会推送到这里。",
        "时间：%s" % now,
    ]
    en = [
        "If you can read this, the '%s' channel is configured correctly." % name_en,
        "Ban, unban and service alerts are delivered here.",
        "Time: %s" % now,
    ]
    return title, bilingual_text(zh, en)


def format_event(event: dict) -> tuple[str, str]:
    """把一个事件渲染成双语 (标题, 正文)。

    事件里已经带了渲染好的双语 title/text（来自 record_event），这里直接复用，
    保证「界面看到的那条」与「手机上收到的那条」是同一段文字。
    """
    title = str((event or {}).get("title") or "").strip()
    text = str((event or {}).get("text") or "").strip()
    return title, text


# ---------------------------------------------------------------------------
# HTTP 传输
# ---------------------------------------------------------------------------

def _is_loopback(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("localhost", "::1") or host.startswith("127.")


def _opener(url: str):
    """指向本机地址的 Webhook 不能走系统代理，否则同一个盒子里的请求也会被拦。"""
    if _is_loopback(url):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


def _open(request, timeout: float):
    return _opener(request.full_url).open(request, timeout=timeout)


def _post_json(url: str, payload: dict, timeout: float = 10,
               headers: dict | None = None) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    merged = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        merged.update(headers)
    request = urllib.request.Request(url, data=data, method="POST", headers=merged)
    with _open(request, timeout) as response:
        return response.status, response.read(8192).decode("utf-8", errors="replace")


def _post_form(url: str, fields: dict, timeout: float = 10) -> tuple[int, str]:
    data = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded; charset=utf-8"})
    with _open(request, timeout) as response:
        return response.status, response.read(8192).decode("utf-8", errors="replace")


def _fetch(url: str, timeout: float = 10) -> tuple[int, str]:
    with _open(urllib.request.Request(url, method="GET"), timeout) as response:
        return response.status, response.read(4096).decode("utf-8", errors="replace")


def _judge(status: int, text: str, code_field: str | None = None,
           expect=None) -> tuple[bool, str]:
    """把一次响应翻译成 (是否成功, 说明)。

    code_field 指明真正表达结果的 JSON 键：企业微信/钉钉的 errcode、
    Telegram 的 ok、Server 酱/PushPlus/WxPusher 的 code。只看 HTTP 状态会把
    「200 + 业务失败」当成投递成功，这是最难发现的一类通知故障。
    """
    text = text or ""
    if status < 200 or status >= 300:
        return False, "HTTP %d %s" % (status, text[:200])
    if not code_field:
        return True, text[:120]
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        # 上游没给 JSON 就没法判定，按送达处理，但把原文留给用户
        return True, text[:120]
    if not isinstance(data, dict):
        return True, text[:120]
    code = data.get(code_field)
    if code is None:
        return True, text[:120]
    if isinstance(expect, bool):
        ok = bool(code) is expect
    else:
        try:
            ok = int(code) == int(expect)
        except (TypeError, ValueError):
            ok = str(code) == str(expect)
    if ok:
        return True, ""
    reason = (data.get("errmsg") or data.get("message") or data.get("msg")
              or data.get("description") or text[:160])
    return False, "服务返回 %s：%s" % (code, reason)


# ---------------------------------------------------------------------------
# 各渠道的请求构造
# ---------------------------------------------------------------------------

def dingtalk_signed_url(url: str, secret: str) -> str:
    """钉钉加签：HMAC-SHA256(secret, ts + "\\n" + secret) → base64 → urlencode。"""
    secret = str(secret or "").strip()
    if not secret:
        return url
    stamp = str(int(time.time() * 1000))
    to_sign = "%s\n%s" % (stamp, secret)
    digest = hmac.new(secret.encode("utf-8"), to_sign.encode("utf-8"),
                      digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest))
    joiner = "&" if "?" in url else "?"
    return "%s%stimestamp=%s&sign=%s" % (url, joiner, stamp, sign)


def feishu_payload(title: str, text: str, secret: str) -> dict:
    """飞书文本消息；配了签名密钥时带上 timestamp 与 sign。"""
    body = _clip("%s\n%s" % (title, text) if title else text, MAX_TEXT_BYTES)
    payload = {"msg_type": "text", "content": {"text": body}}
    secret = str(secret or "").strip()
    if secret:
        stamp = str(int(time.time()))
        to_sign = ("%s\n%s" % (stamp, secret)).encode("utf-8")
        digest = hmac.new(to_sign, digestmod=hashlib.sha256).digest()
        payload["timestamp"] = stamp
        payload["sign"] = base64.b64encode(digest).decode("ascii")
    return payload


# --- 企业微信应用消息（需要先换 access_token）-------------------------------

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
_TOKEN_LOCK = threading.Lock()
#: 这些错误码表示 access_token 失效，刷新后重试一次即可
_TOKEN_INVALID_CODES = (40014, 42001, 41001)


def _wecom_errmsg(data) -> str:
    if isinstance(data, dict):
        return str(data.get("errmsg") or "调用失败")
    return "调用失败"


def _wecom_base(channel: dict) -> str:
    base = str(channel.get("apiBase") or "").strip().rstrip("/") or DEFAULT_WECOM_API
    if not base.startswith(("http://", "https://")):
        base = "https://" + base
    return base


def _wecom_token(base: str, corpid: str, corpsecret: str,
                 force: bool = False) -> tuple[str | None, str | None]:
    if not force:
        with _TOKEN_LOCK:
            cached = _TOKEN_CACHE.get(corpid)
            if cached and cached[1] > time.time():
                return cached[0], None

    url = ("%s/cgi-bin/gettoken?corpid=%s&corpsecret=%s"
           % (base, urllib.parse.quote(corpid), urllib.parse.quote(corpsecret)))
    try:
        _, detail = _fetch(url)
        data = json.loads(detail)
    except urllib.error.HTTPError as exc:
        return None, "获取 access_token 失败：HTTP %d" % exc.code
    except urllib.error.URLError as exc:
        return None, "网络错误：%s" % getattr(exc, "reason", exc)
    except (OSError, ValueError) as exc:
        return None, "获取 access_token 失败：%s" % exc

    if not isinstance(data, dict) or data.get("errcode") != 0:
        code = (data or {}).get("errcode") if isinstance(data, dict) else None
        return None, "%s（errcode %s）" % (_wecom_errmsg(data), code)
    token = data.get("access_token")
    if not token:
        return None, "企业微信未返回 access_token"
    try:
        expires = int(data.get("expires_in") or 7200)
    except (TypeError, ValueError):
        expires = 7200
    with _TOKEN_LOCK:
        # 提前 2 分钟过期，避免边界失效
        _TOKEN_CACHE[corpid] = (token, time.time() + max(60, expires - 120))
    return token, None


def _send_wecom_app(channel: dict, title: str, text: str,
                    timeout: float) -> tuple[bool, str]:
    corpid = str(channel.get("corpid") or "").strip()
    corpsecret = str(channel.get("corpsecret") or "").strip()
    agentid = str(channel.get("agentid") or "").strip()
    touser = str(channel.get("touser") or "").strip() or "@all"
    base = _wecom_base(channel)

    if not corpid or not corpsecret:
        return False, "请填写企业 ID 与应用 Secret"
    if not agentid.isdigit():
        return False, "AgentId 必须是数字"

    payload = {
        "touser": touser,
        "msgtype": "text",
        "agentid": int(agentid),
        "text": {"content": _clip("%s\n%s" % (title, text) if title else text,
                                  MAX_TEXT_BYTES)},
        "safe": 0,
    }
    for attempt in (1, 2):
        token, error = _wecom_token(base, corpid, corpsecret, force=(attempt == 2))
        if not token:
            return False, error
        try:
            _, detail = _post_json("%s/cgi-bin/message/send?access_token=%s"
                                   % (base, token), payload, timeout=timeout)
            data = json.loads(detail)
        except urllib.error.HTTPError as exc:
            return False, "HTTP %d" % exc.code
        except urllib.error.URLError as exc:
            return False, "网络错误：%s" % getattr(exc, "reason", exc)
        except (OSError, ValueError) as exc:
            return False, "发送失败：%s" % exc

        code = data.get("errcode") if isinstance(data, dict) else None
        if code == 0:
            return True, "已下发给 %s" % touser
        if code in _TOKEN_INVALID_CODES and attempt == 1:
            continue
        hint = "（请检查企业可信 IP 是否包含本机公网 IP）" if code == 60020 else ""
        return False, "%s（errcode %s）%s" % (_wecom_errmsg(data), code, hint)
    return False, "发送失败"


def _compose(title: str, text: str) -> str:
    title = str(title or "").strip()
    text = str(text or "").strip()
    if title and text:
        return _clip("%s\n%s" % (title, text), MAX_TEXT_BYTES)
    return _clip(title or text, MAX_TEXT_BYTES)


def _contact(url: str, timeout: float, method: str = "POST",
             payload: dict | None = None, fields: dict | None = None,
             headers: dict | None = None) -> tuple[int, str]:
    """统一出口：按需选 JSON / 表单 / GET，方便验证脚本统一指向本地 sink。"""
    if method == "GET":
        return _fetch(url, timeout=timeout)
    if fields is not None:
        return _post_form(url, fields, timeout=timeout)
    return _post_json(url, payload or {}, timeout=timeout, headers=headers)


def _mark_leak(channel: dict) -> str | None:
    """渠道里若还留着 "__SET__"，说明密钥没保存成功，绝不能真的发出去。

    直接发会把字面量当密钥提交给对端服务（bark 会回 HTTP 400
    "failed to get [__SET__] device token"），在界面上却看不出异常。
    """
    for key in CHANNEL_FIELD_KEYS:
        # 用「包含」而不是「等于」：Webhook 地址这类密钥字段即使在整值打码之外
        # 被拼接过（前端拼 base + key），只要里面还有占位符就说明没保存成功。
        if SECRET_MARK in str(channel.get(key) or ""):
            return "该渠道的「%s」尚未保存，请重新填写后再试" % key
    return None


def send(channel: dict, title: str, text: str,
         event: dict | None = None) -> tuple[bool, str]:
    """向单个渠道发送一条消息，返回 (是否成功, 说明)。永不抛异常。"""
    channel = channel if isinstance(channel, dict) else {}
    kind = str(channel.get("type") or "")
    timeout = 10.0

    leak = _mark_leak(channel)
    if leak:
        return False, leak

    try:
        if kind == "wecom_app":
            return _send_wecom_app(channel, title, text, timeout)
        if kind not in CHANNEL_BY_TYPE:
            return False, "未知渠道类型：%s" % (kind or "(空)")
        return _send_plain(kind, channel, title, text, event, timeout)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(300).decode("utf-8", errors="replace")
        except (OSError, ValueError):
            pass
        return False, "HTTP %d %s" % (exc.code, detail[:200])
    except urllib.error.URLError as exc:
        return False, "网络错误：%s" % str(getattr(exc, "reason", exc))[:300]
    except (OSError, ValueError) as exc:
        return False, "发送失败：%s" % exc


def _send_plain(kind: str, channel: dict, title: str, text: str,
                event: dict | None, timeout: float) -> tuple[bool, str]:
    """非企业微信应用消息的十种渠道：每种一次请求，构造各不相同。"""
    title = str(title or "")
    text = str(text or "")

    if kind == "dingtalk":
        url = str(channel.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False, "Webhook 地址必须以 http:// 或 https:// 开头"
        url = dingtalk_signed_url(url, channel.get("secret") or "")
        status, detail = _contact(url, timeout, payload={
            "msgtype": "text",
            "text": {"content": _compose(title, text)},
        })
        return _judge(status, detail, "errcode", 0)

    if kind == "wecom":
        url = str(channel.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False, "Webhook 地址必须以 http:// 或 https:// 开头"
        status, detail = _contact(url, timeout, payload={
            "msgtype": "text",
            "text": {"content": _compose(title, text)},
        })
        return _judge(status, detail, "errcode", 0)

    if kind == "feishu":
        url = str(channel.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False, "Webhook 地址必须以 http:// 或 https:// 开头"
        status, detail = _contact(url, timeout,
                                  payload=feishu_payload(title, text,
                                                         channel.get("secret") or ""))
        return _judge(status, detail, "code", 0)

    if kind == "bark":
        url = str(channel.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False, "Bark 地址必须以 http:// 或 https:// 开头"
        status, detail = _contact(url, timeout, payload={
            "title": _clip(title, MAX_TITLE_BYTES),
            "body": _clip(text, MAX_TEXT_BYTES),
            "group": "Fail2ban",
        })
        return _judge(status, detail, "code", 200)

    if kind == "slack":
        url = str(channel.get("url") or "").strip()
        if not url.startswith(("http://", "https://")):
            return False, "Webhook 地址必须以 http:// 或 https:// 开头"
        # Slack 的 mrkdwn 里 *xx* 是加粗；没有标题时只发正文
        body = ("*%s*\n%s" % (title, text)).strip() if title else text
        status, detail = _contact(url, timeout,
                                  payload={"text": _clip(body, MAX_TEXT_BYTES)})
        return _judge(status, detail)

    if kind == "serverchan":
        key = str(channel.get("sendkey") or "").strip()
        if not key:
            return False, "未填写 SendKey"
        url = "%s/%s.send" % (SERVERCHAN_API.rstrip("/"),
                              urllib.parse.quote(key, safe=""))
        status, detail = _contact(url, timeout, fields={
            "title": _clip(title, MAX_TITLE_BYTES),
            "desp": _clip(text, MAX_TEXT_BYTES),
        })
        return _judge(status, detail, "code", 0)

    if kind == "pushplus":
        token = str(channel.get("token") or "").strip()
        if not token:
            return False, "未填写 Token"
        status, detail = _contact(PUSHPLUS_API, timeout, payload={
            "token": token,
            "title": _clip(title, MAX_TITLE_BYTES),
            "content": _clip(text, MAX_TEXT_BYTES),
            # 正文是纯文本行，用 txt 渲染最稳：markdown 会把「Jail：x」当成列表
            "template": "txt",
        })
        return _judge(status, detail, "code", 200)

    if kind == "wxpusher":
        app_token = str(channel.get("appToken") or "").strip()
        if not app_token:
            return False, "未填写 AppToken"
        uids = [item.strip() for item in str(channel.get("uids") or "").split(",")
                if item.strip()]
        topics = []
        for item in str(channel.get("topics") or "").split(","):
            item = item.strip()
            if item.isdigit():
                topics.append(int(item))
        if not uids and not topics:
            return False, "UID 与主题 ID 至少填一个"
        payload = {
            "appToken": app_token,
            "content": _compose(title, text),
            "summary": _clip(title or text, 99),
            "contentType": 1,
        }
        if uids:
            payload["uids"] = uids
        if topics:
            payload["topicIds"] = topics
        status, detail = _contact(WXPUSHER_API, timeout, payload=payload)
        return _judge(status, detail, "code", 1000)

    if kind == "telegram":
        token = str(channel.get("botToken") or "").strip()
        chat_id = str(channel.get("chatId") or "").strip()
        if not token:
            return False, "未填写 Bot Token"
        if not chat_id:
            return False, "未填写 Chat ID"
        base = str(channel.get("apiBase") or "").strip() or DEFAULT_TELEGRAM_API
        if not base.startswith(("http://", "https://")):
            base = "https://" + base
        url = "%s/bot%s/sendMessage" % (base.rstrip("/"), token)
        status, detail = _contact(url, timeout, payload={
            "chat_id": chat_id,
            "text": _compose(title, text),
            "disable_web_page_preview": True,
        })
        # HTTP 200 只代表请求到达，真正的结果在 ok 字段里
        return _judge(status, detail, "ok", True)

    # generic：把事件字段一并交出去，接收方可以自己按 event/jail/ips 分流
    url = str(channel.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return False, "目标地址必须以 http:// 或 https:// 开头"
    headers = {}
    secret = str(channel.get("secret") or "").strip()
    if secret:
        headers["Authorization"] = "Bearer %s" % secret
    payload = {
        "app": "fail2ban",
        "event": (event or {}).get("type", "test"),
        "title": title,
        "text": text,
        "jail": (event or {}).get("jail"),
        "ips": list((event or {}).get("ips") or []),
        "time": (event or {}).get("time", int(time.time())),
    }
    status, detail = _contact(url, timeout, payload=payload, headers=headers)
    return _judge(status, detail)


# ---------------------------------------------------------------------------
# 脱敏与投递记录
# ---------------------------------------------------------------------------

def _secret_values(channel: dict) -> list[str]:
    """该渠道当前配置里的全部密钥值，用于在报错信息里把它们擦掉。"""
    values = []
    ctype = str(channel.get("type") or "")
    for key in CHANNEL_FIELD_KEYS:
        if not _is_secret(ctype, key):
            continue
        value = str(channel.get(key) or "").strip()
        # 太短的值擦除会让正常报错变得不可读，四字符以上才处理
        if len(value) >= 4 and value != SECRET_MARK:
            values.append(value)
    return values


def redact(text: str, channel: dict | None = None) -> str:
    """把渠道密钥从任意文本里擦掉。

    上游服务常把收到的 token / Webhook 地址原样回显在错误信息里，投递记录
    会把 detail 展开给用户看，所以必须在入库前擦一次。
    """
    out = str(text or "")
    for value in _secret_values(channel or {}):
        if value in out:
            out = out.replace(value, SECRET_MARK)
        quoted = urllib.parse.quote(value, safe="")
        if quoted and quoted != value and quoted in out:
            out = out.replace(quoted, SECRET_MARK)
    return out


def _record(channel: dict, event_name: str, title: str, body: str,
            ok: bool, detail: str) -> dict:
    """写一条投递记录（成功失败都写）。记录本身失败不影响推送结果。"""
    channel = channel if isinstance(channel, dict) else {}
    name = str(channel.get("name") or "").strip() or str(channel.get("type") or "")
    try:
        return delivery.record(
            event=event_name,
            channel=name,
            channel_id=str(channel.get("id") or ""),
            type=str(channel.get("type") or ""),
            ok=bool(ok),
            detail=redact(detail, channel),
            title=redact(title, channel),
            body=redact(body, channel),
        )
    except Exception:  # noqa: BLE001 - 记录失败不能让推送链路崩
        return {}


def resolve_channel(channel: dict) -> dict:
    """把前端提交的渠道草稿补成可以真正发送的配置。

    编辑已有渠道时，表单里的密钥输入框是空的（浏览器从没拿到过真实值），
    直接拿这份草稿去发会把 "__SET__"/空串当密钥。这里按 id 跟磁盘上的配置
    做一次同样的密钥合并，测试按钮与保存按钮因此用同一条规则。
    """
    channel = channel if isinstance(channel, dict) else {}
    cid = str(channel.get("id") or "")
    if not cid:
        return channel
    for saved in load_settings().get("channels", []):
        if str(saved.get("id") or "") == cid:
            return _merge_channel(saved, channel)
    return channel


def test_channel(channel: dict) -> tuple[bool, str, dict]:
    """发送测试通知并落一条记录，返回 (ok, detail, 记录)。

    草稿渠道会先补齐密钥，所以「编辑已保存的渠道 → 直接点测试」能正常发出，
    用户不用为了测试重新输一遍密钥。
    """
    channel = resolve_channel(channel if isinstance(channel, dict) else {})
    title, text = format_test(str(channel.get("name") or "").strip())
    ok, detail = send(channel, title, text, {"type": "test"})
    record = _record(channel, "test", title, text, ok, detail)
    return ok, detail, record


# ---------------------------------------------------------------------------
# 分发
# ---------------------------------------------------------------------------

def dispatch(event: dict, settings: dict | None = None) -> list[dict]:
    """按事件类型把消息推送到所有启用且已配齐的渠道。

    每个真正发出请求的渠道都写一条投递记录 —— 成功失败都写。渠道没配齐时
    不记录，因为根本没发请求。
    """
    settings = settings if isinstance(settings, dict) else load_settings()
    event = event if isinstance(event, dict) else {}
    if not settings.get("enabled"):
        return []
    kind = str(event.get("type") or "")
    if kind != "test" and not settings.get("events", {}).get(kind, False):
        return []

    title, text = format_event(event)
    results = []
    for channel in settings.get("channels", []):
        if not isinstance(channel, dict):
            continue
        if not channel.get("enabled") or not channel_ready(channel):
            continue
        try:
            ok, detail = send(channel, title, text, event)
        except Exception as exc:  # noqa: BLE001 - 一个渠道坏了不影响其它渠道
            ok, detail = False, "发送异常：%s" % exc
        _record(channel, kind or "manual", title, text, ok, detail)
        name = str(channel.get("name") or "").strip() or str(channel.get("type") or "")
        results.append({
            "id": str(channel.get("id") or ""),
            "name": name,
            "channel": name,
            "type": str(channel.get("type") or ""),
            "ok": bool(ok),
            "detail": detail,
        })
    return results


# ---------------------------------------------------------------------------
# 后台巡检
# ---------------------------------------------------------------------------

class Watcher(threading.Thread):
    """定期检查 fail2ban 状态，发现新封禁或服务异常时产生事件。"""

    daemon = True

    def __init__(self):
        super().__init__(name="f2b-notify-watcher")
        self._snapshot: dict[str, set] = {}
        self._seeded = False
        self._service_up: bool | None = None
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        while not self._stop.is_set():
            settings = load_settings()
            # 通知、浏览器提醒与 fn-knock 联动全关时不做无谓的巡检
            if (settings.get("enabled") or settings.get("browserNotify")
                    or fnknock.enabled()):
                try:
                    self.tick()
                except Exception:  # noqa: BLE001 - 巡检线程不应因异常退出
                    pass
            try:
                interval = max(10, int(settings.get("pollInterval", 30)))
            except (TypeError, ValueError):
                interval = 30
            self._stop.wait(interval)

    def tick(self) -> None:
        installed = f2b.installed()
        running = f2b.ping() if installed else False

        if self._service_up is not None and self._service_up != running:
            if running:
                event = self._service_event("serviceRecover")
            else:
                event = self._service_event("serviceDown")
            dispatch(event)
        self._service_up = running

        if not running:
            return

        try:
            jails = f2b.jail_names()
        except f2b.Fail2banError:
            return

        current: dict[str, set] = {}
        for jail in jails:
            status = f2b.jail_status(jail)
            if not status.get("ok"):
                continue
            current[jail] = set(status.get("bannedIps", []))

        if not self._seeded:
            # 首次运行只记录基线，不产生事件，避免安装后立刻刷屏
            self._snapshot = current
            self._seeded = True
            return

        for jail, ips in current.items():
            previous = self._snapshot.get(jail, set())
            added = sorted(ips - previous)
            removed = sorted(previous - ips)

            # fn-knock 联动：把新增封禁推送到敲门通用黑名单，解封时同步移除
            if added or removed:
                try:
                    fnknock.sync(jail, added, removed)
                except Exception:  # noqa: BLE001 - 联动失败不应影响巡检与通知
                    pass

            if added:
                event = self._ban_event(jail, added)
                dispatch(event)
            if removed:
                event = self._unban_event(jail, removed)
                dispatch(event)

        self._snapshot = current

    # -- 事件文本（中英双语，判题与推送共用同一段） -------------------------

    def _service_event(self, kind: str) -> dict:
        if kind == "serviceRecover":
            title = bilingual_title("Fail2ban 服务已恢复", "Fail2ban service recovered")
            text = bilingual_text(
                ["fail2ban 已恢复响应，防护策略重新生效。",
                 "时间：%s" % _stamp()],
                ["fail2ban is responding again; the rules are back in effect.",
                 "Time: %s" % _stamp()],
            )
            return record_event("serviceRecover", title, text)
        title = bilingual_title("Fail2ban 服务异常", "Fail2ban service down")
        text = bilingual_text(
            ["fail2ban 无响应，当前可能未在防护。请检查服务状态。",
             "时间：%s" % _stamp()],
            ["fail2ban is not responding; the box may be unprotected right now.",
             "Time: %s" % _stamp()],
        )
        return record_event("serviceDown", title, text)

    def _ban_event(self, jail: str, ips: list) -> dict:
        shown, more = _shorten_ips(ips)
        title = bilingual_title("Fail2ban 封禁了 %d 个 IP" % len(ips),
                                "Fail2ban banned %d IP(s)" % len(ips))
        text = bilingual_text(
            ["Jail：%s" % jail,
             "IP：%s%s" % (shown, "（其余 %d 个省略）" % more if more else ""),
             "时间：%s" % _stamp()],
            ["Jail: %s" % jail,
             "IP: %s%s" % (shown, " (and %d more)" % more if more else ""),
             "Time: %s" % _stamp()],
        )
        return record_event("ban", title, text, jail=jail, ips=ips)

    def _unban_event(self, jail: str, ips: list) -> dict:
        shown, more = _shorten_ips(ips)
        title = bilingual_title("Fail2ban 解封了 %d 个 IP" % len(ips),
                                "Fail2ban unbanned %d IP(s)" % len(ips))
        text = bilingual_text(
            ["Jail：%s" % jail,
             "IP：%s%s" % (shown, "（其余 %d 个省略）" % more if more else ""),
             "时间：%s" % _stamp()],
            ["Jail: %s" % jail,
             "IP: %s%s" % (shown, " (and %d more)" % more if more else ""),
             "Time: %s" % _stamp()],
        )
        return record_event("unban", title, text, jail=jail, ips=ips)
