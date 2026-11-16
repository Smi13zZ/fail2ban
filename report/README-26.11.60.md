# Fail2ban 26.11.60 交付说明

一键更新全链路收口：失败不再被上一轮的残留遮蔽、失败提示任意页面可见、英文界面不再冒出中文，
回滚包终于能找到，另清掉一批确认无引用的死代码。

## 一、交付物

| 项 | 值 |
| --- | --- |
| 版本 | 26.11.60（上一版 26.11.59） |
| 包 | `output/fn-fail2ban-26.11.60-all.fpk`（427,843 B） |
| 构建方式 | `python tools/build_fpk.py --host fnos-test`（fnpack 只在 Linux 上可用） |
| 闸门 | `python tools/preflight.py` → 10 道全绿 |

## 二、逐条对照规格

| 规格条目 | 落点 | 证据 |
| --- | --- | --- |
| 2.1 失败状态不得跨尝试复用 | `installer.begin_attempt()/attempt()`、`h_update_install`、`pollUpdateProgress` | 探针 `retry`：polls=1 读到 attempt=1 的死信后**没有**收工，继续轮询到 checking → downloading → installing → 成功 |
| 2.1 进行中判定包含「检查中」 | `ACTIVE_PHASES`、`busy()` | `test_update_flow.py` [8]：checking 期间第二次认领返回 0、`install()` 返回 `busy` |
| 2.2 下载阶段不先强制重查 | `h_update_install` 先 `update.cached()`，无答案才 `check(force=True)`；`runUpgrade` 先显示「检查中…」 | 探针 `retry` 的 poll2 阶段文字 = `检查中…`，不是「正在下载」 |
| 2.3 回滚包必须能找到 | `find_previous_fpk`：包目录第一位、按 mtime 取最新、`exclude` 排除本次待装 | `test_update_flow.py` [5] 六个断言；设备上 `/api/update/progress` 的 `rollback` = true |
| 2.3 找不到时诚实说明 | `update.noRollbackNotice` + `state.update.rollback === false` 分支 | 探针 `norollback`：设置页出现「当前版本没有可用的安装包副本，安装失败时无法自动回滚…」 |
| 2.4 成功帧不显示假字节 | `finishUpgrade` 用真实 `done/total` | 探针 `retry` 收尾帧：`bytes[9.00 MB / 9.00 MB]`，全程无 `100 B / 100 B` |
| 2.4 无总量退回「准备中…」 | `paintUpProgress` 只在 downloading 且 total>0 时画进度条 | 探针 `nobytes`：`ready[准备中…]`、bytes 与 pct 皆空 |
| 2.5 失败提示任意页面可见 | `#upFail` 失败条（index.html + style.css + `paintUpdateFail`） | 探针 `failbar`：停留在概览页（view=dashboard）就能看到失败条与「查看详情」；点关闭只剩它自己消失，设置页那行仍在 |
| 2.6 错误码化 | `installer.error_code` / `update.net_code` / `ApiError.code` + 20 条 `update.code.*` 双语词条 | 探针 `failbar-en`：`Update failed: DNS lookup failed — check the device's network and DNS settings.` 全英文零中文 |
| 第一节文案 | 阶段表 + `update.stageChecking` / `installingReady` / `done` / `rolledBack` / `pollTimeout` | 与规格表逐字比对（见下） |
| 第四节死代码 | 42 条 i18n 键、5 个前端函数、7 个后端函数/常量、7 个 CSS 变量、26 个 CSS 类 | 审计脚本带变异自检：注入 `zzInjectedDeadKey` 被抓到、真实键零误伤 |
| 第三节验收 | `tools/test_update_flow.py` 10 组用例 + 探针 6 个场景 | 见下两份表 |

## 三、阶段文案与实际显示（zh / en）

