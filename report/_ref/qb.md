# qbpanel 参考实现提取：日志页 / 通知设置页 / 字体

本文件从 qbpanel 现有前端源码里逐行抄出三块界面的真实实现，供 fn-fail2ban 改造时照做。

源码根目录（只读）：

    D:\Workspace\DeepSeek Harness\qbpanel-maintenance\src\qbpanel\qbpanel\app\web\
    ├─ index.html      （1482 行，Vue 3 in-DOM 模板）
    ├─ style.css       （1044 行，全局样式与变量）
    └─ js\
       ├─ app.js       （Vue 根组件：data / computed / watch / methods）
       ├─ i18n.js      （扁平点键，zh + en）
       ├─ api.js       （HTTP 层）
       └─ util.js      （FMT 格式化：fmtDate / fmtBytes / fmtSpeed …）

每段代码块上方标注了出处行号，便于回查。

---

# A. 日志页

**这块是干什么的**：左侧导航「日志」进入后的一整页 —— 顶栏内是日志来源下拉框、绿色「已连接」状态点、刷新按钮；
内容是一排级别分段标签（常规 / 信息 / 警告 / 严重）+ 自动刷新开关 + 刷新按钮，下面是圆角卡片里的纯列表，
每行「灰色等宽时间 + 消息」，无边框行、行距紧凑、整块可滚动。

## A1. 整页 HTML 结构原文

### A1-1 顶栏（index.html 59–90 行）

这是所有视图共用的顶栏，日志页看到的下拉框 + 绿点 + 刷新按钮就在这里。注意顶栏里的下拉框是**服务器选择器**
（servers.length > 1 才出现），不是日志来源选择器；span.chip 是那个「已连接」胶囊。

```html
    <header class="topbar">
      <button class="icon-btn ghost mobile-only" @click="navOpen = !navOpen" :title="t('side.menu')">
        <span v-html="ic('menu')"></span>
      </button>
      <div class="page-title">{{ pageTitle }}</div>
      <div class="spacer"></div>

      <!-- :value + @change instead of v-model: v-model would update currentId
           before the handler runs, making the "already current" guard swallow
           every switch so the torrent list never reloaded. -->
      <select v-if="!isMobile && servers.length > 1" class="select topbar-server"
              :value="currentId"
              @change="switchServer($event.target.value)" style="width:auto;max-width:210px">
        <option v-for="s in servers" :key="s.id" :value="s.id">{{ s.name }}</option>
      </select>
      <button v-else-if="isMobile && servers.length > 1" type="button" class="select picker-trigger topbar-server"
              @click="openPicker({
                title: t('top.switchServer'),
                value: currentId,
                options: servers.map(function (s) { return { value: s.id, label: s.name }; }),
                onPick: (id) => switchServer(id)
              })"><span class="v">{{ serverName() }}</span></button>

      <span class="chip" :title="connError || t('top.connected')">
        <i class="dot" :class="connected ? 'on' : 'err'"></i>
        <span class="hide-sm">{{ connected ? t('top.connected') : t('top.disconnected') }}</span>
      </span>

      <button class="icon-btn" @click="refresh(true)" :disabled="busy" :title="t('top.refresh')">
        <span v-html="ic('refresh')"></span>
      </button>
    </header>
```

关键点：三个元素都在同一个 .topbar flex 容器里，靠 div.spacer 把左侧标题和右侧控件分开。
绿点是 i.dot.on（7px 圆点，background: var(--ok)），外面套 .chip（26px 高、999px 圆角胶囊）。
刷新按钮是 .icon-btn（34×34 方形按钮，内嵌 18px svg）。

### A1-2 日志页主体（index.html 448–469 行）

```html
      <!-- ================= LOGS ================= -->
      <template v-if="view === 'logs' && servers.length">
        <div class="toolbar">
          <div class="seg">
            <button v-for="l in logLevels" :key="l" :class="{ on: logLevel === l }"
                    @click="logLevel = l; loadLogs()">{{ t('logs.level.' + l) }}</button>
          </div>
          <div class="spacer"></div>
          <label class="switch"><input type="checkbox" v-model="logAuto"> {{ t('logs.auto') }}</label>
          <button class="btn sm" @click="loadLogs()">{{ t('logs.refresh') }}</button>
        </div>
        <div class="card" style="max-height:70vh;overflow:auto">
          <div v-for="(l, i) in logs" :key="i" class="logline" :class="l.type">
            <span class="t">
              <span class="d">{{ logDate(l.timestamp) }}</span>
              <span class="h">{{ logTime(l.timestamp) }}</span>
            </span>
            <span class="m">{{ l.message }}</span>
          </div>
          <div class="empty" v-if="!logs.length">{{ logsLoading ? t('logs.loading') : t('logs.empty') }}</div>
        </div>
      </template>
```

结构只有两层容器：.toolbar（筛选行）+ .card（列表卡片）。滚动**不在 .card 上靠 CSS 写死**，
而是在标签上写 style="max-height:70vh;overflow:auto" —— 滚动容器就是卡片本身，列表贴着圆角内边距滚。

对应文案（js/i18n.js 196–203 行中文、683–690 行英文）：

```js
      "logs.level.normal": "常规",
      "logs.level.info": "信息",
      "logs.level.warning": "警告",
      "logs.level.critical": "严重",
      "logs.auto": "自动刷新",
      "logs.refresh": "刷新",
      "logs.empty": "暂无日志",
      "logs.loading": "读取中…",
```

## A2. 全部相关 CSS 原文

### A2-1 工具栏与分段标签（style.css 368–378 行）

```css
/* ---------------- toolbar ---------------- */
.toolbar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: var(--gap); }
.toolbar .input, .toolbar .select { width: auto; }
.seg { display: inline-flex; background: var(--muted); border-radius: var(--radius); padding: 3px; gap: 2px; }
.seg button {
  border: 0; background: none; color: var(--dim); cursor: pointer;
  padding: 0 12px; height: 28px; border-radius: var(--radius-sm);
  font-size: 13px; font-weight: 500; white-space: nowrap;
}
.seg button.on { background: var(--card); color: var(--text); }
.seg button:hover:not(.on) { color: var(--text); }
```

