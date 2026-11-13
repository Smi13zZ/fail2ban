"""一键升级：把「有新版本」变成「新版本已装上」。

这件事比看起来难，三个原因分别决定了设计：

1. **fnOS 不支持原地升级。** appcenter-cli install-fpk 对着已安装的应用不会
   执行升级，uninstall 在部分版本上还会要求走应用中心。所以唯一可靠的顺序是
   stop → uninstall → install，而其中「替换应用」这一步恰好会杀掉正在运行
   本模块的这个进程。

2. **因此安装不能是本进程的子进程。** 它被写成脚本、以 start_new_session
   分离执行：卸载会停掉服务，作为普通子进程它会跟着一起收到终止信号，卡在
   升级中途。脚本放在 @appdata —— 这个目录被 fnOS 刻意保留过卸载，放在
   @appcenter 会在最糟糕的时刻被删掉。

3. **新包没被证明可用之前不许卸掉旧的。** 包先下载到 .part，落位后按 tar
   打开，检查 fnpack 必然写入的成员（manifest + app.tgz）、应用名与版本号，
   全部对上才交给脚本。一次被截断的下载或下载错了资产，代价只能是一次失败的
   升级，绝不能是一个消失的应用。

旧包留在 update/rollback.fpk；新版本没装上或没起来时，脚本把旧包装回去。

整个过程只用标准库。本模块不读任何密钥：下载地址来自公开的发布元数据。
"""

from __future__ import annotations

import http.client
import os
import posixpath
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request

APP_NAME = "fn-fail2ban"
# 对外暴露的状态机。"failed" 在下次启动前是终态。
PHASES = ("idle", "downloading", "installing", "failed")

DOWNLOAD_LIMIT = 128 * 1024 * 1024
CHUNK = 256 * 1024
HTTP_TIMEOUT = 180
LOG_TAIL_LINES = 14

# 安装完成后最多观察多久：install-fpk 返回时应用中心往往还没登记完，
# 版本文件与运行状态都得轮询一阵才可信。
#: 装完之后观察多久。每次 appcenter-cli start 自己就要跑十几秒进度条，
#: 20 轮能把「升级中」拖上两分钟；版本号一变就说明包已经换掉了，
#: 剩下的只是把服务拉起来，不需要这么多轮。
VERIFY_ATTEMPTS = 6
VERIFY_INTERVAL = 3

# appcenter-cli 在 fnOS 上的实际位置。面板由应用框架拉起、PATH 很精简，
# 所以除了 which 还要试这些绝对路径。
APPCENTER_CANDIDATES = (
    "/usr/local/bin/appcenter-cli",
    "/usr/bin/appcenter-cli",
    "/usr/sbin/appcenter-cli",
    "/vol1/@appcenter/appcenter-cli",
)

# 已安装应用信息的落点（fnOS 把 target 挂在这里，manifest 在外层）。
INSTALL_MANIFEST = "/var/apps/%s/manifest"

# 可能还留着当前运行版本安装包的位置，仅用于拼出回滚包。
PREV_SEARCH_DIRS = (
    "/vol1/@appshare/%s",
    "/vol1/@appshare",
    "/tmp",
)

#: 安装时必须在 env 文件里给出的向导字段。少一个，install-fpk 会直接报
#: "Something wrong with environment variables." 而不是给出缺哪个。
WIZARD_FIELDS = ("auto_install", "enable_ssh_jail", "default_bantime",
                 "default_maxretry", "default_ignoreip")

_LOCK = threading.RLock()
_STATE = {
    "phase": "idle",
    "version": "",
    "detail": "",
    "done": 0,
    "total": 0,
    "at": 0,
}
# var_dir 供 progress() 找 install.log；current 回显给界面。启动时配置一次。
_CTX = {"var_dir": "", "current": ""}

_LOG = None


def _noop(_msg):
    pass


def _emit(msg):
    fn = _LOG
    if callable(fn):
        try:
            fn(msg)
        except Exception:
            pass


def set_context(var_dir=None, current=None, log=None):
    """告知本模块文件在哪、当前跑的是哪个版本。

    progress() 不带参数（被 HTTP 处理器直接轮询），所以上下文只能在启动时
    装一次，或者在测试里先设好。
    """
    global _LOG
    if log is not None:
        _LOG = log if callable(log) else None
    with _LOCK:
        if var_dir is not None:
            _CTX["var_dir"] = str(var_dir)
        if current is not None:
            _CTX["current"] = str(current)


