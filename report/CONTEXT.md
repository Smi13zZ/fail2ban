# fn-fail2ban · 上下文恢复卡

> 本文件是跨会话恢复用的压缩上下文。最后更新 2026-11-07。
> 详细版本说明见同目录 `README-26.11.*.md`，改造规范见 `SPEC.md`，
> i18n 契约见 `I18N-CONTRACT.md`，官方包比对见 `官方包对比-1.7.0.md`。

## 一、项目定位与边界

| | 路径 | 用途 |
| --- | --- | --- |
| 本项目 | `fn-fail2ban-upgrade/` | 升级版源码、构建脚本、交付包、报告 |
| 提取基线 | `fn-fail2ban-extract/` | 从生产机提取的 1.7.0，`output/fn-fail2ban-1.7.0-all.fpk` 是逐字节比对基线 |
| 官方原版 | 用户提供 `fn-fail2ban_1.7.0_all (1).fpk`，sha256 `762b8d68…`，111,602 B | 用于核对初始安装方式 |

**红线（长期有效）**：
- **绝不自己升级/改动 `fnos-prod`（192.168.1.10:520, ranger）**。只读巡检可以；任何变更必须先取得用户明确同意。升级由用户自己在应用中心 Web UI 操作。
- `fnos-test`（192.168.1.13）可自由改、装、重启、造测试数据。
- 对外文案：CHANGELOG / Release / 代码注释 / 文档 / commit message 里**不写**「参照 xxx」「和 xxx 一致」「对齐 xxx」，只讲做了什么、为什么。
- 交付新包必须 bump 版本号（`年.月.序号`，本产品线当月独立计数），绝不原地覆盖。

## 二、用户需求演进（一句话一轮）

1. 从生产机提取 fail2ban 应用为可安装包。
2. 按最新 qbpanel 样式/字体升级 + 补全实用功能；**生产机不改配置原地升级；不要自己升级**。
3. 换图标、应用名改 `Fail2ban`、删掉应用登录页。
4. 修侧栏 logo 显示成方框（emoji 在 fnOS 无彩色字体）。
5. HD 图标；标题只叫 Fail2ban；删概览上方文字；主题做成图标按钮 + 加中英文切换按钮。
6. 用新的黑白盾牌图作图标；删侧栏顶部整块品牌行；修表格列错位并给每列固定最小宽度；把排序从表头移到标题旁的独立升/降按钮。
7. 拿官方原版包对比初始安装方式有什么不同。

## 三、版本与交付

