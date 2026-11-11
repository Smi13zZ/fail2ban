# pvepanel 界面实现参考

源码根：`D:\Workspace\DeepSeek Harness\pve-panel\src\fn-pvepanel\app\web\`
文件：`index.html`（Vue in-DOM 模板）、`style.css`、`js/app.js`（图标与数据）、`js/i18n.js`（文案键）

---

## 零、全局骨架（A / B / C 三块都挂在它上面）

### 0.1 页面骨架 HTML

这块是整个应用的外壳：侧栏 + 顶栏 + 内容区。维护页与存储页都渲染在 `.content` 里。

```html
<div class="shell" :class="{ collapsed: collapsed }">

  <aside class="side" :class="{ open: navOpen }">
    <nav class="nav">
      <button v-for="n in navItems" :key="n.id" class="nav-item"
              :class="{ on: view === n.id }" :title="t(n.key)"
              @click="go(n.id)">
        <span class="ic" v-html="ic(n.icon)"></span>
        <span class="label">{{ t(n.key) }}</span>
        <span class="badge" v-if="n.id === 'guests' && guestCount">{{ guestCount }}</span>
        <span class="badge" v-if="n.id === 'dash' && alertErrors" style="background:var(--danger);color:#fff">{{ alertErrors }}</span>
        <i class="nav-alert" v-if="n.id === 'settings' && updateNew" role="img"
           :title="t('update.found', { v: updateInfo.latest })"></i>
      </button>
    </nav>

    <div class="side-foot">
      <div class="foot-row">
        <button class="icon-btn" @click="cycleTheme" :title="themeTooltip" :aria-label="themeTooltip">
          <span v-html="ic(themeIcon)"></span>
        </button>
        <button class="icon-btn" @click="toggleLang" :title="t('side.lang')" :aria-label="t('side.lang')">
          <span v-html="ic('lang')"></span>
        </button>
      </div>
      <div class="ver-line" :title="'PVEpanel v' + appVersion">
        <span class="ic" style="width:14px;height:14px;display:flex;align-items:center;justify-content:center" v-html="ic('globe')"></span>
        <span class="mono">v{{ appVersion }}<template v-if="pveVersion"> · PVE {{ pveVersion }}</template></span>
      </div>
      <button class="icon-btn wide ghost collapse-btn" @click="collapsed = !collapsed"
              :title="collapsed ? t('side.expand') : t('side.collapse')">
        <span v-html="ic(collapsed ? 'expand' : 'collapse')"></span>
      </button>
    </div>
  </aside>
  <div class="mask" v-if="navOpen" @click="navOpen = false" style="justify-content:flex-start;background:rgba(0,0,0,.4)"></div>

  <div class="main">
    <header class="topbar">
      <button class="icon-btn ghost mobile-only" @click="navOpen = !navOpen" :title="t('side.menu')">
        <span v-html="ic('menu')"></span>
      </button>
      <div class="page-title">{{ pageTitle }}</div>
      <div class="spacer"></div>
      <button class="icon-btn" @click="refresh(true)" :disabled="busy" :title="t('top.refresh')">
        <span v-html="ic('refresh')"></span>
      </button>
    </header>

    <div class="content">
      <!-- 各视图模板都渲染在这里 -->
    </div>
  </div>
</div>
```

说明：`.content` 没有任何 `max-width`、`margin:auto` 或外层容器包裹，它是 `.main`（`flex:1; min-width:0`）的子元素，而 `.main` 又是 `.shell`（`display:flex`）里除侧栏外的剩余部分。宽屏铺满就是这么来的 —— 没有约束，就是铺满。

### 0.2 设计令牌（`:root` 与深色主题）

所有颜色 / 圆角 / 间距都走变量，改一处即可全局生效。

```css
:root {
  --radius: 10px;
  --radius-sm: 6px;
  --radius-lg: 14px;
  --gap: 12px;

  --bg: #ffffff;
  --sidebar: #fafafa;
  --card: #ffffff;
  --muted: #f5f5f5;
  --hover: #f5f5f5;
  --border: #e5e5e5;
  --border-soft: #efefef;

  --text: #0a0a0a;
  --dim: #737373;
  --faint: #a3a3a3;

  --primary: #1c1c1c;
  --primary-fg: #fafafa;
  --nav-active: #1c1c1c;
  --nav-active-fg: #fafafa;
  --accent-bar: #2563eb;
  --ring: #a3a3a3;

  --danger: #e7000b;
  --danger-soft: #fef2f2;
  --ok: #16a34a;
  --warn: #f59e0b;
  --idle: #a3a3a3;
  --dl: #2563eb;
  --ul: #d97706;

  --shadow-pop: 0 8px 24px -8px rgba(0, 0, 0, .18);
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color-scheme: light;
}

[data-theme="dark"] {
  --bg: #0a0a0a;
  --sidebar: #1c1c1c;
  --card: #1c1c1c;
  --muted: #262626;
  --hover: #262626;
  --border: rgba(255, 255, 255, .10);
  --border-soft: rgba(255, 255, 255, .07);

  --text: #fafafa;
  --dim: #a1a1a1;
  --faint: #737373;

  --primary: #e5e5e5;
  --primary-fg: #1c1c1c;
  --nav-active: #e5e5e5;
  --nav-active-fg: #1c1c1c;
  --accent-bar: #3b82f6;
  --ring: #737373;

  --danger: #ff6467;
  --danger-soft: rgba(255, 100, 103, .12);
  --ok: #4ade80;
  --warn: #fbbf24;
  --idle: #737373;
  --dl: #60a5fa;
  --ul: #fbbf24;

  --shadow-pop: 0 10px 30px -10px rgba(0, 0, 0, .6);
  color-scheme: dark;
}
```

### 0.3 通用基础件（卡片 / 行 / 分隔线 / 提示文字 / 文件选择器）

维护页与存储页都靠这几件拼起来，原文如下：

```css
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px;
}
.card + .card { margin-top: var(--gap); }
.grid > .card { margin-top: 0; }
.card.flush { padding: 0; overflow: hidden; }
.card h3 { margin: 0 0 12px; font-size: 14px; font-weight: 600; letter-spacing: -.005em; }
.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.card-head h3 { margin: 0; }

.row { display: flex; align-items: center; gap: 10px; }
.row.wrap { flex-wrap: wrap; }
.spacer { flex: 1; }
.sep { height: 1px; background: var(--border-soft); margin: 14px 0; }

