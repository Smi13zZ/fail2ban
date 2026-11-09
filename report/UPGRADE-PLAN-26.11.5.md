# fn-fail2ban 26.11.5 升级开发计划（架构 / 样式 / 功能 / 结构 / 设计）

> 本文件是 26.11.5 这一版的施工图，参与开发的每个人按自己那一节执行。
> 基线：已交付的 26.11.4（`output/fn-fail2ban-26.11.4-all.fpk`），源码在 `src/pkg/fn-fail2ban/`。
> 目标版本号：**26.11.5**（本产品线 2026 年 11 月第 6 个版本，序号只能 +1，不得跳号）。

## 零、不可违反的约束（所有人先读）

| 约束 | 说明 |
| --- | --- |
| **不许改 `cmd/*`** | 这十一个脚本与官方 1.7.0 逐字节一致，是「生产机原地升级不改配置」的保证。端口/启动方式一律不动 |
| **不许改 `wizard/install`、`cmd/install_callback`、`cmd/config_callback` 的凭证相关逻辑** | 删登录页的既有差异要保留，不要回退 |
| **后端只用 Python 标准库** | 无第三方依赖 |
| **前端零构建** | 原生 JS + CSS，无 npm、无 CDN |
| **不许出现「参照 qbpanel / 对齐 xxx」这类文案** | CHANGELOG、注释、文档、commit message 一律只讲做了什么、为什么 |
| **不碰生产机** | `fnos-prod` 只读；一切实机验证在 `fnos-test` |
| **密钥不落日志** | 投递记录、日志、报错里都不许出现渠道密钥/Tencent Secret/Token |

## 一、这一版要补齐的差距（对照三个既有产品的当前形态）

1. 通知渠道只有 7 种，且字段标签是后端硬编码中文、没有双语元数据、没有密钥打码 → 补齐渠道、字段元数据双语、密钥掩码。
2. 没有「投递记录」→ 每次推送留痕（成功失败都记，失败写原因），可筛选 / 刷新 / 清空。
3. 推送正文只有中文 → 改成中英双语（标题 `中文 / English`，正文中文段 + 空行 + 英文段）。
4. 没有「存储」页 → 各目录占用、备份清单、清理日志（就地截断）、清旧备份、全部清理；启动时自动清理。
5. 没有版本自检与一键更新 → 新增更新分区（三层检查、数值版本比较、缓存节流）+ 一键升级（下载→校验→卸载→安装→回滚）。
6. 没有 CI / 结构闸门 / 文档 → 补 GitHub Actions 打包发布、结构检查、打包前闸门、解包自检、CHANGELOG、README、开发文档。

**明确不做**（写了理由，不是遗漏）：
- **不搬目录**（`app/bin/*.py` 保持平铺）：`cmd/main` 直接引用 `bin/server.py`，改路径必须动 `cmd/*`，与红线冲突。
- **不改桌面入口为 /cgi/ThirdParty/...**：`desktop_applaunchname` 与网关 socket 是已装实例的桌面快捷方式和登录校验入口，改变会让老实例桌面图标失效。
- **不做「自动改系统防火墙策略」**：超出应用职责。

## 二、HTTP 契约（后端与前端按此并行开发，谁也别改字段名）

```
GET  /api/notifications
     -> { settings, channelTypes, eventTypes, events, deliveries, fail2ban }
     channelTypes[i] = {
        value, label, hint, secretFields: [key...],
        fields: [{ key, label, hint, type: text|password|select, required,
                   placeholder, default, options: [{value,label}] }],
        labels_i18n: {key: {zh,en}}, hints_i18n: {key: {zh,en}}
     }
     settings.channels[i] = { id, name, type, enabled, ...fields, secrets 用 "__SET__" 掩码 }
     deliveries = 最近 60 条投递记录（结构见 delivery.py）

PUT  /api/notifications                  -> { ok, settings }
POST /api/notifications/test             -> { ok, detail, delivery }
GET  /api/notifications/events?since=    -> { events }            （保留，浏览器通知轮询用）
POST /api/notifications/events/clear     -> { ok }
GET  /api/notifications/deliveries?status=all|ok|fail&limit=   -> { items, total, kept }
POST /api/notifications/deliveries/clear -> { ok }

GET  /api/storage
     -> { items:[{key,label,bytes,files,path,kind,nested}], total, var_dir, backups:[], keep, current_version }
POST /api/storage/cleanup  { target: logs|backups|all, keep?: int }
     -> { ok, freed, removed, items, backups, detail }
POST /api/backup/delete  { name }        （已有，复用）

GET  /api/update?force=0|1   -> { ok, has_update, current, latest, repo, notes, assets:[], reason, error }
POST /api/update/install     -> { ok, phase }
GET  /api/update/progress    -> { phase, version, detail, done, total, at, log }
```

## 三、分工与写作用域

### A. 通知后端 —— `src/pkg/fn-fail2ban/app/bin/notify.py`（重写）+ 新建 `app/bin/delivery.py`

1. **渠道扩到 11 种**，保留现有 7 种（dingtalk / wecom / wecom_app / feishu / bark / slack / generic）的键名与语义不变（老配置要能直接发），新增 4 种：
   - `serverchan`  Server 酱（SendKey）
   - `pushplus`    PushPlus（token）
   - `wxpusher`    WxPusher（appToken + uid/topicId）
   - `telegram`    Telegram（bot token + chat id + 可选 API 地址）
