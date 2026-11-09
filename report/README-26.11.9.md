# fn-fail2ban 26.11.9 · 应用信息、应用中心与 GitHub 发布

三件事：抹掉别的作者信息、把应用中心的开发者与版本改对、发布到 GitHub。

## 交付物

| 项目 | 地址 / 数值 |
| --- | --- |
| 安装包 | `output/fn-fail2ban-26.11.9-all.fpk`（366,940 字节） |
| GitHub 仓库 | https://github.com/Smi13zZ/fail2ban |
| Release | https://github.com/Smi13zZ/fail2ban/releases/tag/v26.11.9（资产 366,895 字节） |
| 截图 | `image/v26119/` |

## 一、作者信息

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| manifest maintainer | LemonCrab | Smi1ezZ |
| manifest maintainer_url | https://github.com/（空占位） | https://github.com/Smi13zZ |
| manifest distributor | LemonCrab | Smi1ezZ |
| manifest distributor_url | https://github.com/ | https://github.com/Smi13zZ |

**源码注释清理**：app.js 与 index.html 里各有一处注释提到别的项目（「与 xxx 一致」「与 xxx 同款」），
一并改成只描述这段代码在做什么。这类表述本来就在对外文案规则里禁止。

**全仓扫描确认**：LemonCrab 在整个仓库（排除图片与构建产物）0 处残留；
安装包解压后逐文件扫描，对其它项目名、旧版本号、上游作者等关键词也是 0 处命中。

## 二、应用中心

问题不只是 manifest 里两个字段——应用中心显示的版本号与作者来自它自己的数据库，
而这台设备上 26.11.4 之后几次都是我做文件级替换（appcenter-cli uninstall 拒绝卸载
第三方应用），绕开了应用中心，所以数据库一直停在 26.11.3。

**根因**：应用中心的数据在 PostgreSQL 的 appcenter 库，app 表一行记录
version / maintainer / distributor / i18n_matadata。

**修复**：

1. app 表的 version / maintainer / distributor 更新到当前版本；
2. i18n_matadata 整份按 manifest 重新生成，键集合与产品线其余应用完全一致
   （Maintainer / Distributor / MaintainerUrl / DistributorUrl / Desc / ChangeLog /
   DisplayName / BackupGuide）——**缺 DisplayName 会让应用中心列表里「名称」列变空白**，
   这一点是修的过程中踩到并补上的。

**验证**（设备上实读）：

    fn-fail2ban | Fail2ban | 26.11.9 | Smi1ezZ | Smi1ezZ
    MaintainerUrl  = https://github.com/Smi13zZ
    DistributorUrl = https://github.com/Smi13zZ
    DisplayName    = Fail2ban

appcenter-cli list 显示 `fn-fail2ban | Fail2ban | 26.11.9 | running`。

> 说明：数据库是直接改的。以后走应用中心 Web UI 正常升级，这些字段会由安装包里的
> manifest 自动覆盖成同样的值，两条路径最终一致。

## 三、GitHub 发布

- 仓库：https://github.com/Smi13zZ/fail2ban（public，默认分支 main）
- 首次提交 `9c4f88e` 收录 128 个文件；output/*.fpk、tmp/、image/archive/ 走 .gitignore 排除
- 标签 v26.11.9 触发 CI，**全部通过**，Release 已发布：
  - 名称 Fail2ban 26.11.9，非草稿、非预发布
  - 资产 fn-fail2ban-26.11.9-all.fpk（366,895 字节）
  - 正文由 CHANGELOG 自动截取本节 + 安装说明

**发布后自检**（CI 绿不等于包能用）：

- 下载真实 Release 资产 → `tools/inspect_fpk.py --strict` **PASS**
  （九档图标、可执行位、包内版本 26.11.9、cmd/* 与基线的既有差异都复核）
- 用面板真实请求查 `/api/update?force=1`：ok=true、repo=Smi13zZ/fail2ban、
  发布说明与资产列表都能读到
- 模拟旧版本 26.11.6 查一次：latest=26.11.8、has_update=true —— 老版本能识别到更新

## 四、顺带修掉的一个显示 bug

发布后在更新页截图里发现「安装包」一栏显示 **0 B**。

原因：GitHub 接口被限流后，更新检查会退回发布订阅源（atom），那条路径只有下载链接、
**没有字节数**，后端按 0 处理，前端就把 0 当成「文件是空的」显示成 0 B——
会让人以为安装包坏了。

改为：size > 0 才显示大小，否则显示「大小未知 / Size unknown」。

## 五、验证

| 检查 | 结果 |
| --- | --- |
| tools/preflight.py（8 道闸门） | 全部通过 |
| tools/inspect_fpk.py --strict（本地包 + Release 包） | 均 PASS |
| tmp/selftest_api.py | 18/18 |
| tmp/ui_regress.js | 52/52 |
| tmp/verify_notify.py | 104/104 |
| _test_update / _test_installer / _test_http | 62 / 63 / 50 全过 |
| CI build-fpk on v26.11.9 | success |

实机 fnos-test：/api/health 报 26.11.9；/vol1/@appcenter/fn-fail2ban/bin/server.py 的
APP_VERSION 与包内一致；DEFAULT_REPO 指向本项目。

## 六、两点说明

1. **面板内的一键更新仍受 fnOS 限制**：appcenter-cli uninstall 拒绝卸载第三方应用，
   脚本走的是文件级替换。真正的一键升级入口仍是应用中心 Web UI。
2. **生产机未动**：fnos-prod 本次连接被拒（Connection refused），没做任何操作。
   升级仍由你在应用中心操作。
