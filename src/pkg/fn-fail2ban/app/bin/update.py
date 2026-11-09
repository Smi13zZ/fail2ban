"""新版本检查：有没有比当前更新的发布。

三层依次尝试，纯标准库：

  1. GitHub REST 的 releases/latest —— 信息最全（发布说明 + 资产真实大小与下载地址）；
  2. releases.atom 订阅源 —— 不吃 API 配额。家用宽带共享出口 IP，GitHub 匿名
     API 每小时只有 60 次，所以第 1 层拿到 403 是正常绕行，不是失败；
  3. releases/expanded_assets/<tag> 的 HTML 片段 —— API 拒绝服务时，下载链接
     还能从这里读到。

两条必须说清楚的约定：

  * 版本号一律按**数字**比较，绝不按文本：26.11.10 比 26.11.9 新，字符串比较
    会得出相反的结论。
  * 「还没有任何发布」是一个答案，不是错误。仓库可能还不存在，也可能存在但
    一个 Release 都没发；GitHub 对这两种情况都回 404。监控面板在自检里抛异常，
    比老老实实显示「暂时没有更新」糟糕得多。

缓存让每小时巡检与每次页面加载都不必花配额：成功结果复用 SUCCESS_TTL 秒，
失败结果冷却 FAIL_TTL 秒。成功时间戳只在查询**回来之后**才写 —— 一进来就写的话，
一次断网就会白白吞掉接下来十几分钟的补查机会。

对外接口：

    check(current, force=False, repo=None) -> dict
    cached(current) -> dict
    poll_loop(get_settings, log, stop_event, initial_delay=60, interval=3600)

check() 永不抛异常：任何失败都以 {ok: False, has_update: False, reason: <码>,
error: <文字>} 的形式返回。
"""

from __future__ import annotations

import html as _html
import http.client
import json
import os
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = "https://api.github.com"
WEB_BASE = "https://github.com"
# 本项目的发布仓库。面板里的版本检查、发布说明与一键升级都指向这里，
# 换仓库（改名 / 迁移）只改这一处。
DEFAULT_REPO = "Smi13zZ/fail2ban"

SUCCESS_TTL = 900          # 15 分钟：页面反复刷新不该产生请求
FAIL_TTL = 60              # 1 分钟：被限流的接口不该被反复敲
HTTP_TIMEOUT = 8.0
NOTES_MAX = 6000
MAX_ASSETS = 100

# 由服务在启动时告知，供 poll_loop 与 User-Agent 使用（check() 自己收版本参数）。
CURRENT_VERSION = ""

# 缓存里存「上一次答案」与「产生它的时刻」。at 只在查询完成后写。
_CACHE = {"at": 0.0, "payload": None}
_FAIL = {"at": 0.0, "payload": None}
_LOCK = threading.RLock()
# 同一时刻只允许一次网络查询：后台巡检与「立即检查」撞在一起时，
# 同一样的问题不该花掉两次配额。
_FETCH = threading.Lock()


def _noop(_msg):
    pass


_LOG = _noop


def set_logger(fn):
    """装上服务的 log()，让降级与失败出现在面板日志里。"""
    global _LOG
    _LOG = fn if callable(fn) else _noop


def set_version(version):
    """告知本模块当前运行的版本。

    check() 本来就收版本参数，但 poll_loop 只拿到 settings、里面没有版本号；
    在这里设一次，后台巡检就不必让调用方把版本塞进配置里。
    """
    global CURRENT_VERSION
    CURRENT_VERSION = str(version or "").strip()


def _now():
    """单一时钟来源，测试可以改时间而不用真的 sleep。"""
    return time.time()


def reset():
    """清空两个缓存：给测试用，切换仓库时也用。"""
    with _LOCK:
        _CACHE["at"] = 0.0
        _CACHE["payload"] = None
        _FAIL["at"] = 0.0
        _FAIL["payload"] = None


# ---------------------------------------------------------------------------
# 版本比较
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"\d+(?:\.\d+)*")


