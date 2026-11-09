#!/usr/bin/env python3
"""CSS 闸门：括号配平 + 变量先定义后使用 + 静态资源存在 + 契约类齐全。

样式表坏了不会报错，只会「某块区域突然没样子」，而设备上的截图里很难一眼定位到
是哪条规则断的。能在本地查出来的，都放进来：

  1. 花括号不配平 —— 少一个 } 之后，后面的全部规则会被浏览器当成无效内容丢掉；
  2. var(--x) 用了没定义的变量 —— 该属性的值整条作废（颜色变透明、间距变 0）；
  3. 变量定义的位置不对 —— 定义了但不在 :root / [data-theme] 里，等于只在那一条
     选择器下有效，换个上下文就是未定义；
  4. index.html 里引用的本地文件不存在 —— 图标空白、脚本 404，只有控制台会提示；
  5. 契约要求的类不存在 —— 结构与样式是两方并行写的，少一个类不会报错，
     表现只是「那块区域没有样子」。

用法： python tools/check_css.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

STYLE = os.path.join(G.WEB, "style.css")
INDEX = os.path.join(G.WEB, "index.html")

REF = re.compile(r'(?:href|src)\s*=\s*"([^"#?]+)"')
VAR_USE = re.compile(r"var\(\s*(--[\w-]+)")
VAR_DEF = re.compile(r"(--[\w-]+)\s*:")

# 主题块：变量只能定义在这两种选择器里，别处定义等于换个上下文就丢失
THEME_SELECTOR = re.compile(r"(^|,)\s*(:root|html)\s*($|,)|\[data-theme")

# 结构契约要求的类：类名 -> 说明。少一个不会报错，只是那块区域没样式。
# 来源是 SPEC 第七节（26.11.6 设置页），新增页面时在这里补一行。
REQUIRED_CLASSES = {
    ".workbar": "设置页的标签条容器",
    ".workbar button": "标签条里的按钮",
    ".workbar button.on": "选中态标签",
    ".workbar button:hover:not(.on)": "未选中标签的悬停态",
    ".workbar-dot": "标签上的红点",
    ".card-head": "卡片标题行（标题 + 行内动作）",
    ".card-foot": "卡片底部动作条",
    'html[data-density="compact"]': "紧凑密度开关",
}


def strip_comments(text):
    """去掉 /* */ 注释。注释里的花括号和示例代码不应该参与配平。

    用空格替换每个字符而不是整段删掉，这样行号不会漂移，
    报出来的行号能直接对着编辑器跳过去。
    """
    out = []
    i = 0
    while i < len(text):
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            if end < 0:
                out.append(" " * (len(text) - i))
                break
            chunk = text[i:end + 2]
            out.append("".join("\n" if ch == "\n" else " " for ch in chunk))
            i = end + 2
        else:
            ch = text[i]
            out.append(ch)
            i += 1
    return "".join(out)


def rule_blocks(text):
    """按花括号切出「选择器 { 主体 }」，返回 [(选择器, 主体, 起始行号)]。

    只处理一层嵌套：这里要判定的是顶层规则的选择器，@media 里的规则也一并返回，
    因为它们的变量定义同样不代表主题级定义 —— 判定时按选择器过滤就够了。
    """
    blocks = []
    depth = 0
    start = 0
    selector = ""
    i = 0
    line = 1
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            line += 1
        if ch == "{":
            if depth == 0:
                selector = text[start:i]
                body_start = i + 1
                body_line = line
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blocks.append((selector.strip(), text[body_start:i], body_line))
                start = i + 1
        i += 1
    return blocks


def check_variables(report, text):
    """变量必须定义在主题块里，且用到的都要有定义。

    两条分开判：
      * 用到了但哪儿都没定义 → 该属性整条失效，一定是 bug；
      * 定义了但不在 :root / [data-theme] 里 → 只在那个选择器下有效，
        换个上下文就是未定义，属于「埋着的坑」，按失败处理。
    """
    theme_defs = set()
    stray_defs = []
    for selector, body, line in rule_blocks(text):
        for name in VAR_DEF.findall(body):
            if THEME_SELECTOR.search(selector):
                theme_defs.add(name)
            else:
                stray_defs.append((name, selector, line))
    # 兜底：整体再扫一遍，防止 rule_blocks 因为语法异常漏掉定义
    all_defs = set(VAR_DEF.findall(text)) | theme_defs

    for name, selector, line in stray_defs:
        report.fail("%s:%d" % (G.rel(STYLE), line),
                    "%s 定义在 %r 里，而不是 :root / [data-theme] —— "
                    "这条规则之外的地方用它会变成未定义" % (name, selector[:40]))

    for match in VAR_USE.finditer(text):
        name = match.group(1)
        if name not in all_defs:
            line = text.count("\n", 0, match.start()) + 1
            report.fail("%s:%d" % (G.rel(STYLE), line),
                        "var(%s) 使用了但没有定义 —— 该属性整条失效" % name)

    print("  CSS 变量：主题级定义 %d 个，使用 %d 处"
          % (len(theme_defs), len(set(VAR_USE.findall(text)))))
    return theme_defs, all_defs


def selector_pattern(selector):
    """把契约选择器编译成「按标识符边界匹配」的正则。

    不能用朴素子串判断：改名或加后缀之后（.workbar 改成 .workbarGONE）子串仍然命得中，
    闸门就成了摆设 —— 变异测试第一次跑就是这么漏的。
    这里在两端加词边界，并允许选择器内部空白写成任意个空格/换行。
    """
    parts = selector.split()
    body = r"\s+".join(re.escape(part) for part in parts)
    return re.compile(r"(?<![-\w])" + body + r"(?![-\w])")


def check_required_classes(report, text):
    """结构契约里点名的类必须在样式表里出现。"""
    rules = rule_blocks(text)
    missing = []
    for selector, note in REQUIRED_CLASSES.items():
        pattern = selector_pattern(selector)
        found = any(pattern.search(sel) for sel, _body, _line in rules)
        if not found:
            missing.append((selector, note))
    for selector, note in missing:
        report.fail(G.rel(STYLE), "缺少选择器 %s（%s）—— 结构与样式并行开发时，"
                                  "少一个类不会报错，只是那块区域没有样子" % (selector, note))
    if not missing:
        print("  契约类齐全：%d 个" % len(REQUIRED_CLASSES))


def check_assets(report):
    if not os.path.isfile(INDEX):
        report.warn(G.rel(INDEX), "index.html 不存在，跳过资源引用检查")
        return
    html = G.read_text(INDEX)
    for ref in sorted(set(REF.findall(html))):
        if ref.startswith(("http://", "https://", "data:", "//", "/")):
            continue
        target = os.path.normpath(os.path.join(G.WEB, ref))
        if not os.path.exists(target):
            report.fail(G.rel(INDEX), "引用的本地资源不存在：%s" % ref)
    print("  静态资源引用已核对")


def main():
    G.setup_stdio()
    report = G.Report("CSS 检查")
    if not os.path.isfile(STYLE):
        print("找不到 %s" % G.rel(STYLE))
        print("RESULT: FAIL")
        return 1

    text = strip_comments(G.read_text(STYLE))

    depth = 0
    for lineno, line in enumerate(text.splitlines(), 1):
        for ch in line:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    report.fail("%s:%d" % (G.rel(STYLE), lineno),
                                "多出一个 }（此处已经闭合到最外层了）")
                    depth = 0
    if depth > 0:
        report.fail(G.rel(STYLE),
                    "花括号少闭合 %d 个 —— 从这个位置往后的规则会被浏览器整体丢弃" % depth)
    else:
        print("  花括号配平")

    theme_defs, all_defs = check_variables(report, text)
    unused = sorted(all_defs - set(VAR_USE.findall(text)))
    if unused:
        report.note(G.rel(STYLE), "%d 个变量定义了但没有用到：%s"
                    % (len(unused), "、".join(unused[:8])))

    check_required_classes(report, text)
    check_assets(report)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())