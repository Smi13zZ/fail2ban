# fn-fail2ban 26.11.17 · 一键升级跑通

你说得对——之前把「一键升级做不了」归到 fnOS 限制上是错的。它做不了是两个可修的 bug。
现在整条链路在真机上跑通了。

## 交付物

| 项目 | 地址 / 数值 |
| --- | --- |
| 安装包 | `output/fn-fail2ban-26.11.17-all.fpk`（364,158 字节） |
| Release | https://github.com/Smi13zZ/fail2ban/releases/tag/v26.11.17 |
| 上一版 | https://github.com/Smi13zZ/fail2ban/releases/tag/v26.11.16 |

## 为什么之前做不了

**第一个 bug：升级命令用错了。** 原来的顺序是 `stop → uninstall → install-fpk`。实测：

    appcenter-cli uninstall fn-fail2ban
    [Error]Failed to uninstall fn-fail2ban, please uninstall it from Web UI.

    appcenter-cli install-fpk <包>
    [Info]Application [fn-fail2ban] is installed.      ← 什么都没做

但 `install-local` 是通的——它自己会先停服务、再卸载、再安装：

    [Info]Application [fn-fail2ban] is installed.
    [Info]Application [fn-fail2ban] uninstall success
    [Info]Installation in progress with .../update.env
    [Info]Installation complete.

**第二个 bug：升级文件放错了地方。** 安装包、向导环境变量、解包目录原本都在
`@appdata` 下的 `update/`。而 `install-local` 的第一步就是卸载——那一步会把
`@appdata` 整个清空，于是安装流程随即找不到这些文件，结果是**应用被卸掉、装不回来**，
回滚包也跟着一起没了。现在这些文件放在卸载不会清除的位置，日志仍留在数据目录
供存储页统计与清理。

## 顺带修掉两个「校验把自己判死」的问题

**安装前自检把 .fpk 当 gzip 打开。** `.fpk` 外层是未压缩的 tar，只有内层 `app.tgz`
才是 gzip。写死 gzip 模式时，一个完全正常的包会被判成「不是 gzip 文件」，
升级永远停在下完包之后。

**manifest 的 changelog 涨到 13 KB。** 打包时被折成多行，安装器判定 manifest 不合法，
连卸载都会被拒。现在 changelog 只保留最近两版摘要，并在打包自检里加了行结构校验——
遇到折行会直接 FAIL，不会静默跳过那半截。

## 真机验证

在 fnos-test 上起一个本地假发布源指向 26.11.99，走面板自己的
`POST /api/update/install`，不再手动敲命令：

    下载 → 校验 → 停止 → 卸载 → 安装 → 启动

install.log 实录：

    === 2026-11-09 ... 升级到 26.11.99 ===
    === stop fn-fail2ban ===
    === install-local from /vol1/@apphome/fn-fail2ban/update/unpacked ===
    [Info]Application [fn-fail2ban] uninstall success
    [Info]Installation complete.

结果：应用从 26.11.15 变成 **26.11.99** 并正常提供服务。测试后已恢复正式版本，
假源、临时包、测试进程全部清理。

## 验证

| 检查 | 结果 |
| --- | --- |
| `tools/preflight.py`（8 道闸门） | 全部通过 |
| `tools/inspect_fpk.py --strict`（本地包 + Release 包） | 均 PASS |
| `tmp/selftest_api.py` | 18/18 |
| `tmp/ui_regress.js` | 52/52 |
| `_test_update` / `_test_installer` / `_test_http` | 62 / 63 / 50 全过 |
| CI on `v26.11.17` | success |

实机 fnos-test 已装 26.11.17（`/api/health` 与 `appcenter-cli list` 一致），
manifest 18 行、1.0 KB。

## 还剩一处

`install-local` 装完之后，应用中心拉起服务偶尔会报 `11000`（启动握手超时）。
那时新版本其实已经装好，只是握手先超时。现在脚本遇到这种情况会直接用面板自己的
启动脚本把服务带起来——**实测这个兜底还没被真正走到过**（因为验证时手工启动过服务），
标记为已知的一处未覆盖路径。