def reset():
    """回到干净的 idle 状态：给测试用，服务启动时也调一次。"""
    with _LOCK:
        _STATE.update({
            "phase": "idle", "version": "", "detail": "",
            "done": 0, "total": 0, "at": 0,
        })


def _set(phase, version="", detail="", done=None, total=None):
    """更新共享进度状态。

    字节计数只在调用方明确传入时覆盖：一次下载从 0/0 开始、由传输过程填满，
    后续阶段不动它们，进度条就停在下载结束时的位置，而不是在进入安装阶段时
    突然跳回空。
    """
    with _LOCK:
        _STATE["phase"] = phase
        _STATE["version"] = version
        _STATE["detail"] = detail
        _STATE["at"] = int(time.time())
        if done is not None:
            _STATE["done"] = int(done)
        if total is not None:
            _STATE["total"] = int(total)


def _percent(done, total):
    """整数百分比；服务器没给长度时为 0。

    总量未知时保持 0 而不是假装 100%：一条明明还在收字节却显示已完成的进度条，
    比一条不确定的进度条更糟。
    """
    try:
        done = int(done)
        total = int(total)
    except (TypeError, ValueError):
        return 0
    if total <= 0 or done <= 0:
        return 0
    return max(0, min(100, int(done * 100 / total)))


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

#: install-local 卸载应用时会清空 @appdata，所以升级要用的文件不能放在那里。
#: @apphome 不会被卸载流程删除，正好用来放这次升级的工作区。
def _work_dir(var_dir=None):
    """升级工作区：安装包、向导环境变量、安装脚本、解包目录都放这里。

    必须放在**卸载不会清掉**的位置。install-local 的第一步就是卸载当前应用，
    它会把 @appdata/<app> 整个清空 —— 如果安装包与 update.env 还在里面，
    卸载一完成，接下来的安装就找不到它们了，结果是应用被卸掉、装不回来，
    连回滚包也跟着一起没了。
    """
    base = os.environ.get("TRIM_PKGHOME") or ""
    if not base:
        raw = var_dir or _CTX.get("var_dir") or os.environ.get("TRIM_PKGVAR") or ""
        # 先把路径解析成真实路径再替换。var_dir 在设备上通常是
        # /var/apps/<app>/var —— 那是指向 /vol1/@appdata/<app> 的软链接，
        # 字面量里根本没有 "@appdata"，直接替换会原样返回、把工作区仍算在
        # @appdata 里（等于没起作用，卸载时照样被清空）。
        # 解析之后再替换，两种写法都能落到 @apphome。
        try:
            real = os.path.realpath(raw) if raw else ""
        except OSError:
            real = raw
        base = real.replace("/@appdata/", "/@apphome/")
        if base == real and "@appdata" in real:
            base = real.replace("@appdata", "@apphome")
        if base == real and not base.startswith("/@apphome"):
            # 认不出 @appdata 结构（非常规部署、或本地单测传了个临时目录）：
            # 退回 <var>/update，宁可与日志同目录，也不要算出个不存在的路径。
            base = raw
    if not base:
        return ""
    return base.rstrip("/") + "/update"


def _update_dir(var_dir=None):
    """日志与状态留在 @appdata，用户能在存储页看到并清理。"""
    base = var_dir or _CTX.get("var_dir") or os.environ.get("TRIM_PKGVAR") or ""
    if not base:
        return ""
    return os.path.join(base, "update")


def package_dir(var_dir=None):
    """安装包实际落盘的目录（@apphome 下的工作区）。

    存储页要列「安装包缓存」，就得按这个真实位置去找 —— 只看 @appdata/update
    永远看不到任何包，因为下载自始至终没有写到那里（见 _work_dir 的说明：
    卸载会清空 @appdata，包放那里会被卸掉一半）。
    """
    return _work_dir(var_dir)


def _install_log_path(var_dir=None):
    d = _update_dir(var_dir)
    return os.path.join(d, "install.log") if d else ""


def _env_path(var_dir=None):
    d = _update_dir(var_dir)
    return os.path.join(d, "update.env") if d else ""


def _tail(path, lines=LOG_TAIL_LINES):
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return ""
    rows = text.strip().splitlines()
    return "\n".join(rows[-lines:])