### A2-2 卡片（style.css 304–316 行）

```css
.card {
  background: var(--card); border: 1px solid var(--border);
  border-radius: var(--radius-lg); padding: 16px;
}
.card + .card { margin-top: var(--gap); }
/* A sibling margin must not leak into grids: with align-items:stretch the
   margin box is what gets stretched, so the 2nd card would sit 12px lower and
   end up 12px shorter than the 1st. */
.grid > .card { margin-top: 0; }
.card.flush { padding: 0; overflow: hidden; }
.card h3 { margin: 0 0 12px; font-size: 14px; font-weight: 600; letter-spacing: -.005em; }
.card-head { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
.card-head h3 { margin: 0; }
```

### A2-3 列表行（style.css 786–800 行，含原注释）

这段是日志页视觉的核心，注释里写了两个真实踩过的坑：模板换行被 in-DOM 编译丢掉导致时间粘成
2026-11-0320:44；时间列写死 148px 导致 30–43px 死空。

```css
.logline { font-family: var(--mono); font-size: 12px; padding: 5px 0; border-bottom: 1px solid var(--border-soft); display: flex; gap: 12px; }
/* The timestamp is two spans (.d date / .h time). They used to sit next to each
   other with only a newline between them, and Vue's in-DOM template compiler
   drops whitespace-only text nodes that contain a newline - so the desktop log
   column rendered "2026-11-0320:44" with date and time jammed together. Making
   .t a flex container with an explicit gap puts the space back.
   The column used to be pinned to 148px, but the timestamp only ever measures
   15 mono chars (~105-114px at 12px), so 30-43px of the column was dead space
   sitting between the time and the message - the 12px gap on .logline was
   really ~50px on screen. flex-basis:auto sizes the column to the text, which
   still lines every row up (the timestamp is fixed-width by construction) and
   makes that 12px gap mean 12px again. */
.logline .t { color: var(--faint); flex: 0 0 auto; min-width: calc(15ch + 6px); display: flex; align-items: baseline; gap: 6px; }
.logline.warn .m { color: var(--warn); }
.logline.err .m { color: var(--danger); }
```

### A2-4 手机端列表行（style.css 985–998 行，在 @media (max-width: 640px) 内）

```css
  /* Log entry: timestamp renders as one line "YYYY-MM-DD HH:MM" with the
     message wrapping below - once the message stopped forcing horizontal
     scroll (overflow-wrap:anywhere), the stacked two-line timestamp was
     wasted vertical space. The .d/.h spans stay in the template so the
     desktop fixed-width column layout is untouched. */
  .logline { flex-direction: column; gap: 2px; padding: 8px 0; align-items: stretch; }
  .logline .t {
    flex: 0 0 auto; width: auto;
    display: flex; flex-direction: row; align-items: baseline; gap: 6px;
    font-size: 11px;
  }
  .logline .t .d { color: var(--dim); }
  .logline .t .h { color: var(--faint); }
  .logline .m { word-break: break-word; overflow-wrap: anywhere; }
```

### A2-5 空态（style.css 365–366 行）

```css
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--faint); padding: 32px 16px; font-size: 13px; }
.empty .big { opacity: .35; }
```

### A2-6 状态点 / 胶囊 / 图标按钮 / 顶栏（style.css 335–345、184–196、207–215 行）

```css
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  height: 26px; padding: 0 9px; border-radius: 999px;
  font-size: 12px; font-weight: 500;
  background: var(--muted); color: var(--dim);
}
.chip .dot { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: var(--idle); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--idle); flex: 0 0 7px; }
.dot.on { background: var(--ok); }
.dot.err { background: var(--danger); }
.dot.warn { background: var(--warn); }
```

```css
.icon-btn {
  width: 34px; height: 34px; flex: 0 0 34px;
  display: inline-flex; align-items: center; justify-content: center;
  padding: 0; border-radius: var(--radius);
  border: 1px solid var(--border); background: var(--card);
  color: var(--text); cursor: pointer;
  transition: background .12s, color .12s, border-color .12s;
}
.icon-btn:hover { background: var(--muted); }
.icon-btn:active { transform: translateY(.5px); }
.icon-btn.ghost { border-color: transparent; background: transparent; color: var(--dim); }
.icon-btn.ghost:hover { background: var(--muted); color: var(--text); }
.icon-btn.wide { width: 100%; flex: 1 1 auto; }
```

```css
.topbar {
  display: flex; align-items: center; gap: 10px;
  padding: 10px 18px; min-height: 56px;
  border-bottom: 1px solid var(--border);
  background: var(--bg);
  position: sticky; top: 0; z-index: 20;
}
.topbar .grow { flex: 1; }
.page-title { font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.topbar-server {
  /* hidden by default, the @media blocks below re-show it on small windows
     so the same node is shared between desktop and phone topbars */
}
```

### A2-7 按钮（style.css 224–244 行）

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
```

### A2-8 用到的设计令牌（style.css 7–47 行、49–79 行）

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
  /* Active nav pill inverts with the theme:
     near-black + white text by day, near-white + dark text by night. */
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

## A3. 级别标签页的切换逻辑

**结论：后端拉取，不是前端过滤。** 每切一次级别就带着不同的查询参数重新请求一次日志接口，列表整体被替换。

```js
/* js/app.js 1303-1328 */
      /* ---------------- logs ---------------- */
      resetLogs: function () {
        this.logs = [];
        this._lastLog = 0;
      },
      loadLogs: async function () {
        /* snapshot the server id: a switch can land while this request is in
           flight and the reply would then belong to the previous downloader */
        const sid = this.currentId;
        this.logsLoading = true;
        try {
          const params = {};
          ["normal", "info", "warning", "critical"].forEach((k) => { params[k] = false; });
          params[this.logLevel] = true;
          const arr = await API.qb("api/v2/log/main", { params: params });
          if (sid !== this.currentId) return;
          const order = { critical: "err", warning: "warn", info: "", normal: "" };
          this.logs = (Array.isArray(arr) ? arr : []).map(function (l) {
            return { timestamp: l.timestamp, message: l.message, type: order[l.type] || "" };
          }).reverse();
        } catch (e) {
          if (sid === this.currentId) this.logs = [];
        } finally {
          if (sid === this.currentId) this.logsLoading = false;
        }
      },
