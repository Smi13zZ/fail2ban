# fn-fail2ban 界面升级规范（v26.11.0）

基线：生产机 `fnos-prod` 上运行的 Fail2ban 防护 1.7.0（已完整提取到
`fn-fail2ban-extract/`，本项目 `src/pkg/fn-fail2ban/` 是它的拷贝）。

目标：
1. 视觉体系换成 qbpanel 的设计语言（配色 / 字体 / 圆角 / 间距 / 组件形态）。
2. 补全缺失的实用功能。
3. **能在生产机上原地升级，不改动任何既有配置**。

参考实现：`D:\\Workspace\\DeepSeek Harness\\qbpanel-maintenance\\src\\qbpanel\\qbpanel\\app\\web\\`
（`style.css` 设计令牌与组件形态、`index.html` 的 shell 结构）。

---

## 一、硬约束（不可违反）

| 项 | 要求 |
| --- | --- |
| appname | `fn-fail2ban`（不变） |
| 服务端口 | `18752`（不变） |
| 桌面入口 | `fn-fail2ban.main`、gatewayPrefix `/app/fn-fail2ban`、gatewaySocket `app.sock`（不变） |
| 配置文件 | 升级过程中**不得**改写 `/etc/fail2ban/jail.d/50-fn-fail2ban.local` 里已有的键与值 |
| 面板凭证 | 升级**不得**调用 `--set-credentials`，不得动 `@appconf/fn-fail2ban/credentials.json` |
| 数据目录 | `@appdata/fn-fail2ban/*`（server.json / prefs / notifications / fnknock / custom-filters.list）读取方式不变、键名不变 |
| 前端资源 | 仍是零构建静态资源，无 CDN、无 npm 依赖，只允许原生 JS + CSS |
| 后端 | 只用 Python 标准库 |

版本号：`1.7.0` → `26.11.0`（遵循全局 `年.月.序号` 规则，本条产品线首个版本）。

---

## 二、设计令牌契约（CSS）

qbpanel 的令牌为准，fail2ban 原有的语义名作为**别名**保留（前端代码里大量引用，
不做重命名以免引入回归）：

```css
:root {
  --radius: 10px; --radius-sm: 6px; --radius-lg: 14px; --gap: 12px;
  --bg: #ffffff; --sidebar: #fafafa; --card: #ffffff;
  --muted: #f5f5f5; --hover: #f5f5f5;
  --border: #e5e5e5; --border-soft: #efefef;
  --text: #0a0a0a; --dim: #737373; --faint: #a3a3a3;
  --primary: #1c1c1c; --primary-fg: #fafafa;
  --nav-active: #1c1c1c; --nav-active-fg: #fafafa;
  --accent-bar: #2563eb; --ring: #a3a3a3;
  --danger: #e7000b; --danger-soft: #fef2f2;
  --ok: #16a34a; --warn: #f59e0b; --idle: #a3a3a3;
  --shadow-pop: 0 8px 24px -8px rgba(0,0,0,.18);
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  --sans: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue",
          "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans SC", sans-serif;
  color-scheme: light;
}
[data-theme="dark"] { /* qbpanel 深色一组，见参考文件第 49-79 行 */ }
```

**别名（必须保留，值指向新令牌）**

| 旧名 | 新值 | 说明 |
| --- | --- | --- |
| `--panel` | `var(--card)` | 卡片/侧栏底色 |
| `--panel-2` | `var(--muted)` | 次级填充 |
| `--bg-soft` | `var(--muted)` | 输入框/抽屉底 |
| `--accent` | `var(--primary)` | 主按钮、选中态（qbpanel 是反色黑/白，不再是蓝色） |
| `--accent-soft` | `color-mix(in srgb, var(--text) 8%, transparent)` | 选中底 |
| `--ok-soft` / `--warn-soft` / `--danger-soft` | 对应 `color-mix` 10-12% 淡底 | 沿用 fail2ban 语义 |
| `--shadow` | `var(--shadow-pop)` | 抽屉/弹层 |

字体：`body` 用 `var(--sans)`，14px / 1.55，`-webkit-font-smoothing: antialiased`；
等宽用 `var(--mono)`。字重只用 400/500/600。

---

## 三、组件契约

**既有类名一律保留**（`.card .stat .btn .badge .dot .kv .field .help .hint .input
.table-wrap .segmented .drawer .drawer-mask .toast .toast-host .spinner .banner
.ip-list .ip-chip .log-box .event-list .event-item .section-title .row-actions
.sidebar .sidebar-head .nav .sidebar-foot .scrim .content .topbar .page-head
.icon-btn .empty .toolbar .view-head .boot .boot-card`），**只改样式，不改语义**。

新增类（本次改造要用）：

| 类名 | 用途 |
| --- | --- |
| `.app.collapsed` | 侧栏收起为 62px 图标轨道 |
| `.foot-row` | 侧栏底部按钮行（主题 / 退出并排） |
| `.ver-line` | 侧栏底部版本号一行（mono，12px，--dim） |
| `.collapse-btn` | 收起/展开按钮（宽 100%） |
| `.cell-actions` | 表格行内操作区，默认 `opacity:.55`，行 hover/选中时 1 |
| `.copy-btn` | 行内复制小按钮（icon-btn 尺寸 28px） |
| `.row-check` | 表格里的手绘复选框单元格（宽 36px） |
| `th.sortable` | 可排序表头，hover 变 `--text`，`::after` 绘制 ▲▼ 指示 |
| `.toolbar-search` | 工具栏搜索框，宽 240px（窄屏 100%） |
| `.ban-expiry` | 封禁到期时间，`--dim` + tabular-nums |

