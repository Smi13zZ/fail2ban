#!/usr/bin/env python3
"""前端闸门：JS 语法 + i18n 反查表一致性。

这部分原先散在几个一次性脚本里，每次改完靠手跑；现在收进一道闸门，CI 与本地同一条命令。

  * 语法：用 node --check 逐个文件解析（没有 node 时明确报「跳过」，不假装通过）；
  * i18n 反查表：i18n.js 里 SRC2KEY / SRC2KEY_ALT / LINE_TPL 引用的每个 key 都必须在
    字典里存在。这些表是手写的中文原文 → key 映射，漏改一处不会报错，
    表现只是某一句不跟着语言切换（一直显示中文）；
  * 页面装配：index.html 必须真的引用到样式表与两个脚本。少一个 <script> 不会报错，
    表现只是「整块功能消失」，而且本地预览时因为缓存还能看到旧行为，最难查；
  * 内联间距补丁：设置页范围内不许出现 style="margin…"。这类补丁绕过设计令牌，
    改全局间距时它们纹丝不动，同一张卡片里就会有两种节奏。

用法：

    python tools/check_web.py                  # 查仓库里的 web 目录
    python tools/check_web.py a.js index.html  # 指定文件（变异测试时用）
"""
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

I18N = os.path.join(G.WEB, "i18n.js")
INDEX = os.path.join(G.WEB, "index.html")
APP_JS = os.path.join(G.WEB, "app.js")

# index.html 必须引用到的东西：缺一个就是整块功能消失，而且不报错
REQUIRED_ASSETS = {
    "style.css": "样式表",
    "i18n.js": "中英文字典",
    "app.js": "前端主脚本",
}

# 内联间距补丁：绕过设计令牌，改全局间距时它们纹丝不动。
# 只查间距相关属性 —— width/height 这类一次性数值常常是数据驱动的，不算补丁。
INLINE_SPACING = re.compile(r"""style\s*=\s*(?:"([^"]*)"|'([^']*)')""")
REF_ANY = re.compile(r"""(?:href|src)\s*=\s*["']([^"']+)["']""")
SPACING_PROP = re.compile(r"(?:^|;)\s*(margin|padding)(-[a-z]+)?\s*:", re.I)


# 设置页范围的函数白名单：设置页（含并入它的四个分区）渲染出来的 HTML 里
# 不允许出现内联间距补丁。名单按 26.11.6 的结构写；新增设置页辅助函数时要补进来，
# 名单外的函数会作为「非设置页残留」打进 NOTE，不会消失。
SETTINGS_FUNCS = {
    "renderSettings", "settingsWorkbarHtml", "settingsTabHtml",
    "cardFoot", "cardHead", "setSection", "prefsOf",
    "renderSetPanel", "renderSetAppearance", "renderSetPolicy",
    "renderSetBackup", "renderSetAbout",
    "renderNotifications", "notificationsHtml", "deliveryCardHtml",
    "renderStorage", "storageHtml",
    "renderUpdate", "updateHtml",
    "ignoreIpFieldHtml", "switchRowFor", "switchRow", "renderChannelFields",
}

FUNC_START = re.compile(r"^  function ([A-Za-z0-9_]+)\(", re.M)


def functions_with_lines(text):
    """-> ([(起始行号, 函数名)]，供「某行属于哪个函数」判定)。"""
    return [(text.count("\n", 0, m.start()) + 1, m.group(1))
            for m in FUNC_START.finditer(text)]


def owner_of(line_no, starts):
    """某一行最内层的顶层函数名；判定不了返回空串。"""
    current = ""
    for start, name in starts:
        if start <= line_no:
            current = name
        else:
            break
    return current


def inline_spacing_patches(report, app_js=None, index=None):
    """找出所有内联间距补丁，设置页范围内的判失败。

    为什么只盯 margin / padding：内联 width、height 常常是数据驱动的（进度条百分比、
    图表尺寸），那是正当用法；而内联 margin/padding 一定是「样式没地方放，随手补一笔」，
    改全局间距时它们不会跟着变，同一张卡片里就会出现两种节奏。
    """
    targets = [app_js or APP_JS, index or INDEX]
    inside, outside = [], []
    for path in targets:
        if not os.path.isfile(path):
            continue
        text = G.read_text(path)
        starts = functions_with_lines(text) if path.endswith(".js") else []
        for match in INLINE_SPACING.finditer(text):
            value = match.group(1) if match.group(1) is not None else match.group(2) or ""
            if not SPACING_PROP.search(value):
                continue
            line_no = text.count("\n", 0, match.start()) + 1
            owner = owner_of(line_no, starts) if starts else "(index.html)"
            item = (G.rel(path), line_no, owner, value.strip()[:60])
            if owner in SETTINGS_FUNCS or owner == "(index.html)":
                inside.append(item)
            else:
                outside.append(item)

    for path, line_no, owner, value in inside:
        report.fail("%s:%d" % (path, line_no),
                    "%s 里的内联间距补丁 style=\"%s\" —— 用 .card-head / .card-foot / "
                    "现有的间距类，别在卡片内部手工补 margin" % (owner, value))
    if outside:
        owners = sorted({item[2] for item in outside})
        report.note(G.rel(app_js or APP_JS),
                    "设置页之外还有 %d 处内联间距补丁（涉及 %d 个函数：%s）——"
                    "本版未要求处理，记在这里以免被忘掉"
                    % (len(outside), len(owners), "、".join(owners[:6])))
    if not inside:
        print("  设置页内联间距补丁：0 处")
    return inside