```

四档全部先置 false，只把当前档置 true 再发请求 —— 这是 qBittorrent WebAPI api/v2/log/main 的协议形状，
**一次只能取一档**。返回结果 reverse()：接口给的是新→旧，页面要的是旧→新（新的在底部）。

**上色只分三种，不是四种。** order 映射表把四个级别压成三个 CSS 类：

| 接口返回的级别 | 映射后的 l.type | 用的类 | 消息颜色 |
| --- | --- | --- | --- |
| normal | ""（空串） | class="logline" | 继承 --text |
| info | ""（空串） | class="logline" | 继承 --text |
| warning | "warn" | class="logline warn" | var(--warn) = #f59e0b |
| critical | "err" | class="logline err" | var(--danger) = #e7000b |

也就是说「信息」和「常规」在视觉上完全一样，只有警告/严重会上色。这条要照做还是改，需要自己决定：
如果 fail2ban 想让「信息」也有颜色，得新增一条 .logline.info .m。

数据与事件绑定（js/app.js 275–279 行 data、504–514 行 watch、840–850 行 go）：

```js
        logs: [],
        logsLoading: false,
        logLevel: "normal",
        logLevels: ["normal", "info", "warning", "critical"],
        logAuto: false,
```

```js
    watch: {
      logAuto: function (v) { if (v) this.loadLogs(); },
      filter: function () { this.selection = []; },
      /* the logs page reads the *active* downloader through the proxy, so a
         server switch has to drop the old list and pull the new one - keeping
         the previous server's lines on screen was the bug. */
      currentId: function () {
        if (this.view !== "logs") return;
        this.resetLogs();
        this.loadLogs();
      },
```

```js
      go: function (id) {
        this.view = id;
        this.navOpen = false;
        if (id === "logs") this.loadLogs();
        if (id === "settings") {
          this.loadBackupStatus();
          this.loadNotify();
        }
        if (id === "servers") this.testAllServers();
        if (id === "stats" || id === "dash") this.$nextTick(this.drawCharts);
      },
```

## A4. 时间戳的排版

DOM 上是**两个 span**：.d（日期）+ .h（时间），装在 .t 里。桌面端并排一行，手机端也并排但字号降到 11px。

```html
            <span class="t">
              <span class="d">{{ logDate(l.timestamp) }}</span>
              <span class="h">{{ logTime(l.timestamp) }}</span>
            </span>
```

```js
/* js/app.js 597-610 */
      /* Log timestamps render as two stacked spans on mobile (date / time),
         one fixed-width column on desktop. Splitting here keeps the same
         template for both viewports and avoids reassembling the date + time
         pair on the css side. */
      logDate: function (ts) {
        const full = FMT.fmtDate(ts);
        const sp = full.indexOf(" ");
        return sp > 0 ? full.slice(0, sp) : full;
      },
      logTime: function (ts) {
        const full = FMT.fmtDate(ts);
        const sp = full.indexOf(" ");
        return sp > 0 ? full.slice(sp + 1) : "";
      },
```

```js
/* js/util.js 54-61 */
  function fmtDate(unix) {
    const v = Number(unix) || 0;
    if (v <= 0) return "—";
    const d = new Date(v * 1000);
    const p = (x) => String(x).padStart(2, "0");
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
      " " + p(d.getHours()) + ":" + p(d.getMinutes());
  }
```

排版参数逐条对应：

| 项目 | 值 | 出自 |
| --- | --- | --- |
| 字体 | var(--mono) —— 整行都是等宽（.logline 上设的，时间与消息共用） | .logline |
| 字号 | 12px（桌面）；11px（<=640px 时只压 .t） | .logline / .logline .t |
| 颜色 | var(--faint) = #a3a3a3（浅色）/ #737373（深色） | .logline .t |
| 手机端 .d | var(--dim)（比 .h 深一档） | @media 640 |
| 手机端 .h | var(--faint) | @media 640 |
| 宽度 | flex: 0 0 auto; min-width: calc(15ch + 6px) —— 下限锁在 15 个等宽字符，不写死 px | .logline .t |
| 日期与时间之间 | gap: 6px（不能用模板换行，会被 in-DOM 编译丢弃） | .logline .t |
| 时间与消息之间 | .logline 的 gap: 12px | .logline |
| 行内边距 | padding: 5px 0（手机 8px 0） | .logline |
| 分隔线 | border-bottom: 1px solid var(--border-soft)，最后一行也有线（没有 :last-child 去线规则） | .logline |

时间串是 YYYY-MM-DD + 空格 + HH:MM，共 16 字符；15ch 的下限正好覆盖日期部分之外的固定宽度。

## A5. 「自动刷新」开关

**HTML**（就是 label.switch 包一个 checkbox，文字直接写在 label 里）：

```html
          <label class="switch"><input type="checkbox" v-model="logAuto"> {{ t('logs.auto') }}</label>
```

```css
/* style.css 285-288 */
.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
/* .field > label { display:block } would otherwise win over .switch and kill
   the flex layout - which silently drops both the gap and the centring. */
.field > label.switch { display: inline-flex; align-items: center; gap: 8px; margin-bottom: 0; }
```

方框本身是**手绘 checkbox**，不是滑动轨道（style.css 259–281 行）：

```css
/* Checkbox is drawn by hand - the native control with accent-color renders as
   an ambiguous solid square in dark mode and can't follow --primary. */
input[type="checkbox"] {
  appearance: none; -webkit-appearance: none;
  width: 15px; height: 15px; margin: 0; flex: 0 0 auto;
  position: relative; cursor: pointer;
  border: 1px solid var(--border); border-radius: 4px;
  background: transparent;
  transition: background .12s, border-color .12s;
  vertical-align: middle;
}
input[type="checkbox"]:hover { border-color: var(--faint); }
input[type="checkbox"]:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ring) 30%, transparent);
}
input[type="checkbox"]:checked { background: var(--primary); border-color: var(--primary); }
input[type="checkbox"]:checked::after {
  content: ""; position: absolute; left: 4px; top: 1px;
  width: 4px; height: 8px;
  border: solid var(--primary-fg); border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
input[type="file"] { padding: 7px 10px; height: auto; }
input[type="number"] { -moz-appearance: textfield; }
```

自动刷新的**行为**不在开关上，在全局轮询 tick 里（js/app.js 771–783 行）：

```js
      /* ---------------- polling ---------------- */
      tick: function () {
        const now = Date.now();
        const iv = Number(this.settings.refresh_ms) || 2000;
        if (!this._lastPoll || now - this._lastPoll >= iv) {
          this._lastPoll = now;
          this.refresh();
        }
        if (this.logAuto && this.view === "logs" && (!this._lastLog || now - this._lastLog >= 5000)) {
          this._lastLog = now;
          this.loadLogs();
        }
      },
```

即：开关为真 + 当前在日志页 + 距上次拉取 >= 5 秒，才发一次请求。logAuto 的 watcher 在打开开关的瞬间补拉一次。

## A6. 空态与「已连接」状态点

### 空态 HTML（与列表在同一个卡片里，v-if="!logs.length"）

```html
          <div class="empty" v-if="!logs.length">{{ logsLoading ? t('logs.loading') : t('logs.empty') }}</div>
```

文案：加载中「读取中…」，无数据「暂无日志」。

### 空态 CSS

```css
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--faint); padding: 32px 16px; font-size: 13px; }
.empty .big { opacity: .35; }
```

### 「已连接」状态点 HTML

```html
      <span class="chip" :title="connError || t('top.connected')">
        <i class="dot" :class="connected ? 'on' : 'err'"></i>
        <span class="hide-sm">{{ connected ? t('top.connected') : t('top.disconnected') }}</span>
      </span>