def version_tuple(value):
    """'v26.11.10' -> (26, 11, 10, 0)，于是元组比较就能排出先后。

    只取开头那段点分数字：'-beta1' 这类后缀不能让预发布版本悄悄变成更新的版本，
    缺少的段按 0 补齐而不是让后面的段整体错位。
    """
    match = _NUM_RE.search(str(value or ""))
    if not match:
        return (0, 0, 0, 0)
    parts = [int(part) for part in match.group(0).split(".")][:4]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


def is_newer(latest, current):
    """只有两边都给出真实版本号、且 latest 确实更大时才为真。"""
    if not str(latest or "").strip() or not str(current or "").strip():
        return False
    return version_tuple(latest) > version_tuple(current)


# ---------------------------------------------------------------------------
# 失败原因
# ---------------------------------------------------------------------------

class CheckError(Exception):
    """某一层失败了。reason 给机器看，message 给人看。"""

    def __init__(self, reason, message):
        Exception.__init__(self, message)
        self.reason = reason
        self.message = message


# 越靠前越优先：两层对「为什么失败」意见不一致时，可操作性更强的那个胜出。
# no_release 排在 not_found 前面是刻意的 —— GitHub 对 releases/latest 回 404
# 既可能是仓库不存在，也可能是仓库在但没有发布；而一个 200 但空的订阅源
# 恰好证明了「仓库在、只是没发版本」。
_REASON_ORDER = ("no_release", "not_found", "rate_limited", "http",
                 "timeout", "network", "parse")

# 每个失败码配一句能读懂的话，设置页直接显示，不必回显机器码。
_FAIL_HINT = {
    "not_found": "仓库或发布页还不存在，暂时无法检查更新",
    "no_release": "仓库已就绪，但还没有发布任何版本",
    "rate_limited": "GitHub 接口限流（匿名每小时 60 次），稍后再试",
    "http": "GitHub 接口返回异常状态",
    "timeout": "连接 GitHub 超时",
    "network": "网络不可达，无法连接 GitHub",
    "parse": "发布信息解析失败",
}


def _pick_reason(pairs):
    for reason in _REASON_ORDER:
        for candidate, _msg in pairs:
            if candidate == reason:
                return reason
    return pairs[0][0] if pairs else "error"


def _messages(pairs):
    seen = []
    for _candidate, msg in pairs:
        if msg and msg not in seen:
            seen.append(msg)
    return "；".join(seen)


def _describe(exc):
    if isinstance(exc, CheckError):
        return exc.reason, exc.message
    if isinstance(exc, urllib.error.HTTPError):
        code = int(getattr(exc, "code", 0) or 0)
        if code in (403, 429):
            return "rate_limited", "HTTP %d（GitHub 接口限流）" % code
        if code == 404:
            return "not_found", "HTTP 404（仓库或发布不存在）"
        return "http", "HTTP %d" % code
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "timeout", "请求超时"
    return "error", str(exc) or exc.__class__.__name__


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _user_agent():
    version = str(CURRENT_VERSION or "").strip() or "unknown"
    return "fn-fail2ban/" + version


