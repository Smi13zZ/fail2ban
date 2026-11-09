# fn-fail2ban 26.11.1 · 改名 / 换图标 / 移除登录页

在 26.11.0 基础上的三项改动。**配置保留的保证不变**：`cmd/upgrade_init`、
`cmd/upgrade_callback`、`cmd/uninstall_*`、`cmd/main`、`cmd/env.sh` 全部逐字节未改动。

## 交付物

| 文件 | 说明 |
| --- | --- |
| `output/fn-fail2ban-26.11.1-all.fpk` | 可安装包（199,860 字节，已在 fnos-test 实装验证） |
| `tools/make_icons.py` | 图标生成脚本（九档一次出齐） |
| `image/v2611-desktop.png` / `v2611-phone.png` | 实机截图：打开即主界面，无登录页 |
| `image/icon-sizes.png` | 九档图标并排对比（16 / 24 / 48 / 64 / 72 / 96 / 128 / 256） |

## 1. 应用名改为 Fail2ban

改动点：`manifest` 的 `display_name`、`app/ui/config` 的 `title`、
`index.html` 的 `<title>` 与侧栏标题、`app.js` 的版本提示与页面标题兜底。

`appname` 仍是 `fn-fail2ban` —— 这是安装目录、网关前缀、数据卷路径的依据，
改名会导致被识别成另一个应用、丢掉现有配置，所以只改显示名。

## 2. 图标

按参考图重绘：浅灰圆角方块 + 钢蓝描边盾牌 + 左右两档分面 + 边缘铆钉。
盾牌轮廓是参数化生成（顶部微拱、圆肩、侧边内收、椭圆收尖），不是固定点列，
所以每一档缩放都保持平滑。

**九档必须齐**：fnOS 应用中心按设备像素密度把 `icon_{0}.png` 里的 `{0}` 换成
16/24/48/64/72/96/128/256/512 去取文件，缺档就是空白格子。外层 `ICON.PNG`(64)
与 `ICON_256.PNG`(256) 是 fnpack 单独要求的两份。旧包里遗留的
`icon-64.png` / `icon-256.png`（连字符命名，从不会被取用）已删除。

重建命令：`python tools/make_icons.py`

## 3. 移除登录页

前端：删掉 `login-wrap` 整块 DOM、`btnLogout`、设置页的「安全 / 修改密码」卡片、
以及全部登录相关逻辑（`showLogin`、会话门禁、401 跳登录）。`boot()` 现在直接进主界面。

后端：删掉 `h_setup` / `h_login` / `h_logout` / `h_change_password` 与登录失败限流；
`/api/session` 改为只回报版本号与允许网段。

### 为什么必须同时加访问控制

面板能写 `/etc/fail2ban`、能封禁/解封 IP、能改防火墙动作。去掉登录页后，如果只靠
「知道地址」来保护，等于把增删改权限开放给整个局域网。

所以入口加了一层网段校验（`server.py` 的 `client_allowed`），在**解析任何请求之前**执行：

- 默认只放行回环 + 私有网段：`127.0.0.0/8 ::1 192.168.0.0/16 10.0.0.0/8 172.16.0.0/12`
- 公网来源一律 403，看不到任何数据
- Unix Socket（fnOS 统一网关）来源为空串，总是放行 —— 它只能由本机网关连上
- 可用环境变量覆盖，例如只允许单台管理机：
  `F2B_ALLOW_CIDRS="192.168.1.50/32"`；设成 `*` 表示完全放开（不推荐）

## 验证结果

| 项 | 结果 |
| --- | --- |
| 应用中心显示名 | `Fail2ban` ✓ |
| 面板版本 | 26.11.1 ✓ |
| 图标九档 | 齐全，九档渲染均清晰 ✓ |
| 打开首页 | 直接进主界面，无登录页 ✓ |
| 不带 cookie 访问 8 个接口 | 全部 200 ✓ |
| `POST /api/login` / `/api/setup` | 404（旧接口已移除）✓ |
| 设置页 | 无「安全 / 修改密码」卡片，侧栏无「退出登录」✓ |
| 数据卷 | `prefs.json` / `notifications.json` / `backup.json` 哈希与升级前一致 ✓ |

## 开发中修掉的一个自伤 bug

用正则删除失效的认证函数时，前瞻断言把 `ROUTES` 整块和 `STRIP_PREFIXES` 一起吞掉了，
导致服务启动后所有请求 500（`name 'STRIP_PREFIXES' is not defined`）。

发现方式是实机 `/api/health` 返回 500 —— 静态检查（`py_compile`）发现不了这类
「删多了」的问题，因为语法完全合法。修法是从 26.11.0 的包里把路由表原文取回来，
再把 40 处 `True` 改成 `False`、剔除四个已删接口。之后校验：
`len(ROUTES)=41`、`any(need_auth)=False`、`STRIP_PREFIXES` 有定义。

## 升级到生产机

仍走**应用中心 Web UI** 上传 fpk 升级（`appcenter-cli install-fpk` 对已安装应用
不执行升级，这是 fnOS 的安全设计）。

升级后：应用中心显示名变 Fail2ban、图标换新、版本 26.11.1；打开面板直接进界面；
原有 Jail、白名单、通知、备份设置全部保留。