2. **元数据驱动**：每个渠道带 `fields[].label/hint`（zh 源文）+ `labels_i18n`/`hints_i18n`（zh/en 两份），`secretFields` 标出哪些字段是密钥。前端据此渲染，**前端零硬编码**。
3. **密钥掩码**：`GET` 回传时密钥字段一律换成 `"__SET__"`；`PUT` 时值为 `"__SET__"` 或空串表示「保持原值」，按 `id` 与原配置合并。**这道防线必须写在服务端**（整份配置回传时 list 会被整体替换，前端收窄只是第二道）。
4. **双语推送**：`dispatch` 出去的消息标题 `中文 / English`，正文「中文段 + 空行 + 英文段」。所有事件（ban / unban / serviceDown / serviceRecover / test）都走同一套。测试通知也走双语。
5. **投递记录** `delivery.py`：字段固定 `id/at/event/channel/channel_id/type/ok/detail/title/body`，**绝不含密钥**；环形缓冲 **300** 条；`tmp + os.replace` 原子写；加锁；文件损坏当空数组，不能让通知页整页报错。
6. 所有真正发出请求的路径（`dispatch` 里每个渠道、`/test`）都要写一条投递记录；失败要带 HTTP 状态码或上游错误码。

### B. 存储与更新后端 —— 新建 `app/bin/storage.py`、`app/bin/update.py`、`app/bin/installer.py`

1. `storage.py`：`report(var_dir, current_version)` 列出占用（备份目录、server.log、server.log.1、deliveries.json、events.json、notifications.json、backup.json、prefs.json …），条目带 `nested` 标记避免重复计总量；`cleanup(var_dir, target, keep)` 支持 logs / backups / all；日志**运行中只能就地截断**（`open(path,"wb")`），不能 unlink；`realpath` 限制只删本应用目录内。
2. `update.py`：三层检查（GitHub API → releases.atom → expanded_assets HTML），**版本按数值比较**（26.11.10 > 26.11.9），成功缓存 900s / 失败冷却 60s，时间戳只在查成功时写；`check(current, force)` 永不抛异常；`poll_loop` 启动 60s 后首查、之后每小时一次，循环体整体 try/except。
3. `installer.py`：下载到 `.part` → 校验（tar 成员 manifest + app.tgz、appname、version）→ 写安装脚本到 `@appdata`（**不能在 @appcenter，卸载时会一起没**）→ `start_new_session` 分离进程 → stop / uninstall / install / start → 失败用 `update/rollback.fpk` 回滚。默认仓库 `Smi13zZ/fail2ban`，可在 settings 覆盖；仓库不存在 / 没有 Release 属于正常答案，不许抛错。

### C. 工程与发布 —— `.github/workflows/build-fpk.yml`、`tools/*`、`CHANGELOG.md`、`docs/`、`README.md`/`README_EN.md`

1. CI：tag 名 == `v` + manifest version 才发 Release，否则只出 artifact；打包在 ubuntu-latest 上跑 fnpack（或复用现有 SSH 构建脚本的做法，二者择一但要能真跑）。
2. `tools/preflight.py` 一条命令跑齐本地闸门：结构检查 → python 编译 → 前端语法（node --check）→ i18n key 差集 → CSS 花括号配平。
3. `tools/check_structure.py`：抓 py_compile 抓不到的问题（方法缩进跑偏、用了未定义的名字、self 属性只读不写）。
4. `tools/inspect_fpk.py --strict`：可执行位、九档图标、无 `.pyc/.log`、包内版本号、`cmd/*` 与基线同一性。
5. `CHANGELOG.md` 按版本倒序；`docs/DEVELOPMENT.md` 写构建/验证/踩坑；`README.md` 是产品介绍风格（不是更新日志），中英同步。

### D. 前端 —— `app/web/app.js` + `app/web/i18n.js`（整个文件归你，别人不碰）

1. 通知页：渠道表单由 `channelTypes` 元数据渲染（label/hint 按当前语言取 `labels_i18n`/`hints_i18n`），切语言当场生效；密钥输入框留空 + 「已保存，留空保留原值」提示，**不许把 `__SET__` 回填进输入框**。
2. 新增「投递记录」卡片：全部 / 成功 / 失败筛选、刷新、清空，每行显示时间、渠道、事件、结果，失败展开原因。
3. 新增「存储」侧栏页：占用表（含合计）、备份清单、清理日志 / 清理旧备份 / 全部清理三个按钮，清理前说明预计回收多少。
4. 新增「更新」侧栏页：当前版本 vs 最新版本、状态条（只有「发现新版本」着色，其余走中性色）、发布说明、检查更新 / 一键更新按钮、进度区（固定高度、进度只增不减、无总量阶段显示「准备中…」）、装完自动刷新。
5. i18n：新增 key 必须 zh/en 一一对应，差集 0；`app.js` 里中文字面量保持 0（除注释）。

### E. Lead 负责

`app/bin/server.py`（路由与处理器接线）、`manifest`（版本 + changelog）、`app/web/index.html`、`app/web/style.css`、`app/ui/config`、构建、测试机实装验证、截图、交付报告。

## 四、验收

1. `preflight.py` 全绿。
2. `fnos-test` 上覆盖安装 26.11.5，八→十个视图逐页可开，中英文各截一轮图（桌面 1440 + 手机 390）。
3. 配置保留：`50-fn-fail2ban.local`、`prefs.json`、`notifications.json`、`backup.json` 升级前后哈希一致。
4. 通知：本地 sink 服务收 POST，验证 11 种渠道的真实请求构造（含钉钉加签、企业微信 errcode、Telegram ok 判定），并确认投递记录里没有密钥。
5. 存储清理：造一堆旧备份 + 大日志，逐个按钮点一遍，确认回收量与磁盘实际一致，当前日志文件 inode 不变（截断而非删除）。
6. 更新：`/api/update` 对真实（或本地假）源返回结构正确；`has_update=false` 时不报错。