.mono { font-family: var(--mono); font-size: 12px; }
.dim { color: var(--dim); }
.faint { color: var(--faint); }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.hint { font-size: 12px; color: var(--faint); margin-top: 5px; }
.field { margin-bottom: 14px; }
.field:last-child { margin-bottom: 0; }
.field > label { display: block; font-size: 13px; font-weight: 500; color: var(--dim); margin-bottom: 6px; }
.field > label.switch { display: inline-flex; align-items: center; gap: 8px; margin-bottom: 0; }
.field .row > .input, .field .row > .select { flex: 1; }
.field .btn { flex: 0 0 auto; }

.form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; margin-bottom: 14px; }
.form-grid .field { margin-bottom: 0; }
.form-grid .full { grid-column: 1 / -1; }

.kv { display: grid; grid-template-columns: 128px minmax(0, 1fr); gap: 9px 16px; font-size: 13px; }
.kv .k { color: var(--faint); }
.kv .v { margin: 0; min-width: 0; word-break: break-word; font-variant-numeric: tabular-nums; }

.filepick { display: flex; align-items: center; gap: 10px; min-width: 0; }
.filepick input[type="file"] {
  position: absolute; width: 1px; height: 1px; margin: 0; padding: 0; border: 0;
  opacity: 0; pointer-events: none;
}
.filepick-name {
  font-size: 13px; color: var(--dim); min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
```

按钮与输入控件（维护页 / 存储页的表单与底排都用它们）：

```css
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 36px; padding: 0 14px;
  border-radius: var(--radius); border: 1px solid var(--border);
  background: var(--card); color: var(--text);
  font-size: 14px; font-weight: 500;
  cursor: pointer; white-space: nowrap;
  transition: background .12s, border-color .12s, opacity .12s, color .12s;
}
.btn:hover:not(:disabled) { background: var(--muted); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn .ic, .btn svg { flex: 0 0 auto; }
.btn.primary { background: var(--primary); border-color: var(--primary); color: var(--primary-fg); }
.btn.primary:hover:not(:disabled) { opacity: .88; background: var(--primary); }
.btn.danger { background: transparent; border-color: var(--border); color: var(--danger); }
.btn.danger:hover:not(:disabled) { background: var(--danger-soft); border-color: var(--danger); }
.btn.ghost { background: transparent; border-color: transparent; color: var(--dim); }
.btn.ghost:hover:not(:disabled) { background: var(--muted); color: var(--text); }
.btn.sm { height: 30px; padding: 0 10px; font-size: 13px; border-radius: var(--radius-sm); }
.btn.icon { width: 36px; padding: 0; }
.btn.icon.sm { width: 30px; }

.input, .select, .textarea {
  width: 100%; height: 36px; padding: 0 11px;
  border-radius: var(--radius); border: 1px solid var(--border);
  background: var(--card); color: var(--text);
  outline: none; transition: border-color .12s, box-shadow .12s;
}
.input:focus, .select:focus, .textarea:focus { border-color: var(--ring); box-shadow: 0 0 0 3px color-mix(in srgb, var(--ring) 22%, transparent); }
.input::placeholder, .textarea::placeholder { color: var(--faint); }

.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
```

### 0.4 设置页的分区切换条（维护 / 存储 两个 tab 的入口）

```html
<div class="workbar">
  <button v-for="s in setSections" :key="s.id" :class="{ on: setTab === s.id }" @click="setTab = s.id">
    {{ s.label }}<i class="workbar-dot" v-if="s.id === 'update' && updateNew"></i>
  </button>
</div>
```

```css
.workbar {
  display: flex; gap: 2px; padding: 4px; margin-bottom: var(--gap);
  background: var(--muted); border-radius: var(--radius);
  overflow-x: auto; scrollbar-width: none; -ms-overflow-style: none;
}
.workbar::-webkit-scrollbar { display: none; }
.workbar button {
  position: relative; flex: 0 0 auto;
  height: 30px; padding: 0 14px; border: 0; border-radius: var(--radius-sm);
  background: none; color: var(--dim); cursor: pointer;
  font-size: 13px; font-weight: 500; white-space: nowrap;
}
.workbar button.on { background: var(--card); color: var(--text); }
.workbar button:hover:not(.on) { color: var(--text); }
.workbar-dot {
  position: absolute; top: 4px; right: 4px;
  width: 6px; height: 6px; border-radius: 50%; background: var(--danger);
  flex: 0 0 auto; pointer-events: none;
}
```

分区表在 `app.js` 的 `computed.setSections` 里：

```js
setSections() {
  return [
    { id: "servers", label: t("set.servers") },
    { id: "notify", label: t("set.notify") },
    { id: "maint", label: t("maint.title") },
    { id: "storage", label: t("storage.tab") },
    { id: "update", label: t("update.title") },
    { id: "ui", label: t("set.theme") },
  ];
},
```

---

## A. 维护页（设置 → 维护）

这块是配置备份与恢复页：把服务器列表、阈值、通知设置打包成一个文件，同时管理设备上的自动备份与备份文件列表。

### A.1 整块 HTML（三段卡片，原文）

```html
<template v-if="setTab === 'maint'">
  <div class="card">
    <div class="card-head"><h3>{{ t('maint.title') }}</h3></div>
    <p class="dim" style="margin:0 0 14px">{{ t('backup.desc') }}</p>
    <div class="row wrap" style="gap:8px">
      <button class="btn" type="button" @click="exportBackup">
        <span v-html="ic('download')"></span>{{ t('backup.export') }}
      </button>
      <button class="btn" type="button" @click="exportToFolder" :disabled="maintBusy">
        <span v-html="ic('folder')"></span>{{ t('backup.toFolder') }}
      </button>
    </div>
    <div class="hint" style="margin-top:9px">{{ t('backup.exportHint') }}</div>
    <div class="sep"></div>
    <div class="field">
      <label>{{ t('backup.import') }}</label>
      <div class="filepick">
        <button class="btn" type="button" @click="$refs.backupInput.click()">
          <span v-html="ic('folder')"></span>{{ t('backup.choose') }}
        </button>
        <span class="filepick-name" :title="backupFile ? backupFile.name : ''">
          {{ backupFile ? backupFile.name : t('file.none') }}
        </span>
        <input ref="backupInput" type="file" accept=".json,application/json" @change="onPickBackup">
      </div>
      <div class="row" style="margin-top:10px">
        <button class="btn primary" type="button" :disabled="!backupFile || maintBusy" @click="doImport">{{ t('backup.import') }}</button>
      </div>
      <div class="hint">{{ t('backup.importWarning') }}</div>
    </div>
  </div>

  <div class="card" style="margin-top:var(--gap)">
    <div class="card-head"><h3>{{ t('auto.title') }}</h3></div>
    <p class="dim" style="margin:0 0 14px">{{ t('auto.desc') }}</p>
    <div class="form-grid">
      <div class="field full">
        <label>{{ t('auto.enabled') }}</label>
        <label class="switch">
          <input type="checkbox" v-model="autoForm.auto_backup_enabled">
          <span>{{ autoForm.auto_backup_enabled ? t('settings.on') : t('auto.none') }}</span>
        </label>
      </div>
      <div class="field">
        <label>{{ t('auto.interval') }}</label>
        <input class="input" type="number" v-model.number="autoForm.auto_backup_interval_hours" min="1" max="8760">
        <div class="hint">{{ t('auto.intervalHelp') }}</div>
      </div>
      <div class="field">
        <label>{{ t('auto.retention') }}</label>
        <input class="input" type="number" v-model.number="autoForm.auto_backup_retention_days" min="1" max="3650">
        <div class="hint">{{ t('auto.retentionHelp') }}</div>
      </div>
    </div>
    <div class="kv" style="margin-top:14px">
      <div class="k">{{ t('auto.directory') }}</div><div class="v mono">{{ maint.directory || '—' }}</div>
      <div class="k">{{ t('auto.lastSuccess') }}</div><div class="v">{{ maint.last_success ? FMT.fmtDate(maint.last_success) : t('auto.none') }}</div>
      <div class="k">{{ t('auto.nextRun') }}</div><div class="v">{{ maint.next_run ? FMT.fmtDate(maint.next_run) : t('auto.none') }}</div>
      <div class="k">{{ t('auto.lastError') }}</div>
      <div class="v" :style="{ color: maint.last_error ? 'var(--danger)' : '' }">{{ maint.last_error || t('auto.none') }}</div>
    </div>
    <div class="sep"></div>
    <div class="row wrap" style="gap:8px">
      <button class="btn primary" type="button" @click="saveAuto" :disabled="maintBusy">{{ t('set.save') }}</button>
      <button class="btn" type="button" @click="runBackupNow" :disabled="maintBusy">{{ t('auto.runNow') }}</button>
    </div>
  </div>

  <div class="card" style="margin-top:var(--gap)">
    <div class="card-head">
      <h3>{{ t('backup.listTitle') }}</h3>
      <div class="spacer"></div>
      <span class="chip">{{ maint.backups.length }}</span>
    </div>
    <div v-if="!maint.backups.length" class="faint">{{ t('backup.empty') }}</div>
    <div class="bscroll" v-else>
      <table class="ttable tbackup">
        <thead>
          <tr>
            <th>{{ t('backup.createdAt') }}</th>
            <th class="num">{{ t('table.size') }}</th>
            <th class="col-acts"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in maint.backups" :key="b.name">
            <td class="bcname">
              <div class="mono ellipsis bname" :title="b.name">{{ backupLabel(b.name) }}</div>
            </td>
            <td class="num mono">{{ FMT.fmtBytes(b.size) }}</td>
            <td class="col-acts">
              <div class="acts">
                <button class="btn sm" @click="askRestore(b)">{{ t('backup.restore') }}</button>
                <button class="btn sm danger" @click="askDeleteBackup(b)">{{ t('backup.delete') }}</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
```

### A.2 维护页专属 CSS（原文）

```css
/* ================================================================
 * Maintenance (settings → 维护)
 * ================================================================ */
.bscroll { max-height: 300px; overflow-y: auto; overscroll-behavior: contain; }
.ttable.tbackup { table-layout: fixed; min-width: 0; }
.ttable.tbackup th:nth-child(1) { width: auto; }
.ttable.tbackup th:nth-child(2) { width: 100px; }
.ttable.tbackup th:nth-child(3) { width: 176px; }
.ttable.tbackup thead th {
  position: sticky; top: 0; z-index: 1;
  height: 36px; padding-top: 0; padding-bottom: 0;
}
.ttable.tbackup tbody td { height: 42px; padding-top: 0; padding-bottom: 0; }
.ttable.tbackup .bname { max-width: 100%; }
.ttable.tbackup td.col-acts { text-align: left; }
```

维护页的列表还依赖通用表规则（表头、单元格、.num、省略号、操作列）：

```css
.ttable { width: 100%; border-collapse: separate; border-spacing: 0; }
.ttable th {
  text-align: left; font-size: 12px; font-weight: 500; color: var(--faint);
  padding: 9px 12px; border-bottom: 1px solid var(--border);
  white-space: nowrap; user-select: none; background: var(--card);
  vertical-align: middle;
}
.ttable td { padding: 10px 12px; border-bottom: 1px solid var(--border-soft); vertical-align: middle; }
.ttable tbody tr:last-child td { border-bottom: 0; }
.ttable tbody tr { transition: background .1s; }
.ttable tbody tr:hover { background: var(--muted); }
.ttable .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }
.ttable td.num { text-align: right; }
.ttable td { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ttable .col-acts { text-align: left; }
.ttable .acts { display: flex; gap: 4px; justify-content: flex-start; align-items: center; }
```

`chip`（备份条数徽标）：

```css
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  height: 26px; padding: 0 9px; border-radius: 999px;
  font-size: 12px; font-weight: 500;
  background: var(--muted); color: var(--dim);
}
.chip .dot { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: var(--idle); }
```

### A.3 功能块与各自作用

| 区块 | 位置 | 作用 |
| --- | --- | --- |
| 导出配置 | 卡片一，`.row.wrap` 两个按钮 | 「导出配置」下载到本地（不含 API Token）；「导出到备份目录」直接写设备上的备份目录（保留 Token） |
| 导入配置 | 卡片一，`.sep` 之后 | `.filepick` 选一个 `.json`；选中后显示文件名（空则显示「未选择」）；主按钮在文件为空或忙时禁用；下方 hint 说明会覆盖现有配置 |
| 自动备份 | 卡片二，`.form-grid` | 开关 + 间隔小时（1~8760）+ 保留天数（1~3650），网格两列，开关占整行（`.full`） |
| 运行状态 | 卡片二，`.kv` | 四行键值：备份目录（等宽字体）／上次成功／下次运行／上次错误（有错时整行变 `var(--danger)`） |
| 底部按钮 | 卡片二，`.sep` 之后 | 「保存」（primary）+「立即备份」 |
| 备份文件列表 | 卡片三 | 表头右侧 `.chip` 显示条数；空列表显示一行 `.faint` 文本；有数据时 `.bscroll` 包住表格 |

数据侧动作都在 `app.js`：`loadMaint`、`exportBackup`、`exportToFolder`、`onPickBackup`、`doImport`、`saveAuto`、`runBackupNow`、`askRestore`、`askDeleteBackup`；全部用 `maintBusy` 一个布尔量互斥，恢复 / 导入 / 删除都先走 `confirmState` 二次确认。

### A.4 表格怎么排

- `table-layout: fixed` + `min-width: 0`：三列自己不撑出横向滚动，宽度按表头规则分配。
- 列宽：第 1 列 `auto`（吃掉剩余宽度），第 2 列固定 `100px`，第 3 列固定 `176px`。
- 对齐：第 2 列加 `.num` 右对齐 + 等宽数字；第 3 列 `.col-acts` 左对齐（注释里写明右对齐会把按钮推到列右边缘、离表头更远）。
- 行高锁死：`thead th` 36px、`tbody td` 42px，上下 padding 归零，行高不随文件名长度浮动。
- 表头 `position: sticky; top:0` 配合 `.bscroll` 的 `max-height:300px; overflow-y:auto`，滚动时表头不跟着走。
- 省略号：文件名外层 `.mono.ellipsis.bname`，`.bname { max-width:100% }` 让 `text-overflow: ellipsis` 真正生效；完整文件名放 `:title`。
- 文件名不是原样显示：`backupLabel()` 用正则把 `pvepanel-backup-20261106-021500.json` 转成 `2026-11-06 02:15:00`。

手机端（`max-width: 640px`）三条覆盖：隐藏第 2 列（大小）、第 3 列收窄到 `150px`、`.bmeta` 变成块级小字。

```css
  /* backup table: filename gets the room, size moves under it */
  .ttable.tbackup th:nth-child(2), .ttable.tbackup td:nth-child(2) { display: none; }
  .ttable.tbackup th:nth-child(3) { width: 150px; }
  .ttable.tbackup .bmeta { display: block; font-size: 11px; color: var(--faint); }