def progress():
    """当前进度快照，附带安装脚本日志的尾部。

    日志是每次调用都从磁盘现读的，这是刻意的：有意思的那几行是本进程被杀之后
    由分离出去的脚本写的，文件是唯一还能传过来的通道。
    """
    with _LOCK:
        out = dict(_STATE)
        out["current"] = _CTX.get("current") or ""
        out["percent"] = _percent(out.get("done"), out.get("total"))
    if not out["current"]:
        # 还没走过任何一次 install() 时没有上下文，退到已安装目录的 manifest：
        # 升级完成后浏览器要靠「界面上的版本变了」判断收尾，这个值不能是空的。
        out["current"] = read_installed_version()
    out["log"] = _tail(_install_log_path())
    return out


def busy():
    """下载或安装进行中。存储清理据此整体拒绝。"""
    with _LOCK:
        return str(_STATE.get("phase") or "") in ("downloading", "installing")


# ---------------------------------------------------------------------------
# 资产
# ---------------------------------------------------------------------------

def pick_asset(info):
    """从发布的资产列表里挑出要安装的 fpk。

    优先带平台后缀的那一个；不是 .fpk 的东西（校验和、构建日志、源码归档）
    一律忽略 —— 下载到那种文件再走卸载，会直接毁掉安装。
    """
    assets = (info or {}).get("assets") or []
    fpks = []
    for item in assets:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        url = str(item.get("url") or item.get("browser_download_url") or "")
        if not name.lower().endswith(".fpk") or not url:
            continue
        fpks.append({"name": name, "url": url})
    if not fpks:
        return None
    for item in fpks:
        if "-all" in item["name"].lower():
            return item
    return fpks[0]


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name):
    """把资产名收敛成可以放心放进 shell 脚本里的样子。

    名字来自远端 JSON，最终会落进一条带引号的参数里，所以是过滤而不是信任。
    """
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = _SAFE_NAME.sub("-", base).strip("-.")
    if not base.lower().endswith(".fpk"):
        base = (base or APP_NAME) + ".fpk"
    return base or (APP_NAME + ".fpk")
# ---------------------------------------------------------------------------
# 下载
# ---------------------------------------------------------------------------

