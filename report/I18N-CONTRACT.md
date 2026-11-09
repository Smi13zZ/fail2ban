# Fail2ban 面板 i18n 改造契约（26.11.3）

## 背景

面板要加中英文切换按钮（与 qbpanel 一致：侧栏左下角一排图标按钮）。
当前 `app/web/app.js` 有 **677 处中文字符串字面量（606 个唯一值）**，
`app/web/index.html` 有 17 处静态中文。目标是把它们全部改成字典驱动。

## 你的写作用域（只允许改这两个文件）

- `src/pkg/fn-fail2ban/app/web/i18n.js`（**新建**）
- `src/pkg/fn-fail2ban/app/web/app.js`（把中文字面量换成 t() 调用）

**绝对不要碰**：`index.html`、`style.css`（Lead 负责）、`app/bin/*`（后端）、`manifest`。

## 交付物一：i18n.js

```js
/* 全局字典 + 译文函数。零依赖，浏览器直接 <script> 引入。 */
(function (global) {
  'use strict';
  var DICT = {
    zh: { /* 中文，源文 */ },
    en: { /* 英文 */ }
  };
  var LANG_KEY = 'f2b-lang';
  function currentLang() { /* localStorage 读，默认 'zh' */ }
  function setLang(lang) { /* 写 localStorage，触发重绘回调 */ }
  function t(key, params) { /* 查表 + {name} 占位替换；查不到返回 key */ }
  global.I18N = { t: t, setLang: setLang, currentLang: currentLang, onLangChange: fn, DICT: DICT };
})(window);
```

要求：
1. `t(key, params)` 支持 `{name}` 形式的占位符替换，例如
   `t('bans.selected', {n: 3})` → 中文「已选中 3 个 IP」/ 英文「3 IPs selected」。
   **不要**用字符串拼接（`'已选中 ' + n + ' 个'`），否则英文语序无法正确。
2. 查不到 key 时**返回 key 本身**（便于发现漏翻），并在 `console.warn` 里提示一次。
3. 语言持久化到 `localStorage['f2b-lang']`，默认 `'zh'`。
4. `onLangChange(fn)` 注册回调，`setLang` 时依次调用（app.js 用它重绘当前视图）。
5. 字典按模块分 key，前缀：`nav.` `dash.` `jail.` `ban.` `filter.` `notify.`
   `fnk.` `set.` `log.` `common.` `err.` `unit.`

## 交付物二：app.js 改造

把**所有**面向用户的中文字符串换成 `t('key')`：

- 静态文案、按钮文字、表头、提示、toast、confirm 消息 → `t()`
- 带变量的 → `t('key', {n: x})`，**不要**留下中文字符串拼接
- 中文标点（、，。：！？）随译文一起进字典，不要在代码里硬编码

**必须保留不动**：
- `VIEW_TITLES` 里的 view id（`dashboard`/`jails`/... 是路由键）
- CSS 类名、DOM id、data-act 值、API 路径
- 数字/日期格式化逻辑
- `esc()` 转义：t() 的结果如果含用户数据，仍要经过 esc()

**质量红线**：这是界面改造不是重写，**功能一行都不能坏**。改完必须：
- `node --check app.js` 通过
- 现有 69 个 `$('#id')` 引用的 id 全部还存在
- 所有 `data-act` 值一个不改

## 英文翻译要求

- 术语用 fail2ban 官方英文：jail / ban / unban / filter / bantime / findtime /
  maxretry / ignoreip / recidive 等**不要翻译**
- 简洁主动：`保存`→`Save`、`解封`→`Unban`、`重载配置`→`Reload config`
- 句子避免中式英语，按英文 UI 习惯写（`Load failed` 而不是 `Load failure happened`）
- 单位：`个 IP`→`IPs`，`次`→`times`，`秒/分钟/小时/天`→`s/min/h/d`

## 验收

Lead 会在测试机上实机验证：切到英文后逐页截图检查有无残留中文、有无 undefined、
布局是否因英文变长而溢出。所以**漏翻比翻得不完美更严重**。