| 版本 | 大小 | 主要内容 |
| --- | --- | --- |
| 26.11.0 | 141,598 | 界面整套重做 + 8 项功能补全 |
| 26.11.1 | 199,860 | 改名 Fail2ban / 换图标 / 删登录页 |
| 26.11.2 | 212,060 | 面板内图标换成自身资源（修豆腐块） |
| 26.11.3 | 266,028 | i18n 中英文全量切换、主题图标按钮、删概览标题行、侧栏只留应用名 |
| 26.11.4 | 291,407 | 新黑白盾牌九档图标、删侧栏品牌行、表格固定列宽、排序独立控件；顺带修 3 个缺陷 |
| **26.11.45** | **398,464** | 「已接管」徽章与「启用」同款样式；手机端补上接管列（jail 表最小宽度 856px） |
| **26.11.44** | **398,462** | 手机端存储页「空间占用」卡片头收成一行（刷新挪到标题右侧） |
| **26.11.43** | **398,328** | 运行日志区固定露出最新 10 条（fitLogView 按实测行高定高 + 按剩余可视高度封顶），更早在卡片内滚 |
| **26.11.42** | **398,729** | 通知渠道空态的支持列表居中（新增 .card-note-center，只作用于该处） |
| **26.11.41** | **398,514** | 通知页三处：通知渠道标题与「添加渠道」同行、投递记录标题与「刷新/清空」同行（筛选另起一行）、空态文案不再劈成两半；清掉 3 条孤儿 i18n 词条 |
| **26.11.40** | **398,072** | 白名单列表外框圆角统一为 10px（与输入框同档）、与输入行拉开 8px 间距 |
| **26.11.39** | **397,900** | 白名单列表移到输入行下方；每行增「编辑」按钮，行内编辑（回车保存 / Esc 取消 / 查重） |
| **26.11.38** | **396,942** | 全局白名单从彩色胶囊改成逐行列表（地址 + 行尾「删除」+ 分隔线），输入框与「添加」在下 |
| **26.11.37** | **396,531** | 手机端「手动处理」三颗按钮一行均分（中文）；修 fnk.instantHelp2 译文自带 `</div>` 被 esc 转义后印在界面上；check_web 新增 check_escaped_markup 闸门 |
| **26.11.36** | **396,008** | fn-knock 页两处：手机端「刷新列表」与标题同行；探测状态标签去掉胶囊灰底改文字色（新增 .ch-inline 卡片头型） |
| **26.11.35** | **395,279** | 修 fn-knock 黑名单表窄屏被压垮（三列各 7px、行高 520px）；补列宽与最小宽度，窄屏横向滚动 |
| **26.11.34** | **394,755** | 更新页卡片头改成标题+说明叠放在左、按钮靠右；说明 12px 更淡灰贴标题下 5px；版本号去掉等宽字体 |
| **26.11.33** | **394,062** | 更新页去掉页首那句概括性说明与对应的两条 i18n 词条 |
| **26.11.32** | **393,833** | 手机端抽屉侧栏去掉顶部安全区内边距，导航从屏幕顶端起算（真机 47px 悬空消除） |
| **26.11.31** | **393,541** | 手机端状态指示从 34×34 圆盘收回 24×26 中性药丸（圆点 6px）；抽屉侧栏按同产品线度量重排（236→216 / 44→38 / 46→40 / 13.5→14） |
| **26.11.30** | **393,081** | 手机端顶栏收成一行（菜单 / 页面名 / 状态 / 重载配置 / 刷新），状态在窄屏收成圆形状态灯；刷新保持文字按钮；顺带修切语言把状态徽章打回「检测中…」、徽章整块重写会抹掉圆点两处 |
| **26.11.29** | **391,821** | Jail 管理页顶部收成一行（搜索 / 作用域 / 条数 / 新建）；修条数文案自带 `</div>` 提前闭合工具栏的缺陷；check_web 新增闭合标签闸门 |
| **26.11.28** | **391,347** | 封禁管理页两条工具栏合成一条（八控件一行）；输入框按比例分配空间，桌面各宽度不折行不溢出；手机端拆三段 |
| **26.11.27** | **390,241** | 日志页工具栏锁成一行：放不下时先压窄关键字框（最小 120px、超出省略号），不再让末尾两个按钮掉到第二行；控件密度整体调紧约 45px |
| **26.11.26** | **388,748** | 日志页最新的行在最上面；关键字 / 行数 / 导出搬到顶部工具栏，与刷新、自动刷新排成一行；自动刷新换成方形勾选框；重绘时保住滚动位置 |
| **26.11.25** | **388,748** | fn-knock 标签条灰底按内容自适应、到「联动日志」为止；「测试连接 / 保存设置」移出灰底靠右，手机端换第二行均分 |
| **26.11.24** | **388,390** | 概览页与 Jail 管理页共用同一套表格渲染（列 / 徽章 / 对齐一致，仅右侧按钮按页面语义不同）；清掉三处因此失效的样式 |
| **26.11.23** | **388,626** | Jail 表去掉「过滤器」「日志文件」两列（变量引用，实测需 229/117px 却只有 74/90px）；列宽按中英文实测重排，四张表 1440 下零截断零横向滚动；窄屏收起接管与三个参数列并保留横滚 |
| **26.11.22** | **387,700** | 修列表的横向 / 纵向多余滚动：限高滚动区收掉横向溢出、盒内取消表格负边距；手机端按钮高度统一为普通 38 / 小按钮 32 / 输入框 38 |
| **26.11.21** | **387,090** | 存储页去掉重复的备份文件卡片；维护页备份清单重排成「创建时间 / 大小 / 恢复·删除」三列并按时间倒序；新增从备份恢复（先校验后落盘、覆盖前自动留快照、重载失败单独提示）；修 `list_backups` 同秒归档排序可能误删最新一份 |
| **26.11.20** | **383,329** | 界面收口：Jail 表接管 / 启用 / 运行状态拆三列、名称左对齐其余居中、行内按钮补描边；日志页改时间戳 + 消息纯列表并可筛级别；设置页去掉分区标题后缀与灰色说明行、通知 / 维护 / 存储三块重做；内容区铺满窗口；侧栏换图标、版本行只留面板版本 |
| **26.11.19** | **374,079** | 封禁管理去掉「刷新」后页面头部整行变空、白占 32px，把这一行也去掉，Jail 选择器紧贴顶栏 |
| **26.11.18** | **373,909** | 界面收口：设置页新增「说明」分区（20 条路径 + 复制），按归属分面板 / fail2ban 系统文件 / 敲门联动三卡；「关于」改为产品介绍 + 五项键值；侧栏「运行日志」→「日志」并移到设置之前、「fnknock 联动」→「fn-knock 联动」；fn-knock 联动页拆成六块标签条（封禁方式 / 同步 / 连接 / 运行状态 / 黑名单 / 联动日志）；更新页手机端排布与升级遮罩重做（两种进度形态、补读秒图标）；手机端原生 select 改为底部选择条；封禁管理去掉重复刷新按钮；提示条去掉成功/提醒的彩色描边；顺带修版本箭头 0×0 |
| **26.11.17** | **364,158** | 一键升级真正跑通：改用 install-local（uninstall 被拒、install-fpk 对已装应用无效），升级文件搬到卸载不清的位置（@apphome），修 .fpk 被当 gzip 打开、manifest changelog 折行两处校验自伤；更新页改成整页一张卡片；开发者信息与应用中心数据库同步 | 
| **26.11.10** | **368,640** | 更新页改成整页一张卡片：卡片头带「打开发布页面」，版本对比 + 状态条（右侧两个按钮）+ 限高滚动更新说明 + 安装包清单 + 仓库/检查时间/发布时间；无新版本时一键更新不渲染；修「检查于 {t}」占位符与本地版本高于线上时的显示 | 
| **26.11.9** | **366,940** | 应用信息与发布渠道：开发者/发布者改为 Smi1ezZ、主页指向仓库；应用中心数据库（PostgreSQL appcenter.app）版本与作者同步；GitHub 仓库上线并发布 v26.11.8/v26.11.9；更新页拿不到包大小时显示「大小未知」而非 0 B | 
| **26.11.7** | **366,587** | 侧栏左下角对齐：主题（显示器/月亮/太阳）、语言（文/A）、折叠（方框+竖线+箭头）三个图标统一 18px 笔触；版本行加地球图标并并列显示引擎版本 `v26.11.7 · Fail2ban 1.0.2`；去掉语言角标；收起态按钮保持 34×34 方形竖排 | 
| **26.11.6** | **365,449** | 设置页重做：侧栏十项收成七项，通知 / 存储 / 更新 / 全局设置合并为一个设置页，页内 8 个分区（面板 / 外观 / 防护策略 / 通知 / 备份 / 存储 / 更新 / 关于），深链 `#settings/<tab>` + 旧地址重定向；新增界面语言 / 侧栏布局 / 表格密度三项服务端偏好；卡片按钮统一 .card-head / .card-foot 排版；备份清单改定宽表 | 
| **26.11.5** | **357,966** | 通知渠道 7→11 + 元数据驱动 + 密钥打码 + 双语推送 + 投递记录；新增存储页与清理；新增版本检查与一键更新；8 道工程闸门 + CI 打包发布 |