def check_page_assembly(report, index=None):
    """index.html 必须真的引用到样式表与两个脚本。

    少一个 <script> 不会报错，表现只是「整块功能消失」；而本地预览因为缓存，
    往往还能看到旧行为，最难查。
    """
    index = index or INDEX
    if not os.path.isfile(index):
        report.fail(G.rel(index), "找不到 index.html")
        return
    html = G.read_text(index)
    refs = set(REF_ANY.findall(html))
    for asset, note in REQUIRED_ASSETS.items():
        target = os.path.join(G.WEB, asset)
        if not os.path.isfile(target):
            report.fail(G.rel(G.WEB), "缺少文件 %s（%s）" % (asset, note))
            continue
        if asset not in refs:
            report.fail(G.rel(index), "没有引用 %s（%s）—— 页面会缺掉这一块，"
                                      "而且不会有任何报错" % (asset, note))
    # 引用了但文件不存在（改文件名忘改引用）也算
    for ref in sorted(refs):
        if ref.startswith(("http://", "https://", "data:", "//", "/")):
            continue
        if not os.path.exists(os.path.normpath(os.path.join(G.WEB, ref))):
            report.fail(G.rel(index), "引用的文件不存在：%s" % ref)
    print("  页面装配：style.css / i18n.js / app.js 均已引用")


def js_syntax(report, node):
    if not node:
        # 明确区分「跳过」和「通过」：静默跳过看起来和通过一模一样，最容易骗过自己。
        # 这是「闸门没生效」，按失败处理；机器上确有 node 时 find_node() 自己会找到它。
        report.fail(G.rel(G.WEB), "找不到 node，JS 语法检查无法进行 —— 设置 DSH_NODE 或 "
                                  "把 node 放进 PATH（打包机与 CI 上都有 node）")
        return
    print("  使用 node：%s" % node)
    files = G.walk_files(G.WEB, [".js"])
    if not files:
        report.fail(G.rel(G.WEB), "没有找到任何 .js 文件")
        return
    for path in files:
        proc = subprocess.run([node, "--check", path], capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            report.fail(G.rel(path), "语法错误：%s" % (detail[0] if detail else "node --check 失败"))
        else:
            print("  ok  %s" % G.rel(path))


def reverse_tables(report):
    """反查表里引用的 key 必须在字典里；LINE_TPL 的 params 个数要与占位符对得上。"""
    if not os.path.isfile(I18N):
        report.fail(G.rel(I18N), "找不到字典文件")
        return
    src = G.read_text(I18N)
    keys = set(re.findall(r'^\s{4}"((?:[^"\\]|\\.)*)"\s*:\s*', src, re.M))

    def block(name):
        marker = re.search(r"var\s+%s\s*=\s*\{" % name, src)
        if not marker:
            return ""
        start = marker.end() - 1
        depth, i, in_str, escape = 0, start, None, False
        while i < len(src):
            ch = src[i]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == in_str:
                    in_str = None
            elif ch in "\"'":
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return src[start:i + 1]
            i += 1
        return ""

    total = 0
    for name in ("SRC2KEY", "SRC2KEY_ALT"):
        body = block(name)
        if not body:
            report.fail(G.rel(I18N), "找不到 %s" % name)
            continue
        # 值可能是字符串，也可能是 [key, key] 备选数组
        refs = re.findall(r'"([A-Za-z][\w.]*)"', body)
        refs = [r for r in refs if "." in r and not r.startswith("http")]
        total += len(refs)
        for ref in sorted(set(refs) - keys):
            report.fail(G.rel(I18N), "%s 里引用了字典中没有的 key %r" % (name, ref))

    body = block("LINE_TPL")
    if body:
        entries = re.findall(r"key:\s*\"([^\"]+)\"\s*,\s*params:\s*\[([^\]]*)\]", body)
        for key, params in entries:
            if key not in keys:
                report.fail(G.rel(I18N), "LINE_TPL 里引用了字典中没有的 key %r" % key)
                continue
            count = len([p for p in params.split(",") if p.strip()])
            total += 1
            # 占位符个数：key 的译文里 {x} 的个数应 >= params 个数
            value = re.search(r'"%s"\s*:\s*"(.*?)",?\s*$' % re.escape(key), src, re.M)
            if value:
                holes_in_text = len(re.findall(r"\{\w+\}", value.group(1)))
                if holes_in_text < count:
                    report.fail(G.rel(I18N),
                                "LINE_TPL 的 %r 声明了 %d 个占位值，译文里只有 %d 个"
                                % (key, count, holes_in_text))
    print("  反查表引用的 key %d 处已核对" % total)


def main(argv=None):
    G.setup_stdio()
    argv = sys.argv[1:] if argv is None else argv
    # 可选的显式路径，仅用于在副本上做变异测试：
    #   python tools/check_web.py <app.js> <index.html>
    app_js = os.path.abspath(argv[0]) if len(argv) > 0 else APP_JS
    index = os.path.abspath(argv[1]) if len(argv) > 1 else INDEX
    report = G.Report("前端检查")
    js_syntax(report, G.find_node())
    reverse_tables(report)
    check_page_assembly(report, index)
    inline_spacing_patches(report, app_js, index)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())