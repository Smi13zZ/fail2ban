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

    extra = check_assets_removed()
    if extra:
        for item in extra:
            print("FAIL", item)
        return 1

    extra = check_install_icon()
    if extra:
        for item in extra:
            print("FAIL", item)
        return 1

    print("OK   升级遮罩阶段收敛规则齐备")
    return 0


def check_install_icon():
    """「一键更新」按钮必须带下载图标，且与「检查更新」的图标同档。

    截图里两个按钮并排时，左边检查更新有刷新图标、右边一键更新是空的，
    看着像没做完。两个入口都要有：设置页更新卡、顶部提示条。

    两条容易踩的坑，断言里都盯着：
      1. 横幅按钮的文字如果写成 `install.textContent = ...`，会把刚加的图标
         整个替换掉 —— 必须文字单独一个 span，只改那个 span。
      2. 图标尺寸要靠 .btn .ic 的 16px，不能各写各的，否则并排不一样粗。
    """
    fails = []
    web = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web")
    app = io.open(os.path.join(web, "app.js"), encoding="utf-8",
                  errors="replace").read()
    html = io.open(os.path.join(web, "index.html"), encoding="utf-8",
                   errors="replace").read()

    # 必须真的有定义，不能只在别处引用某个同名变量
    if not re.search(r"var\s+DOWNLOAD_ICON\s*=", app):
        fails.append("app.js 缺少 DOWNLOAD_ICON 定义")
    # 更新卡上的按钮：图标 span + 文字
    if "data-act=\"update-install\"" not in app:
        fails.append("找不到更新卡的「一键更新」按钮")
    else:
        i = app.find('data-act="update-install"')
        seg = app[i:i + 260]
        if "DOWNLOAD_ICON" not in seg:
            fails.append("更新卡的「一键更新」没有下载图标")
        if 'class="ic"' not in seg:
            fails.append("更新卡的图标缺 .ic 容器（尺寸会塌）")

    # 横幅：静态结构里要有图标占位与文字 span
    if "upBannerInstallIcon" not in html:
        fails.append("横幅「一键更新」缺图标占位")
    if "upBannerInstallLabel" not in html:
        fails.append("横幅「一键更新」缺文字 span（图标会被 textContent 抹掉）")

    # 横幅重绘：不能再直接写按钮的 textContent
    if re.search(r"\$\('#upBannerInstall'\)\.textContent", app) or \
            "install.textContent = t('update.install')" in app:
        fails.append("横幅重绘仍在写按钮的 textContent（图标会被抹掉）")
    if "upBannerInstallIcon" in html and \
            "installIcon.innerHTML = DOWNLOAD_ICON" not in app:
        fails.append("横幅图标没有被填充")

    return fails


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
    #    这一条踩过两次，两次都是「看着齐了、换个字就不齐」：
    #    a) 只对齐墨迹中线：中线确实重合，但 14px 的地球墨迹比文字高 3.6px，
    #       上下各超出近 2px，看着像图标沉下去；
    #    b) 换成固定补偿 translateY(-1.5px)：那个像素值是照一种等宽字标定的，
    #       换一种等宽字，数字墨迹从 8.17px 变 8.71px，偏移立刻失准（实测偏 1.12px）。
    #    现在要求：对齐基线，尺寸与偏移一律用 em，并且禁止固定像素尺寸。
    if not re.search(r"\.ver-line\s*\{[^}]*line-height:\s*16px", css):
        fails.append("版本行没有 line-height: 16px（行盒比字形高小半行，图标会偏）")
    if not re.search(r"\.ver-line\s*\{[^}]*align-items:\s*baseline", css):
        fails.append("版本行没有 align-items: baseline（固定像素补偿会随字体失准）")
    rules = re.findall(r"\.ver-line\s+svg\s*\{([^}]*)\}", css)
    if not rules:
        fails.append("找不到 .ver-line svg 规则")
    else:
        if not any("em" in b and "translateY(" in b for b in rules):
            joined = " | ".join(b.strip()[:70] for b in rules)
            fails.append("版本行图标尺寸/偏移没用 em（换一种字就会偏：%s）" % joined)
        # 固定像素是那两次出错的共同形态，抓死它
        fixed = [b for b in rules
                 if re.search(r"(?:^|[;{\s])(?:width|height|flex)\s*:\s*[\d.]+px", b)]
        if fixed:
            fails.append("版本行图标写回了固定像素尺寸（%s）" % fixed[0].strip()[:70])
    # 图标盒子的尺寸也必须用 em：只改 svg、不改盒子，盒子仍按固定 px 占位，
    # 图标会被挤在中间，居中对齐时又回到「看着偏」的老问题。
    ic_rules = re.findall(r"\.ver-line\s+\.ic\b[^{]*\{([^}]*)\}", css)
    if not any("em" in b for b in ic_rules):
        fails.append("版本行图标盒子的尺寸没用 em（只改 svg 不够）")

    # 3) 「检查更新」按钮的旋转图标
    #    检查是一段持续状态，图标要一直转；用 animation 而不是 transition。
    # 注意两个坑（都是变异测试抓出来的）：
    #   a) reduced-motion 里也有一条同选择器的 `{ animation: none; }`，
    #      用单次 search 会先命中它，主规则删掉也能过 —— 必须要求动画名是 checkSpin。
    #   b) 关键帧用 `in` 做子串判断，改名成 checkSpinXX 仍是前缀，必须正则 + 边界。
    spin_rules = re.findall(r"\.btn\s+\.ic\.spinning\s+svg\s*\{([^}]*)\}", css)
    if not any("checkSpin" in b and "animation" in b for b in spin_rules):
        fails.append("检查按钮缺少 .btn .ic.spinning 的 checkSpin 旋转动画")
    if not re.search(r"@keyframes\s+checkSpin\s*\{", css):
        fails.append("缺少 checkSpin 关键帧（图标不会转）")
    # 关掉动效时要退回静止，不能一直转
    if not re.search(r"prefers-reduced-motion[\s\S]{0,400}?spinning[\s\S]{0,200}?animation:\s*none",
                     css):
        fails.append("缺少 spinning 的 reduced-motion 兜底")


    # 4) 按钮图标容器必须有显式尺寸
    #    有的产品的图标是 <svg viewBox="0 0 24 24"> 不带 width/height 属性，
    #    尺寸全靠 CSS；没有这条规则会塌成 0，按钮上只剩文字。
    if not re.search(r"\.btn\s+\.ic\s*\{[^}]*width:\s*16px", css):
        fails.append("缺少 .btn .ic 的显式尺寸（图标宽度会塌成 0）")


    fails.extend(check_app_shell(css))

    return fails