def download(url, dest, limit=DOWNLOAD_LIMIT, on_progress=None, timeout=HTTP_TIMEOUT):
    """把 URL 流式写到 dest，返回字节数。

    先写 dest + ".part"，最后一个字节到了才改名：一个下了一半的文件绝不能出现在
    校验器正要打开的那个路径上，否则掉线的下载看起来和正常的下载一模一样。
    """
    if not str(url or "").strip():
        raise ValueError("下载地址为空")
    folder = os.path.dirname(os.path.abspath(dest))
    if folder:
        os.makedirs(folder, exist_ok=True)
    part = dest + ".part"
    req = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    total = 0
    expect = 0
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        raise ValueError("下载失败：HTTP %d" % int(getattr(exc, "code", 0) or 0))
    except Exception as exc:
        raise ValueError("下载失败：%s" % (exc,))
    try:
        with resp:
            try:
                expect = int(resp.headers.get("Content-Length") or 0)
            except (TypeError, ValueError):
                expect = 0
            if on_progress:
                on_progress(0, expect)
            with open(part, "wb") as handle:
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("安装包超过 %d MB，已中止"
                                         % (limit // 1024 // 1024))
                    handle.write(chunk)
                    if on_progress:
                        on_progress(total, expect)
                handle.flush()
                os.fsync(handle.fileno())
    except ValueError:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise
    except http.client.IncompleteRead:
        # 服务器承诺了 Content-Length 却提前断开。显式接住，因为「下了一半的包」
        # 是唯一会伪装成下载成功的失败。
        try:
            os.unlink(part)
        except OSError:
            pass
        raise ValueError("下载不完整：只收到 %d 字节（连接中断）" % total)
    except Exception as exc:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise ValueError("下载中断：%s" % (exc,))
    if total <= 0:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise ValueError("下载内容为空")
    # 分块 read() 以返回空字节串表示 EOF，不会抛异常 —— 所以一个承诺了
    # Content-Length 又提前挂断的服务器，看起来就和完整下载一样。比对字节数是
    # 唯一能抓到它的办法，而被截断的包绝不能走到校验之后的卸载那一步。
    if expect > 0 and total != expect:
        try:
            os.unlink(part)
        except OSError:
            pass
        raise ValueError("下载不完整：收到 %d 字节，应为 %d 字节" % (total, expect))
    os.replace(part, dest)
    return total


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _find_appcenter(explicit=""):
    if explicit:
        return explicit if os.path.isfile(explicit) else ""
    found = shutil.which("appcenter-cli")
    if found:
        return found
    for candidate in APPCENTER_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return ""


def _manifest_fields(raw):
    fields = {}
    for line in (raw or "").splitlines():
        line = line.strip().lstrip("\ufeff")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        # 第一次出现优先，与 fnpack 读取方式一致
        fields.setdefault(key.strip(), value.strip())
    return fields


def validate_fpk(path, app_name=APP_NAME, appcenter=None, expect_version=None):
    """除非这个包确实是我们自己的新版本，否则不许卸载任何东西。

    抛 ValueError，消息可直接给界面看。在脚本被写出来之前就调用 —— 脚本里的
    那次卸载从面板的角度看是不可逆的。

    appcenter：显式指定 appcenter-cli 路径；None 表示按常规位置搜索。
    测试可以传一个替身，从而在设备之外把这条检查跑通。
    """
    import tarfile

    if not path or not os.path.isfile(path):
        raise ValueError("安装包不存在")
    if os.path.getsize(path) == 0:
        raise ValueError("安装包是空文件")
    try:
        # r:* 而不是 r:gz：包的外层是未压缩的 tar，只有内层 app.tgz 才 gzip。
        # 写死 r:gz 时，一个完全正常的 .fpk 会被判成「不是 gzip 文件」。
        with tarfile.open(path, "r:*") as tf:
            names = tf.getnames()
            # fnpack 总会写这两个；缺任何一个，说明要么不是这种包，要么包是坏的。
            if "manifest" not in names:
                raise ValueError("安装包缺少 manifest")
            if "app.tgz" not in names:
                raise ValueError("安装包缺少 app.tgz")
            raw = tf.extractfile("manifest").read().decode("utf-8", "replace")
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("安装包无法解压：%s" % (exc,))

    fields = _manifest_fields(raw)
    found = fields.get("appname") or ""
    if found != app_name:
        raise ValueError("安装包应用名不匹配：%s" % (found or "?"))

    have = (fields.get("version") or "").strip().lstrip("vV")
    if not have:
        raise ValueError("安装包 manifest 里没有版本号")
    if expect_version is not None:
        want = str(expect_version or "").strip().lstrip("vV")
        if want and have != want:
            raise ValueError("安装包版本不匹配：期望 %s，实际 %s" % (want, have))

    cli = _find_appcenter(appcenter or "")
    if not cli:
        raise ValueError("找不到 appcenter-cli，无法自动安装")
    return {"version": have, "appcenter": cli}


# ---------------------------------------------------------------------------
# 回滚包
# ---------------------------------------------------------------------------

def find_previous_fpk(current, app_name=APP_NAME, extra_dirs=(), exclude=()):
    """找出与当前运行版本对应的安装包，供回滚使用。

    找不到是正常情况（全新安装的机器上就没有更早的包），返回空串而不是报错：
    升级照常进行，只是没有回滚可用。
    """
    want = str(current or "").strip()
    if not want:
        return ""
    prefix = "%s-%s" % (app_name, want)
    skip = set()
    for path in exclude:
        try:
            skip.add(os.path.abspath(path))
        except OSError:
            continue
    dirs = list(extra_dirs)
    for pattern in PREV_SEARCH_DIRS:
        dirs.append(pattern % app_name if "%s" in pattern else pattern)
    seen = set()
    for folder in dirs:
        if not folder or folder in seen:
            continue
        seen.add(folder)
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for name in entries:
            if not name.startswith(prefix) or not name.lower().endswith(".fpk"):
                continue
            full = os.path.join(folder, name)
            try:
                if not os.path.isfile(full) or os.path.abspath(full) in skip:
                    continue
            except OSError:
                continue
            return full
    return ""


def read_installed_version(app_name=APP_NAME, manifest=None):
    """读设备上已安装应用的版本号；读不到返回空串。

    安装脚本用它判断「这次升级到底成没成」：install-fpk 对着已装应用会静默
    什么都不做，唯一能识破这件事的证据就是「装完以后版本号没变」。
    """
    path = manifest or (INSTALL_MANIFEST % app_name)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read()
    except OSError:
        return ""
    return (_manifest_fields(raw).get("version") or "").strip().lstrip("vV")

# ---------------------------------------------------------------------------
# 安装脚本
# ---------------------------------------------------------------------------
#
# 分离执行的安装器。占位符用字面文本替换而不是 % 格式化：脚本里本来就有
# printf 与 date 的百分号，走格式化就得层层转义，稍错一处就把脚本弄坏。
#
# raw string 是必须的：这是 shell 源码，反斜杠与字母 n 这两个字符必须原样
# 到达脚本。写成普通三引号字符串时它会被 Python 展开成真正的换行，printf 的
# 格式串于是断了引号 —— 而 sh -n 与单测都能通过，只有真跑才暴露。
_SCRIPT = r"""#!/bin/sh
# Fail2ban 一键升级。
#
# 从面板分离执行：它要停掉并且卸载掉拉起它的那个服务，所以不能与面板共享
# 进程组。它做的每件事都追加写进下面这个日志，旧进程消失之后，这是唯一
# 还活着的通道。
set -u

LOG="@LOG@"
exec >>"$LOG" 2>&1
echo "=== $(date '+%F %T') 升级到 @VERSION@ ==="

APP="@APP@"
PKG="@PKG@"
PREV="@PREV@"
ENVF="@ENVF@"
AC="@AC@"
TARGET="@VERSION@"
MANIFEST="@MANIFEST@"
WORK="@WORK@"

installed() {
  [ -f "$MANIFEST" ] || return 0
  awk -F= '/^version/ { gsub(/[ \t]/, "", $2); print $2; exit }' "$MANIFEST" 2>/dev/null
}

started() {
  "$AC" status "$APP" 2>/dev/null | grep -qE 'running|starting'
}

if [ ! -f "$PKG" ]; then
  echo "=== 安装包不见了，什么都没改：$PKG ==="
  exit 1
fi
if [ ! -f "$ENVF" ]; then
  echo "=== 环境变量文件不见了，什么都没改：$ENVF ==="
  exit 1
fi

# 留一点时间让浏览器读到自己正在轮询的「安装中」状态。
sleep 2

echo "=== stop $APP ==="
"$AC" stop "$APP" 2>&1 || true
sleep 1

# 用 install-local 而不是「uninstall + install-fpk」：
#   * appcenter-cli uninstall 对第三方应用一律被拒（"please uninstall it
#     from Web UI"），这一步永远做不成；
#   * install-fpk 对着已安装的应用只会回一句 "is installed"，什么都不做；
#   * install-local 自己会先 stop → uninstall → install，而且这条路径是被
#     允许的（实测它能真把应用换掉）。
# 包先解到临时目录，因为 install-local 吃的是目录而不是 .fpk。
WORK="@WORK@"
rm -rf "$WORK"
mkdir -p "$WORK"
if tar -xf "$PKG" -C "$WORK" 2>&1 && [ -f "$WORK/manifest" ]; then
  echo "=== install-local from $WORK ==="
  "$AC" install-local -d "$WORK" -e "$ENVF" -v 1 -a yes 2>&1 || true
else
  # 解包失败就退回老的 install-fpk：至少还有一条路能试。
  echo "=== unpack failed, falling back to install-fpk ==="
  "$AC" uninstall "$APP" 2>&1 || true
  sleep 3
  "$AC" install-fpk "$PKG" -e "$ENVF" -v 1 2>&1 || true
fi

# install-fpk 返回时应用中心往往还没登记完，所以两个结论都得轮询：
# 已安装的版本号是不是新版本，以及服务有没有起来。
# start 只在「版本号已经是目标、但服务没起」时才发一次 —— install-local 自己
# 启动失败后再反复.start 只会把日志刷满进度条，不会带来新结果。
n=0
got=""
started_once=0
while [ "$n" -lt @ATTEMPTS@ ]; do
  got=$(installed)
  if [ "$got" = "$TARGET" ] && started; then
    break
  fi
  if [ "$got" = "$TARGET" ] && [ "$started_once" = "0" ]; then
    "$AC" start "$APP" 2>&1 || true
    started_once=1
  fi
  sleep @INTERVAL@
  n=$((n + 1))
done
got=$(installed)

if [ "$got" = "$TARGET" ] && started; then
  echo "=== 已升级到 $TARGET ==="
  exit 0
fi

if [ "$got" = "$TARGET" ]; then
  echo "=== $TARGET 已安装，但服务没有报告运行，重试启动 ==="
  "$AC" start "$APP" 2>&1 || true
  sleep 5
  if started; then
    echo "=== 已升级到 $TARGET ==="
    exit 0
  fi
  # 应用中心报 11000（拉起超时）时，其实 cmd/main 自己就能把服务带起来 ——
  # 面板的启动脚本会把 pid/ready 写好，只是应用中心的握手已经先超时了。
  # 版本号已经是目标版本，这里补一次直接启动，别让一次握手超时被当成升级失败。
  echo "=== 应用中心拉起超时，直接用 cmd/main 启动 ==="
  MAIN="@MAIN@"
  if [ -x "$MAIN" ]; then
    "$MAIN" start 2>&1 || true
    sleep 6
    if started; then
      echo "=== 已升级到 $TARGET（由 cmd/main 启动） ==="
      exit 0
    fi
  fi
fi

echo "=== 升级没有成功（当前已安装版本：${got:-无}） ==="

# 分两种情况，处理必须相反：
#   * 应用还在（版本号非空且不是目标）—— 旧的安装完好，说明卸载或安装被
#     拒绝了。这里绝对不能回滚：卸载再装一次只会把一个能用的实例拆掉。
#   * 应用不在了（版本号为空）—— 卸载成功了但安装没成，这才是必须回滚的
#     场景，否则用户连面板都没有了。
if [ -n "$got" ]; then
  echo "=== 原安装仍在，保持现状，不执行回滚 ==="
  "$AC" start "$APP" 2>&1 || true
  exit 1
fi

if [ -n "$PREV" ] && [ -f "$PREV" ]; then
  echo "=== 回滚到上一个版本 ==="
  rm -rf "$WORK"
  mkdir -p "$WORK"
  if tar -xf "$PREV" -C "$WORK" 2>&1 && [ -f "$WORK/manifest" ]; then
    "$AC" install-local -d "$WORK" -e "$ENVF" -v 1 -a yes 2>&1 || true
  else
    "$AC" install-fpk "$PREV" -e "$ENVF" -v 1 2>&1 || true
  fi
  sleep 3
  "$AC" start "$APP" 2>&1 || true
  echo "=== 已尝试回滚，当前版本：$(installed) ==="
else
  echo "=== 没有可用的上一个安装包，无法回滚 ==="
fi
exit 1
"""


def build_script(version, pkg, prev, log_path, env_path, appcenter,
                 manifest="", work_dir=""):
    """渲染分离执行的安装脚本。

    单独成一个函数，脚本文本才能在不执行的情况下被断言；同时路径为空这类
    错误会在这里就炸，而不是在卸载进行到一半时才发现。

    manifest 是「判断这次到底装没装上」要读的那份文件，默认取设备上的常规
    位置。做成参数而不是环境变量：脚本会继承面板的进程环境，能用环境把它
    指到别处，等于让这一层自检形同虚设。
    """
    if not str(pkg or "").strip():
        raise ValueError("安装包路径为空")
    if not str(log_path or "").strip():
        raise ValueError("日志路径为空")
    if not str(env_path or "").strip():
        raise ValueError("环境变量文件路径为空")
    if not str(appcenter or "").strip():
        raise ValueError("appcenter-cli 路径为空")
    local = {
        "VERSION": str(version or "").strip().lstrip("vV") or "unknown",
        "PKG": str(pkg),
        "PREV": str(prev or ""),
        "LOG": str(log_path),
        "APP": APP_NAME,
        "ENVF": str(env_path),
        "AC": str(appcenter),
        "MANIFEST": str(manifest or (INSTALL_MANIFEST % APP_NAME)),
        # install-local 吃目录不吃 .fpk，包要先解到这里。这个目录必须在卸载
        # 时保留：install-local 的第一步就是卸载当前应用，@appdata 会被清空，
        # 而安装包与 update.env 此刻正在被安装流程使用。
        "WORK": str(work_dir or "") + "/unpacked" if work_dir else
                posixpath.dirname(str(pkg).replace("\\", "/")) + "/unpacked",
        # 应用中心拉起服务偶尔会报 11000（握手超时），这时 cmd/main 往往还能
        # 把服务带起来。路径从 appcenter-cli 推：.../@appcenter/<app> 对应的
        # 入口脚本在 /var/apps/<app>/cmd/main。
        "MAIN": "/var/apps/%s/cmd/main" % APP_NAME,
        "ATTEMPTS": str(int(VERIFY_ATTEMPTS)),
        "INTERVAL": str(int(VERIFY_INTERVAL)),
    }
    text = _SCRIPT
    for key, value in local.items():
        text = text.replace("@" + key + "@", value)
    return text


# ---------------------------------------------------------------------------
# 向导环境变量
# ---------------------------------------------------------------------------

def current_policy():
    """读当前生效的默认策略，供升级时回填向导字段。

    只依赖 jailconf 的只读解析，任何失败都退回内置默认值：这几个值只是
    bootstrap 的初值，读不到不该让一次升级开不了工。
    """
    out = {"default_bantime": "1h", "default_maxretry": "5",
           "default_ignoreip": "127.0.0.1/8 ::1"}
    try:
        import jailconf
    except Exception:
        return out
    try:
        merged, _sources = jailconf.load_merged()
        values = jailconf.options_to_values(
            jailconf.effective_jail("DEFAULT", merged))
    except Exception:
        return out
    for key in out:
        value = str(values.get(key) or "").strip()
        if value:
            out[key] = value
    return out


def wizard_env(current_settings=None):
    """拼出升级时要喂给 install-fpk 的向导字段。

    install-fpk -e 必须拿到向导里的**全部**字段，少一个就报 "Something wrong
    with environment variables."；而其中两个的值必须与安装时不同：

    * enable_ssh_jail 一律传 false —— install_callback 收到 true 会无条件把
      sshd Jail 的 enabled 写成 true，那等于替用户重新打开一个他关掉的防护。
      传 false 时那段代码整段跳过，现有 sshd 配置原样保留。
    * auto_install 一律传 false —— 面板自己的升级不该在背后用 apt 装系统软件。

    其余三个只是 bootstrap 的初值，传当前生效的值最不会意外。
    """
    policy = current_policy()
    settings = current_settings if isinstance(current_settings, dict) else {}
    values = {
        "auto_install": "false",
        "enable_ssh_jail": "false",
        "default_bantime": str(settings.get("default_bantime")
                               or policy["default_bantime"]),
        "default_maxretry": str(settings.get("default_maxretry")
                                or policy["default_maxretry"]),
        "default_ignoreip": str(settings.get("default_ignoreip")
                                or policy["default_ignoreip"]),
    }
    lines = []
    for key in WIZARD_FIELDS:
        # 换行会破坏「一行一个键值」的文件结构，回车与换行一并压成空格。
        value = str(values.get(key) or "")
        value = value.replace("\r", " ").replace("\n", " ").strip()
        lines.append("%s=%s" % (key, value))
    return "\n".join(lines) + "\n"


def write_wizard_env(path, current_settings=None):
    """写出向导环境变量文件，返回写入的路径。"""
    if not str(path or "").strip():
        raise ValueError("环境变量文件路径为空")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    text = wizard_env(current_settings)
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    return path


# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------

def _spawn_detached(script, workdir):
    """在独立会话里跑安装脚本。

    start_new_session 就是关键所在：脚本第二步会卸载应用，也就是停掉本模块
    所在的这个服务。作为普通子进程，它会收到同一份终止信号，卡死在升级中途。
    """
    return subprocess.Popen(
        ["/bin/sh", script],
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def install(version, url, var_dir, current, *, appcenter="", prev_fpk="",
            settings=None, spawn=None, timeout=HTTP_TIMEOUT, limit=DOWNLOAD_LIMIT,
            log=None):
    """下载、校验并交接给分离出去的安装脚本。

    立刻返回：真正的活在后台线程里做，浏览器于是能在下载过程中读到真实百分比。
    下载与校验阶段的任何失败都写进 progress()，并且不改动任何已装的东西。

    返回 {"ok": True, "phase": "downloading", ...} 表示已经开工；
    {"ok": False, "error": ...} 表示还没碰就失败了。
    """
    global _LOG
    if log is not None:
        _LOG = log if callable(log) else None
    if busy():
        return {"ok": False, "error": "升级已在进行中",
                "phase": _STATE.get("phase")}

    target = str(version or "").strip().lstrip("vV")
    if not target:
        return {"ok": False, "error": "版本信息里没有版本号"}
    if not str(url or "").strip():
        return {"ok": False, "error": "没有可下载的安装包地址"}

    updir = _update_dir(var_dir)
    if not updir:
        return {"ok": False, "error": "无法确定更新目录"}
    try:
        os.makedirs(updir, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": "无法创建更新目录：%s" % exc}

    # 升级用的文件放到卸载不会清的位置（见 _work_dir 的说明）。
    workdir = _work_dir(var_dir) or updir
    try:
        os.makedirs(workdir, exist_ok=True)
    except OSError as exc:
        return {"ok": False, "error": "无法创建升级工作目录：%s" % exc}

    cli = _find_appcenter(appcenter or "")
    if not cli:
        return {"ok": False, "error": "找不到 appcenter-cli，无法自动安装"}

    set_context(var_dir=var_dir, current=current)

    base = os.path.basename(str(url).split("?")[0])
    name = _safe_name(base) if base else "%s-%s.fpk" % (APP_NAME, target)

    _set("downloading", target, "准备下载 %s" % name, done=0, total=0)
    _emit("update: 开始下载 %s（%s）" % (name, target))

    def worker():
        try:
            _run_install(target, url, os.path.join(workdir, name), workdir, updir, cli,
                         current, prev_fpk, settings, spawn, timeout, limit)
        except Exception as exc:  # noqa: BLE001 - 后台线程必须自己收尾
            _set("failed", target, "升级失败：%s" % exc)
            _emit("update: 升级失败：%r" % (exc,))

    threading.Thread(target=worker, name="f2b-updater", daemon=True).start()
    return {"ok": True, "phase": "downloading", "version": target,
            "file": name, "script": "", "rollback": False}


def _run_install(target, url, dest, updir, logdir, cli, current, prev_fpk, settings,
                 spawn, timeout, limit):
    """后台线程的主体：下载 → 校验 → 写脚本 → 分离执行。

    updir 是卸载后仍然保留的工作区（安装包、env、脚本、解包目录都在这里）；
    logdir 是 @appdata 下的 update/，只放日志 —— 那份日志要留在用户能看到、
    能被存储页统计与清理的地方。
    """
    log_path = os.path.join(logdir, "install.log")
    env_path = os.path.join(updir, "update.env")

    def on_progress(done, total):
        _set("downloading", target, "正在下载 %s" % os.path.basename(dest),
             done=done, total=total)

    try:
        download(url, dest, limit=limit, on_progress=on_progress, timeout=timeout)
        _emit("update: 已下载 %d 字节" % os.path.getsize(dest))
        # 在任何东西被卸载之前校验，并且对着发布声称的版本号核对，
        # 这样一份过期的资产不会被装上去。
        info = validate_fpk(dest, appcenter=cli, expect_version=target)
    except Exception as exc:
        _set("failed", target, str(exc))
        _emit("update: 准备阶段失败：%s" % (exc,))
        return {"ok": False, "error": str(exc)}

    prev = ""
    try:
        source = prev_fpk or find_previous_fpk(current, exclude=[dest])
        if source:
            prev = os.path.join(updir, "rollback.fpk")
            if os.path.abspath(source) != os.path.abspath(prev):
                shutil.copyfile(source, prev)
            _emit("update: 回滚包已备好，来自 %s" % source)
    except Exception as exc:
        # 少了回滚是安全网变薄，但不能挡住升级：新包已经验过。
        _emit("update: 跳过回滚包准备：%r" % (exc,))
        prev = ""

    script = os.path.join(updir, "install.sh")
    try:
        write_wizard_env(env_path, settings)
        text = build_script(info.get("version") or target, dest, prev,
                            log_path, env_path, info.get("appcenter") or cli,
                            work_dir=os.path.dirname(dest))
        with open(script, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.chmod(script, 0o755)
    except Exception as exc:
        _set("failed", target, "无法写入安装脚本：%s" % exc)
        _emit("update: 无法写入安装脚本：%r" % (exc,))
        return {"ok": False, "error": "无法写入安装脚本：%s" % exc}

    try:
        runner = spawn or _spawn_detached
        runner(script, updir)
    except Exception as exc:
        _set("failed", target, "无法启动安装脚本：%s" % exc)
        _emit("update: 无法启动安装脚本：%r" % (exc,))
        return {"ok": False, "error": "无法启动安装脚本：%s" % exc}

    _set("installing", target, "安装包已就绪，正在重启服务")
    _emit("update: 安装脚本已启动，目标版本 %s" % target)
    return {"ok": True, "version": target, "file": os.path.basename(dest),
            "script": script, "rollback": bool(prev)}


def status():
    """progress() 的别名，保持旧调用点可用。"""
    return progress()