**当前交付**：`output/fn-fail2ban-26.11.45-all.fpk`。**下一个版本号 26.11.46**。
`report/README-26.11.45.md` 是已接管徽章与手机端接管列那一版；`report/README-26.11.44.md` 是存储页刷新按钮同行那一版；`report/README-26.11.43.md` 是日志区固定 10 行那一版；`report/README-26.11.42.md` 是支持列表居中那一版；`report/README-26.11.41.md` 是通知页三处收口那一版；`report/README-26.11.40.md` 是白名单圆角与间距那一版；`report/README-26.11.39.md` 是白名单行内编辑那一版；`report/README-26.11.38.md` 是白名单改列表那一版；`report/README-26.11.37.md` 是三按钮一行 + 译文闭合标签那一版；`report/README-26.11.36.md` 是 fn-knock 页两处调整那一版；`report/README-26.11.35.md` 是黑名单表窄屏压垮修复那一版；`report/README-26.11.34.md` 是更新页卡片头与版本号字形那一版；`report/README-26.11.33.md` 是更新页去掉页首说明那一版；`report/README-26.11.32.md` 是抽屉从屏幕顶端起算那一版；`report/README-26.11.31.md` 是手机端状态指示与抽屉尺寸收口那一版；`report/README-26.11.30.md` 是手机端顶栏收成一行那一版；`report/README-26.11.29.md` 是 Jail 页顶部收成一行那一版；`report/README-26.11.28.md` 是封禁页工具栏合成一条那一版；`report/README-26.11.27.md` 是日志工具栏合成一行那一版；`report/README-26.11.26.md` 是日志页行序与工具栏那一版；`report/README-26.11.25.md` 是 fn-knock 标签条那一版；`report/README-26.11.24.md` 是两表统一那一版；`report/README-26.11.23.md` 是 Jail 表列宽那一版；`report/README-26.11.22.md` 是滚动条与按钮高度那一版；`report/README-26.11.21.md` 是备份恢复那一版；`report/README-26.11.20.md` 是界面收口那一版（26.11.19 只是它的前一步收尾）；`report/README-26.11.17.md` 是一键升级那一版；`report/README-26.11.10.md` 是更新页那一版；`report/README-26.11.9.md` 是上一版；`report/README-26.11.7.md` 是侧栏左下角那一版；`report/README-26.11.6.md` 是设置页重做的说明，`report/SPEC-26.11.6-设置页重构.md` 是对应的施工图；26.11.5 的说明与施工图在 `report/README-26.11.5.md`、`report/UPGRADE-PLAN-26.11.5.md`。

## 四、包结构与构建

```
.fpk (外层 tar)
├─ manifest            version / display_name / service_port=18752 / checksum…
├─ cmd/*               install_init, install_callback, config_*, upgrade_*, uninstall_*, main, env.sh
├─ config/             privilege, resource
├─ wizard/             install, uninstall
├─ ICON.PNG (64)  ICON_256.PNG (256)   ← 外层必须大写
└─ app.tgz (内层)
   ├─ bin/*.py       server.py, f2b.py, banlog.py, jailconf.py, notify.py, store.py, …
   ├─ ui/config      icon = images/icon_{0}.png, gatewayPrefix=/app/fn-fail2ban, gatewaySocket=app.sock
   ├─ ui/images/     icon_{16,24,48,64,72,96,128,256,512}.png  ← 九档，缺档即空白
   └─ web/           index.html, app.js, i18n.js, style.css, logo.png, favicon-32.png, apple-touch-icon.png
```

**落点**：外层 → `/var/apps/fn-fail2ban/`；`app.tgz` → `/vol1/@appcenter/fn-fail2ban/`。

**构建（只能在 Linux）**：`python tools/build_fpk.py --host fnos-test`。
脚本把 `src/pkg/fn-fail2ban` 打成 tar → scp 到测试机 → `chmod 755 cmd/*` + `server.py` → `fnpack build` → scp 回 `output/`。
Windows 重包会丢执行位，安装报 **10111**。

- `manifest` 的 `checksum` = `app.tgz` 的 md5，**fnpack 打包时自动重算，不要手工填**（5 个交付包逐一核对过，全部正确）。
- `tools/build_fpk.py` 有 `check_tree()` 出货闸门：发现 `_` 开头散落文件或 `.pyc/.log` 直接中止。
- `tools/make_icons.py` 以 `image/icon-master-1024.png`（用户给的 1024px 原图）为母版 LANCZOS 重采样九档；≤48px 额外走对比曲线，否则细边框降采样后是一团灰雾。
- 零构建静态前端（vanilla JS + CSS）+ Python 标准库后端。