视觉要点（对齐 qbpanel）：
- 卡片扁平无阴影，1px `--border`，`--radius-lg`
- 主按钮：`--primary` 底 + `--primary-fg` 字，hover `opacity:.88`
- 危险按钮：透明底 + 红字，hover 淡红底
- 侧栏激活项：整行反色药丸（`--nav-active`），图标描边 2.1
- 表格：表头 12px `--faint`，行 hover `--muted`，最后一行无下边框
- 输入框 36px 高，`--card` 底 + `--border`，focus 用 `--ring` 的 3px 环
- 手绘复选框（不用原生 `accent-color`）
- `:focus-visible` 2px `--accent-bar` 轮廓
- 手机 ≤900px：侧栏改抽屉 + scrim，触控目标 ≥44px
- `prefers-reduced-motion` 降级

---

## 四、本次补全的功能

### 后端（新增/修改）

1. **`app/bin/banlog.py`（新文件）**
   解析 `/var/log/fail2ban.log`（以及 `-*` 轮转的当前文件）里的
   `Ban <ip>` / `Unban <ip>` / `Restore Ban <ip>` 行，得到
   `{jail: {ip: {"bannedAt": ts, "lastSeen": ts}}}`。
   以文件 `(mtime, size)` 做缓存，避免每次请求全量扫描；文件不存在或不可读时
   返回空结构且不抛异常（面板不能因为这个功能坏掉）。

2. **`GET /api/bans` 增加字段**
   ```
   entries: [{ ip, jails[], bannedAt?: epoch秒, expiresAt?: epoch秒 }]
   jails:   [{ name, ok, count, error? }]
   ```
   `expiresAt` = `bannedAt + 该 Jail 当前 bantime 秒数`（bantime 从
   `jailconf` 解析；解析不出来就不给这个字段）。取不到时间的 IP 不给字段，
   前端显示 `—`。

3. **`GET /api/jails/<name>` 的 `status` 增加同名字段**（单 Jail 视图复用）。

4. **`GET /api/jails/<name>/log?lines=200`（新增）**
   读取该 Jail 配置里 `logpath` 指向的日志文件尾部（最多 2000 行），
   返回 `{files: [...], lines: [...], truncated: bool}`。
   安全约束：只允许读取 `logpath` 中**存在的绝对路径**，必须
   `os.path.realpath` 后仍是普通文件、大小 ≤ 32MB；不允许多于 8 个文件；
   任何越界或不存在都返回 400/404 而不是 500。只读，不写任何东西。

5. `APP_VERSION` 改 `26.11.0`；`manifest` 的 `version` 改 `26.11.0` 并追加
   `changelog` 条目（本次升级内容，写在最前面）。`--bootstrap` 保持
   `setdefault` 语义（不得覆盖已有键），`upgrade_callback` 保持只补缺失项。

### 前端

| 功能 | 说明 |
| --- | --- |
| 侧栏收起 | 底部按钮切换，状态存 `localStorage['f2b-collapsed']`，刷新后保持 |
| 封禁列表增强 | 搜索框、按 IP 排序、复制 IP、显示「封禁时间 / 到期时间 / 剩余」 |
| 批量解封 | 汇总视图里勾选多个 IP 一次解封，带确认 |
| Jail 列表排序 | 点表头按名称/当前封禁/累计封禁/失败次数排序 |
| 单 Jail 日志 | 抽屉里看该 Jail 的 logpath 尾部日志 |
| 自动刷新 | 概览页 30s 轮询 + 页面重新可见时补刷（`visibilitychange`） |
| 白名单编辑 | 全局设置的 ignoreip 改成「输入框 + 已添加 Chip 列表」，可单条删除 |
| 键盘 | `/` 聚焦当前页搜索框；`Esc` 关抽屉（已有，保留） |

未做（明确不做）：版本自检/在线升级（本应用无 GitHub 仓库，属上游能力）、
自动改系统防火墙策略（超出应用职责）。

---

## 五、验收

1. `python3 -m py_compile` 全部通过；`server.py --self-check` 正常。
2. `fnpack build` 通过，包内权限正确（`cmd/*` 0755）。
3. `fnos-test` 上**先装 1.7.0 并配置好（改 bantime、加白名单、设管理员密码）**，
   再原地升级到 26.11.0，验证：
   - `@appdata` 下 `server.json / prefs.json / notifications.json / fnknock.json` 内容未变
   - `credentials.json` 未变（原密码仍能登录）
   - `/etc/fail2ban/jail.d/50-fn-fail2ban.local` 未变（逐字节哈希一致）
   - 端口仍是 18752，桌面入口仍是 `fn-fail2ban.main`
   - 面板功能逐页可用（概览 / Jail / 封禁 / 过滤器 / 通知 / fnknock / 设置 / 日志）
4. 截图：浅色 + 深色、桌面 + 手机宽度各一张。