def _http_get(url, timeout, accept=None):
    """GET 一个 URL 取回文本，把每种失败翻译成 CheckError。

    上面几层只比较 reason，不认识裸的 urllib 异常 —— 所以这里必须把它们全部
    收干净，否则三层降级逻辑就变成在猜异常类型。
    """
    headers = {"User-Agent": _user_agent()}
    if accept:
        headers["Accept"] = accept
    req = urllib.request.Request(url, headers=headers)
    try:
        if url.lower().startswith("https"):
            resp = urllib.request.urlopen(
                req, timeout=timeout, context=ssl.create_default_context())
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        code = int(getattr(exc, "code", 0) or 0)
        if code in (403, 429):
            raise CheckError("rate_limited", "HTTP %d（GitHub 接口限流）" % code)
        if code == 404:
            raise CheckError("not_found", "HTTP 404（仓库或发布不存在）")
        raise CheckError("http", "HTTP %d" % code)
    except (socket.timeout, TimeoutError):
        raise CheckError("timeout", "请求超时")
    except urllib.error.URLError as exc:
        detail = getattr(exc, "reason", None) or exc
        if isinstance(detail, (socket.timeout, TimeoutError)):
            raise CheckError("timeout", "请求超时")
        raise CheckError("network", "网络不可达：%s" % (detail,))
    except ssl.SSLError as exc:
        raise CheckError("network", "TLS 握手失败：%s" % (exc,))
    except (http.client.HTTPException, OSError) as exc:
        raise CheckError("network", "网络错误：%s" % (exc,))
    try:
        with resp:
            body = resp.read()
    except (socket.timeout, TimeoutError):
        raise CheckError("timeout", "读取响应超时")
    except (http.client.HTTPException, OSError) as exc:
        raise CheckError("network", "读取响应失败：%s" % (exc,))
    return body.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def _strip_html(raw):
    """订阅源里带转义过的 HTML，摊平成可读文本再给界面。"""
    text = _html.unescape(raw or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|div|li|h[1-6]|tr|blockquote)>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "· ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = _html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _attr(tag, name):
    match = re.search(r'\b%s\s*=\s*"([^"]*)"' % re.escape(name), tag or "")
    return _html.unescape(match.group(1)) if match else ""