## 五、配置保留为什么是硬保证

**靠文件同一性，不靠承诺**。与官方 1.7.0 忽略行尾后逐字节一致：

```
cmd/main, env.sh, install_init, config_init,
upgrade_init, upgrade_callback, uninstall_init, uninstall_callback,
wizard/uninstall, config/privilege, config/resource     ← 完全相同
```

改动过的只有 `install_callback`、`config_callback`、`wizard/install`（三者都只因为「移除登录页」，不再写入/重置面板凭证）、`manifest`、两个 ICON。
`jailconf.bootstrap()` 是 `setdefault` 语义，只补缺失键。

**实测**（测试机覆盖装 26.11.3 → 26.11.4，前后哈希一致）：
`50-fn-fail2ban.local`、`prefs.json`、`notifications.json`、`backup.json`。
`credentials.json` 保留不删，回退旧版仍可登录。

**官方包对比结论**：初始安装方式完全一样（同 appname / 端口 / 桌面向导 / 网关接入 / 文件落点）。
唯一实质差别是**向导少了「管理面板账号」一步**（用户名+密码），因为没有登录页。
官方 `app.tgz` 只有 bin/ui/web，我的多一层 `config/`（提取时从 target 带进来的冗余，内容与外层相同、无代码引用，无害）。
官方图标只有 64/256 两档且 `-`/`_` 两套重复；我补全九档。
官方 `cmd/*` 是 CRLF，我的是 LF；fnOS 安装会归一化，设备上实测部署后 `CRLF=0 LF=119`。

## 六、访问控制（删登录页的配套）

`client_allowed()` 在**任何请求解析之前**按 CIDR 白名单放行，默认回环 + RFC1918：
`127.0.0.0/8 ::1 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12`。
`F2B_ALLOW_CIDRS` 覆盖，取值 `* / all / any` 关闭校验。网关 unix-socket 请求地址为空，总放行。
41 个路由全部 `need_auth = False`。`h_session` 返回 `loginRequired: false`。

## 七、i18n 架构（26.11.3 建立，key 名已冻结）

- `I18N.t(key, params)` 带 `{name}` 占位；`SRC2KEY` 源文反查 `tSrc()`；`LINE_TPL` / `tLine()`；`localStorage['f2b-lang']`；`onLangChange()`。
- `index.html` 静态外壳用 `data-i18n`，`paintStaticI18n()` 回填；动态区 `repaintForLang()`。
- 后端中文运行时消息在 `en` 下由 `localizeErr()` / `localizeMsg()` / `localizeStatusMessage()` 压制替换；`zh` 显示后端原文（更具体）。
- 运行时长/备份位置/备份结果由后端返回结构化值（`uptimeParts` / `kind` / `resultKind`），前端按语言拼。
- 字典 1065 个 key（26.11.6），zh/en 1:1（差集 0）；`app.js` 代码里中文字面量 0 个。
- **改 key 名必须同步字典**。`tools/check_i18n.py` 会查：key 差集、同书内重复、代码引用、
  占位符一致，另外两条是 26.11.5 补的 —— 词条值必须是**非空字符串字面量**
  （写成 `undefined` 照样能过老闸门，英文界面会把 key 名直接印在页面上），
  以及 en 与 zh 逐字相同且含中文视为漏翻。

## 八、26.11.4 的三个缺陷修复（都值得记住）

1. **`renderBanList` 缺 `<div class="card"><h3>` 开标签**：26.11.3 做 i18n 改写时误删前缀，只剩孤立 `</h3>`，卡片样式整块塌掉。单 Jail 与汇总视图都已补回。
2. **`f2b.py` 把多个封禁 IP 合成一条**：`fail2ban-client status` 的 `Banned IP list` 是**空格分隔**，却统一按逗号切。改为 IP 用 `re.split(r"[\\s,]+")`、`fileList` 仍按逗号。实测三个 IP 恢复三条。
3. **英文残留扫描临时文件 `_missed.json` / `_remain.txt` 等被打进 `app.tgz`**：已删，并加 `check_tree()` 闸门。

## 八之二、26.11.5 的新增模块与三条硬规矩

新增 `app/bin/delivery.py`（投递记录）、`storage.py`（占用与清理）、`update.py`（版本检查）、
`installer.py`（一键升级）；`notify.py` 重写为 11 渠道 + 元数据驱动。

1. **密钥打码必须在服务端**。GET 打码成 `__SET__`；PUT 收到 `__SET__`/空串/缺字段按 id 沿用原值。
   整份设置回传时 `channels` 是数组、会被整体替换，前端收窄只是第二道防线。
2. **运行中的日志只能就地截断**（`open(path,"wb")`），不能 `unlink`：面板持有句柄，
   删了只清目录项、空间仍在旧 inode 上。清理路径一律 realpath 校验，只动本应用目录内。
3. **升级的向导字段里 `enable_ssh_jail` 必须传 false**：`install_callback` 收到 true 会无条件把
   sshd Jail 的 enabled 写成 true，等于替用户重开一个他关掉的防护。回滚只在「应用真的不见了」
   时做；版本号仍是旧值说明卸载/安装被拒、原安装完好，回滚等于亲手拆一个能用的实例。

## 九、踩坑与工具环境