```

### 「已连接」状态点 CSS

```css
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  height: 26px; padding: 0 9px; border-radius: 999px;
  font-size: 12px; font-weight: 500;
  background: var(--muted); color: var(--dim);
}
.chip .dot { width: 6px; height: 6px; flex: 0 0 6px; border-radius: 50%; background: var(--idle); }
.dot { width: 7px; height: 7px; border-radius: 50%; background: var(--idle); flex: 0 0 7px; }
.dot.on { background: var(--ok); }
.dot.err { background: var(--danger); }
```

```css
/* style.css 845 —— 窄屏隐藏文字，只留圆点 */
  .hide-sm { display: none !important; }
```

状态点颜色：--ok = #16a34a（浅色）/ #4ade80（深色）；断开用 --danger = #e7000b / #ff6467。
.chip 里没有状态文字的颜色覆盖，文字始终是 var(--dim)，靠左的圆点表达状态。

**改到 fail2ban 里最需要注意什么**：qbpanel 的列表是「卡片即滚动容器 + 每行一条底线」，
.card 的 border-radius: 14px 配 overflow: auto 时最后一行底线会紧贴圆角内缘，视觉上是正常的；
但如果照抄 .card { overflow: hidden } 那条（qbpanel 只在 .card.flush 上才有），滚动条会被裁掉。
另外 .logline .t 的 min-width: calc(15ch + 6px) 依赖整行是等宽字体 —— fail2ban 现有 .log-box 是整块
pre 风格的多行文本，改成逐行 .logline 时请保持 font-family: var(--mono) 挂在 .logline 上（不是挂在 .t 上），
否则 15ch 会按比例字体算宽，时间列对不齐。

---

# B. 通知设置页（设置 → 通知）

**这块是干什么的**：设置页「通知」分区里的三张卡片 —— 通知总开关 + 两个子开关；通知渠道（右上「+ 添加渠道」、
空态铃铛、底部灰字列出支持类型）；投递记录（右上「全部 / 成功 / 失败 / 刷新 / 清空」、中部空态、底部灰字说明存哪）。

设置页本身有个分区工作条（.workbar），三张卡都是 v-if="settingsTab === 'notify'"，.card + .card 让它们自动 12px 间距。

## B1. 卡片一：通知总开关 + 两个子开关（index.html 819–858 行）

```html
        <!-- ---------- notifications ---------- -->
        <div class="card" v-if="settingsTab === 'notify'">
          <div class="card-head">
            <h3>{{ t('notify.title') }}</h3>
          </div>
          <p class="dim" style="margin:0 0 12px">{{ t('notify.desc') }}</p>
          <div class="hint" style="margin:-6px 0 12px" v-if="servers.length">
            {{ t('notify.scopeHint', { n: servers.length }) }}
          </div>
          <div class="swrow">
            <div class="swtext">
              <div class="swname">{{ t('notify.enabled') }}</div>
              <div class="hint">{{ t('notify.enabledHint') }}</div>
            </div>
            <label class="switch">
              <input type="checkbox" :checked="notifyCfg.enabled"
                     @change="saveNotifyFlag('enabled', $event.target.checked)">
            </label>
          </div>
          <div class="swrow">
            <div class="swtext">
              <div class="swname">{{ t('notify.onComplete') }}</div>
              <div class="hint">{{ t('notify.onCompleteHint') }}</div>
            </div>
            <label class="switch">
              <input type="checkbox" :checked="notifyCfg.on_complete"
                     @change="saveNotifyFlag('on_complete', $event.target.checked)">
            </label>
          </div>
          <div class="swrow last">
            <div class="swtext">
              <div class="swname">{{ t('notify.onUpdate') }}</div>
              <div class="hint">{{ t('notify.onUpdateHint') }}</div>
            </div>
            <label class="switch">
              <input type="checkbox" :checked="notifyCfg.on_update"
                     @change="saveNotifyFlag('on_update', $event.target.checked)">
            </label>
          </div>
        </div>