| 阶段 | 中文实测 | 英文实测 |
| --- | --- | --- |
| checking | 检查中… | Checking… |
| downloading | 正在下载安装包… → `4.50 MB / 9.00 MB` `50%` | Downloading the package… |
| installing | 安装包已就绪，正在重启服务…（进度区「准备中…」） | Package ready, restarting the service… |
| 成功 | 已更新到 v26.11.61，正在刷新… | Updated to v26.11.61, reloading… |
| 失败（任意页面） | 升级失败：域名解析失败，请检查设备的网络与 DNS 设置 | Update failed: DNS lookup failed — … |
| 无回滚包 | 当前版本没有可用的安装包副本，安装失败时无法自动回滚，需要到飞牛应用中心手动处理 | （同义英文） |

## 四、后端验收（tools/test_update_flow.py，10 组全 PASS）

| # | 用例 | 结果 |
| --- | --- | --- |
| 1 | 中断下载不留假包（`.part` 不变成成品、码为 netReset） | PASS |
| 2 | 正常下载走 `.part` → 原子改名，进度回调收到最终总量 | PASS |
| 3 | HTTP 404 → gh404 | PASS |
| 4 | 错误码映射：gaierror→netDns、HTTP403→gh403、timeout→netTimeout、SSL→netTls、refused/reset；URLError 拆壳 | PASS |
| 5 | 回滚包在包目录被找到、取最新、排除待装包；`rollback_available` 三态 | PASS |
| 6 | 存储合计 = 非嵌套行之和；install_log 的 nested 与真实包含关系一致 | PASS |
| 7 | 两次尝试 attempt 递增；新尝试清掉上一轮 code | PASS |
| 8 | 「检查中」再点返回 busy；busy() 覆盖 checking | PASS |
| 9 | 新一轮不被上一轮死信打断（旧快照编号 < 新基线） | PASS |
| 10 | 坏包→pkgUnreadable、空包→pkgMissing | PASS |

下载用例起的是真 HTTP 服务（真 socket、真 Content-Length、真截断），不是打桩函数。

## 五、界面验收（fnos-test 真机，无头 chromium + DOM 探针，6 个场景）

| 场景 | 读回的关键证据 |
| --- | --- |
| retry（冷启动重试） | poll1 读到 attempt=1 的 `failed/gh403` 后**遮罩未关**；poll2 `检查中…`；poll3 `4.50 MB / 9.00 MB 50%`；poll6 `9.00 MB / 9.00 MB 100%`；poll8 `安装包已就绪，正在重启服务…` 且进度区回「准备中…」；poll13 `已更新到 v26.11.61，正在刷新…` + `9.00 MB / 9.00 MB` |
| nobytes（无 Content-Length） | 全程 `ready[准备中…]`，bytes/pct 为空，不编百分比 |
| failbar（zh，从横幅发起） | 停留在 `view=dashboard` 时失败条可见，文案正确，有「查看详情」与关闭；关闭后 failbar=0 而设置页那行仍在 |
| failbar-en（英文） | 失败条与设置页全英文零中文；「查看详情」= Details |
| norollback | 设置→更新出现「无法自动回滚」说明 |
| 成功帧 | 无 `100 B / 100 B`，显示真实总量 |

进度区高度在 downloading ↔ 其余阶段之间不跳动（两种形态共用同一块固定高度）。

## 六、死代码审计（带变异自检）

审计脚本口径：i18n 键的「引用」语料排除 i18n.js 本身（否则键的定义字面量就是它自己），
并把 SRC2KEY / SRC2KEY_ALT / LINE_TPL 的值也算作引用；`.filter-pick` 这类动态拼接类名单独核对。

