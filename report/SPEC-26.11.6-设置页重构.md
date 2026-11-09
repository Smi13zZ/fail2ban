# 26.11.6 施工图 · 设置页重构

本文件是唯一契约。三方（Lead / ui-dev / eng-dev）按它执行，出现歧义以本文件为准。

---

## 一、目标

把 Fail2ban 侧栏里散落的「通知设置 / 存储 / 更新 / 全局设置」四页收进**一个设置页**，
页内用顶部标签条分区；「布局、主题、备份」各自独立成块；按钮排版统一。

## 二、最终导航

侧栏只剩 7 项（顺序即 DOM 顺序）：

| # | data-view | 中文 | 英文 |
|---|---|---|---|
| 1 | dashboard | 概览 | Overview |
| 2 | jails | Jail 管理 | Jails |
| 3 | bans | 封禁管理 | Bans |
| 4 | filters | 过滤器 | Filters |
| 5 | fnknock | fnknock 联动 | fnknock |
| 6 | settings | 设置 | Settings |
| 7 | logs | 运行日志 | Logs |

**删掉** `data-view="notifications"` / `data-view="storage"` / `data-view="update"` 三个侧栏按钮。
「更新红点」从 `#navUpdateDot` 挂在**设置**按钮上（id 保持不变）。

## 三、设置页标签（8 个）

```html
<div class="workbar" id="setWorkbar" role="tablist">
  <button type="button" data-set-tab="panel" role="tab" aria-selected="true" class="on">面板</button>
  <button type="button" data-set-tab="appearance" role="tab" aria-selected="false">外观</button>
  <button type="button" data-set-tab="policy" role="tab" aria-selected="false">防护策略</button>
  <button type="button" data-set-tab="notify" role="tab" aria-selected="false">
    通知<i class="workbar-dot" id="setNotifyDot" hidden></i>
  </button>
  <button type="button" data-set-tab="backup" role="tab" aria-selected="false">备份</button>
  <button type="button" data-set-tab="storage" role="tab" aria-selected="false">存储</button>
  <button type="button" data-set-tab="update" role="tab" aria-selected="false">
    更新<i class="workbar-dot" id="setUpdateDot" hidden></i>
  </button>
  <button type="button" data-set-tab="about" role="tab" aria-selected="false">关于</button>
</div>
```

标签文案由 JS 渲染（`t('set.tab.' + id)`），**不要**写成 data-i18n 静态串。

### 每个标签里放什么

| tab | 内容 | 数据源 |
|---|---|---|
| panel | 日志页默认行数 / 保存后自动重载 / 危险操作二次确认 | `state.prefs` |
| appearance | 主题 / 界面语言 / 侧栏布局 / 表格密度 | `state.prefs` + localStorage |
| policy | `globalFields` 全部字段 + ignoreip 芯片控件 | `state.settings` |
| notify | 原通知页整页（主开关 + 事件 + 渠道列表 + 投递记录） | `state.notifications` |
| backup | 原 `renderBackupCard()` | `state.backup` |
| storage | 原存储页整页 | `state.storage` |
| update | 原更新页整页 | `state.update` |
| about | 版本、托管文件、读取顺序、备份目录、数据目录 | `state.meta` + `state.settings` |

**每个标签内部不再有页面级 `.view-head`**（标题已经在顶栏和标签条里了）。
卡片标题用 `<h3>`，动作按钮统一放卡片底部的 `.card-foot`。

## 四、路由

- 地址栏形如 `#settings/notify`；无 tab 段时默认 `panel`。
- `hashchange` 与首屏都要能还原 tab。
- `setView('settings')` 只切 view，不改 tab；切 tab 用 `setSettingsTab(id)`，写 hash 但不重进视图。
- 旧地址 `#notifications` / `#storage` / `#update` 要**重定向**到 `#settings/notify` 等，
  防止老书签和白屏。

## 五、按钮排版（统一）

- 卡片底部动作条：

```html
<div class="card-foot">
  <div class="spacer"></div>
  <button class="btn" data-act="…">次要</button>
  <button class="btn primary" data-act="…">主要</button>
</div>
```

  主要动作在最右，次要动作在其左；只有一个按钮时也靠右。