- **app.js 两次被手写字符扫描器写坏**：CRLF 行尾（`[^'\\n]` 跨行匹配）+ 转义判断失误，吞掉 60 行。都从 26.11.2 包还原。安全配方：先 CRLF→LF，用单次正则 `/'([^'\\\\\\n]*(?:\\\\.[^'\\\\\\n]*)*)'|"([^"\\\\\\n]*(?:\\\\.[^"\\\\\\n]*)*)"/g`，写盘前 `new vm.Script(out)` 校验。
- `ToolCallError: cannot write/edit … file changed since it was read` → 编辑前重读。
- `appcenter-cli install-fpk` **不升级已装应用**（直接返回 "is installed"）；`uninstall` 拒绝并提示走 Web UI。**真正升级路径只有 Web UI**。appcenter 记录在 PostgreSQL `appcenter` 库，清残留行要一次清 `app`/`app_service`/`app_env`/`app_package`/… 并修 `install_type`。
- 错误码：`10111` 丢执行位、`10332` 陈旧 `app_package` 行指向不存在的 tpk、`10237` 生命周期脚本缺 shebang。
- `install-fpk -e` 的 env 文件必须给**全部**向导字段，缺一个报 "Something wrong with environment variables."（现只需 5 个：`auto_install, enable_ssh_jail, default_bantime, default_maxretry, default_ignoreip`）。
- **PowerShell 引号**：`||` 在 `ssh -c` 里被解析；`\\"SELECT …\\"` 会断。稳妥做法：把脚本 base64 解码后通过 heredoc 管道给 `sudo bash -s`；或写成 `.py`/`.sh` 文件 scp 过去再执行（本次主要用这个）。
- 本机无 python/node：`C:\\Users\\PVE\\.dsh\\dsh-runtimes\\dsh-next-primary-runtime\\dependencies\\python\\python.exe`、同目录 `node\\bin\\node.exe`。
- **附件落盘路径**：图片 `C:\\Users\\PVE\\.dsh\\attachments\\v1\\objects\\<sha前2位>\\<sha256>`；文件 `…\\v1\\files\\<sha前2位>\\<sha>\\<文件名>`。用 `read_image` / `write` 处理，别改附件库。
- `run_code` 里 `tools` 的解构必须包含要用的绑定（漏了会 `ReferenceError: pwsh is not defined`）。
- 被中断的工具调用结果未知时，**先查外部状态再决定是否重试**，别盲目重放。

## 十、验证手段（都在 fnos-test）

- 无头截图 / DOM：`/usr/bin/chromium --headless=new --no-sandbox --disable-gpu --hide-scrollbars --window-size=1440,1000 --screenshot=… --virtual-time-budget=9000`，`--dump-dom` 读注入探针写进页面的 `<pre id="probe-out">`。
- **注入探针页**：把 `index.html` 复制一份、在 `</body>` 前插脚本（设置 `localStorage['f2b-lang']`、点按钮、量 `getBoundingClientRect`），跑完**务必删掉** `web/_*.html`。
- `--dump-dom` **忽略 `--window-size`**（三个尺寸都报 `innerWidth=780`），量手机布局要看 `--screenshot`。
- 表格对齐 / 排序验证：读每列 `getBoundingClientRect().left`，表头与单元格逐列比对；脚本真实 `.click()` 方向按钮、`dispatchEvent(new Event('change'))` 换字段，再读回行顺序。
- 造封禁数据：`POST /api/jails/sshd/actions` + `{"action":"ban","ip":"…"}`；清空用 `{"action":"unban-all"}`。**注意不是 `/api/ban`**（该路由不存在）。
- 重启服务：`appcenter-cli stop fn-fail2ban` → `start fn-fail2ban`（`start` 会转圈一会儿，属正常）；`systemctl restart fn-fail2ban` 不存在这个 unit。
- 打包前闸门：`node --check app.js/i18n.js`、CSS `{}` 配平、`py_compile`、`t()` key 与字典差集、`data-i18n` key 存在性。
- 交付前解包核对：九档图标、无 `.pyc/.log`/`credentials.json`、`cmd/*` 与基线同一性、`checksum` 与 `app.tgz` md5。

## 十一、已知技术债（未处理，非缺陷）

- `app.tgz` 内多一层 `config/`（冗余，无害）。若要与官方包结构完全一致，可从 `src/pkg/fn-fail2ban/app/` 移除 —— 但需重新实测安装，别顺手改。
- 早期 `report/README-26.11.0.md`、`README-26.11.3.md` 里有「换成 qbpanel 的设计语言」「与 qbpanel 同款」等表述，与后来强调的「对外文案不写出处」规则冲突。若这些文档要对外发布，需先清理。
- 26.11.3 的 `i18n.js` 中 `ban.sortByInline` / `ban.asc` / `ban.desc` 等 key 在排序控件改版后已无引用，字典里留着无害（`<span class="sort-ind">` 之类的旧 CSS 已删）。

## 十二、如需继续动手

1. 先确认 `output/` 最新版本号 → **bump 到 26.11.6**（manifest 的 `version` 与 `changelog`、
   `server.py` 的 `APP_VERSION`、`CHANGELOG.md` 四处同步）。
2. 改完 `python tools/preflight.py` 先跑齐 8 道闸门，再 `python tools/build_fpk.py --host fnos-test`
   出包，然后 `python tools/inspect_fpk.py --strict output/<包>` 自检。