```

注意三个细节：开关用 :checked + @change 而不是 v-model（保存失败时服务端会回写 notifyCfg，v-model 会和它抢）；
最后一行加 .swrow.last 去掉底线；.hint 用负 margin -6px 0 12px 抵掉 .hint 自带的 margin-top: 5px。

## B2. 卡片二：通知渠道（index.html 860–888 行）

```html
        <div class="card" v-if="settingsTab === 'notify'">
          <div class="card-head">
            <h3>{{ t('notify.channels') }}</h3>
            <div class="spacer"></div>
            <button class="btn sm primary" type="button" @click="openChannelForm(null)">
              <span class="ic" v-html="ic('plus')"></span>{{ t('notify.addChannel') }}
            </button>
          </div>
          <div class="empty" v-if="!notifyChannels.length">
            <span class="ic big" style="width:34px;height:34px" v-html="ic('bell')"></span>
            {{ t('notify.empty') }}
          </div>
          <div class="chlist" v-else>
            <div class="chrow" v-for="ch in notifyChannels" :key="ch.id">
              <span class="chico" v-html="ic('bell')"></span>
              <div class="chin">
                <div class="chname ellipsis">{{ ch.name }}</div>
                <div class="chmeta">{{ notifyTypeLabel(ch.type) }} · {{ ch.enabled ? t('notify.stateOn') : t('notify.stateOff') }}</div>
              </div>
              <div class="spacer"></div>
              <button class="btn sm" type="button" :disabled="testingChannel === ch.id" @click="testChannel(ch)">
                {{ testingChannel === ch.id ? t('notify.testing') : t('notify.test') }}
              </button>
              <button class="btn sm" type="button" @click="openChannelForm(ch)">{{ t('notify.edit') }}</button>
              <button class="btn sm danger" type="button" @click="removeChannel(ch)">{{ t('notify.remove') }}</button>
            </div>
          </div>
          <div class="hint" style="margin-top:12px">{{ t('notify.channelHint') }}</div>
        </div>
```

## B3. 卡片三：投递记录（index.html 890–936 行）

```html
        <div class="card" v-if="settingsTab === 'notify'">
          <div class="card-head nlog-head">
            <h3>{{ t('notifyLog.title') }}</h3>
            <div class="spacer"></div>
            <div class="seg nlog-seg">
              <button type="button" :class="{ on: notifyLogFilter === 'all' }"
                      @click="notifyLogFilter = 'all'">{{ t('notifyLog.filter.all') }}</button>
              <button type="button" :class="{ on: notifyLogFilter === 'ok' }"
                      @click="notifyLogFilter = 'ok'">{{ t('notifyLog.filter.ok') }}</button>
              <button type="button" :class="{ on: notifyLogFilter === 'fail' }"
                      @click="notifyLogFilter = 'fail'">{{ t('notifyLog.filter.fail') }}<span
                      class="nlogbadge" v-if="notifyLogFailed">{{ notifyLogFailed }}</span></button>
            </div>
            <button class="btn sm" type="button" :disabled="notifyLogLoading" @click="loadNotifyLog()">
              {{ t('notifyLog.refresh') }}
            </button>
            <button class="btn sm danger" type="button" :disabled="clearingNotifyLog || !notifyLog.length"
                    @click="askClearNotifyLog()">{{ t('notifyLog.clear') }}</button>
          </div>
          <p class="dim" style="margin:0 0 12px">{{ t('notifyLog.desc', { n: notifyLogMax }) }}</p>
          <div class="empty" v-if="!notifyLogShown.length">
            {{ notifyLogLoading ? t('notifyLog.loading')
              : (notifyLog.length ? t('notifyLog.noneMatch') : t('notifyLog.empty')) }}
          </div>
          <div class="nlog" v-else ref="notifyLogList"
               :style="notifyLogHeight ? { maxHeight: notifyLogHeight + 'px' } : null">
            <div class="nlogrow" v-for="r in notifyLogShown" :key="r.id">
              <span class="nlogstate" :class="r.ok ? 'ok' : 'err'">{{ r.ok ? t('notifyLog.ok') : t('notifyLog.fail') }}</span>
              <div class="nlogin">
                <div class="nlogtop">
                  <span class="nlogtitle ellipsis">{{ r.title }}</span>
                  <span class="nlogtag">{{ notifyEventLabel(r.event) }}</span>
                </div>
                <div class="nlogbody">{{ r.body }}</div>
                <div class="nlogmeta">
                  <span class="ellipsis" v-if="r.server">{{ r.server }}</span>
                  <span class="sep" v-if="r.server">·</span>
                  <span class="ellipsis">{{ r.channel }}</span>
                  <span class="sep">·</span>
                  <span>{{ fmt.fmtDate(r.at) }}</span>
                </div>
                <div class="nlogerr" v-if="!r.ok && r.detail">{{ r.detail }}</div>
              </div>
            </div>
          </div>
          <div class="hint" style="margin-top:12px">{{ t('notifyLog.hint') }}</div>
        </div>
```

## B4. 全部相关 CSS 原文

### B4-1 卡片头 / 开关行 / 渠道行（style.css 640–663 行）

```css
/* ---------------- settings switch rows / channels ---------------- */
.swrow {
  display: flex; align-items: center; gap: 14px;
  padding: 12px 0; border-bottom: 1px solid var(--border-soft);
}
.swrow.last { border-bottom: 0; padding-bottom: 2px; }
.swtext { min-width: 0; flex: 1; }
.swname { font-size: 13.5px; font-weight: 500; }
.swrow .hint { margin-top: 2px; }
.swrow .switch { margin-left: auto; flex: 0 0 auto; }

.chlist { display: flex; flex-direction: column; gap: 8px; }
.chrow {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius);
}
.chico {
  flex: 0 0 30px; width: 30px; height: 30px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: var(--muted); color: var(--dim);
}
.chin { min-width: 0; }
.chname { font-size: 13.5px; font-weight: 600; }
.chmeta { font-size: 12px; color: var(--faint); margin-top: 1px; }
```

### B4-2 投递记录（style.css 665–707 行）

```css
/* ---- delivery records: one row per push attempt ---- */
.nlog-head { flex-wrap: wrap; }
.nlogbadge {
  margin-left: 5px; font-size: 10.5px; font-weight: 600; line-height: 15px;
  padding: 0 5px; border-radius: 999px;
  background: rgba(231, 0, 11, .12); color: var(--danger);
}
.nlog-seg { flex: 0 0 auto; }
/* 高度由 app.js 的 measureNotifyLog() 按前 5 行实测值写进 inline style，
   这里只留兜底值（脚本还没量到时）并接管滚动 */
