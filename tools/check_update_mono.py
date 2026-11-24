#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""升级遮罩阶段收敛规则的静态闸门（Fail2ban）。

  1. 阶段只允许前进（UP_PHASE_ORDER）
  2. 单调性只在同一 attempt 内成立
  3. 不拿安装响应体绘制进度（install() 的返回体没有 done/total）

用法：python3 tools/check_update_mono.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP_JS = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web", "app.js")


def main():
    src = io.open(APP_JS, encoding="utf-8").read()
    fails = []

    if "UP_PHASE_ORDER" not in src:
        fails.append("规则 1 缺少阶段顺序表 UP_PHASE_ORDER")
    if not re.search(r"checking\s*:\s*0", src):
        fails.append("规则 1 顺序表里没有 checking: 0")
    if "nextRank < prevRank" not in src:
        fails.append("规则 1 缺少「后退快照丢弃」")
    if "state.update.monoAttempt" not in src:
        fails.append("规则 3 缺少 attempt 基线")
    if not re.search(r"attempt !== state\.update\.monoAttempt", src):
        fails.append("规则 3 缺少「attempt 变了就重置」")
    # 安装响应不得用于绘制进度
    if re.search(r"if \(data && data\.phase\) paintUpProgress\(data\)", src):
        fails.append("规则 3 仍在用安装响应里的 phase 绘制进度")
    if "update.installingReady" not in src:
        fails.append("缺少安装阶段文案")
    if "update.phaseVerify" not in src:
        fails.append("缺少校验阶段文案")

    if fails:
        for f in fails:
            print("FAIL", f)
        return 1

    if not check_notes_and_log(src):
        return 1

    extra = check_indeterminate_and_verline()
    if extra:
        for item in extra:
            print("FAIL", item)
        return 1

    print("OK   升级遮罩阶段收敛规则齐备")
    return 0


def check_notes_and_log(src):
    """Release 正文渲染、安装日志、底部图标尺寸、重复 toast。

    这几条都是用户投诉或自踩之后补上的：
      1. Release 正文经 md.js 渲染（原先只做 esc，Markdown 原样显示）
      2. index.html 引入 md.js
      3. 安装日志只在失败时展开
      4. 装完不再弹第二条 toast（遮罩里已经有同一句话）
      5. 侧栏底部图标 18px / 1.8 描边，不再被 16px 那条规则压小
    """
    fails = []
    web = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web")

    html_path = os.path.join(web, "index.html")
    html = io.open(html_path, encoding="utf-8").read() if os.path.isfile(html_path) else ""
    css_path = os.path.join(web, "style.css")
    css = io.open(css_path, encoding="utf-8").read() if os.path.isfile(css_path) else ""

    if "renderNotes" not in src:
        fails.append("Release 正文没有走 Markdown 渲染（缺少 renderNotes）")
    # 定义存在但调用点被换回 esc() 同样等于没渲染，所以查调用点。
    if "renderNotes(info.notes)" not in src:
        fails.append("更新页没有调用 renderNotes（正文退回纯转义）")
    if html and "md.js" not in html:
        fails.append("index.html 没有引入 md.js")
    if not os.path.isfile(os.path.join(web, "md.js")):
        fails.append("缺少 web/md.js")

    if "state.update.installLog" not in src:
        fails.append("前端没有收下安装日志（state.update.installLog）")
    if "upinstalllog" not in src:
        fails.append("更新页没有安装日志块")

    # 装完的 toast：整个 finishUpgrade 都不该再弹「已更新到」——
    # 遮罩里已经写了同一句话。超时后才发现其实装成的那条分支也已改成留在遮罩上，
    # 所以这里可以直接对整个函数查。
    idx = src.find("function finishUpgrade(")
    if idx >= 0:
        brace = src.find("{", idx)
        depth = 0
        end = len(src)
        for i in range(brace, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        body = src[idx:end]
        if re.search(r"toast\(t\(['\"]update\.done", body):
            fails.append("finishUpgrade 仍在弹 toast（遮罩已显示同一句话）")

    # 底部图标尺寸：允许的写法是 .sidebar-foot .icon-btn svg 18px + 1.8 描边
    if css:
        if not re.search(r"\.sidebar-foot \.icon-btn svg\s*\{[^}]*width:\s*18px", css):
            fails.append("侧栏底部图标没有统一成 18px")
        if not re.search(r"\.sidebar-foot \.icon-btn svg\s*\{[^}]*stroke-width:\s*1\.8", css):
            fails.append("侧栏底部图标没有统一成 1.8 描边")
        # 那条把底部图标压成 16px 的规则必须不再作用于 .icon-btn
        m = re.search(r"\.sidebar-foot button svg\s*\{[^}]*\}", css)
        if m and "16px" in m.group(0):
            fails.append(".sidebar-foot button svg 仍在压 16px（会盖掉 .icon-btn 的 18px）")

    # 后端清洗转圈帧
    py = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "bin", "installer.py")
    back = io.open(py, encoding="utf-8").read() if os.path.isfile(py) else ""
    if "clean_install_log" not in back:
        fails.append("后端没有 clean_install_log（转圈帧会原样进界面）")
    elif "clean_install_log(text)" not in back:
        fails.append("_tail 没有调用 clean_install_log")

    for f in fails:
        print("FAIL", f)
    return not fails