def _release_from_api(repo, api, timeout):
    raw = _http_get("%s/repos/%s/releases/latest" % (api, repo), timeout,
                    accept="application/vnd.github+json")
    try:
        rel = json.loads(raw)
    except ValueError:
        raise CheckError("parse", "接口返回的不是合法 JSON")
    if not isinstance(rel, dict):
        raise CheckError("parse", "接口返回的发布信息格式不对")
    tag = str(rel.get("tag_name") or rel.get("name") or "").strip()
    if not tag:
        raise CheckError("parse", "发布信息里没有版本号")
    assets = []
    for item in (rel.get("assets") or [])[:MAX_ASSETS]:
        if not isinstance(item, dict) or not item.get("browser_download_url"):
            continue
        try:
            size = int(item.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        assets.append({
            "name": str(item.get("name") or ""),
            "size": size,
            "url": str(item.get("browser_download_url") or ""),
        })
    return {
        "tag": tag,
        "name": str(rel.get("name") or tag),
        "notes": str(rel.get("body") or ""),
        "published_at": str(rel.get("published_at") or ""),
        "html_url": str(rel.get("html_url") or ""),
        "assets": assets,
        "source": "api",
    }


def _release_from_atom(repo, web, timeout):
    xml = _http_get("%s/%s/releases.atom" % (web, repo), timeout)
    entry = re.search(r"<entry>(.*?)</entry>", xml, re.S)
    if not entry:
        # 200 但一条条目都没有：仓库在，只是没发过版本。这与整个仓库 404
        # 是两回事，失败原因必须分开，否则界面会让人去检查仓库地址。
        raise CheckError("no_release", "发布订阅源里没有条目")
    block = entry.group(1)
    title_raw = re.search(r"<title>(.*?)</title>", block, re.S)
    title = _html.unescape(title_raw.group(1)) if title_raw else ""
    link_tag = ""
    for match in re.finditer(r"<link\b[^>]*>", block):
        if _attr(match.group(0), "rel") == "alternate":
            link_tag = match.group(0)
            break
    url = _attr(link_tag, "href")
    updated = re.search(r"<updated>(.*?)</updated>", block, re.S)
    content = re.search(r"<content[^>]*>(.*?)</content>", block, re.S)
    tag = ""
    match = re.search(r"/tag/([^/?#]+)", url)
    if match:
        tag = urllib.parse.unquote(match.group(1))
    if not tag:
        match = re.search(r"(v?\d+(?:\.\d+)+)", title)
        tag = match.group(1) if match else ""
    if not tag:
        raise CheckError("parse", "无法从发布订阅源识别版本号")
    return {
        "tag": tag,
        "name": _strip_html(title) or tag,
        "notes": _strip_html(content.group(1)) if content else "",
        "published_at": updated.group(1) if updated else "",
        "html_url": url,
        "assets": [],
        "source": "atom",
    }


def _assets_from_html(repo, tag, web, timeout):
    """从发布页的资产片段里读下载链接。

    那段 HTML 是普通页面，不占 API 配额 —— 这正是 API 回 403 时它还有用的原因。
    """
    if not tag:
        return []
    page = _http_get("%s/%s/releases/expanded_assets/%s"
                     % (web, repo, urllib.parse.quote(tag, safe="")), timeout)
    out = []
    seen = set()
    # 可见名称在嵌套 <span> 里，直接从 URL 取更稳。
    for href in re.findall(r'href="(/[^"]+/releases/download/[^"]+)"', page):
        href = _html.unescape(href)
        if href in seen:
            continue
        seen.add(href)
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        out.append({"name": name, "size": 0, "url": web + href})
        if len(out) >= MAX_ASSETS:
            break
    return out


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def _blank(current, repo):
    cur = str(current or "").strip()
    if cur[:1] in ("v", "V"):
        cur = cur[1:]
    return {
        "ok": False,
        "current": cur,
        "latest": "",
        "has_update": False,
        "name": "",
        "notes": "",
        "published_at": "",
        "url": "",
        "html_url": "",
        "repo": repo,
        "assets": [],
        "source": "",
        # reason 是机器可读的码（not_found / rate_limited / …），
        # detail 是给人读的句子，界面显示 detail。
        "reason": "",
        "detail": "",
        "error": "",
        "cached": False,
        "checked": False,
        "checked_at": 0,
    }


def _freeze(payload):
    """结果 dict 的独立副本，assets 也复制一份。

    缓存把同一个答案发给每个调用方；没有这层复制，某个处理器原地对
    info["assets"] 排序或截断，就会改写下一次请求看到的内容。
    """
    out = dict(payload)
    out["assets"] = [dict(item) for item in (payload.get("assets") or [])]
    return out


def _cached_copy(payload):
    out = _freeze(payload)
    out["cached"] = True
    return out


def _resolve_base(explicit, env_name, fallback):
    value = str(explicit or os.environ.get(env_name) or fallback).strip()
    return value.rstrip("/") or fallback


def cached(current=""):
    """内存里已有的最新答案；一次都没有时返回一个说明「还没查过」的 dict。

    给不许阻塞的调用方（首屏 bootstrap）用：宁可显示上一次的状态，也不要为了
    一个网络往返把界面卡住。
    """
    with _LOCK:
        if _CACHE["payload"]:
            return _cached_copy(_CACHE["payload"])
        if _FAIL["payload"]:
            return _cached_copy(_FAIL["payload"])
    out = _blank(current, DEFAULT_REPO)
    out["reason"] = "not_checked"
    out["detail"] = "还没有检查过，稍后会自动检查"
    out["cached"] = True
    return out


def check(current="", force=False, repo=None, *,
          api_base="", web_base="", timeout=HTTP_TIMEOUT, log=None):
    """问 GitHub 最新发布是什么，并与当前版本比较。

    force=True 跳过两个缓存（用户点了「立即检查」）。api_base / web_base 存在
    是为了让整条三层降级路径能对着本地假服务器跑通；环境变量
    F2B_UPDATE_API_BASE / F2B_UPDATE_WEB_BASE 从进程外做同一件事。

    永远返回 dict，永不抛异常。
    """
    repo = str(repo or DEFAULT_REPO).strip().strip("/") or DEFAULT_REPO
    api = _resolve_base(api_base, "F2B_UPDATE_API_BASE", API_BASE)
    web = _resolve_base(web_base, "F2B_UPDATE_WEB_BASE", WEB_BASE)
    emit = log if callable(log) else _LOG

    def cached_hit():
        if force:
            return None
        stamp = _now()
        with _LOCK:
            if _CACHE["payload"] and stamp - _CACHE["at"] < SUCCESS_TTL:
                return _cached_copy(_CACHE["payload"])
            if _FAIL["payload"] and stamp - _FAIL["at"] < FAIL_TTL:
                return _cached_copy(_FAIL["payload"])
        return None

    hit = cached_hit()
    if hit is not None:
        return hit
    with _FETCH:
        # 排队期间别的线程可能已经查完了同一个问题。
        hit = cached_hit()
        if hit is not None:
            return hit
        try:
            return _lookup(current, repo, api, web, timeout, emit)
        except Exception as exc:  # noqa: BLE001 - 兜底：check() 的契约是永不抛
            out = _blank(current, repo)
            out["reason"] = "error"
            out["error"] = str(exc) or exc.__class__.__name__
            out["detail"] = out["error"]
            out["checked"] = True
            out["checked_at"] = int(_now())
            return out


def _lookup(current, repo, api, web, timeout, emit):
    out = _blank(current, repo)
    rel = None
    errors = []
    try:
        rel = _release_from_api(repo, api, timeout)
    except Exception as exc:
        reason, message = _describe(exc)
        errors.append((reason, message))
        emit("版本检查：接口查询失败（%s），改走发布订阅源" % message)
    if rel is None:
        try:
            rel = _release_from_atom(repo, web, timeout)
        except Exception as exc:
            reason, message = _describe(exc)
            errors.append((reason, message))
            out["reason"] = _pick_reason(errors)
            out["error"] = _messages(errors)
            out["detail"] = _FAIL_HINT.get(out["reason"], out["error"])
            stamp = _now()
            out["checked"] = True
            out["checked_at"] = int(stamp)
            with _LOCK:
                _FAIL["at"] = stamp
                _FAIL["payload"] = _freeze(out)
            return out

    tag = str(rel.get("tag") or "").strip()
    latest = tag[1:] if tag[:1] in ("v", "V") else tag
    out["ok"] = True
    out["latest"] = latest or tag
    out["name"] = rel.get("name") or tag
    out["notes"] = str(rel.get("notes") or "")[:NOTES_MAX]
    out["published_at"] = rel.get("published_at") or ""
    out["html_url"] = rel.get("html_url") or ""
    out["url"] = out["html_url"]
    out["source"] = rel.get("source") or ""
    assets = rel.get("assets") or []
    if not assets:
        try:
            assets = _assets_from_html(repo, tag or ("v" + (latest or "")),
                                       web, timeout)
        except Exception as exc:
            # 更新本身已经知道了；拿不到下载链接不该让这条消息消失。
            emit("版本检查：下载列表不可用（%s）" % _describe(exc)[1])
    out["assets"] = assets or []
    out["has_update"] = is_newer(out["latest"], out["current"])
    if not out["current"]:
        out["reason"] = "unknown_current"
        out["error"] = "当前版本未知，无法判断是否有更新"
        out["detail"] = out["error"]
    stamp = _now()
    out["checked"] = True
    out["checked_at"] = int(stamp)
    with _LOCK:
        # 只在此刻写成功时间戳：失败不能让上一次成功续命，反过来也一样。
        _CACHE["at"] = stamp
        _CACHE["payload"] = _freeze(out)
        _FAIL["at"] = 0.0
        _FAIL["payload"] = None
    return out


# ---------------------------------------------------------------------------
# 后台巡检
# ---------------------------------------------------------------------------

def _setting(cfg, *names):
    """按顺序在配置里找一个值，兼容扁平的 update_* 与嵌套的 update 分区。"""
    if not isinstance(cfg, dict):
        return None
    for name in names:
        if name in cfg and cfg.get(name) not in (None, ""):
            return cfg.get(name)
    section = cfg.get("update")
    if isinstance(section, dict):
        for name in names:
            key = name[7:] if name.startswith("update_") else name
            if key in section and section.get(key) not in (None, ""):
                return section.get(key)
    return None


#: 已安装应用的 manifest。升级后它立刻反映新版本号，比进程内的常量更可信。
INSTALL_MANIFEST = "/var/apps/%s/manifest"
APP_NAME = "fn-fail2ban"


def installed_version(app_name=APP_NAME, manifest=""):
    """从已安装目录的 manifest 里读版本号；读不到返回空串。

    后台巡检只拿到 settings，里面没有版本号 —— 而「当前版本」正是判断有没有
    更新的另一半答案。面板进程自己就是从这份 manifest 部署起来的，读它比让
    调用方额外传一路参数更省事，也不会因为常量忘了跟着改而错判。
    """
    path = manifest or os.environ.get("F2B_MANIFEST") or (INSTALL_MANIFEST % app_name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                text = line.strip().lstrip("﻿")
                if text.startswith("version") and "=" in text:
                    return text.split("=", 1)[1].strip().strip('"').lstrip("vV")
    except OSError:
        pass
    return ""


def _current_from(cfg):
    """当前跑的是哪个版本？

    来源按可信度排序：配置里显式的键、set_version() 给的常量、F2B_VERSION
    环境变量，最后是已安装 manifest。这样普通 fpk 进程不用额外接线也能得到
    正确答案，只有拿不到 manifest 的测试环境才需要显式喂一个。
    """
    for key in ("current", "version"):
        value = _setting(cfg, key)
        if value:
            return str(value).strip().lstrip("vV")
    value = str(CURRENT_VERSION or "").strip().lstrip("vV")
    if value:
        return value
    value = str(os.environ.get("F2B_VERSION") or "").strip().lstrip("vV")
    if value and value != "0.0.0":
        return value
    return installed_version()


def poll_loop(get_settings, log, stop_event, initial_delay=60, interval=3600, *,
              notify=None):
    """在后台线程里一直检查新版本。

    面板即使没人开着也要知道有新发布，这就是它存在的理由。第一次查询等
    initial_delay 秒，让 HTTP 服务先起来；之后每 interval 秒一轮。

    每一轮都重新读 get_settings()，所以切换「检查更新」开关或换仓库不用重启。
    配置里也可以带 api_base / web_base 指向别处。notify() 仅在 has_update 对一个
    尚未通告过的版本成立时调用一次，长驻进程不会每小时重复同一句。
    """
    emit = log if callable(log) else _noop
    if stop_event.wait(max(0.0, float(initial_delay))):
        return
    announced = ""
    while not stop_event.is_set():
        try:
            cfg = get_settings()
            if not isinstance(cfg, dict):
                cfg = {}
            enabled = _setting(cfg, "check", "update_check", "enabled")
            if enabled is None or bool(enabled):
                current = _current_from(cfg)
                info = check(current, force=False,
                             repo=_setting(cfg, "repo", "update_repo") or DEFAULT_REPO,
                             api_base=_setting(cfg, "update_api_base") or "",
                             web_base=_setting(cfg, "update_web_base") or "",
                             log=emit)
                if not info.get("ok"):
                    emit("版本检查失败（%s）：%s"
                         % (info.get("reason") or "error", info.get("error") or ""))
                elif info.get("has_update"):
                    latest = info.get("latest") or ""
                    if latest != announced:
                        announced = latest
                        emit("发现新版本 %s（当前 %s），来源 %s"
                             % (latest, current or "未知", info.get("source") or "?"))
                        if callable(notify):
                            try:
                                notify(info)
                            except Exception as exc:
                                emit("新版本通知回调失败：%r" % (exc,))
                elif info.get("latest") and info.get("latest") != announced:
                    # 远端每个版本只记一行，日志不至于变成每小时一次的打卡。
                    announced = info.get("latest")
                    emit("版本检查：当前 %s 已是最新（远端 %s）"
                         % (current or "未知", info.get("latest")))
        except Exception as exc:  # noqa: BLE001 - 断网、限流、DNS 挂了都不能让线程死
            try:
                emit("版本检查线程异常：%r" % (exc,))
            except Exception:
                pass
        if stop_event.wait(max(0.05, float(interval))):
            break