| 类别 | 数量 | 内容 |
| --- | --- | --- |
| i18n 无引用键 | 42 | `notify.ch*`/`notify.fd*`/`notify.hint*` 一组、`update.hasUpdate`/`upToDate`/`unknown`/`noRepo`/`phaseInstall`/`installing`/`installingDownload`/`verifying`、`stor.colPath`/`varDir`/`currentVersion`、`err.network`/`err.unauthorized`、`common.sortDir`、`set.dataDir`/`sidebarLockedHint`、`fnk.noteHelpText` |
| 前端函数 | 5 | `shorten` / `shortenPath` / `inFnkTab` / `channelFields` / `switchRow` |
| 后端函数/常量 | 7 | `banlog.reset_cache` / `fnknock.blacklist_status` / `jailconf.set_enabled` / `store.change_password` / `store.verify_password` / `store.issue_session` / `installer._env_path` / `storage.SNAPSHOT_KEEP_MIN`（另把 `installer.PHASES` 换成具名阶段常量） |
| CSS 变量 | 7 | `--panel` `--panel-2` `--bg-soft` `--accent` `--hover` `--shadow` `--log-warn` |
| CSS 类 | 26 | `.nav-item`（兼容写法）、`.row-actions`、`.evgrid`/`.evrow`/`.evtext`、`.tfixed`、`.picker-trigger`、`.channel-url`、`.card-h4`、`.chsum`、`.strow`、`.l-ban`/`.l-unban`/`.l-found`/`.l-warn`、`.c-filter`/`.c-logpath`、`.card-note-center`、`.faint-note`、`.danger-text`、`.nowrap`、`.sr-only`、`.page-title`、`.small`、`.block` |

**保留并记录理由**（不算死代码）：
- `--accent-soft`：原先靠 app.js 的内联 `style` 使用，样式表里查不到 → 改成 `.filter-pick.on` 后重新回到样式表，变量保留。
- `.grow`：CSS 里写的是 `.toolbar .grow`，实际标记用的是 `.h3-grow`；两者都不是无引用，保留。
- `cardHead(title, actions, extra)` 的第三参：5 处调用真的传了 `ch-stack`/`ch-inline`，保留。

变异自检结果：注入 `zzInjectedDeadKey` → 被抓到；真实键集零误伤 → `SELF-CHECK: PASS`。
清理后复跑：`zh 1149 条 / dead 0`。

## 七、实测发现的额外缺陷（本版一并修掉）

| 缺陷 | 后果 | 修法 |
| --- | --- | --- |
| `server.APP_VERSION` 停在 26.11.59 | 面板把自己报成旧版本：版本检查说「有新版本」、一键更新的目标版本算错 | 常量同步到 26.11.60；`app_version()` 改为优先读 `/var/apps/<app>/manifest`（设备上 `TRIM_APPDEST` 指向 `@appcenter` 子树，外层 manifest 不在里面，原先那两级路径永远读不到，一直退回常量） |
| `backup._app_version()` 同一处路径错误 | 备份归档里版本号恒为 `unknown` | 同一套候选路径 |
| 成功帧的版本号取自上一帧 progress 快照 | 会显示「已更新到 v旧版本」 | 收尾用 `meta` 报的新版本号 |
| 设置→更新拿不到「能不能回滚」 | 没有副本时也继续承诺会自动回滚 | 打开更新分区时顺带读一次 `/api/update/progress` |

## 八、实机自检（fnos-test，192.168.1.13）

```
version: version               = 26.11.60
status : running
health : {"ok": true, "app": "fn-fail2ban", "version": "26.11.60", "loginRequired": false}
progress: {"phase":"idle","attempt":0,"rollback":true,"current":"26.11.60"}
```

升级前后配置保留复核：包覆盖安装（install-local 走 stop → uninstall → install）后
`prefs.json` 内容与安装前一致（theme/lang/sidebar/density/logLines 等键一个不少）。

## 九、红线自查

- 未触碰 `fnos-prod`（192.168.1.10）：本版所有设备操作都只对 `fnos-test` 执行。
- 测试机上的下载任务未删除（无关本版，未做任何删除操作）。
- CHANGELOG / 本文件 / 代码注释 / commit message 均未出现「参照」「对齐」「和 xx 一致」这类表述。
- 版本号已 bump 到 26.11.60，未原地覆盖任何旧包（`output/` 里历史包全部保留）。
- 探针页 `_probe_up260.html` 用完已从设备 web 目录删除（复核：`grep -c _probe` = 0）。