def check_indeterminate_and_verline():
    """不确定态进度条 + 版本行图标对齐。

    两条都是「实测过才定下来的数值」，删掉会以同样的形态回来：

    1. 遮罩在检查 / 校验 / 安装阶段没有可测总量，原先显示一句静态的
       「准备中…」。它看不出「还在动」，而且与上方阶段文字
       （「安装包已就绪，正在重启服务…」）语义打架。改成一条来回流动的
       条，颜色取 var(--primary) —— 浅色主题近黑、深色主题近白，自动反色。
    2. 版本行的地球图标与版本文字：flex 的 align-items:center 对齐的是
       行盒，而数字墨迹偏上、默认行高又比字形高小半行，实测图标低
       2.0–2.3px。收窄行高 + 图标上移 1px 后降到 0.0–0.33px。
    """
    fails = []
    css_path = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web", "style.css")
    css = io.open(css_path, encoding="utf-8").read() if os.path.isfile(css_path) else ""
    if not css:
        fails.append("找不到 style.css，无法核对遮罩样式")
        return fails

    # 1) 不确定态：必须有 .upprog.indeterminate 与流动动画
    #    这里一律用「选择器 + 大括号 + 关键声明」精确匹配。
    #    裸子串会把 @media 里的 .upprog.indeterminate .upprog-flow 也算进来，
    #    删掉主规则后仍然判过 —— 变异测试抓过这个洞。
    if not re.search(r"\.upprog\.indeterminate\s*\{[^}]*\}", css):
        fails.append("缺少 .upprog.indeterminate 规则（不确定态退回静态文字）")
    if not re.search(r"\.upprog\.indeterminate\s+\.upprog-flow\s*\{[^}]*\}", css):
        fails.append("缺少 .upprog-flow 规则（那条流动的亮条）")
    if not re.search(r"@keyframes\s+upprogFlow\s*\{", css):
        fails.append("缺少 upprogFlow 动画（条不会动，等于还是静态）")
    # 动画必须真的被挂到流动条上
    m = re.search(r"\.upprog\.indeterminate\s+\.upprog-flow\s*\{([^}]*)\}", css)
    if m and "animation" not in m.group(1):
        fails.append(".upprog-flow 没有挂动画")
    # 颜色必须走主题变量，不能写死蓝色或黑色
    if m:
        body = m.group(1)
        if "var(--primary)" not in body:
            fails.append("不确定条没有用 var(--primary)（夜间模式不会反色）")
        for hard in ("#3b82f6", "#2563eb", "#000", "#111", "rgb(0, 0, 0)"):
            if hard in body:
                fails.append("不确定条写死了颜色 %s（夜间模式不会反色）" % hard)
    # 关掉动效时必须退回静止条，不能继续动
    # 同样要精确到「那条 reduced-motion 媒体查询里确实改了 .upprog-flow」
    rm = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{"
                   r"[\s\S]{0,400}?\.upprog-flow[\s\S]{0,200}?\}", css)
    if not rm:
        fails.append("缺少 prefers-reduced-motion 兜底（前庭敏感用户会不适）")
    elif "animation: none" not in rm.group(0):
        fails.append("reduced-motion 兜底里没有关掉动画")

    # 2) 版本行对齐
    if not re.search(r"\.ver-line\s*\{[^}]*line-height:\s*16px", css):
        fails.append("版本行没有 line-height: 16px（图标会比文字低 2px）")
    if not re.search(r"\.ver-line\s+svg\s*\{[^}]*translateY\(-1px\)", css):
        fails.append("版本行图标没有 translateY(-1px) 补偿")

    return fails


if __name__ == "__main__":
    sys.exit(main())
