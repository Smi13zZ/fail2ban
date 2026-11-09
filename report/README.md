# fn-fail2ban 26.11.0 · 界面升级 + 功能补全

从生产机在用的 1.7.0 升级到 26.11.0：视觉体系换成 qbpanel 的设计语言，
补全 8 项实用功能。**升级不触碰任何现有配置**。

## 交付物

| 文件 | 说明 |
| --- | --- |
| `output/fn-fail2ban-26.11.0-all.fpk` | 可安装包（141,598 字节，已在 fnos-test 实装验证） |
| `src/pkg/fn-fail2ban/` | 打包源（35 个文件） |
| `tools/build_fpk.py` | 一键重建（`python tools/build_fpk.py --host fnos-test`） |
| `report/SPEC.md` | 改造规范：硬约束、设计令牌、组件契约、功能清单 |
| `image/final-desktop.png`、`image/final-phone.png` | 实机截图（1440px / 390px） |
| `image/style/views/` | 32 组合矩阵截图（8 视图 × 2 宽度 × 2 主题，不进包） |

## 为什么"升级不改配置"是有保证的

不是靠承诺，是靠**文件层面的事实**。基线 1.7.0 与本版逐字节比对：

```
cmd     : 完全一致 ✓   ← 生命周期脚本（install/upgrade/uninstall 全在这里）
wizard  : 完全一致 ✓   ← 安装向导（决定首次装的默认值）
config  : 完全一致 ✓   ← privilege / resource
```

改动集只有 5 个文件：`manifest`、`app/bin/server.py`、`app/bin/banlog.py`(新增)、
`app/web/{app.js,index.html,style.css}`。**没有任何一个文件参与配置写入路径。**

三重保险：
1. `cmd/*` 未变 → 升级回调仍是 `--bootstrap`，而 `jailconf.bootstrap()` 用的是
   `setdefault` 语义，**只补缺失键，绝不覆盖已存在的键**
2. `upgrade_callback` 只做权限修复 + 一次 bootstrap，不调用 `--set-credentials`
3. 数据卷（`@appdata` / `@appconf`）在升级中不参与覆盖

实测证据（测试机装 1.7.0，改策略、加白名单、设密码、建自定义 Jail 之后升级）：

| 文件 | 升级前 | 升级后 |
| --- | --- | --- |
| `/etc/fail2ban/jail.d/50-fn-fail2ban.local` | eb9104cc… | eb9104cc… 一致 |
| `@appdata/prefs.json` | 0d163fe9… | 0d163fe9… 一致 |
| `@appdata/notifications.json` | c0a2256f… | c0a2256f… 一致 |
| `@appdata/backup.json` | 28e3574f… | 28e3574f… 一致 |

## 界面改造

设计令牌整套换成 qbpanel 的（浅色 + 深色两组），主色从蓝色改为**反色**（近黑/近白），
选中导航项是整行反色药丸。字体、圆角、间距、卡片形态、按钮、表格、输入框、
手绘复选框、徽章、抽屉、toast 全部对齐。

9 个旧语义令牌（`--panel` / `--panel-2` / `--bg-soft` / `--accent` /
`--accent-soft` / `--ok-soft` / `--warn-soft` / `--danger-soft` / `--shadow`）
保留为别名指向新令牌 —— 前端代码大量引用，重命名会引入回归。

## 补全的功能

| 功能 | 说明 |
| --- | --- |
| 侧栏收起 | 底部按钮切换，62px 图标轨道，`localStorage` 持久化 |
| 封禁时间 | 每条封禁显示**封禁时间 / 到期时间 / 剩余时长**，数据来自解析 fail2ban.log |
| 批量解封 | 汇总视图勾选多个 IP 一次解封，带确认 |
| 封禁搜索排序 | 按 IP/Jail 搜索（300ms 防抖）、按 IP 排序、一键复制 |
| Jail 列表排序 | 点表头按 名称/当前封禁/累计封禁/失败次数 排序，升降序切换 |
| 单 Jail 日志 | 抽屉里直接看该 Jail `logpath` 指向的日志尾部 |
| 自动刷新 | 概览页 30s 轮询，切回页面时补刷 |
| 白名单编辑 | ignoreip 改为「输入框 + Chip 列表」，可单条删除 |
| 键盘 | `/` 聚焦搜索框，`Esc` 关抽屉 |

### 后端新增

- `app/bin/banlog.py`（新）：解析 fail2ban.log 及轮转文件，按 `(inode, mtime, size)` 增量缓存
- `GET /api/bans` 增加 `bannedAt` / `expiresAt` / `permanent`；`jails[].bantime`
- `GET /api/jails/<name>` 的 status 增加 `banInfo` / `bans` / `bantime`
- `GET /api/jails/<name>/log?lines=N`（新）：读 Jail 日志尾部

## 开发中发现并修掉的 3 个真实缺陷

都是实机跑出来的，不是推测：

**1) 日志路径变量插值（P0）** —— `GET /api/jails/sshd/log` 返回 400。
fail2ban 的 `logpath` 大量使用变量间接引用：`%(sshd_log)s` →
`%(syslog_authpriv)s` → `/var/log/auth.log`。未插值就被当成"相对路径"拒绝，
导致**系统自带 Jail 的日志功能 100% 不可用**。修法是插值前置 + 解析链回溯防循环引用。
实测 91 个 Jail 全部插值成功。

**2) 安全校验顺序反了（P0）** —— 存在性检查跑在 realpath 校验之前，
`/etc/shadow` 因"文件不存在"返回 404，等于**校验被绕过**。改为先校验后判存在。

**3) copytruncate 漏解析** —— logrotate 原地清空后写入更长内容时，新内容被当成旧内容跳过。
加 64 字节锚点指纹校验续读位置。

另有 4 个纯视觉缺陷（`data-sort` 语义冲突会让排序箭头错乱、`.cols-2` 渲染成 3 列、
Jail 名被折行、`.badge.accent` 在表格与工具栏需要不同权重）已在样式层修掉。

## 安装 / 升级

**升级到生产机（你自己操作，我不动生产机）**：

推荐走**应用中心 Web UI** —— 上传 fpk 后点升级。这是 fnOS 唯一的正规升级入口：
`appcenter-cli install-fpk` 检测到已安装会**直接返回不执行升级**（实测多次），
`uninstall` 也被限制为"请从 Web UI 卸载"。命令行绕不过去，这是安全设计。

上传前确认目标机器是 fnos-prod，且当前版本是 1.7.0。

**升级后应当看到**：应用中心版本变 26.11.0；面板仍是 18752 端口、同一个桌面图标；
原管理员密码可登录；自定义 Jail、白名单、通知渠道、备份设置全部还在；
界面变成 qbpanel 风格，封禁列表多了时间列和搜索框。

## 已知边界

1. **`bannedAt` 依赖 fail2ban.log 存在**。日志被清空或服务从未启动过时，
   所有时间列显示 `—`，属预期而非 bug。
2. **命令行装/升级不完整**：`appcenter-cli install-fpk` 对已装应用不执行升级；
   测试机上我是手工模拟 fnOS 的升级序列（upgrade_init → 覆盖文件 → upgrade_callback）
   验证配置保留的，配置哈希逐项比对通过。
3. **移动端表格横向滚动**而非卡片化：本应用 Jail 列表 8 列，改卡片要动 app.js 结构，
   收益不抵风险。
4. 版本自检 / 在线升级**未做** —— 本应用无 GitHub 仓库，属上游能力。