.nlog { display: flex; flex-direction: column; gap: 8px; max-height: 420px;
  overflow-y: auto; overscroll-behavior: contain; }
.nlogrow {
  display: flex; align-items: flex-start; gap: 10px;
  padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius);
}
.nlogstate {
  flex: 0 0 auto; font-size: 11px; font-weight: 600; line-height: 18px;
  padding: 0 8px; border-radius: 999px; white-space: nowrap;
  background: var(--muted); color: var(--dim);
}
.nlogstate.ok { background: rgba(22, 163, 74, .12); color: var(--ok); }
.nlogstate.err { background: rgba(231, 0, 11, .10); color: var(--danger); }
.nlogin { min-width: 0; flex: 1; display: flex; flex-direction: column; gap: 3px; }
.nlogtop { display: flex; align-items: center; gap: 8px; min-width: 0; }
.nlogtitle { font-size: 13.5px; font-weight: 600; min-width: 0; }
.nlogtag {
  flex: 0 0 auto; font-size: 11px; color: var(--dim);
  background: var(--muted); border-radius: 999px; padding: 0 7px; line-height: 17px;
}
.nlogbody {
  font-size: 12.5px; color: var(--dim); white-space: pre-wrap; word-break: break-word;
  display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
}
.nlogmeta {
  font-size: 11.5px; color: var(--faint); display: flex; align-items: center;
  gap: 6px; min-width: 0;
}
.nlogmeta .sep { opacity: .6; }
.nlogerr {
  font-size: 11.5px; color: var(--danger); font-family: var(--mono);
  word-break: break-word; margin-top: 2px;
}
```

### B4-3 卡片 / 卡片头 / 空态 / 灰字 / 工具类（style.css 304–316、364–366、555、357–360 行）

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
```

```css
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--faint); padding: 32px 16px; font-size: 13px; }
.empty .big { opacity: .35; }
```

```css
.hint { font-size: 12px; color: var(--faint); margin-top: 5px; }
```

```css
.dim { color: var(--dim); }
.ellipsis { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.spacer { flex: 1; }
.sep { height: 1px; background: var(--border-soft); margin: 14px 0; }
```

### B4-4 卡片头与渠道行的手机端重排（style.css 950–961 行）

```css
  .chrow .btn { flex: 1 1 auto; }
  .chrow { gap: 6px; }
  .chrow .spacer { display: none; }
  .chico { order: 1; }
  .chin { order: 2; flex: 1 1 calc(100% - 46px); }
  .chrow .btn { order: 3; }

  /* Delivery records on a phone: filters take a full row of their own so the
     refresh / clear pair keeps its tap size. */
  .nlog-head .nlog-seg { order: 5; flex: 1 1 100%; }
  .nlog-head .nlog-seg button { flex: 1; }
  .nlogrow { gap: 8px; padding: 9px 10px; }
```

## B5. 开关（switch）—— HTML + CSS 完整原文

**HTML 结构**（两种写法，效果一致）：

```html
<!-- 带文字的独立开关（日志页自动刷新） -->
<label class="switch"><input type="checkbox" v-model="logAuto"> 自动刷新</label>

<!-- 纯方框、文案在外面（设置页每一行） -->
<label class="switch">
  <input type="checkbox" :checked="notifyCfg.enabled"
         @change="saveNotifyFlag('enabled', $event.target.checked)">
</label>
```

**CSS 完整原文**（.switch 与手绘 checkbox，两段都要抄）：

```css
.switch { display: inline-flex; align-items: center; gap: 8px; cursor: pointer; font-size: 14px; }
/* .field > label { display:block } would otherwise win over .switch and kill
   the flex layout - which silently drops both the gap and the centring. */
.field > label.switch { display: inline-flex; align-items: center; gap: 8px; margin-bottom: 0; }
```

```css
/* Checkbox is drawn by hand - the native control with accent-color renders as
   an ambiguous solid square in dark mode and can't follow --primary. */
input[type="checkbox"] {
  appearance: none; -webkit-appearance: none;
  width: 15px; height: 15px; margin: 0; flex: 0 0 auto;
  position: relative; cursor: pointer;
  border: 1px solid var(--border); border-radius: 4px;
  background: transparent;
  transition: background .12s, border-color .12s;
  vertical-align: middle;
}
input[type="checkbox"]:hover { border-color: var(--faint); }
input[type="checkbox"]:focus-visible {
  outline: none;
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--ring) 30%, transparent);
}
input[type="checkbox"]:checked { background: var(--primary); border-color: var(--primary); }
input[type="checkbox"]:checked::after {
  content: ""; position: absolute; left: 4px; top: 1px;
  width: 4px; height: 8px;
  border: solid var(--primary-fg); border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
input[type="file"] { padding: 7px 10px; height: auto; }
input[type="number"] { -moz-appearance: textfield; }
```

配套的 button, input, select, textarea { font: inherit; color: inherit; }（style.css 105 行）也要有，
否则浏览器默认字体不会跟着走。

**这是方框勾选，不是滑动轨道。** 想做成滑轨需要另写 40×22 的 .switch-track（fail2ban 现在就是滑轨）。
两者不能混用：.swrow .switch { margin-left: auto } 只负责把控件推到行尾，宽度由控件自己决定。

## B6. 空态占位（图标 + 文案）HTML/CSS 原文

```html
          <div class="empty" v-if="!notifyChannels.length">
            <span class="ic big" style="width:34px;height:34px" v-html="ic('bell')"></span>
            {{ t('notify.empty') }}
          </div>
```

```css
.empty { display: flex; flex-direction: column; align-items: center; gap: 8px; text-align: center; color: var(--faint); padding: 32px 16px; font-size: 13px; }
.empty .big { opacity: .35; }
```