- 卡片标题行的行内动作（如「立即备份」「新增渠道」）用：

```html
<div class="card-head"><h3>标题</h3><div class="spacer"></div><button class="btn sm primary">…</button></div>
```

- **禁止**再出现 `style="margin:0"` / `style="margin:14px 0 0"` 这种内联间距补丁，
  一律用 `.card-foot` / `.card-head`。
- 同一张卡片里多个按钮：主按钮最右、危险按钮最左。

## 六、新增 / 修改的 i18n key（zh / en 都必须有值，禁止 undefined）

```
nav.settings                 设置                                / Settings
set.tab.panel                面板                                / Panel
set.tab.appearance           外观                                / Appearance
set.tab.policy               防护策略                            / Policy
set.tab.notify               通知                                / Notifications
set.tab.backup               备份                                / Backup
set.tab.storage              存储                                / Storage
set.tab.update               更新                                / Update
set.tab.about                关于                                / About
set.panelTitle               面板行为                            / Panel behavior
set.panelHint                影响面板本身的操作习惯，不改动 fail2ban 配置 / ...
set.appearanceTitle          外观与布局                          / Appearance and layout
set.appearanceHint          ...
set.langLabel                界面语言                            / Language
set.sidebarLabel             侧栏布局                            / Sidebar
set.sidebarAuto              记住上次状态                        / Remember last
set.sidebarExpanded          默认展开                            / Expanded by default
set.sidebarCollapsed         默认收起                            / Collapsed by default
set.densityLabel             表格密度                            / Table density
set.densityComfort           舒适                                / Comfortable
set.densityCompact           紧凑                                / Compact
set.densityHint              紧凑模式压缩表格行高，一屏多看几行 / ...
set.aboutTitle               关于面板                            / About
set.versionLabel             面板版本                            / Panel version
set.dataDir                  数据目录                            / Data directory
set.notifyHint               通知相关设置已并入本页              / ...
set.storageHint              存储占用与清理                      / ...
set.updateHint               版本检查与一键更新                  / ...
set.savedHint                设置已保存                          / Settings saved
```

## 七、样式契约（eng-dev 写 style.css）

必须新增：

1. `.workbar` / `.workbar button` / `.workbar button.on` / `.workbar-dot`
   —— 与 qbpanel 一致：`--muted` 底、4px padding、2px gap、圆角 `--radius`、
   横向滚动（`overflow-x:auto` + 隐藏滚动条）、`button.on` 用 `--card` 底。
2. `.card-head` —— `display:flex; align-items:center; gap:8px; margin-bottom:10px`，
   内部 `h3` 取消自身下边距。
3. `.card-foot` —— `display:flex; gap:8px; align-items:center; margin-top:14px;
   padding-top:14px; border-top:1px solid var(--border)`；`.card-foot .spacer{flex:1}`。
4. 密度：`html[data-density="compact"]` 下压缩 `.ttable th/td` 的上下 padding
   与 `.card` 内边距。
5. 手机端（≤720px）：`.workbar button{height:34px;padding:0 12px}`、
   `.card-foot{flex-wrap:wrap}`，主要按钮 `flex:1 1 100%` 独占一行。

## 八、后端（Lead 负责）

`store.DEFAULT_PREFS` 增加三项，全部有默认值、旧 prefs.json 缺键时回落：

```python
DEFAULT_PREFS = {
    "theme": "auto",
    "lang": "",            # "" = 跟随浏览器
    "sidebar": "auto",     # auto | expanded | collapsed
    "density": "comfortable",  # comfortable | compact
    "logLines": 400,
    "autoReload": True,
    "confirmDanger": True,
}
```

`/api/prefs` 的 PUT 已经按白名单过滤，新键自动生效，无需改路由。

## 九、验收

- 侧栏 7 项；设置页 8 个标签可点、可滚、深链可还原。
- 8 个标签在 zh/en × 桌面/手机 × 浅色/深色 下都不溢出、按钮不错位。
- 通知渠道增删改、测试发送、投递记录功能不回归。
- 存储清理、更新检查、一键更新入口功能不回归。
- `tools/check_i18n.py` / `check_css.py` / `check_web.py` 全绿。
- 无内联间距补丁残留（grep `style="margin`）。