3. 实装：测试机上 `appcenter-cli uninstall` 被 fnOS 拒绝（要 Web UI），所以本地验证用
   `report` 里记的「解包 + 逐文件替换 + 重启」法（`sudo bash` 一段脚本，swap `/vol1/@appcenter/fn-fail2ban`
   与 `/var/apps/fn-fail2ban/manifest`）。装完核对 `/api/health` 的 version、
   四个配置文件的 md5、以及 `web/` 下文件 sha256 与源码一致。
4. 交付时给用户 `output/` 的 fpk + `report/README-<版本>.md` + `image/v26115/` 截图，
   **明确说生产机未动、升级由他自己操作**。


## 八之三、26.11.6 设置页的结构约束

这一版之后，改「设置」相关界面要守住下面几条，否则会立刻退化：

1. **侧栏只允许 7 项**：概览 / Jail 管理 / 封禁管理 / 过滤器 / fnknock 联动 / 设置 / 运行日志。
   26.11.5 之后侧栏涨到十项，其中四项都是配置页，这一版才收回来。
   以后新功能要么进设置页当分区，要么先想清楚为什么值得多一个一级入口。
2. **分区注册表是 `SET_TABS`**（app.js 顶部数组）。加分区要同时改四处：
   `SET_TABS`、`settingsTabHtml` 的 switch、`loadSettingsTab` 的取数分支、i18n 的 `set.tab.<id>`。
   漏掉 `loadSettingsTab` 的表现是「分区永远停在加载中」。
3. **异步回调用 `inSettingsTab(tab)` 判定**，不要写 `state.view === 'notifications'`：
   那三个视图已经不存在，这类判断永远为假，数据回来了界面不动。
4. **切分区用 `history.replaceState`**，不要写 `location.hash`：写 hash 会每个分区留一条
   历史记录，后退键退不出设置页。
5. **旧地址必须重定向**：`#notifications` / `#storage` / `#update` 走 `LEGACY_VIEW_HASH`，
   在 `parseHash` 里一次性改写。老书签不能白屏。
6. **卡片排版只有两种合法结构**：`.card-head`（标题 + 间隙 + 行内动作）与
   `.card-foot`（上边线 + 右对齐动作，主按钮最右）。设置页范围内出现
   `style="margin…"` 会被 `tools/check_web.py` 判 FAIL。
7. **外观偏好走服务端**：`theme` / `lang` / `sidebar` / `density` 存 `prefs.json`，
   `store.PREFS_CHOICES` 收敛非法值。折叠按钮只写 localStorage、不回写 `sidebar` 偏好
   —— 偏好是「每次打开的初值」，按钮是「这一次」。

### 这一版踩过的三个坑

- **表头宽度只能写在表头上**：`table-layout: fixed` 只看第一行声明，写进 `<td>` 完全无效。
  改备份清单定宽时先动的是 `<td>`，白改一轮。
- **太长前缀会让省略号掩盖全部差异**：`fn-fail2ban-auto-20261108-191750.tar.gz` 塞进
  200px 列里，四条记录截出来一模一样、认不出哪条最新。结构问题不该靠改文案解决，
  这版是把清单从窄栏挪到整卡宽度。
- **`direction: rtl` 做左侧省略必须配 `unicode-bidi: plaintext`**，否则整串字符顺序反转。
  能改结构就别用这个技巧。


## 八之四、侧栏左下角的形态契约（26.11.7 定型）

这一处四个面板是同一套，改之前先量、别靠肉眼补：

| 部件 | 规格 |
| --- | --- |
| `.side-foot / .sidebar-foot` | `margin-top:auto` 贴底、上边框 1px `--border-soft`、`padding:10px 10px 12px`、列向、gap 8px |
| `.foot-row` | 横排居中、gap 6px |
| 图标按钮 | 34×34、边框 1px `--border`、圆角 `--radius`、底色 `--card`、图标 18px |
| 主题图标 | 跟随系统=显示器、深色=月亮、浅色=太阳 |
| 语言图标 | 文/A 字符，**不要角标**（收起时角标会挤成一团） |
| 版本行 | 地球图标 + `v<面板版本> · <引擎> <引擎版本>`，11px 等宽、居中、超长省略 |
| 折叠按钮 | 方框 + 左侧竖线 + 方向箭头，幽灵态、占满整行、高 32px |
| 收起态（62px 轨道） | 两个图标按钮**竖排**、保持 34×34 方形（拉成全宽就不是方形了）、版本行隐藏 |

### 三个坑

- **版本行宽度是算出来的，不是调出来的**：`v26.11.7 · Fail2ban 1.0.2` 25 个等宽字符，
  216px 侧栏可用约 170px；12px 会被截成 `Fail2ban 1....`，11px 才 `scrollWidth == clientWidth`。
  以后面板版本或引擎版本变长（比如引擎出到 1.10.2），这一行会重新溢出——量 `scrollWidth`
  而不是看截图。
- **`paintStaticI18n` 曾被定义两次**，后一个覆盖前一个，前一个函数体里那几行「侧栏按钮 title」
  是死代码。清理时一并收敛：图标类由 `paintSideIcons()` 一次画齐（不随语言变），
  文案与 title 归 `paintLangTag()`。
- **收起态的 `.foot-row > button { width:100% }` 会把方形按钮拉扁**。三个面板都是靠
  `.icon-btn` 的 `flex: 0 0 34px` 顶住；本面板早先的 `.sidebar-foot button:not(.icon-btn)`
  选择器比重更高，所以收起态那条规则必须删掉，不能留着「反正被覆盖」。


## 九、应用中心与应用信息的三个坑（26.11.9 定型）

