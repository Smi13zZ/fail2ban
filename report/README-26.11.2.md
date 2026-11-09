# fn-fail2ban 26.11.2 · 面板内图标改用自有资源

## 交付物

| 文件 | 说明 |
| --- | --- |
| `output/fn-fail2ban-26.11.2-all.fpk` | 可安装包（212,060 字节，已在 fnos-test 实装验证） |
| `image/v2611.2-logo.png` | 实机截图：侧栏左上角已是新盾牌图标 |
| `tools/make_icons.py` | 应用图标九档生成器 |
| `tools/make_logo.py` / `tools/make_favicon.py` | 侧栏 logo 与浏览器标签页图标生成器 |
| `tools/check_assets.py` | 前端静态资源引用对账（页面引用的文件是否都存在） |

## 这一版修的是什么

上一版截图里侧栏左上角显示成一个「方框里打叉」——那不是设计，是**字形缺失**。

侧栏标识原本用 emoji 字符 `🛡️`（U+1F6E1 U+FE0F）。fnOS 没装彩色 emoji 字体，
浏览器找不到这个码位的字形，就画成替代框（俗称 tofu）。同理，浏览器标签页图标
用的是一份写着旧蓝色渐变的老 svg，跟新图标完全不搭。

所以这一版把面板内部的两处标识都换成应用自己的图标资源，彻底不依赖系统字体：

| 位置 | 改前 | 改后 |
| --- | --- | --- |
| 侧栏标识 `.sidebar-head .mark` | `<span>🛡️</span>`（emoji 文本） | `<img src="logo.svg" width="32" height="32">` |
| 标签页图标 | `favicon.svg`（旧蓝色渐变 + 白色盾） | 新盾牌 svg + `favicon-32.png` + `apple-touch-icon.png` |

配套样式：`.mark` 去掉了原来的 `background: var(--primary)` 与 `font-size`
（图标自带圆角方块底，再叠一层深色底会把盾牌压成一个小点），改为
`place-items:center` + `overflow:hidden`，`img` 用 `object-fit:contain`。

## 顺带排查

扫了 `index.html` 与 `app.js` 里所有 U+2600–27BF / U+1F300 区段字符，只剩三处 `✕`
（关闭 / 移除按钮）。`✕` 是普通文字符号（U+2715），任何中文字体都有，不受影响；
真正的彩色 emoji 依赖已清零。

## 验证

| 项 | 结果 |
| --- | --- |
| 应用中心 | `Fail2ban` / 26.11.2 / running ✓ |
| 侧栏标识 | 显示新盾牌图标，无方框 ✓ |
| `favicon.svg` `favicon-32.png` `apple-touch-icon.png` `logo.svg` | 全部 200，Content-Type 正确 ✓ |
| 页面引用的 6 个静态资源 | 全部存在（`check_assets.py` 对账）✓ |
| CSS 变量 | 35 个引用全部有定义，括号配平 ✓ |
| 无登录后直接访问接口 | overview / jails / bans 全 200 ✓ |
| 数据卷 | 三个 json 哈希与升级前一致 ✓ |

## 版本说明

按「交付即 bump、不原地覆盖」的约定，26.11.1 已经产出过包，本次改动发行 **26.11.2**。
两版差异只有图标资源与随之调整的样式，后端逻辑零改动。