```

**改到 fail2ban 里最需要注意什么：** fail2ban 已有 `.ttable` 与 `.storage-total`，但没有 `.bscroll`、`.tbackup`、`.chip` 的完整定义；搬过去时三件要一起补：`.bscroll` 的 `max-height:300px`、`.tbackup` 的三列定宽、`.col-acts` 的左对齐。另外 fail2ban 的 `main` 有 `max-width:1400px`，表格右边缘会比这里更早出现（见 C 节）。

---

## B. 存储页（设置 → 存储）

这块是面板自身目录的磁盘占用与安装包缓存清理页：先给一个合计条，再给明细表，最后给一排清理按钮 + 安装包缓存表。

### B.1 整块 HTML（原文）

```html
<template v-if="setTab === 'storage'">
  <div class="card">
    <div class="card-head">
      <h3>{{ t('storage.title') }}</h3>
      <div class="spacer"></div>
      <button class="btn sm" type="button" :disabled="storageLoading" @click="loadStorage">
        <span v-html="ic('refresh')"></span>{{ storageLoading ? t('common.loading') : t('storage.refresh') }}
      </button>
    </div>
    <p class="dim" style="margin:0 0 14px">{{ t('storage.desc') }}</p>

    <div class="storage-total">
      <span class="k">{{ t('storage.total') }}</span>
      <b class="mono">{{ FMT.fmtBytes(storage.total) }}</b>
      <span class="faint mono">{{ storage.var_dir || '—' }}</span>
    </div>

    <div class="faint" v-if="!storageItems.length">{{ t('storage.empty') }}</div>
    <div class="bscroll storage-scroll" v-else>
      <table class="ttable tstorage">
        <thead>
          <tr>
            <th>{{ t('storage.item') }}</th>
            <th class="num" style="width:96px">{{ t('storage.size') }}</th>
            <th class="num hide-sm" style="width:78px">{{ t('storage.files') }}</th>
            <th class="acts-col hide-md">{{ t('storage.act') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="it in storageItems" :key="it.key" :class="{ faint: !it.bytes }">
            <td class="sname">
              <div class="ellipsis">{{ it.label }}</div>
              <div class="sub mono ellipsis" :title="it.path">{{ it.path }}</div>
            </td>
            <td class="num mono">{{ it.bytes ? FMT.fmtBytes(it.bytes) : '—' }}</td>
            <td class="num mono hide-sm">{{ it.files || '—' }}</td>
            <td class="hide-md">
              <button class="btn sm" type="button"
                      v-if="it.key === 'updates' && it.bytes"
                      :disabled="!!storageBusy" @click="askCleanup('updates')">
                {{ t('storage.cleanUpdates') }}
              </button>
              <span class="faint" v-else-if="it.key === 'updates'">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="sep"></div>
    <div class="row wrap" style="gap:8px">
      <button class="btn" type="button" :disabled="!!storageBusy" @click="askCleanup('updates')">
        {{ t('storage.cleanUpdates') }}
      </button>
      <span class="faint" style="font-size:12.5px"
            :title="t('storage.keepHint', { n: storage.updates_keep || 2 })">
        {{ t('storage.keepLabel', { n: storage.updates_keep || 2 }) }}
      </span>
      <button class="btn" type="button" :disabled="!!storageBusy" @click="askCleanup('logs')">
        {{ t('storage.cleanLogs') }}
      </button>
      <div class="spacer"></div>
      <button class="btn danger" type="button" :disabled="!!storageBusy" @click="askCleanup('all')">
        {{ t('storage.cleanAll') }}
      </button>
    </div>

    <div class="hint" style="margin-top:12px">{{ t('storage.hintLogs') }}</div>

    <h4 class="storage-h4">{{ t('storage.updatesTitle') }}</h4>
    <div class="faint" v-if="!storageUpdates.length">{{ t('storage.updatesEmpty') }}</div>
    <div class="bscroll storage-scroll" v-else>
      <table class="ttable tstorage">
        <thead>
          <tr>
            <th>{{ t('storage.pkg') }}</th>
            <th class="num" style="width:96px">{{ t('storage.size') }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="u in storageUpdates" :key="u.name">
            <td class="sname">
              <div class="mono ellipsis" :title="u.name">{{ u.name }}</div>
              <div class="sub">
                <span class="chip err" v-if="u.partial">{{ t('storage.partial') }}</span>
                <span class="tagcur" v-else-if="storage.current_version && u.name.indexOf(storage.current_version) >= 0">{{ t('storage.inUse') }}</span>
                <span class="faint" v-else>{{ FMT.fmtDate(u.t) }}</span>
              </div>
            </td>
            <td class="num mono">{{ FMT.fmtBytes(u.bytes) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="hint" style="margin-top:10px">{{ t('storage.hintUpdates') }}</div>
  </div>
</template>
```

### B.2 存储页专属 CSS（原文）

```css
/* ================================================================
 * Storage (settings → 存储)
 * ================================================================ */
/* The usage table is short (five rows) so it does not need the fixed 300px
   scroll box the backup list uses - that would cut the last row in half. It
   only becomes scrollable on short viewports, where the card would otherwise
   push the buttons off screen. */
.bscroll.storage-scroll { max-height: none; overflow-y: visible; }
@media (max-height: 900px) {
  .bscroll.storage-scroll { max-height: 330px; overflow-y: auto; }
}
.ttable.tstorage { table-layout: fixed; min-width: 0; }
.ttable.tstorage thead th {
  position: sticky; top: 0; z-index: 1;
  height: 36px; padding-top: 0; padding-bottom: 0;
  background: var(--card); border-bottom: 1px solid var(--border);
}
.ttable.tstorage tbody td { height: 46px; padding-top: 0; padding-bottom: 0; }
/* min-width:0 lets the path ellipsis actually work inside a table cell: the
   default min-content floor measures the longest unbreakable token, and a long
   path has no break opportunity at all. */
.ttable.tstorage .sname { min-width: 0; }
.ttable.tstorage .sname .sub { font-size: 11px; margin-top: 2px; color: var(--faint); }
.ttable.tstorage .acts-col { text-align: left; }
/* English labels are wider than the Chinese ones, so an auto-width action
   column can clip its own button. 168px fits "Clean old packages" at this
   font size; the column is hidden below 1140px anyway. */
.ttable.tstorage th:nth-child(4), .ttable.tstorage td:nth-child(4) { width: 168px; }
.ttable.tstorage td .btn.sm { white-space: nowrap; }
.ttable.tstorage .chip { height: 20px; padding: 0 7px; font-size: 11px; }
.ttable.tstorage .chip.err { background: var(--danger-soft); color: var(--danger); }

.storage-total {
  display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;
  margin: 0 0 12px; padding: 12px 14px;
  border: 1px solid var(--border); border-radius: var(--radius);
  background: var(--muted);
}
.storage-total .k { font-size: 12.5px; color: var(--dim); }
.storage-total b { font-size: 19px; font-variant-numeric: tabular-nums; }
.storage-total .faint { font-size: 11.5px; word-break: break-all; min-width: 0; }

.storage-h4 { margin: 18px 0 8px; font-size: 13px; font-weight: 600; }
```

存储页用到的 `.tagcur`（「当前版本」标记，通用组件）：

```css
.tagcur {
  display: inline-flex; align-items: center;
  padding: 3px 6px; border-radius: 5px;
  font-size: 11px; font-weight: 500; line-height: 1;
  white-space: nowrap;
  background: var(--primary); color: var(--primary-fg);
}
```

隐藏类（存储表表头分别挂 `.hide-sm` / `.hide-md`）：

下面这三个断点里与布局宽度相关的规则，逐条摘出来（代码原样，未改动）：

```css
@media (max-width: 1140px) {
  .hide-md { display: none !important; }
}
@media (max-width: 900px) {
  .hide-sm { display: none !important; }
}
```

### B.3 「合计占用」块的结构

```html
<div class="storage-total">
  <span class="k">{{ t('storage.total') }}</span>
  <b class="mono">{{ FMT.fmtBytes(storage.total) }}</b>
  <span class="faint mono">{{ storage.var_dir || '—' }}</span>
</div>
```

三段是同一行 flex：标签（`.k`，12.5px 灰）→ 数值（`b`，19px、`.mono` 字体、`tabular-nums` 等宽数字）→ 目录路径（`.faint.mono`，11.5px，`word-break: break-all` 允许任意位置断行）。`align-items: baseline` 让三段文字基线对齐（不是顶对齐），`flex-wrap: wrap` 保证窄屏自动换行不溢出。整块是 `var(--muted)` 底 + 1px 边框 + `--radius` 圆角，`padding: 12px 14px`。

手机端（`max-width:640px`）改成竖排：

```css
  .storage-total { flex-direction: column; align-items: flex-start; gap: 4px; padding: 10px 12px; }
  .storage-total b { font-size: 17px; }
  .storage-total .faint { font-size: 10.5px; }
```

### B.4 明细表列定义与对齐

主表（项目 / 大小 / 文件数 / 操作）四列：

| 列 | 表头 class / 内联宽 | 单元格 | 对齐与省略号 |
| --- | --- | --- | --- |
| 项目 | 无 class，宽度由 `table-layout:fixed` 的 auto 吃剩余空间 | `td.sname`，内含两行：`.ellipsis` 的标签 + `.sub.mono.ellipsis` 的路径 | 左对齐；两行都 `text-overflow: ellipsis`；`.sname{min-width:0}` 是省略号生效的前提 |
| 大小 | `class="num"` + 内联 `width:96px` | `td.num.mono`，无字节时显示 `—` | `.num` = 右对齐 + `tabular-nums` + `nowrap` |
| 文件数 | `class="num hide-sm"` + `width:78px` | `td.num.mono.hide-sm`，0 显示 `—` | 同上；900px 以下整列隐藏 |
| 操作 | `class="acts-col hide-md"`，CSS 里覆盖成 `168px` | `td.hide-md`，仅 `key === 'updates'` 且有字节时给一个 `.btn.sm`，其余显示 `—` | `.acts-col{text-align:left}`；1140px 以下整列隐藏 |

路径小字的排版要点：

- 结构是「标签一行 + 路径一行」，不是横向并排，靠 `.sname .sub { font-size:11px; margin-top:2px; color:var(--faint) }` 压成 11px 淡色小字。
- 路径必须带 `ellipsis` 且父 `td` 必须 `min-width:0` —— 表格单元格默认的 min-content 地板等于最长不可断词（一整条路径就是一个「词」），不写 `min-width:0` 时 `text-overflow` 完全不生效。
- 完整路径放 `:title`。
- 行高锁死：`thead th` 36px、`tbody td` 46px（比备份表高 4px，因为这里是两行内容）。
- 表头 sticky + 背景色（`.tstorage thead th` 显式写了 `background: var(--card)`），否则滚动时下面内容会透出来。

手机端（`max-width:640px`）：路径行用 `direction: rtl` 把省略号挪到前面，保留尾部（文件名那一段）可读：

```css
  .ttable.tstorage th, .ttable.tstorage td { padding-left: 8px; padding-right: 8px; }
  .ttable.tstorage tbody td { height: 42px; }
  .ttable.tstorage thead th { height: 32px; font-size: 12px; }
  /* Direction rtl puts the ellipsis at the front, so the end of a long path -
     the part that says which file it is - stays readable. */
  .ttable.tstorage .sname .sub {
    font-size: 10px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    direction: rtl; text-align: left;
  }
  .bscroll.storage-scroll .btn.sm { padding: 0 8px; font-size: 12px; }
```

安装包缓存表只有两列（文件 / 大小），共用 `.tstorage` 的规则；第二行 `.sub` 里放三种状态之一：`.chip.err`「半截」／`.tagcur`「当前版本」／`.faint` 时间。

### B.5 底部操作按钮那一排

```html
<div class="sep"></div>
<div class="row wrap" style="gap:8px">
  <button class="btn" type="button" :disabled="!!storageBusy" @click="askCleanup('updates')">
    {{ t('storage.cleanUpdates') }}
  </button>
  <span class="faint" style="font-size:12.5px"
        :title="t('storage.keepHint', { n: storage.updates_keep || 2 })">
    {{ t('storage.keepLabel', { n: storage.updates_keep || 2 }) }}
  </span>
  <button class="btn" type="button" :disabled="!!storageBusy" @click="askCleanup('logs')">
    {{ t('storage.cleanLogs') }}
  </button>
  <div class="spacer"></div>
  <button class="btn danger" type="button" :disabled="!!storageBusy" @click="askCleanup('all')">
    {{ t('storage.cleanAll') }}
  </button>
</div>
```

结构是「一行 flex + 一个 `.spacer` 把危险操作推到最右」：

- `.row.wrap`：`display:flex; align-items:center; gap:10px` + `flex-wrap:wrap`；内联 `gap:8px` 覆盖成 8px。
- 前三个元素（清理旧安装包按钮 / 保留个数提示 / 清空日志按钮）排左边。
- `<div class="spacer">` 是 `flex:1`，吃掉中间所有空隙。
- 「全部清理」用 `.btn.danger`（透明底 + 红字 + 灰边，hover 时才出现 `--danger-soft` 背景），被推在最右，视觉上和另两个常规清理分开。
- 三个按钮共用 `storageBusy`（字符串，存当前正在清理的目标）做互斥，全部 `:disabled`。
- 按钮上方的 `.sep` 是 1px 分隔线（`--border-soft`，上下 14px）。
- 按钮下方 `.hint`（`margin-top:12px`）说明日志就地截断的语义。

**改到 fail2ban 里最需要注意什么：** fail2ban 已有 `.storage-total` 与 `.ttable.tfixed`（定宽四列、第 4 列 320px），与本节的 `.tstorage` 是两套不同规则 —— 不要两套同时挂到同一张表上，否则列宽互相覆盖。要搬就整块换成本节的 `.tstorage` 命名，并同时补 `.bscroll`、`.hide-sm`、`.hide-md`、`.acts-col` 这四个 fail2ban 目前没有或不完整的类。另外 fail2ban 的路径列若沿用「定宽 320px」写法，窄屏会直接把表撑出横向滚动条；用 `min-width:0` + 省略号 + 手机端 `direction:rtl` 更稳。

---

## C. 全屏布局宽度（为什么内容区能铺满）

这块回答一个具体问题：内容区在宽屏下没有任何宽度上限，靠 flex 的自然伸展铺满窗口。

### C.1 布局骨架的 CSS（原文，逐条）

```css
.shell { display: flex; min-height: 100vh; }

.side {
  width: 216px; flex: 0 0 216px;
  background: var(--sidebar);
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column;
  position: sticky; top: 0; height: 100vh;
  transition: width .16s ease, flex-basis .16s ease;
}
.shell.collapsed .side { width: 62px; flex: 0 0 62px; }

.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }

.topbar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 18px; min-height: 56px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  position: sticky; top: 0; z-index: 20;
}

.content { padding: 16px 18px 40px; flex: 1; min-width: 0; }
```

关键几条：

1. `.content` 没有 `max-width`、没有 `margin: 0 auto`、没有 `width`，只有 `padding: 16px 18px 40px`。整份 `style.css` 里所有 `max-width` 都用在别的元素上（弹窗 520px、chip 320px、顶栏下拉 190px），没有任何一条作用在页面内容容器上。
2. `.main { flex: 1; min-width: 0 }` —— `min-width:0` 是关键：不加它，flex 项的默认 `min-width:auto` 会被内部宽内容（比如定宽表格）顶大，导致整页出现横向滚动条；加上它，`.main` 可以自由收缩到视口剩余宽度。
3. `.shell { display: flex }` + 侧栏 `flex: 0 0 216px` → 主区域精确等于「视口宽度 − 216px」。
4. `.content { flex: 1 }` 只是让它在竖直方向吃掉顶栏以下的空间，横向什么都不限。

### C.2 响应式断点

一共四个断点，没有一个是给内容区加宽度上限的：

| 断点 | 改了什么 |
| --- | --- |
| `min-width: 1281px` | 只隐藏表格滚动提示 `.scroll-hint { display:none }` |
| `max-width: 1140px` | 只隐藏 `.hide-md`（存储表操作列） |
| `max-width: 900px` | 侧栏改成 `position:fixed` 抽屉（`translateX(-100%)`，`.open` 时归位）、`.mobile-only` 显示、`.content` padding 收紧成 `14px 12px 40px`、`.topbar` 变 `10px 12px`、隐藏 `.hide-sm`、三 / 四列网格降成两列、隐藏折叠按钮、`.kv` 列宽 128px→104px |
| `max-width: 640px` | `.content` 变 `12px 10px calc(28px + env(safe-area-inset-bottom))`、网格全部单列、按钮高度 36→38px（`.sm` 30→32px）、手机顶栏布局、表格保留全列横向滚动、备份 / 存储表的手机端改造 |

```css
@media (max-width: 1140px) {
  .hide-md { display: none !important; }
}

@media (max-width: 900px) {
  .mobile-only { display: inline-flex; }

  .side {
    position: fixed; z-index: 65; left: 0; top: 0; height: 100%;
    transform: translateX(-100%); transition: transform .2s ease;
    box-shadow: var(--shadow-pop);
  }
  .side.open { transform: none; }
  .shell.collapsed .side { width: 216px; flex: 0 0 216px; }

  .content { padding: 14px 12px 40px; }
  .topbar { padding: 10px 12px; min-height: 52px; }
  .hide-sm { display: none !important; }
  .grid.c4, .grid.c3 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .collapse-btn { display: none !important; }
  .page-title { font-size: 14px; }
  .chart-wrap { height: 190px; }
  .kv { grid-template-columns: 104px minmax(0, 1fr); }
  .stkv { grid-template-columns: 56px 120px 56px minmax(0, 1fr); }
}

@media (max-width: 640px) {
  .content { padding: 12px 10px calc(28px + env(safe-area-inset-bottom)); }
  .grid.c1, .grid.c2, .grid.c3 { grid-template-columns: minmax(0, 1fr); }
  .grid.c4 { grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); }
  .stat { padding: 12px; gap: 6px; }
  .stat .v, .hl .big { font-size: 19px; }
  .card { padding: 14px; border-radius: var(--radius); }
}
```

上面这段 640px 是节选：完整块里还有表格、状态条、通知记录等模块的规则，与本节的宽度问题无关，已省略；节选内每一行与源码逐字一致。

结论：没有给内容容器设 `max-width` 的断点，断点只改 padding 和侧栏形态。内容区在任何宽度下都是「视口 − 侧栏」再减左右 padding。

### C.3 侧栏宽度

| 状态 | 宽度 | 源码 |
| --- | --- | --- |
| 展开 | `216px`（`width` 与 `flex-basis` 双写） | `.side { width:216px; flex:0 0 216px }` |
| 收起 | `62px` | `.shell.collapsed .side { width:62px; flex:0 0 62px }` |
| 手机（≤900px） | 216px 但脱离文档流（`position:fixed`，默认移出屏幕，`.open` 才进） | 见 C.2 |
| 过渡 | `transition: width .16s ease, flex-basis .16s ease` | `.side` |

收起态其它配套：`.shell.collapsed .nav-item { justify-content:center; padding:8px 0; gap:0 }`、标签与徽标 `.label/.badge { display:none }`、版本行 `.ver-line` 隐藏、底部按钮列改竖排。

### C.4 1440 / 1920 宽下内容区实际宽度（按 CSS 推算）

公式：内容区宽度 = 视口宽度 − 侧栏宽度 − 左右 padding

| 视口 | 侧栏 | 左右 padding | 内容区可用宽度 |
| --- | --- | --- | --- |
| 1440（展开） | 216 | 18 + 18 = 36 | **1188px** |
| 1440（收起） | 62 | 36 | **1342px** |
| 1920（展开） | 216 | 36 | **1668px** |
| 1920（收起） | 62 | 36 | **1822px** |

对照：同样 1440 视口下，如果容器是 `max-width:1100px; margin:0 auto`，可用宽度会被砍到 1100px，两边各空出 ((1440−216)−1100)/2 = 62px 的空白，宽屏上明显「两边留白、中间一窄条」。这里因为不存在上限，1920 下能一路铺到 1668px，中间的定宽表（`tlist` 976px、`tdisk` 876px）自然分散在宽卡片里。

**改到 fail2ban 里最需要注意什么：** fail2ban 的 `main` 现在是 `padding:16px 18px 40px; max-width:1400px; width:100%; margin:0 auto` —— 删掉 `max-width` 与 `margin:0 auto` 两条、保留 `width:100`（或改用 flex 父级、去掉 width），就能和这里的铺满效果对齐。改完必须确认三点：(1) 父级 `.content` 有 `min-width:0`，否则宽表会把整页顶出横向滚动条；(2) 表格自身保持 `table-layout:fixed` + 显式列宽 + 外层横向滚动容器，否则宽屏下列被拉得很散、窄屏下又被压成一条；(3) 手机端 `env(safe-area-inset-bottom)` 只算一次（底部 padding 只在 `.content` 里加）。注意 fail2ban 的 `max-width:1180px` 断点附近可能另有 `main` 的覆盖规则，改之前先全文搜一遍 `main {` 与 `max-width: 1400px` 的引用，避免只改了桌面、窄屏又被别的规则限回来。

---

## D. 侧栏图标（概览 / 设置）

这块给出「概览」与「设置」两个导航项用的图标 SVG 原文，以及侧栏导航按钮的尺寸与状态色。

### D.1 图标是怎么生成的

所有图标由同一个工厂函数生成，统一 `viewBox="0 0 24 24"`、`fill:none`、`stroke:currentColor`、`stroke-width:1.9`、圆头线帽与圆角连接、默认 18×18：

```js
function svg(body, size) {
  return '<svg viewBox="0 0 24 24" width="' + (size || 18) + '" height="' + (size || 18) +
    '" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" ' +
    'stroke-linejoin="round">' + body + "</svg>";
}
```

侧栏里再把描边加粗一点 —— `.side .nav-item svg, .side .ver-line svg { stroke-width: 2.1; }`

### D.2 「概览」图标（nav.dash）完整原文

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>
```

形状：四个圆角矩形组成的仪表盘网格，左列「高 + 矮」、右列「矮 + 高」，非对称。

源码里的定义（`app.js` 第 15 行）：

```js
dash: svg('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
```

### D.3 「设置」图标（nav.settings）完整原文

```html
<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
```

形状：中心半径 3 的圆（齿轮内孔）+ 一圈外轮廓（单条 path）。

源码里的定义（`app.js` 第 21 行）：

```js
gear: svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/>'),
```

### D.4 导航项绑定的图标名

```js
navItems() {
  return [
    { id: "dash", key: "nav.dash", icon: "dash" },
    { id: "guests", key: "nav.guests", icon: "server" },
    { id: "storage", key: "nav.storage", icon: "storage" },
    { id: "disks", key: "nav.disks", icon: "disk" },
    { id: "tasks", key: "nav.tasks", icon: "list" },
    { id: "settings", key: "nav.settings", icon: "gear" },
  ];
},
```

渲染方式：`<span class="ic" v-html="ic(n.icon)"></span>`，`ic(name)` 就是 `return ICONS[name] || ""`。所以图标是字符串注入，不是组件。

### D.5 侧栏导航按钮 CSS（尺寸 / 图标大小 / active 色）

```css
.nav { padding: 10px 8px; display: flex; flex-direction: column; gap: 2px; overflow: auto; }

.nav-item {
  position: relative;
  display: flex; align-items: center; gap: 10px;
  padding: 8px 10px; min-height: 36px;
  border-radius: var(--radius); border: 0; background: none;
  color: var(--dim); cursor: pointer; width: 100%; text-align: left;
  font-size: 14px; font-weight: 600;
  transition: background .12s, color .12s;
}
.nav-item .ic { flex: 0 0 18px; width: 18px; height: 18px; display: flex; align-items: center; justify-content: center; }
.nav-item .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.nav-item:hover { background: color-mix(in srgb, var(--text) 5%, transparent); color: var(--text); }
.nav-item.on { background: var(--nav-active); color: var(--nav-active-fg); }
.side .nav-item svg, .side .ver-line svg { stroke-width: 2.1; }
.nav-item .badge {
  font-size: 11px; font-weight: 600; line-height: 1;
  min-width: 18px; height: 18px; padding: 0 5px;
  display: inline-flex; align-items: center; justify-content: center;
  border-radius: 999px; background: var(--muted); color: var(--dim);
}
.nav-item.on .badge { background: color-mix(in srgb, var(--nav-active-fg) 14%, transparent); color: var(--nav-active-fg); }
.nav-alert {
  flex: 0 0 auto; width: 8px; height: 8px; border-radius: 50%;
  background: var(--danger);
  box-shadow: 0 0 0 2px var(--bg);
}
.shell.collapsed .nav-item .nav-alert {
  position: absolute; top: 50%; right: 3px;
  transform: translateY(-50%); box-shadow: none;
}
.shell.collapsed .nav-item { justify-content: center; padding: 8px 0; gap: 0; }
.shell.collapsed .nav-item .label,
.shell.collapsed .nav-item .badge { display: none; }
```

要点：

- 按钮：`min-height:36px`、`padding:8px 10px`、`border-radius: var(--radius)`（10px）、圆角矩形无边框、`width:100`、文字左对齐、字号 14px / 字重 600。
- 图标槽：`.nav-item .ic` 固定 18×18、`flex: 0 0 18px` 不参与收缩；svg 的 width/height 属性也是 18，两处一致。
- 默认色：`color: var(--dim)`（`#737373`）；图标用 `stroke: currentColor`，所以图标颜色永远跟随文字色，不用单独给 svg 设色。
- hover：背景 `color-mix(in srgb, var(--text) 5%, transparent)`（5% 文字色叠加），文字色升到 `var(--text)`。
- active（选中态）：`.nav-item.on { background: var(--nav-active); color: var(--nav-active-fg) }` —— 浅色主题是近黑底 + 近白字（`#1c1c1c` / `#fafafa`），深色主题反转（`#e5e5e5` / `#1c1c1c`）；图标因为 `currentColor` 自动变成反白，徽标也有对应的 `.nav-item.on .badge` 反色规则。没有左侧竖条、没有背景色以外的强调。
- 描边加粗：侧栏内 svg 的 `stroke-width` 提到 2.1（工厂默认 1.9），因为 18px 小尺寸下 1.9 太细。
- 收起态：`justify-content:center` + `padding:8px 0`，标签与徽标 `display:none`，红点改成绝对定位贴在右侧。

**改到 fail2ban 里最需要注意什么：** fail2ban 的侧栏按钮已有自己的尺寸规则（`.sidebar-foot` 那套里 `svg { width:16px; height:16px; stroke-width:2.1 }`），与这里的 `.nav-item .ic { 18px }` 不是一套 —— 直接搬 svg 时不要同时依赖 svg 的 width/height 属性和 CSS 尺寸，两处都写会以 CSS 为准但很容易看错。另外两个图标都是纯 `currentColor` 描边画法，搬过去后必须确认父级按钮设置了 `color`（默认、hover、active 三态），否则会继承到别处的颜色；active 态在这里是「整块反转底色」，而不是只变图标色，别只改图标。

---

## 附：i18n 文案键（可直接用的中文值）

维护页在 `js/i18n.js` 里的键值：

```js
"maint.title": "维护",
"maint.desc": "配置备份与恢复：把服务器列表、阈值与通知设置打包成一个文件。",
"backup.desc": "下载的备份不含 API Token，换机后需要重新填写；设备上的自动备份与「导出到备份目录」会保留 Token，请妥善保管。",
"backup.export": "导出配置",
"backup.toFolder": "导出到备份目录",
"backup.exportHint": "下载到本地，或直接写入设备上的备份目录。",
"backup.import": "导入配置",
"backup.choose": "选择文件",
"backup.importWarning": "导入会覆盖当前的服务器列表与设置，请确认已备份。",
"backup.listTitle": "备份文件",
"backup.empty": "备份目录里还没有文件。",
"backup.createdAt": "创建时间",
"backup.restore": "恢复",
"backup.delete": "删除",
"backup.restoreConfirm": "确定用这个备份覆盖当前配置吗？",
"backup.deleteConfirm": "确定删除备份「{name}」吗？",
"backup.done": "已恢复 {n} 台服务器",
"backup.exported": "已导出",
"backup.exportedTo": "已写入 {path}",
"backup.deleted": "已删除备份",
"backup.runDone": "备份完成：{name}",
"backup.importFail": "导入失败：{msg}",

"auto.title": "自动备份",
"auto.desc": "按固定间隔把配置写进备份目录，只在本机进行，不上传任何地方。",
"auto.enabled": "启用自动备份",
"auto.interval": "间隔（小时）",
"auto.intervalHelp": "1 ~ 8760 小时；配置改动不频繁的话 24 小时足够。",
"auto.retention": "保留天数",
"auto.retentionHelp": "超过这个天数的备份会被自动清理。",
"auto.directory": "备份目录",
"auto.lastSuccess": "上次成功",
"auto.nextRun": "下次运行",
"auto.lastError": "上次错误",
"auto.none": "无",
"auto.runNow": "立即备份",
```

存储页：

```js
"storage.tab": "存储",
"storage.title": "存储",
"storage.desc": "面板自身目录占用的磁盘空间，以及缓存的安装包。旧安装包与日志都会一直堆积，这里可以一次清掉。",
"storage.total": "合计占用",
"storage.refresh": "刷新",
"storage.item": "项目",
"storage.size": "大小",
"storage.files": "文件数",
"storage.act": "操作",
"storage.cleanUpdates": "清理旧安装包",
"storage.cleanLogs": "清空日志",
"storage.cleanAll": "全部清理",
"storage.cleanUpdatesText": "将删除旧版本的安装包和所有下载中断留下的半截文件，预计回收 {v}。当前运行的版本会保留。",
"storage.cleanLogsText": "将清空面板日志与更新安装日志（含 .1 轮转文件），预计回收 {v}。",
"storage.cleanAllText": "将同时清理旧安装包和全部日志，预计回收 {v}。",
"storage.cleaned": "已清理，回收 {v}",
"storage.cleanFailed": "清理失败：{msg}",
"storage.cleanBusy": "更新正在进行，暂时不能清理",
"storage.updatesTitle": "安装包缓存",
"storage.pkg": "文件",
"storage.partial": "半截",
"storage.inUse": "当前版本",
"storage.keepLabel": "保留最近 {n} 个",
"storage.keepHint": "清理后最多留最近 {n} 个安装包，当前运行的版本与回滚包始终保留。",
"storage.hintUpdates": "带「半截」标记的是下载中断留下的文件，永远不会被用到。",
"storage.hintLogs": "面板日志超过上限会自动轮转一份，想立刻腾出空间可以手动清空；清理时日志会被就地截断，不影响正在运行的进程。",
"storage.empty": "面板目录里还没有占用",
"storage.item.updates": "更新包缓存",
"storage.item.backups": "配置备份",
"storage.item.panel_log": "面板日志",
"storage.item.panel_log1": "面板日志（上一份）",
"storage.item.update_log": "更新安装日志",
```

英文词条在 `i18n.js` 的 `en` 段里，键名与中文一一对应，不重复抄录。