### 应用中心显示的版本与作者不在 manifest 里，在 PostgreSQL

`appcenter-cli list` 输出的版本、应用中心卡片上的开发者与主页，读的是
**PostgreSQL 的 `appcenter` 库 `app` 表**，不是 `/var/apps/<app>/manifest`。二者会不一致：

- 正常路径（应用中心 Web UI 上传 fpk）：由 `trim` 守护进程写入两者，天然一致；
- 文件级替换（本机唯一能做的方式，因为 `appcenter-cli uninstall` 拒绝卸载第三方应用）：
  只换了 manifest 与 target 目录，**数据库纹丝不动**，于是界面上一直显示旧版本。

排查命令：

    sudo -u postgres psql -d appcenter -A -F'|' -t \
      -c "select app_name, name, version, maintainer, distributor from app where app_name='fn-fail2ban';"

同步时**四个字段都要改**，且 `i18n_matadata` 要整份重写：

    update app set version='...', maintainer='Smi1ezZ', distributor='Smi1ezZ',
                    i18n_matadata='...' where app_name='fn-fail2ban';

### `i18n_matadata` 的键集合必须与其它应用一致

它是 `{"zh-CN": {...}, "en-US": {...}}`，键固定 8 个：
`Maintainer / Distributor / MaintainerUrl / DistributorUrl / Desc / ChangeLog / DisplayName / BackupGuide`。

- **缺 `DisplayName` → 应用中心列表「名称」列变空白**（踩过）；
- **多塞一个 `Version` → 那一列反而空**（也踩过，因为列表读的就是这个键）。

参考现成应用的值：

    sudo -u postgres psql -d appcenter -t -c \
      "select i18n_matadata from app where app_name='fn-qbpanel';"

### 别用 PowerShell here-string 写 SQL / 提交信息

会被写成带 BOM 的 UTF-16，`git log` 出来的提交信息首字符是 ``（踩过一次）。
用 `write` 工具写纯文本文件，或 `Out-File -Encoding utf8` 后确认首字节是 `E4/B8` 之类而不是 `FF FE`。

### GitHub 发布这条线

- 仓库 **Smi13zZ/fail2ban**，默认分支 main，public。
- token 从 Windows 凭据管理器读（`CredRead("git:https://github.com")`），只在内存里用。
- `git push` 把进度写 stderr，PowerShell 会当 `NativeCommandError` 报红，**以输出里的
  `main -> main` / `[new tag]` 为准**，别看 `$LASTEXITCODE`。
- CI 只在「标签名 == v + manifest version」时发 Release；临时标签只出 artifact。
- **发布后必查三件**：下载 Release 资产跑 `inspect_fpk --strict`；用面板真实请求查
  `/api/update?force=1` 确认能读到 release；模拟旧版本号确认 `has_update=true`。
- CI 在 Linux 重打包，md5 与本地包**必然不同**（时间戳与压缩参数），不要拿 md5 对比，
  要比包内版本号与 strict 检查结果。
- GitHub API 匿名额度 60 次/小时，很容易打满；打满后更新检查退回 atom 源，
  **那条路径没有资产字节数**，前端必须把 `size=0` 当「未知」而不是「0 B」显示。


## 十、一键升级：两条路只有一条通（26.11.17 实测定型）

### 走不通的两种写法

| 命令 | 实际结果 |
| --- | --- |
| `appcenter-cli uninstall <app>` | 第三方应用一律被拒：`please uninstall it from Web UI`。**不要再试**，与 install_type / 卷号 / manifest 都无关 |
| `appcenter-cli install-fpk <fpk>` 对着已装应用 | 只回一句 `Application [x] is installed.`，**文件一个都不动**（比过前后 mtime 与数据库版本） |
| `uninstall + install-fpk` 组合 | 永远停在第一步 |

### 走得通的写法

```sh
appcenter-cli install-local -d <解包目录> -e <env文件> -v 1 -a yes
```

它自己会完成 `stop → uninstall → install`，实测能真正替换应用。三个要点：

1. **吃目录不吃 .fpk**：先把包 `tar -xf` 解开，目录里要有 `manifest` 与 `app.tgz`
   （fnpack 打的包本来就带 `app.tgz`，install-local 会回
   `app.tgz exist, no need compress it`）。
2. **env 文件必须含全部向导字段**：`auto_install / enable_ssh_jail / default_bantime /
   default_maxretry / default_ignoreip`。少一个就报
   `Something wrong with environment variables.`，且不说缺哪个。
3. **升级用的文件不能放 @appdata**：install-local 的第一步就是卸载，会把
   `/vol1/@appdata/<app>` 整个清空。安装包、env、解包目录放那里 → 卸载完成时它们
   已经没了 → 安装失败且无回滚，**应用被卸掉装不回来**（真踩过）。
   正确位置是 `/vol1/@apphome/<app>/update/`：同级目录，卸载时保留。
   日志留在 `@appdata/<app>/update/install.log`，用户能在存储页看到并清理。

### 两个「校验把自己判死」的坑

- **`.fpk` 外层是未压缩的 tar**，只有内层 `app.tgz` 才 gzip。自检写
  `tarfile.open(path, "r:gz")` 会把正常包判成「不是 gzip 文件」。用 `"r:*"`。
- **manifest 的 changelog 不能涨太大**。堆到 13 KB 时 fnpack 打包会折行，
  manifest 被切成多行，安装器判它不合法 —— 表现是**连卸载都被拒**，
  而没有任何一条能看懂的报错。changelog 只保留最近两版摘要，
  `tools/inspect_fpk.py` 里有行结构校验兜底。