def check_assets_removed():
    """更新页不再显示 Release 的安装包清单。

    这块渲染的是 Release 资产，走发布订阅源那条路径拿不到字节数，
    每次都印一行「大小未知」，没有信息量。已整体删除（函数、样式、词条），
    不是用样式藏起来 —— 藏起来的东西没人记得它还在。
    """
    fails = []
    web = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web")
    app = io.open(os.path.join(web, "app.js"), encoding="utf-8",
                  errors="replace").read()
    css_p = os.path.join(web, "style.css")
    css = (io.open(css_p, encoding="utf-8", errors="replace").read()
           if os.path.isfile(css_p) else "")
    i18n_p = os.path.join(web, "i18n.js")
    i18n = (io.open(i18n_p, encoding="utf-8", errors="replace").read()
            if os.path.isfile(i18n_p) else "")

    if "updateAssetsHtml" in app:
        fails.append("app.js 里还留着 updateAssetsHtml（安装包区块没删干净）")
    for cls in (".assetlist", ".assetrow"):
        if cls in css:
            fails.append("style.css 里还留着 %s（应为整体删除，不是藏起来）" % cls)
    for key in ("update.assets", "update.sizeUnknown"):
        if '"%s"' % key in i18n:
            fails.append("i18n.js 里还留着 %s 词条" % key)

    # 别把整页删空：发布说明与基本信息必须还在
    for key in ("update.notes", "update.repo", "update.checkedLabel"):
        if '"%s"' % key not in i18n:
            fails.append("i18n.js 缺少 %s（删除安装包区块时误删了别的内容）" % key)

    return fails


def check_app_shell(css):
    """应用壳式布局：文档高度必须固定为视口高度。

    宿主按文档高度给应用框定尺寸，而外壳的 `min-height: 100vh` 又让文档随内容
    长高 —— 两者互相喂，框越撑越高，钉在 100vh 底边的侧栏底部被推出屏幕，
    概览页只剩上方一小块内容、下面一大片空白。

    实测（403x1155 视口）修复前各页文档高度：统计 4313 / 网络诊断 1974 /
    概览 1603 —— 分别 3.7 / 1.7 / 1.4 屏；修复后全部等于 1155。

    四条规则缺一不可，任一条缺失都会让文档重新长高：
      1. html, body 不能滚动（否则文档本身就能比视口高）
      2. 外壳用 height 而不是 min-height —— min-height 正是那个放大器
      3. 主栏的 min-height:0 —— flex 子项默认 min-height:auto 会拒绝缩小
      4. 内容区承担滚动，同样要 min-height:0
    """
    fails = []

    # 1) html, body 锁滚动
    rules = re.findall(r"html,\s*body\s*\{([^}]*)\}", css)
    if not rules:
        fails.append("找不到 html, body 规则")
    elif not any(re.search(r"overflow:\s*hidden", b) for b in rules):
        fails.append("html, body 没有 overflow: hidden（文档自己就能比视口高）")

    # 2) 外壳固定高度
    shell = re.findall(r"\.app\.ready\s*\{([^}]*)\}", css)
    if not shell:
        fails.append("找不到 %s 规则" % '.app.ready')
    else:
        body = " ".join(shell)
        if not re.search(r"(?<![-\w])height:\s*100vh", body):
            fails.append("%s 没有固定 height: 100vh（壳会随内容长高）" % '.app.ready')
        if re.search(r"min-height:\s*100vh", body):
            fails.append("%s 用回了 min-height: 100vh —— 那正是把页面越撑越长的原因"
                         % '.app.ready')
        if not re.search(r"min-height:\s*0", body):
            fails.append("%s 缺少 min-height: 0（会被内容顶高）" % '.app.ready')

    # 3) 主栏 min-height:0
    main = re.findall(r"(?<![-\w.])\.content\s*\{([^}]*)\}", css)
    if not main:
        fails.append("找不到 %s 规则" % '.content')
    elif not any(re.search(r"min-height:\s*0", b) for b in main):
        fails.append("%s 缺少 min-height: 0（flex 子项默认 auto，内容仍会撑高壳）"
                     % '.content')

    # 4) 内容区承担滚动
    content = re.findall(r"(?<![-\w.])main\s*\{([^}]*)\}", css)
    if not content:
        fails.append("找不到 %s 规则" % 'main')
    else:
        if not any(re.search(r"overflow-y:\s*auto", b) for b in content):
            fails.append("%s 没有 overflow-y: auto（长内容无处可滚，只能撑高文档）"
                         % 'main')
        if not any(re.search(r"min-height:\s*0", b) for b in content):
            fails.append("%s 缺少 min-height: 0（长内容会顶高主栏）" % 'main')

    return fails


if __name__ == "__main__":
    sys.exit(main())