铃铛图标不是图片，是内联 SVG 字符串（js/app.js 9–12、40 行）：

```js
  /* ---------- inline svg icons (lucide-style, 1.8px stroke) ---------- */
  const S = 'fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"';
  const svg = (body, size) =>
    '<svg width="' + (size || 18) + '" height="' + (size || 18) + '" viewBox="0 0 24 24" ' + S + '>' + body + '</svg>';
```

```js
    bell: svg('<path d="M6 8a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10.3 20a2 2 0 0 0 3.4 0"/>', 18),
    plus: svg('<path d="M12 5v14M5 12h14"/>', 16),
```

文案（js/i18n.js 353、354 行）：

```js
      "notify.empty": "还没有通知渠道，添加一个后就能收到提醒",
      "notify.channelHint": "支持 Bark、Server 酱、PushPlus、WxPusher、Telegram、企业微信机器人、钉钉机器人、Slack 与自定义 Webhook；密钥只保存在设备本地，不会回显。",
```

底部灰字就是 div.hint style="margin-top:12px"，.hint 是 12px + var(--faint)。
注意 .hint 自带 margin-top: 5px，所以这里用 inline style 覆盖成 12px。

## B7. 一排小按钮（segmented 风格：全部 / 成功 / 失败）

**HTML**（.seg 是纯前端 v-if 过滤，不发请求）：

```html
            <div class="seg nlog-seg">
              <button type="button" :class="{ on: notifyLogFilter === 'all' }"
                      @click="notifyLogFilter = 'all'">{{ t('notifyLog.filter.all') }}</button>
              <button type="button" :class="{ on: notifyLogFilter === 'ok' }"
                      @click="notifyLogFilter = 'ok'">{{ t('notifyLog.filter.ok') }}</button>
              <button type="button" :class="{ on: notifyLogFilter === 'fail' }"
                      @click="notifyLogFilter = 'fail'">{{ t('notifyLog.filter.fail') }}<span
                      class="nlogbadge" v-if="notifyLogFailed">{{ notifyLogFailed }}</span></button>
            </div>
```

**CSS 原文**：

```css
.seg { display: inline-flex; background: var(--muted); border-radius: var(--radius); padding: 3px; gap: 2px; }
.seg button {
  border: 0; background: none; color: var(--dim); cursor: pointer;
  padding: 0 12px; height: 28px; border-radius: var(--radius-sm);
  font-size: 13px; font-weight: 500; white-space: nowrap;
}
.seg button.on { background: var(--card); color: var(--text); }
.seg button:hover:not(.on) { color: var(--text); }
```

```css
.nlog-head { flex-wrap: wrap; }
.nlog-seg { flex: 0 0 auto; }
.nlogbadge {
  margin-left: 5px; font-size: 10.5px; font-weight: 600; line-height: 15px;
  padding: 0 5px; border-radius: 999px;
  background: rgba(231, 0, 11, .12); color: var(--danger);
}
```

**过滤逻辑**（js/app.js 446–454 行，纯 computed，前端过滤）：

```js
      notifyLogShown: function () {
        const f = this.notifyLogFilter;
        if (f === "ok") return this.notifyLog.filter(function (r) { return r.ok; });
        if (f === "fail") return this.notifyLog.filter(function (r) { return !r.ok; });
        return this.notifyLog;
      },
      notifyLogFailed: function () {
        return this.notifyLog.filter(function (r) { return !r.ok; }).length;
      },
```

三个筛选按钮旁边紧跟两个 .btn.sm（刷新 + 清空），清空按钮是 .btn.sm.danger。
分隔不是靠 .sep 元素，是靠 .card-head 的 gap: 10px。

**改到 fail2ban 里最需要注意什么**：卡片头这一行在桌面端是「标题 + spacer + seg + 两个按钮」四个 flex 项挤在一行，
手机端靠 .nlog-head { flex-wrap: wrap } + .nlog-seg { flex: 1 1 100% } 让筛选独占一行 —— 这两条必须成对写，
只写 wrap 不写 flex: 1 1 100% 的话按钮组仍会被塞在一行里挤扁（flex 项不会因为零高等占位自动断行）。
另外 fail2ban 现有的分段控件叫 .segmented，按钮高 28px 没问题，但它带了 margin-bottom: 10px
和 overflow-x: auto，直接放进 .card-head 会把整行往下推 10px，需要单独覆盖。

---

# C. 字体

**这块是干什么的**：qBpanel 的整套字体设定 —— 两个字体栈变量、body 上的渲染开关、以及各组件上的字号覆盖量。

## C1. --sans / --mono 完整值

```css
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue",
    "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
```

--sans 先西文系统字体（-apple-system → Segoe UI → Roboto），后中文（PingFang SC → Hiragino Sans GB →
Microsoft YaHei → Noto Sans SC），最后 sans-serif 兜底。--mono 是标准等宽栈，不含中文字体
（中文字符会落到浏览器默认等宽字体）。

## C2. body 上的字体相关属性

```css
html, body {
  margin: 0;
  padding: 0;
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
```

- font-size: 14px —— 全站基准
- line-height: 1.55 —— 无单位，随字号缩放
- -webkit-font-smoothing: antialiased —— 只影响 macOS / iOS，把次像素抗锯齿关掉
- text-rendering: optimizeLegibility —— 开连字与字距微调
- **没有** letter-spacing 在 body 上（只有局部组件用负值收字距）
- **没有** font-weight 在 body 上

还有一条容易被忽略的继承规则（style.css 105 行）：

```css
button, input, select, textarea { font: inherit; color: inherit; }
```

font: inherit 是简写，会同时重置 font-size / font-weight / line-height / font-family 到继承值。
没有这条，button 会退回浏览器默认的 13.33px Arial。

## C3. 额外的字体渲染微调

- text-rendering: optimizeLegibility（body，见上）
- -webkit-font-smoothing: antialiased（body，见上）
- font-variant-numeric: tabular-nums —— 只加在需要数字对齐的地方，共 6 处：