### 验证手法（可复用）

设备上起一个本地 HTTP 假源返回 `releases/latest` 与 `.fpk` 资产，把面板用
`F2B_UPDATE_API_BASE` / `F2B_UPDATE_WEB_BASE` 指过去，然后走面板自己的
`POST /api/update/install`。这样验的是真实链路，不是手动敲命令。

**造测试包时注意**：`app.tgz` 的内层根必须就是应用目录（`bin/` `web/` 直接在根下），
用 `( cd app && tar -czf ../app.tgz . )`。用 `tar -czf app.tgz -C app .` 会带上 `./` 前缀，
自检能过但安装会失败。

### 已知未覆盖

install-local 装完后，应用中心拉起服务偶尔报 `11000`（启动握手超时），此时新版本
其实已装好。脚本里有「直接用 `/var/apps/<app>/cmd/main start` 兜底」的分支，
但**这条分支还没被真正走到过**（验证时都是手工启动的）。下次真机升级时要盯一眼。

## 十一、26.11.18 的界面契约（新增分区 / 分区标签 / 三种弹层）

### 设置页分区（9 个，顺序即标签条顺序）

`panel / appearance / policy / notify / backup / storage / update / guide / about`

`guide`（说明）在 `update` 与 `about` 之间：它是查阅性质的位置清单，
不夹在会改配置的分区中间。新增分区时必须同时改三处，漏一处就是「有标签没内容」：
`SET_TABS`、`loadSettingsTab()` 的数据拉取、`settingsTabHtml()` 的分支。

### 页面内的分区标签条（两处共用一套样式）

设置页与 fn-knock 联动页都用 `.workbar` + `data-*-tab` 委托：

- 设置页：`<button data-set-tab="id">` → `setSettingsTab(id)`；
- fn-knock：`<button data-fnk-tab="id">` → `setFnkTab(id)`，分区是
  `mode / sync / conn / status / list / log`。

两者都**只重绘本页**，不重进视图。fn-knock 的「黑名单」分区数据按需拉取：
不在那一块时不发 `/api/fnknock/blacklist`。

**动态 i18n key 的前缀必须放变量里**：`t('fnk.tab.' + id)` 会被 i18n 闸门
当成「引用了不存在的 key fnk.tab.」而 FAIL；写成 `var FNK_TAB_KEY = 'fnk.tab.'`
再 `t(FNK_TAB_KEY + id)` 就与 `set.tab.` 同一个写法、闸门通过。

### 手机端选择弹层

原生 `<select>` 在手机上弹的是系统列表，与面板不是一套。做法是在
`document` 上以捕获阶段拦 `mousedown` / `touchstart`，只对「单选、未禁用」的
`select` 生效，打开 `#pickerMask`。选中后 `select.value = v` 再
`dispatchEvent(new Event('change', {bubbles:true}))` —— 既有 `onChange` 全部照旧，
**不需要为每个下拉各写一份手机端逻辑**（这是这套做法的全部理由）。

### 三条「看起来有、其实没有」的坑

1. **只有 `viewBox` 的 SVG 在 flex 里量出 0×0**。版本对比中间那个箭头就是这样，
   从上线起就没显示过。凡是 JS 拼进去的 SVG，样式里必须有显式的宽高
   （`.btn svg` 只给了 `flex: 0 0 auto`，不够）。
2. **`<span class="upspin"></span>` 是空的**。样式按 `.upspin svg` 定尺寸与动画，
   没有 SVG 就什么都不画。现在 `openUpOverlay()` 里按 `!spin.querySelector('svg')`
   注入一次，不重复注入。
3. **CSS 文件带 BOM 会让 `check_css.py` 把 `:root` 当成未知选择器**，
   于是 20 个变量全被判成「定义在别处」。写 CSS 一律 UTF-8 无 BOM + LF。

### 手机端截图：不要相信 --window-size

这台设备的无头 chromium，`--window-size` **小于约 500 时布局仍按 500 CSS px 算**，
`--screenshot` 只是把 500 宽的图裁到 390（`--dump-dom` 同样，实测都报 `innerWidth=500`）。
拿它当「手机布局」会得到「被裁掉的桌面布局」，而且看起来很像真的。

正确做法：走 CDP 的 `Emulation.setDeviceMetricsOverride`（真 390 CSS px，
可配 `deviceScaleFactor=2` 出 780px 宽图），再 `Page.captureScreenshot`。
`tools/shots.py` 就是这条路径；`--screenshot` 直出的图只当粗看。

验证口径可以用一条注入页快速确认：把 `innerWidth` / `matchMedia('(max-width:720px)')`
写进 `document.title`，`--dump-dom` 读回。

### 弹层三种形态（不要混用）

| 用途 | 类名 | 位置 |
| --- | --- | --- |
| 编辑表单 | `.drawer` + `.drawer-mask` | 桌面右侧滑出，手机全宽 |
| 手机选择 | `.picker-mask` + `.picker-sheet` | 底部升起，点遮罩或「取消」关闭 |
| 一键升级 | `.upoverlay` + `.upoverlay-box` | 居中，不可关闭，直到面板重新应答 |

Esc 键的优先级：**选择条 > 抽屉**。选择条盖在页面之上时先关它，
别把下面抽屉一起关掉（`pickerCtx` 非空即优先）。