```css
/* 1. 概览大数字 style.css:330 */
.stat .v { font-size: 22px; font-weight: 600; letter-spacing: -.02em; font-variant-numeric: tabular-nums; line-height: 1.2; }

/* 2. 存储总量 style.css:490 */
.storage-total b { font-size: 19px; font-variant-numeric: tabular-nums; }

/* 3. 表格数字列 style.css:492 */
.ttable .num { font-variant-numeric: tabular-nums; text-align: right; white-space: nowrap; }

/* 4. 键值表 style.css:568 */
.kv dd { margin: 0; word-break: break-all; font-variant-numeric: tabular-nums; }

/* 5. 版本号大标题 style.css:609-612 */
.vv {
  margin-top: 4px; font-size: 26px; font-weight: 600; letter-spacing: -.02em;
  font-variant-numeric: tabular-nums;
}

/* 6. 更新进度数字 style.css:755-761 */
.upmeta {
  display: flex; justify-content: center; align-items: baseline; gap: 10px;
  margin-top: 7px; font-size: 11.5px; color: var(--faint);
  font-variant-numeric: tabular-nums;
  width: 100%; min-height: 17px;
}
```

- **没有** font-feature-settings
- **没有** font-variant-ligatures / font-optical-sizing / font-synthesis
- 手机端禁止长按选中（style.css 1000–1006 行），表单控件单独放行：

```css
  /* On a phone the panel reads as an app, so a long press must not raise the
     native selection / copy-share callout. Form controls opt back in - editing
     a server address or the search box still needs selection. */
  body { -webkit-user-select: none; user-select: none; -webkit-touch-callout: none; }
  input, textarea, select, [contenteditable="true"] {
    -webkit-user-select: text; user-select: text; -webkit-touch-callout: default;
  }
```

## C4. 各组件的字号覆盖清单

| 元素 / 选择器 | 字号 | 字重 | 其他 | 出处行 |
| --- | --- | --- | --- | --- |
| body（基准） | 14px | 继承 400 | line-height: 1.55 | 90–101 |
| .page-title 页面标题 | 15px | 600 | letter-spacing: -.01em | 215 |
| .card h3 卡片标题 | 14px | 600 | letter-spacing: -.005em | 314 |
| .nav-item 侧栏项 | 14px | 600 | — | 132–140 |
| .nav-item .badge 角标 | 11px | 600 | line-height: 1 | 147–152 |
| .stat .v 概览大数字 | 22px | 600 | -.02em, tabular-nums, line-height: 1.2 | 330 |
| .stat .k 数字标签 | 13px | 500 | — | 328 |
| .stat .s 数字副标题 | 12px | — | — | 333 |
| .vv 版本号大标题 | 26px | 600 | -.02em, tabular-nums | 609–612 |
| .vk 版本号小标签 | 12px | — | — | 608 |
| .vstext b 状态条主文 | 13.5px | 600 | — | 635 |
| .vstext span 状态条副文 | 12px | — | — | 636 |
| .btn 按钮 | 14px | 500 | — | 224–232 |
| .btn.sm 小按钮 | 13px | 500 | height: 30px | 242 |
| .seg button 分段标签 | 13px | 500 | height: 28px | 372–376 |
| .workbar button 设置分区条 | 13px | 500 | height: 30px | 393–398 |
| .chip 状态胶囊 | 12px | 500 | height: 26px | 335–340 |
| .mono 等宽工具类 | 12px | — | font-family: var(--mono) | 356 |
| .logline 日志行 | 12px | — | font-family: var(--mono) | 786 |
| .logline .t 手机端时间 | 11px | — | 仅 <=640px | 991–995 |
| .ttable th 表头 | 12px | 500 | var(--faint) | 417–422 |
| .ttable .name 表格主名 | 14px | 500 | — | 430 |
| .ttable .pmeta 表格副文 | 11px | — | white-space: nowrap | 433 |
| .ttable .sub 表格副行 | 12px | — | — | 465 |
| .hint 灰字提示 | 12px | — | margin-top: 5px, var(--faint) | 555 |
| .empty 空态 | 13px | — | var(--faint) | 365 |
| .field > label 表单标签 | 13px | 500 | var(--dim) | 552 |
| .swname 开关行主文 | 13.5px | 500 | — | 647 |
| .chname 渠道名 | 13.5px | 600 | — | 662 |
| .chmeta 渠道副文 | 12px | — | var(--faint) | 663 |
| .nlogtitle 记录标题 | 13.5px | 600 | — | 690 |
| .nlogbody 记录正文 | 12.5px | — | 3 行截断 | 695–698 |
| .nlogstate 成功/失败徽章 | 11px | 600 | line-height: 18px | 681–685 |
| .nlogtag 事件标签 | 11px | — | line-height: 17px | 691–694 |
| .nlogmeta 记录元信息 | 11.5px | — | var(--faint) | 699–702 |
| .nlogerr 失败原因 | 11.5px | — | font-family: var(--mono) | 704–707 |
| .nlogbadge 失败计数角标 | 10.5px | 600 | line-height: 15px | 667–671 |
| .toast 浮层提示 | 13px | — | — | 778–782 |
| .ver-line 侧栏版本行 | 12px | 500 | — | 198–202 |
| .legend 图表图例 | 12px | — | var(--dim) | 805 |
| .tcard .sub 手机端卡片副文 | 12px | — | — | 811 |
| .modal-head 弹窗标题 | 15px | 600 | — | 544 |

**改到 fail2ban 里最需要注意什么**：fail2ban 现在的 --mono 多了 "SF Mono"、--sans 缩进横跨两行，
值和 qbpanel 基本同源，可以直接沿用，不必逐字替换。真正要注意的是三件事：
① button, input, select, textarea { font: inherit } 这条继承规则必须有，否则手写按钮会掉到 13.33px Arial；
② qbpanel 的 .mono 是 12px，fail2ban 的是 12.5px —— 日志页要照抄的是 .logline 的 12px（整行等宽），
不是 .mono 工具类，两者混用会让时间列和消息列字体不一致；
③ .card h3 的 letter-spacing: -.005em 和 .page-title 的 -.01em 是负值收字距，中文标题会略紧，
照抄时可以保留，但别给正文也加负字距。
