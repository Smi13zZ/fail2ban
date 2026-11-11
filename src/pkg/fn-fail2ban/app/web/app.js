/* ---------------------------------------------------------------------------
   Fail2ban · fnOS 原生客户端 · 前端逻辑（无构建、无依赖）
   --------------------------------------------------------------------------- */
(function () {
  'use strict';

  /* ------------------------------------------------------- i18n 桥接 */

  // i18n.js 先于本文件加载，提供 I18N.t / tLine。这里做一层薄包装：
  // 1) 缺少 I18N 时不留白（返回 key 名的兜底由 I18N 自己负责）；
  // 2) 统一从 I18N 取，绝不在本文件缓存语言值，切语言后才即时生效。
  var I18N_ = window.I18N || null;

  function t(key, params) {
    return I18N_ ? I18N_.t(key, params) : key;
  }

  // 拼接行的整句模板：命中返回成品译文，未命中返回空串由调用方兜底。
  function tLine(fragments, values) {
    return I18N_ ? I18N_.tLine(fragments, values) : '';
  }

  function currentLang() {
    return I18N_ ? I18N_.currentLang() : 'zh';
  }

  // 后端返回的 message / error 是中文。中文界面原样显示；
  // 英文界面不能把中文漏出去，改用本地模板（没有就退到通用兜底）。
  function localizeErr(err, fallbackKey, params) {
    var fallback = t(fallbackKey || 'err.generic', params);
    if (currentLang() !== 'en') return (err && err.message) || fallback;
    if (err && err.status) return t('err.httpStatus', { code: err.status });
    return fallback;
  }

  // 成功类消息同理：中文界面用后端原话（更具体），英文界面用本地模板。
  function localizeMsg(message, fallbackKey, params) {
    var fallback = t(fallbackKey, params);
    if (currentLang() !== 'en' && message) return message;
    return fallback;
  }

  // 备份记录里的 message 是后端拼的中文（「已备份到 /vol1/...」）。
  // 中文界面原样用（路径更具体），英文界面按 resultKind 本地重渲染。
  function localizeStatusMessage(status) {
    if (!status) return '';
    if (currentLang() !== 'en') return status.message || '';
    if (status.resultKind === 'backedUp') {
      return t('set.backupTo', { path: status.directory || '' });
    }
    return '';
  }

  // 后端下发的 UI 文案（字段 label/help、渠道名、模板说明…）是中文，
  // 前端按源文反查字典再翻译；查不到就回退原文，绝不显示空串。
  function tSrc(text) {
    if (text === null || text === undefined) return '';
    text = String(text);
    if (currentLang() === 'zh') return text;
    if (!I18N_ || !I18N_.tSrc) return text;
    var translated = I18N_.tSrc(text);
    return (translated === null || translated === undefined) ? text : translated;
  }

  /* ---------------------------------------------------------------- 基础 */

  var API_BASE = (function () {
    var path = location.pathname;
    if (!path.endsWith('/')) {
      var file = path.split('/').pop() || '';
      if (!file.includes('.')) {
        location.replace(path + '/' + location.search + location.hash);
        return path + '/';
      }
      path = path.slice(0, path.length - file.length);
    }
    return path;
  })();

  var state = {
    session: null,
    meta: null,
    prefs: null,
    view: 'dashboard',
    overview: null,
    jails: [],
    settings: null,
    // 全局白名单（ignoreip）的 Chip 草稿；null = 尚未从 settings 拆分
    ignoreIpDraft: null,
    filters: [],
    filterSel: null,
    filterData: null,
    // 过滤器列表的分类与关键字（status: enabled | disabled | all）
    filterOpts: { status: 'enabled', search: '' },
    // bans.sort 只作用于 IP 列；selected 是汇总视图的勾选集（ip -> [jail…]）
    bans: { jail: '', status: null, all: null, search: '', sort: { key: 'ip', dir: 'asc' }, selected: {} },
    // 概览页「防护中的 Jail」表排序：name | count | total | failed
    dashSort: { key: 'name', dir: 'asc' },
    logs: null,
    // level 是日志页的分段筛选（all | ban | unban | found | warn | error），纯前端过滤
    logOpts: { lines: 400, keyword: '', auto: false, level: 'all' },
    jailOpts: { scope: 'enabled', search: '', sort: { key: 'name', dir: 'asc' } },
    fnknock: null,
    fnkBlacklist: null,
    fnkOpts: { page: 1, limit: 20, search: '' },
    // 与敲门的联动拆成六块，一次只看一块（mode | sync | conn | status | list | log）
    fnkTab: 'mode',
    // 投递记录：status 是筛选条件（all | ok | fail），data 是接口返回的 {items,total,kept}
    deliv: { status: 'all', data: null },
    // 存储页：report 是 /api/storage 的原样返回
    storage: null,
    // 设置页当前分区（panel | appearance | policy | notify | backup | storage | update | about）
    setTab: 'panel',
    // 更新页：info 是 /api/update 的返回；版本检查有独立节流，不跟着刷新按钮走
    update: {
      info: null,
      checking: false,
      // 升级过程：poll 定时器、已展示过的最大百分比（只增不减）、安装前版本
      poll: null,
      maxPct: 0,
      before: '',
      phase: '',
      done: false,
      lastCheckAt: 0,
      lastFailAt: 0
    },
    loading: false
  };

  // 视图 id 是路由键，不能翻译；标题用 getter 让切语言即时生效
  var VIEW_TITLES = {
    get dashboard() { return t('nav.dashboard'); },
    get jails() { return t('nav.jails'); },
    get bans() { return t('nav.bans'); },
    get filters() { return t('nav.filters'); },
    get fnknock() { return t('nav.fnknock'); },
    get settings() { return t('nav.settings'); },
    get logs() { return t('nav.logs'); }
  };

  // 设置页的分区。id 是路由键，文案在 set.tab.<id> 下。
  // 「说明」放最后第二位：它列的是文件位置与端口，属于查阅性质的参考页，
  // 不适合夹在会改配置的分区中间。
  var SET_TABS = ['panel', 'appearance', 'policy', 'notify', 'maint', 'storage',
    'update', 'guide', 'about'];
  var SET_TAB_KEY = 'set.tab.';

  function setTabLabel(id) {
    return t(SET_TAB_KEY + id);
  }

  // fn-knock 联动页的分区：与设置页同一套标签条，一次只展开一块。
  var FNK_TABS = ['mode', 'sync', 'conn', 'status', 'list', 'log'];

  // 前缀单独放变量里，与 set.tab. 同一写法：i18n 闸门按字面量核对引用，
  // 直接写 'fnk.tab.' + id 会被当成「引用了不存在的 key fnk.tab.」。
  var FNK_TAB_KEY = 'fnk.tab.';

  function fnkTabLabel(id) {
    return t(FNK_TAB_KEY + id);
  }

  // 旧地址 → 新地址。老书签和浏览器历史里还留着一整页的 hash，
  // 直接当未知视图处理会掉回概览，用户以为功能没了。
  var LEGACY_VIEW_HASH = {
    notifications: 'notify',
    storage: 'storage',
    update: 'update'
  };

  // 主题名随语言变化：用 getter 每次实时取，不在模块加载时固化
  var THEME_LABELS = {
    get auto() { return t('common.themeAuto'); },
    get dark() { return t('common.themeDark'); },
    get light() { return t('common.themeLight'); }
  };
  // 侧栏底部的图标与面板内其它图标同一套笔触：18px、1.8 线宽、圆角端点。
  // 主题三态用「显示器 / 月亮 / 太阳」——显示器表示跟随系统，和「跟随系统」这层
  // 语义对得上；用半明半暗的圆在 18px 下会糊成一团看不出是圆。
  var SVG_OPEN = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">';

  var THEME_ICONS = {
    light: SVG_OPEN + '<circle cx="12" cy="12" r="4"/>' +
      '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2' +
      'M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
    dark: SVG_OPEN + '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z"/></svg>',
    auto: SVG_OPEN + '<rect x="2" y="3" width="20" height="14" rx="2"/>' +
      '<path d="M8 21h8M12 17v4"/></svg>'
  };

  // 语言按钮：文/A 字符图标，表示「界面语言」
  var LANG_ICON = SVG_OPEN + '<path d="m5 8 6 6"/><path d="m4 14 6-6 2-3"/>' +
    '<path d="M2 5h12"/><path d="M7 2h1"/><path d="m22 22-5-10-5 10"/>' +
    '<path d="M14 18h6"/></svg>';

  // 版本行前的地球图标：面板版本是「本机运行的这一套」，
  // 后面跟引擎版本，与概览里显示的同源
  var GLOBE_ICON = SVG_OPEN + '<circle cx="12" cy="12" r="9"/><path d="M3 12h18"/>' +
    '<path d="M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18z"/></svg>';

  // 铃铛：通知空态与渠道行的图标（内联 SVG，不额外引资源）
  var BELL_ICON = SVG_OPEN + '<path d="M6 8a6 6 0 1 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/>' +
    '<path d="M10.3 20a2 2 0 0 0 3.4 0"/></svg>';

  // 折叠按钮：方框 + 左侧竖线 + 方向箭头（收起时箭头朝左，展开时朝右）
  var COLLAPSE_ICONS = {
    collapsed: SVG_OPEN + '<rect x="3" y="3" width="18" height="18" rx="2"/>' +
      '<path d="M9 3v18"/><path d="m14 9 3 3-3 3"/></svg>',
    expanded: SVG_OPEN + '<rect x="3" y="3" width="18" height="18" rx="2"/>' +
      '<path d="M9 3v18"/><path d="m16 15-3-3 3-3"/></svg>'
  };

  var $ = function (sel, root) { return (root || document).querySelector(sel); };

  function esc(value) {
    return String(value === undefined || value === null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function api(path, options) {
    var opts = options || {};
    var init = {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    return fetch(API_BASE + 'api/' + path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        try { data = text ? JSON.parse(text) : null; } catch (e) { data = null; }
        if (!res.ok) {
          var err = new Error((data && data.error) || t('err.http', { code: res.status }));
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function toast(message, kind, ms) {
    var host = $('#toastHost');
    var node = document.createElement('div');
    node.className = 'toast ' + (kind || '');
    node.textContent = message;
    host.appendChild(node);
    setTimeout(function () {
      node.style.opacity = '0';
      node.style.transform = 'translateX(18px)';
      node.style.transition = 'all .18s';
      setTimeout(function () { node.remove(); }, 200);
    }, ms || (kind === 'err' ? 6000 : 3200));
  }

  function busy(button, on) {
    if (!button) return;
    if (on) {
      button.dataset.label = button.innerHTML;
      button.innerHTML = ("<span class=\"spinner\"></span> " + t('common.processing'));
      button.disabled = true;
    } else {
      if (button.dataset.label) button.innerHTML = button.dataset.label;
      button.disabled = false;
    }
  }

  function fmtTime(seconds) {
    if (!seconds) return '—';
    var d = new Date(seconds * 1000);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' +
      p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
  }

  // 运行时长：后端给的是 {days, hours, minutes} 数值，按当前语言拼；
  // 老版本后端只给中文字符串，那时直接用它，避免出现空值。
  function fmtUptime(system) {
    if (!system) return '—';
    var parts = system.uptimeParts;
    if (parts && typeof parts.days === 'number') {
      if (parts.days > 0) return t('unit.daysHours', { d: parts.days, h: parts.hours });
      if (parts.hours > 0) return t('unit.hoursMinutes', { h: parts.hours, m: parts.minutes });
      return t('unit.minutes', { n: parts.minutes });
    }
    return system.uptime || '—';
  }

  function isOn(value) {
    return ['true', 'yes', '1', 'on'].indexOf(String(value).toLowerCase()) >= 0;
  }


  // 短时间戳：MM-DD HH:mm。封禁列表里一列放得下，完整时间放 title
  function fmtShortTime(seconds) {
    if (!seconds) return '—';
    var d = new Date(Number(seconds) * 1000);
    if (isNaN(d.getTime())) return '—';
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  // 剩余时长：到期时间缺失（老版本后端 / 日志里查不到）一律显示「—」
  function fmtRemain(expiresAt) {
    if (!expiresAt) return '—';
    var left = Number(expiresAt) - Math.floor(Date.now() / 1000);
    if (isNaN(left)) return '—';
    if (left <= 0) return t('unit.expired');
    var days = Math.floor(left / 86400);
    var hours = Math.floor((left % 86400) / 3600);
    var mins = Math.floor((left % 3600) / 60);
    var secs = left % 60;
    if (days > 0) return t('unit.daysHours', { d: days, h: hours });
    if (hours > 0) return t('unit.hoursMinutes', { h: hours, m: mins });
    if (mins > 0) return t('unit.minutesSeconds', { m: mins, s: secs });
    return t('unit.seconds', { n: secs });
  }

  // 到期时间单元格的强调态：≤5 分钟用 .soon，已过期用 .expired
  function expiryClass(expiresAt) {
    var cls = 'ban-expiry';
    if (!expiresAt) return cls;
    var left = Number(expiresAt) - Math.floor(Date.now() / 1000);
    if (isNaN(left)) return cls;
    if (left <= 0) return cls + ' expired';
    if (left <= 300) return cls + ' soon';
    return cls;
  }

  // 封禁时间 / 到期时间 / 剩余 三列的单元格，字段缺失时优雅降级成「—」
  function banTimeCells(record) {
    var bannedAt = record && record.bannedAt;
    var expiresAt = record && record.expiresAt;
    var cls = expiryClass(expiresAt);
    var bannedTitle = bannedAt ? ' title="' + esc(fmtTime(bannedAt)) + '"' : '';
    var expiresTitle = expiresAt ? ' title="' + esc(fmtTime(expiresAt)) + '"' : '';
    return '<td class="c-time hint mono"' + bannedTitle + '>' + esc(fmtShortTime(bannedAt)) + '</td>' +
      '<td class="c-time"><span class="' + cls + '"' + expiresTitle + '>' + esc(fmtShortTime(expiresAt)) + '</span></td>' +
      '<td class="c-time"><span class="' + cls + '"' + expiresTitle + '>' + esc(fmtRemain(expiresAt)) + '</span></td>';
  }

  // IP 排序键：IPv4 按数值段比，其它（IPv6 / 主机名）排在后面按字符串比
  function ipSortKey(ip) {
    var parts = String(ip || '').split('.');
    if (parts.length === 4) {
      var nums = [];
      for (var i = 0; i < 4; i++) {
        if (!/^\d{1,3}$/.test(parts[i])) return [1, String(ip)];
        nums.push(parseInt(parts[i], 10));
      }
      return [0, nums[0], nums[1], nums[2], nums[3]];
    }
    return [1, String(ip)];
  }

  function cmpValues(a, b) {
    var left = Array.isArray(a) ? a : [a];
    var right = Array.isArray(b) ? b : [b];
    for (var i = 0; i < Math.max(left.length, right.length); i++) {
      var x = left[i];
      var y = right[i];
      if (x === y) continue;
      if (x === undefined) return -1;
      if (y === undefined) return 1;
      if (typeof x === 'number' && typeof y === 'number') return x < y ? -1 : 1;
      var sx = String(x);
      var sy = String(y);
      if (sx === sy) continue;
      return sx < sy ? -1 : 1;
    }
    return 0;
  }

  /* ------------------------------------------------------ 复制到剪贴板 */

  // navigator.clipboard 只在安全上下文可用（面板走 http 时常常不可用），
  // 失败回退 execCommand('copy') + 临时 textarea。
  function legacyCopy(text) {
    var area = document.createElement('textarea');
    area.value = text;
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-1000px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    var ok = false;
    try {
      area.select();
      area.setSelectionRange(0, area.value.length);
      ok = document.execCommand('copy');
    } catch (e) { ok = false; }
    area.remove();
    return ok;
  }

  function copyLabel(button, text, ok) {
    if (!button) {
      toast(ok ? t('common.copiedToast', { text: text }) : t('common.copyFailedHint'), ok ? 'ok' : 'warn', 2000);
      return;
    }
    if (button.dataset.label === undefined) button.dataset.label = button.innerHTML;
    button.classList.add('done');
    button.textContent = ok ? t('common.copied') : t('common.copyFailed');
    setTimeout(function () {
      button.classList.remove('done');
      if (button.dataset.label !== undefined) button.innerHTML = button.dataset.label;
      delete button.dataset.label;
    }, 1200);
  }

  function copyText(text, button) {
    text = String(text === undefined || text === null ? '' : text);
    if (!text) return;
    var fallback = function () { copyLabel(button, text, legacyCopy(text)); };
    if (navigator.clipboard && navigator.clipboard.writeText && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () {
        copyLabel(button, text, true);
      }).catch(fallback);
      return;
    }
    fallback();
  }

  /* -------------------------------------------------------------- 主题 */

  function currentTheme() {
    return localStorage.getItem('f2b-theme') || 'auto';
  }

  // 侧栏底部版本行只显示面板自身版本。引擎版本在「设置 · 关于」里，
  // 这里再跟一遍会把 25 个等宽字符挤进 216px 的侧栏，尾部一律截断。
  function setVersionLine(version) {
    var text = $('#verText');
    if (!text || !version) return;
    text.textContent = 'v' + version;
    var node = $('#verLine');
    if (node) node.title = 'Fail2ban v' + version;
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    var resolved = theme;
    if (theme === 'auto') {
      resolved = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
        ? 'light' : 'dark';
    }
    root.dataset.theme = resolved;
    localStorage.setItem('f2b-theme', theme);
    // 侧栏底部是图标按钮，图标随模式变化：显示器 / 月亮 / 太阳
    var icon = $('#themeIcon');
    if (icon) icon.innerHTML = THEME_ICONS[theme] || THEME_ICONS.auto;
    var button = $('#btnTheme');
    if (button) {
      button.title = THEME_LABELS[theme] || THEME_LABELS.auto;
      button.setAttribute('aria-label', button.title);
    }
  }

  // 系统主题变化时，“跟随系统”模式下的界面实时切换
  if (window.matchMedia) {
    var systemThemeQuery = window.matchMedia('(prefers-color-scheme: light)');
    var onSystemThemeChange = function () {
      if (currentTheme() === 'auto') applyTheme('auto');
    };
    if (systemThemeQuery.addEventListener) {
      systemThemeQuery.addEventListener('change', onSystemThemeChange);
    } else if (systemThemeQuery.addListener) {
      systemThemeQuery.addListener(onSystemThemeChange);
    }
  }

  /* ---------------------------------------------------------- 侧栏收起 */

  var COLLAPSE_KEY = 'f2b-collapsed';

  function readCollapsed() {
    try { return localStorage.getItem(COLLAPSE_KEY) === '1'; } catch (e) { return false; }
  }

  // 只负责按钮本身的文案与图标，供 applyCollapsed 与偏好切换共用。
  // 方框里那根竖线是侧栏，箭头指向「点了之后侧栏会往哪边动」
  // （收起时箭头朝左 = 展开，展开时箭头朝左带竖线 = 收起）。
  function applyCollapseButton(collapsed) {
    var btn = $('#btnCollapse');
    if (!btn) return;
    btn.title = collapsed ? t('common.sidebarExpand') : t('common.sidebarCollapse');
    btn.setAttribute('aria-label', btn.title);
    btn.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    var icon = $('#collapseIcon');
    if (icon) icon.innerHTML = collapsed ? COLLAPSE_ICONS.collapsed : COLLAPSE_ICONS.expanded;
  }

  function applyCollapsed(collapsed) {
    var app = $('#app');
    if (app) app.classList.toggle('collapsed', !!collapsed);
    applyCollapseButton(!!collapsed);
    // 收起状态下导航只剩图标，补上 title 作为悬停提示（展开时移除，避免重复提示）
    var tabs = $('#tabs');
    if (tabs) {
      Array.prototype.forEach.call(tabs.querySelectorAll('button[data-view]'), function (node) {
        var label = node.querySelector('span');
        if (!node.dataset.label && label) node.dataset.label = label.textContent;
        if (collapsed) node.title = node.dataset.label || '';
        else node.removeAttribute('title');
      });
    }
  }

  function toggleCollapsed() {
    var next = !readCollapsed();
    // 折叠按钮永远只改本机记忆，不改服务端偏好：
    // 偏好里的「默认展开 / 默认收起」是每次打开面板的初值，用户点按钮只是这次想换个看法。
    // 这样也不需要给按钮加「已被固定」之类看不见的状态标记。
    try { localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0'); } catch (e) { /* 隐私模式忽略 */ }
    applyCollapsed(next);
  }

  /* ------------------------------------------------ 外观类偏好的即时生效 */

  // 表格密度：html[data-density] 由样式表驱动（compact 压行高与卡片内边距）
  function applyDensity(density) {
    var next = (density === 'compact') ? 'compact' : 'comfortable';
    document.documentElement.dataset.density = next;
  }

  // 侧栏布局：auto = 用本机记住的上次状态；expanded / collapsed = 每次打开都按这个初值。
  // 无论哪种，点折叠按钮都只改本机记忆，不回写偏好 —— 偏好是初值，按钮是这一次。
  function applySidebarPref(sidebar) {
    var pref = sidebar || 'auto';
    var app = $('#app');
    if (app) app.dataset.sidebar = pref;
    if (pref === 'expanded') applyCollapsed(false);
    else if (pref === 'collapsed') applyCollapsed(true);
    else applyCollapsed(readCollapsed());
  }

  // 界面语言：prefs 里有值就以它为准，空串表示跟随浏览器/上次手动选择。
  function applyLangPref(lang) {
    if (!lang || !window.I18N) return;
    if (I18N.currentLang() === lang) return;
    I18N.setLang(lang);
    repaintForLang();
  }

  // 通知分区红点：通知里有失败投递时提醒一下
  function paintNotifyDot() {
    var dot = $('#setNotifyDot');
    if (!dot) return;
    var payload = deliveryPayload();
    var items = (payload && payload.items) || [];
    var failed = items.filter(function (item) { return item.ok === false; }).length;
    dot.hidden = failed === 0;
  }

  function openSidebar() {
    $('#sidebar').classList.add('open');
    $('#scrim').classList.add('open');
  }

  function closeSidebar() {
    $('#sidebar').classList.remove('open');
    $('#scrim').classList.remove('open');
  }

  /* ---------------------------------------------------------- 初始化 */

  // 面板不再有自己的登录页：没有会话时后端直接放行只读请求，这里也直接进主界面。
  function enterApp() {
    return api('meta').then(function (meta) {
      state.meta = meta;
      state.prefs = meta.prefs;
      setVersionLine((meta.app && meta.app.version) ||
        (state.session && state.session.version) || '');
      paintSideIcons();
      state.logOpts.lines = meta.prefs.logLines || 400;
      applyTheme(meta.prefs.theme || 'auto');
      applyDensity(meta.prefs.density);
      applySidebarPref(meta.prefs.sidebar);
      applyLangPref(meta.prefs.lang);
      $('#app').classList.add('ready');
      // 首次渲染完成后再撤掉首屏占位，避免中间闪一下空白
      var splash = $('#bootSplash');
      // 预取通知设置，以便启用浏览器通知时立即开始轮询
      api('notifications').then(function (data) {
        state.notifications = data;
        restartBrowserNotify();
      }).catch(function () { /* 通知接口不可用不影响主流程 */ });
      return refresh().finally(function () { if (splash) splash.hidden = true; });
    });
  }

  /* ------------------------------------------------------- 概览自动刷新 */

  // 概览页 30s 轮询；页面不可见时停掉定时器，切回来若距上次刷新超过 30s 立刻补刷。
  // 「上次刷新时间」只在真正拉成功后才更新，避免一次失败让补刷再等一整轮。
  var AUTO_REFRESH_MS = 30000;
  var autoTimer = null;
  var lastAutoRefreshAt = 0;

  function stopAutoRefresh() {
    if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
  }

  function startAutoRefresh() {
    if (autoTimer) return;
    autoTimer = setInterval(function () {
      if (document.hidden || state.view !== 'dashboard') { stopAutoRefresh(); return; }
      silentRefresh();
    }, AUTO_REFRESH_MS);
  }

  // 自动刷新走静默路径：不弹 toast、不切登录态，失败就等下一轮
  function silentRefresh() {
    if (state.view !== 'dashboard') return;
    api('overview').then(function (data) {
      if (state.view !== 'dashboard') return;
      state.overview = data;
      lastAutoRefreshAt = Date.now();
      renderDashboard();
      updateBadge();
    }).catch(function (err) {
      if (err && err.status === 401) { stopAutoRefresh(); }
    });
  }

  function syncAutoRefresh() {
    if (state.view === 'dashboard' && !document.hidden) {
      if (!lastAutoRefreshAt) lastAutoRefreshAt = Date.now();
      startAutoRefresh();
    } else {
      stopAutoRefresh();
    }
  }

  function onVisibilityChange() {
    if (document.hidden) {
      stopAutoRefresh();
      return;
    }
    // 切回前台：距上次成功检查超过 30 分钟就补查一次（不进更新页也能收到红点）
    if (updateCheckDue()) checkUpdate(false);
    if (state.view !== 'dashboard') return;
    // 切回来发现已经过期（后台超过 30s）：立刻补刷一次，再恢复轮询
    if (Date.now() - lastAutoRefreshAt > AUTO_REFRESH_MS) silentRefresh();
    startAutoRefresh();
  }

  /* ------------------------------------------------------------- 路由 */

  // 四个配置页并入设置页之后，「当前是不是在通知 / 存储 / 更新」不再等于
  // 「state.view 是不是那个名字」：这些渲染函数现在挂在设置页的分区里，
  // 异步回来时要用它判断该不该重绘，否则数据回来了界面不动。
  function inSettingsTab(tab) {
    return state.view === 'settings' && state.setTab === tab;
  }

  // fn-knock 页同理：数据回来时要判断那块还在不在当前分区上，否则界面不动。
  function inFnkTab(tab) {
    return state.view === 'fnknock' && state.fnkTab === tab;
  }

  // 地址栏形如 #settings/notify；其余视图仍是 #jails。
  function viewHash(view, tab) {
    if (view !== 'settings') return view;
    return 'settings/' + (tab || state.setTab || 'panel');
  }

  // 配置页内部切分区：只改 hash 与重绘，不重进视图（不重新拉整页数据）。
  function setSettingsTab(id) {
    if (SET_TABS.indexOf(id) < 0) id = 'panel';
    if (state.setTab === id) return;
    state.setTab = id;
    try {
      // replaceState 而不是写 hash：否则会多出一条历史记录，用户点后退期望
      // 回到上一个页面，却只是在设置页的分区之间来回跳。
      history.replaceState(null, '', '#' + viewHash('settings', id));
    } catch (e) { location.hash = viewHash('settings', id); }
    // 先画标签条与占位，再去拉这个分区的数据：切标签是瞬时的，
    // 数据没到也不该整页空着。
    renderSettings();
    var tasks = loadSettingsTab(id);
    if (!tasks.length) return;
    Promise.all(tasks).then(function () {
      if (state.view === 'settings' && state.setTab === id) renderSettings();
    }).catch(function (err) {
      if (state.view === 'settings' && state.setTab === id) toast(localizeErr(err), 'err');
    });
  }

  // fn-knock 页内切分区：与设置页同样的做法，只重绘这一页，不重进视图。
  // 「黑名单」那块的数据按需拉，切过去才发请求。
  function setFnkTab(id) {
    if (FNK_TABS.indexOf(id) < 0) id = 'mode';
    if (state.fnkTab === id) return;
    state.fnkTab = id;
    renderFnknock();
    if (id === 'list') {
      loadFnkBlacklist().then(function () {
        if (state.view === 'fnknock' && state.fnkTab === 'list') renderFnknock();
      }).catch(function (err) { toast(localizeErr(err), 'err'); });
    }
  }

  // 从 hash 解析出 { view, tab }。旧 hash 在这里一次性重定向掉。
  function parseHash(hash) {
    var raw = String(hash || '').replace(/^#/, '');
    if (!raw) return { view: '', tab: '' };
    var parts = raw.split('/');
    var head = parts[0];
    var tab = parts[1] || '';
    if (LEGACY_VIEW_HASH[head]) {
      return { view: 'settings', tab: LEGACY_VIEW_HASH[head], legacy: true };
    }
    if (head === 'settings') {
      if (SET_TABS.indexOf(tab) < 0) tab = 'panel';
      return { view: 'settings', tab: tab };
    }
    return { view: head, tab: '' };
  }

  function paintPageTitle() {
    var title = $('#pageTitle');
    if (!title) return;
    // 顶栏只写视图名。设置页不再拼「· 分区」后缀：当前在哪个分区
    // 由标签条自己高亮显示，标题里再写一遍是重复的。
    title.textContent = VIEW_TITLES[state.view] || 'Fail2ban';
  }

  function setView(view, tab) {
    state.view = view;
    if (view !== 'logs') stopLogTimer();
    if (view !== 'dashboard') stopAutoRefresh();
    if (view === 'settings') {
      state.setTab = SET_TABS.indexOf(tab) >= 0 ? tab : (state.setTab || 'panel');
    }
    Array.prototype.forEach.call($('#tabs').children, function (btn) {
      btn.classList.toggle('active', btn.dataset.view === view);
    });
    paintPageTitle();
    closeSidebar();
    location.hash = viewHash(view, state.setTab);
    syncAutoRefresh();
    lastAutoRefreshAt = view === 'dashboard' ? Date.now() : lastAutoRefreshAt;
    refresh();
  }

  function render() {
    switch (state.view) {
      case 'jails': renderJails(); break;
      case 'bans': renderBans(); break;
      case 'filters': renderFilters(); break;
      case 'fnknock': renderFnknock(); break;
      case 'settings': renderSettings(); break;
      case 'logs': renderLogs(true); break;
      default: renderDashboard();
    }
  }

  function refresh() {
    // 顶栏那个服务徽标的数据源是 /api/service，每个页面都要取一次。
    // 这里之前是「tasks = [...]」直接赋值，switch 里一覆盖就丢了，
    // 于是直接停在某个页面时徽标永远停在「检测中…」。
    var viewTasks;
    var seed = state.overview ? [] : [api('service').then(function (info) {
      state.overview = { fail2ban: info, jails: [], summary: {}, system: {} };
    })];
    switch (state.view) {
      case 'dashboard':
        viewTasks = [api('overview').then(function (d) {
          state.overview = d;
          lastAutoRefreshAt = Date.now();
        })];
        break;
      case 'jails':
        // withStatus=1：列表要显示 当前封禁 / 累计封禁 / 失败次数 三列。
        // 未运行的 Jail 后端不返回 status，表格里显示「—」。
        viewTasks = [api('jails?scope=all&withStatus=1').then(function (d) { state.jails = d.jails; })];
        break;
      case 'bans':
        viewTasks = [api('jails?scope=active').then(function (d) {
          state.jails = d.jails;
          if (!state.bans.jail && d.jails.length) state.bans.jail = d.jails[0].name;
          // 「全部 Jail」是汇总视图，不依赖单个 Jail 是否还在运行
          if (state.bans.jail && state.bans.jail !== BAN_ALL &&
              !d.jails.some(function (j) { return j.name === state.bans.jail; })) {
            state.bans.jail = d.jails.length ? d.jails[0].name : BAN_ALL;
          }
          if (state.bans.jail) return loadBanStatus(state.bans.jail);
        })];
        break;
      case 'filters':
        viewTasks = [api('filters').then(function (d) {
          state.filters = d.filters;
          state.filterTemplates = d.templates || [];
        })];
        break;
      case 'settings':
        viewTasks = loadSettingsTab(state.setTab);
        break;
      case 'fnknock':
        viewTasks = [api('fnknock').then(function (d) { state.fnknock = d; })];
        // 黑名单分页数据只在「黑名单」那块才需要，别的分区不用先拉一次
        if (state.fnkTab === 'list') viewTasks.push(loadFnkBlacklist());
        break;
      case 'logs':
        viewTasks = [loadLogs(), api('overview').then(function (d) { state.overview = d; })];
        break;
      default:
        viewTasks = [];
    }
    updateBadge();
    return Promise.all(seed.concat(viewTasks || [])).then(function () {
      render();
      updateBadge();
      if (state.view === 'dashboard') lastAutoRefreshAt = Date.now();
      syncAutoRefresh();
    }).catch(function (err) {
      // 页面数据没取到也要把已经拿到的服务状态画出来，否则一片空白
      render();
      updateBadge();
      toast(localizeErr(err), 'err');
    });
  }

  function updateBadge() {
    var badge = $('#svcBadge');
    var info = state.overview && state.overview.fail2ban;
    if (!info) return;
    if (!info.installed) {
      badge.className = 'badge danger';
      badge.textContent = t('common.notInstalled');
    } else if (info.running) {
      badge.className = 'badge ok';
      badge.innerHTML = ("<span class=\"dot\"></span> " + t('jail.scopeActive'));
    } else {
      badge.className = 'badge warn';
      badge.innerHTML = ("<span class=\"dot\"></span> " + t('common.stopped'));
    }
    var navBadge = $('#navBanCount');
    var summary = state.overview.summary;
    if (navBadge && summary && typeof summary.bannedIps === 'number') {
      navBadge.textContent = summary.bannedIps > 0 ? String(summary.bannedIps) : '';
    }
  }

  /* ------------------------------------------------------------- 概览 */

  function renderDashboard() {
    var data = state.overview;
    var host = $('#view');
    if (!data) { host.innerHTML = t('fnk.loadingInline'); return; }

    var info = data.fail2ban;
    var sum = data.summary;
    var html = '';

    if (!info.installed) {
      html += '<div class="banner danger"><div class="banner-body">' +
        t('dash.installBlock') +
        t('dash.notInstalledBody') +
        t('dash.installButton');
    }

    html += '<div class="grid cols-4">' +
      statCard(t('dash.statJails'), sum.activeJails, t('dash.statJailsSub', { configured: sum.configuredJails, enabled: sum.enabledJails })) +
      statCard(t('dash.statBans'), sum.bannedIps, t('dash.statBansSub')) +
      statCard(t('dash.statTotal'), sum.totalBanned, t('dash.statTotalSub')) +
      statCard(t('dash.statFailed'), sum.totalFailed, t('dash.statFailedSub', { n: sum.currentlyFailed })) +
      '</div>';

    // 服务状态
    var svcState = info.service || {};
    html += '<div class="grid cols-2">' +
      t('dash.serviceTitleInline') +
      '<dl class="kv">' +
      kv(t('notify.colStatus'), info.installed
        ? '<span class="badge ' + (info.running ? 'ok' : 'danger') + '">' +
          (info.running ? t('jail.scopeActive') : t('jail.noneRunning')) + '</span>'
        : t('dash.badgeNotInstalled')) +
      kv('systemd', esc(svcState.raw || '—')) +
      kv(t('dash.autostart'), svcState.enabled ? t('jail.scopeEnabled') : t('notify.pollDisabled')) +
      kv(t('dash.version'), esc(info.version || '—')) +
      kv(t('dash.socket'), '<span class="mono">' + esc(info.socket || '—') + '</span>') +
      kv(t('dash.client'), '<span class="mono">' + esc(info.client || '—') + '</span>') +
      '</dl>' +
      '<div class="toolbar" style="margin:14px 0 0">' +
      '<button class="btn" data-act="svc" data-svc="start" ' + (info.installed ? '' : 'disabled') + (">" + t('common.start') + "</button>") +
      '<button class="btn" data-act="svc" data-svc="stop" ' + (info.running ? '' : 'disabled') + (">" + t('common.stop') + "</button>") +
      '<button class="btn" data-act="svc" data-svc="restart" ' + (info.installed ? '' : 'disabled') + (">" + t('common.restart') + "</button>") +
      '<button class="btn" data-act="svc" data-svc="reload" ' + (info.running ? '' : 'disabled') + (">" + t('common.reload') + "</button>") +
      '</div></div>' +

      ("<div class=\"card\"><h3>" + t('dash.envTitle') + "</h3><dl class=\"kv\">") +
      kv(t('dash.panelVersion'), esc((data.app && data.app.version) || '—')) +
      kv(t('dash.os'), esc((data.system.os || '') + ' ' + (data.system.osVersion || ''))) +
      kv(t('dash.kernel'), esc(data.system.kernel || '—')) +
      kv(t('dash.arch'), esc(data.system.arch || '—')) +
      kv(t('dash.hostname'), esc(data.system.hostname || '—')) +
      kv(t('dash.uptime'), esc(fmtUptime(data.system))) +
      kv('Python', esc(data.system.python || '—')) +
      kv(t('dash.colLogFile'), '<span class="mono">' + esc(data.logFile || t('common.notFound')) + '</span>') +
      '</dl></div></div>';

    // 活跃 Jail：排序由标题右侧的独立控件驱动（字段 + 升降序）
    var dashSort = state.dashSort;
    var dashJails = sortDashboardJails(data.jails, dashSort);
    html += ("<div class=\"card\"><h3>" + t('dash.jailsTitle') +
      '<span class="h3-grow"></span>' +
      sortControl('dash', [
        ['name', t('jail.colName')],
        ['count', t('dash.colBans')],
        ['total', t('dash.colTotal')],
        ['failed', t('dash.colFailed')]
      ], dashSort) + "</h3>");
    if (!data.jails.length) {
      html += t('dash.noJailsInline');
    } else {
      html += jailTableHtml(dashJails, 'bans');
    }
    html += '</div>';

    host.innerHTML = html;
  }

  /* ---------------------------------------------------------- 排序控件 */

  /* 排序从表头挪到标题右侧的独立控件（字段下拉 + 升降序按钮）：
     列宽固定后表头里的点击区与列名对不齐，容易点错列的箭头。
     三个列表各有自己的排序状态，用 scope 区分。 */
  var SORT_ASC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5"/><path d="M6 11l6-6 6 6"/></svg>';
  var SORT_DESC_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M6 13l6 6 6-6"/></svg>';

  function sortStateOf(scope) {
    if (scope === 'dash') return state.dashSort;
    if (scope === 'jail') return state.jailOpts.sort;
    return state.bans.sort;
  }

  function rerenderSort(scope) {
    if (scope === 'dash' && state.view === 'dashboard') renderDashboard();
    else if (scope === 'jail' && state.view === 'jails') renderJails();
    else if (scope === 'ban' && state.view === 'bans') renderBans();
  }

  // options: [[key, label], …]
  function sortControl(scope, options, sort) {
    var up = sort.dir === 'asc';
    var tip = up ? t('common.sortAsc') : t('common.sortDesc');
    var html = '<div class="sort-ctrl">' +
      '<select class="sort-key" data-sortkey="' + scope + '" aria-label="' + esc(t('common.sortBy')) + '">';
    options.forEach(function (item) {
      html += '<option value="' + esc(item[0]) + '"' +
        (sort.key === item[0] ? ' selected' : '') + '>' + esc(item[1]) + '</option>';
    });
    html += '</select>' +
      '<button type="button" class="icon-btn sort-dir" data-act="sort-dir" data-scope="' + scope + '"' +
      ' title="' + esc(tip) + '" aria-label="' + esc(tip) + '">' +
      (up ? SORT_ASC_ICON : SORT_DESC_ICON) + '</button></div>';
    return html;
  }

  // 切换字段时给一个合理的初始方向：文本类升序，计数/时间类降序（先看最大的）
  function defaultSortDir(scope, key) {
    return (key === 'name' || key === 'ip') ? 'asc' : 'desc';
  }

  // 概览表排序：数字列降序（最多的排前面）更符合直觉，名称列升序
  var DASH_SORT_KEYS = {
    name: function (jail) { return String(jail.name || ''); },
    count: function (jail) { return (jail.status && jail.status.currentlyBanned) || 0; },
    total: function (jail) { return (jail.status && jail.status.totalBanned) || 0; },
    failed: function (jail) { return (jail.status && jail.status.totalFailed) || 0; }
  };

  function sortDashboardJails(list, sort) {
    var pick = DASH_SORT_KEYS[sort.key] || DASH_SORT_KEYS.name;
    var dir = sort.dir === 'desc' ? -1 : 1;
    return list.slice().sort(function (a, b) {
      var result = cmpValues(pick(a), pick(b));
      if (!result) return cmpValues(a.name, b.name);
      return result * dir;
    });
  }

  function statCard(label, value, foot) {
    return '<div class="stat"><div class="label">' + esc(label) + '</div>' +
      '<div class="value">' + esc(value) + '</div>' +
      '<div class="foot">' + esc(foot) + '</div></div>';
  }

  function kv(key, value) {
    return '<dt>' + esc(key) + '</dt><dd>' + value + '</dd>';
  }

  function shorten(text) {
    text = String(text || '');
    return text.length > 46 ? '…' + text.slice(-45) : text;
  }

  /* 三种状态各一个徽章，各自占一列（表格里并列显示，不再叠在名称下面）。
     徽章文案本身带 <span class="badge …">，不在这里拼容器。 */
  function managedBadge(jail) {
    if (jail.managed) return t('jail.badgeManaged');
    if (jail.preset) return t('jail.badgePreset');
    return t('jail.badgeCustom');
  }

  function enabledBadge(jail) {
    return jail.enabled ? t('jail.badgeEnabled') : t('jail.badgeDisabled');
  }

  /* 运行状态只表达「引擎此刻是否在跑这个 Jail」，与「是否启用」是两回事：
     停掉一个已启用的 Jail 之后它仍然启用，只是没在运行。 */
  function runStateBadge(jail) {
    if (jail.active) return t('dash.badgeRunning');
    return t('jail.badgeNotRunning');
  }

  // 概览与 Jail 管理两个页面列的是同一批字段，共用一套表头和行模板：
  // 各写一份的结果就是「概览里徽章挤在名称下面、Jail 管理里拆成三列」这种不一致。
  // actions 决定右侧按钮：Jail 管理给「配置 / 日志 / 启动(停止)」，
  // 概览给「配置 / 封禁」—— 一键跳到封禁页并带上该 Jail。
  function jailTableHtml(list, actions) {
    var html = '<div class="table-wrap"><table class="fixed jail-table"><thead><tr>' +
      '<th class="c-jail">' + t('jail.colName') + '</th>' +
      ("<th class=\"c-managed hide-sm\">" + t('jail.colManaged') + "</th>") +
      ("<th class=\"c-enabled\">" + t('jail.colEnabled') + "</th>") +
      ("<th class=\"c-runstate\">" + t('jail.colRunState') + "</th>") +
      ('<th class="c-count hide-md">maxretry</th>' +
       '<th class="c-count hide-md">findtime</th>' +
       '<th class="c-count hide-md">bantime</th>' +
       "<th class=\"c-sum\">" + t('dash.colBans') + "</th>" +
       "<th class=\"c-sum\">" + t('dash.colTotal') + "</th>" +
       "<th class=\"c-sum\">" + t('dash.colFailed') + "</th>" +
       "<th class=\"c-acts\">" + t('common.actions') + "</th>") +
      '</tr></thead><tbody>';
    list.forEach(function (jail) {
      var jst = jail.status || {};
      var acts = actions === 'bans'
        ? '<button class="btn sm" data-act="jail-edit" data-name="' + esc(jail.name) + ("\">" + t('common.config') + "</button>") +
          '<button class="btn sm" data-act="view-bans" data-name="' + esc(jail.name) + ("\">" + t('common.ban') + "</button>")
        : '<button class="btn sm" data-act="jail-edit" data-name="' + esc(jail.name) + ("\">" + t('common.config') + "</button>") +
          '<button class="btn sm" data-act="jail-log" data-name="' + esc(jail.name) + ("\">" + t('common.logs') + "</button>") +
          (jail.active
            ? '<button class="btn sm" data-act="jail-stop" data-name="' + esc(jail.name) + ("\">" + t('common.stop') + "</button>")
            : '<button class="btn sm" data-act="jail-start" data-name="' + esc(jail.name) + ("\">" + t('common.start') + "</button>"));
      html += '<tr>' +
        '<td class="c-jail"><strong>' + esc(jail.name) + '</strong></td>' +
        '<td class="c-managed hide-sm">' + managedBadge(jail) + '</td>' +
        '<td class="c-enabled">' + enabledBadge(jail) + '</td>' +
        '<td class="c-runstate">' + runStateBadge(jail) + '</td>' +
        '<td class="c-count hide-md">' + esc(jail.values.maxretry || '—') + '</td>' +
        '<td class="c-count hide-md mono">' + esc(jail.values.findtime || '—') + '</td>' +
        '<td class="c-count hide-md mono">' + esc(jail.values.bantime || '—') + '</td>' +
        // 未运行的 Jail 后端不给 status，整列显示「—」而不是假 0
        '<td class="c-sum">' + (jail.status ? (jst.currentlyBanned || 0) : '—') + '</td>' +
        '<td class="c-sum">' + (jail.status ? (jst.totalBanned || 0) : '—') + '</td>' +
        '<td class="c-sum">' + (jail.status ? (jst.totalFailed || 0) : '—') + '</td>' +
        '<td class="c-acts"><div class="cell-actions">' + acts + '</div></td></tr>';
    });
    return html + '</tbody></table></div>';
  }

  /* --------------------------------------------------------- Jail 管理 */

  function renderJails() {
    var host = $('#view');
    var opts = state.jailOpts;
    var sort = opts.sort;
    var list = state.jails.filter(function (jail) {
      if (opts.scope === 'managed' && !jail.managed) return false;
      if (opts.scope === 'active' && !jail.active) return false;
      if (opts.scope === 'enabled' && !(jail.enabled || jail.active)) return false;
      if (opts.search) {
        var needle = opts.search.toLowerCase();
        if ((jail.name + ' ' + (jail.values.filter || '') + ' ' + (jail.values.logpath || ''))
            .toLowerCase().indexOf(needle) < 0) return false;
      }
      return true;
    });
    list = sortJailList(list, sort);

    // 页面标题由顶栏统一呈现；写入位置这类路径说明归「设置 · 说明」，
    // 不再在每页标题行重复一遍（正式界面里看不出该不该改、改了有没有用）。
    var html = '<div class="view-head"><div class="spacer"></div>' +
      ("<button class=\"btn primary\" data-act=\"jail-new\">+ " + t('jail.newTitle') + "</button></div>");

    html += '<div class="toolbar">' +
      ("<input type=\"text\" id=\"jailSearch\" class=\"toolbar-search\" placeholder=\"" + t('jail.searchPlaceholder') + "\" value=\"") + esc(opts.search) + '">' +
      '<select id="jailScope" style="width:auto">' +
      option('enabled', t('jail.scopeEnabled'), opts.scope) +
      option('active', t('jail.scopeActive'), opts.scope) +
      option('managed', t('jail.scopeManaged'), opts.scope) +
      option('all', t('jail.scopeAll'), opts.scope) +
      '</select>' +
      "<span class=\"hint\">" + t('common.countInline', { n: list.length }) + "</span>" +
      '</div>' +
      ("<div class=\"card\"><h3>" + t('jail.listTitle') + '<span class="h3-grow"></span>' +
        sortControl('jail', [
          ['name', t('jail.colName')],
          ['count', t('dash.colBans')],
          ['total', t('dash.colTotal')],
          ['failed', t('dash.colFailed')]
        ], sort) + "</h3>");

    if (!list.length) {
      html += t('jail.emptyFilteredInline') + '</div>';
    } else {
      // 过滤器与日志文件不在这里列：两者都是 fail2ban 的变量引用
      // （%(__name__)s[mode=%(mode)s] 这种），一行就要两百多像素，
      // 挤进来会把这个十一列的表撑出卡片、后面几列跟着被截断。
      // 它们仍然可以在「配置」抽屉里看到和修改，搜索也照样能命中。
      html += jailTableHtml(list, 'manage') + '</div>';
    }
    host.innerHTML = html;
  }

  // Jail 列表排序键：名称取字符串，其余取实时 status（未运行 = 0）
  var JAIL_SORT_KEYS = {
    name: function (jail) { return String(jail.name || ''); },
    count: function (jail) { return (jail.status && jail.status.currentlyBanned) || 0; },
    total: function (jail) { return (jail.status && jail.status.totalBanned) || 0; },
    failed: function (jail) { return (jail.status && jail.status.totalFailed) || 0; }
  };

  function sortJailList(list, sort) {
    var pick = JAIL_SORT_KEYS[sort.key] || JAIL_SORT_KEYS.name;
    var dir = sort.dir === 'desc' ? -1 : 1;
    return list.slice().sort(function (a, b) {
      var result = cmpValues(pick(a), pick(b));
      if (!result) return cmpValues(a.name, b.name);
      return result * dir;
    });
  }

  function option(value, label, current) {
    return '<option value="' + esc(value) + '"' + (String(current) === String(value) ? ' selected' : '') +
      '>' + esc(label) + '</option>';
  }

  /* --------------------------------------------------- Jail 编辑抽屉 */

  function findJail(name) {
    for (var i = 0; i < state.jails.length; i++) {
      if (state.jails[i].name === name) return state.jails[i];
    }
    return null;
  }

  var drawerCtx = { jail: null, channelId: null, jailLog: null, channelSecrets: {} };

  function isDrawerDirty() {
    var dirty = false;
    Array.prototype.forEach.call($('#drawerBody').querySelectorAll('[data-field]'), function (input) {
      var orig = input.dataset.orig === undefined ? '' : input.dataset.orig;
      if (String(input.value) !== orig) dirty = true;
    });
    return dirty;
  }

  function openJailEditor(name) {
    var jail = findJail(name);
    if (!jail) { toast(t('err.jailNotFound'), 'err'); return; }
    drawerCtx.jail = name;
    paintJailEditor(jail, name);
    // 列表数据不含实时状态，打开后再拉一次详情补齐封禁信息
    api('jails/' + encodeURIComponent(name)).then(function (detail) {
      if (drawerCtx.jail !== name) return;
      if (!$('#drawer').classList.contains('open')) return;
      if (isDrawerDirty()) return;
      paintJailEditor(detail, name);
    }).catch(function () { /* 详情拉取失败时保留已有界面 */ });
  }

  function paintJailEditor(jail, name) {
    // fail2ban 中 filter 缺省时取 Jail 名，这里补上以便界面显示有效值
    var view = Object.assign({}, jail.values);
    if (!view.filter) view.filter = name;
    var fields = (state.meta.jailFields || []).map(function (f) {
      return fieldHtml(f, view, jail.override, jail.inherited, jail.sources);
    }).join('');

    var body = '';
    if (jail.status && jail.status.ok) {
      body += ("<div class=\"section-title\">" + t('jail.stateTitle') + "</div>") +
        '<div class="grid cols-3">' +
        statCard(t('dash.colBans'), jail.status.currentlyBanned || 0, t('jail.statBansSub', { n: jail.status.totalBanned || 0 })) +
        statCard(t('dash.statFailed'), jail.status.currentlyFailed || 0, t('jail.statFailedSub', { n: jail.status.totalFailed || 0 })) +
        statCard(t('ban.monitorFiles'), (jail.status.fileList || []).length, (jail.status.fileList || [])[0] || '—') +
        '</div>';
      if ((jail.status.bannedIps || []).length) {
        body += ("<div class=\"section-title\">" + t('jail.bannedTitle') + "</div><div class=\"ip-list\">") +
          jail.status.bannedIps.map(function (ip) {
            return '<span class="ip-chip">' + esc(ip) +
              '<button data-act="unban" data-name="' + esc(name) + '" data-ip="' + esc(ip) + ("\" title=\"" + t('common.unban') + "\">✕</button></span>");
          }).join('') + '</div>';
      }
    }

    body += ("<div class=\"section-title\">Jail " + t('jail.paramsTitle') + "</div>") + fields;
    body += ("<div class=\"field\"><label>" + t('jail.manualBanLabel') + "</label>") +
      '<div style="display:flex;gap:8px">' +
      ("<input type=\"text\" id=\"manualIp\" placeholder=\"" + t('jail.manualBanPlaceholder') + "\">") +
      '<button class="btn" data-act="ban" data-name="' + esc(name) + ("\">" + t('common.ban') + "</button></div>") +
      ("<div class=\"help\">" + t('jail.manualBanHelp') + "</div></div>");

    body += ("<details class=\"raw\"><summary>" + t('jail.rawSummary') + "</summary><pre>") +
      esc(rawSnippet(jail)) + '</pre></details>';

    var foot = '<button class="btn danger ghost" data-act="jail-reset" data-name="' + esc(name) + ("\">" + t('jail.unmanage') + "</button>") +
      '<div class="spacer"></div>' +
      // 抽屉里直接看该 Jail 的 logpath 尾部，不用回列表页
      '<button class="btn" data-act="jail-log" data-name="' + esc(name) + ("\">" + t('common.viewLogs') + "</button>") +
      ("<button class=\"btn\" data-act=\"drawer-close\">" + t('common.cancel') + "</button>") +
      '<button class="btn primary" data-act="jail-save" data-name="' + esc(name) + ("\">" + t('common.saveApply') + "</button>");

    openDrawer((t('jail.drawerPrefix') + " ") + name, body, foot);
  }

  /* ------------------------------------------------------ 单 Jail 日志 */

  // 抽屉里的日志区：GET /api/jails/<name>/log?lines=200
  // 后端失败（400 未配置 logpath / 404 文件不存在）会把 error 文案直接展示出来，
  // 不让它变成一句「加载失败」——用户需要知道是配置问题还是文件问题。
  function jailsLogBody(data) {
    var files = data.files || [];
    var lines = data.lines || [];
    var missing = data.missing || [];
    return '<div class="toolbar" style="margin-bottom:10px">' +
      t('log.logHintInline', { n: lines.length }) +
      (data.truncated ? t('jail.badgeTruncated') : '') +
      '<div class="spacer" style="flex:1"></div>' +
      '<button class="btn sm" data-act="jail-log-refresh" data-name="' + esc(data.jail || '') +
      ("\">" + t('common.refresh') + "</button></div>") +
      '<dl class="kv">' +
      kv(t('dash.colLogFile'), '<span class="mono">' + esc(files.join(t('common.dunhao')) || '—') + '</span>') +
      (missing.length
        ? kv(t('common.notFound'), '<span class="mono">' + esc(missing.join(t('common.dunhao'))) + '</span>')
        : '') +
      '</dl>' +
      (lines.length
        ? '<div class="log-box" id="jailLogBox" style="margin-top:12px">' +
          esc(lines.join('\n')) + '</div>'
        : t('jail.logEmptyInline'));
  }

  function openJailLog(name) {
    var body = ("<div class=\"section-title\">" + t('jail.logReading') + "</div>") +
      ("<div class=\"empty\"><span class=\"spinner\"></span> " + t('jail.logReadingPrefix') + " <span class=\"mono\">") +
      esc(name) + ("</span> " + t('jail.logTailSuffix') + "</div>");
    var foot = '<div class="spacer"></div>' +
      ("<button class=\"btn\" data-act=\"drawer-close\">" + t('common.close') + "</button>");
    openDrawer((t('jail.logTitlePrefix') + " ") + name, body, foot);
    // 从「配置 Jail」抽屉点进来时，要作废那边的上下文：
    // 否则它的详情回调（不脏就重绘）会把编辑器重新画回来，把日志盖掉
    drawerCtx.jail = null;
    drawerCtx.jailLog = name;
    loadJailLog(name);
  }

  function loadJailLog(name) {
    api('jails/' + encodeURIComponent(name) + '/log?lines=200').then(function (data) {
      if (!$('#drawer').classList.contains('open')) return;
      if (drawerCtx.jailLog !== name) return;
      $('#drawerBody').innerHTML = jailsLogBody(data);
      var box = $('#jailLogBox');
      if (box) box.scrollTop = box.scrollHeight;
    }).catch(function (err) {
      if (!$('#drawer').classList.contains('open')) return;
      if (drawerCtx.jailLog !== name) return;
      $('#drawerBody').innerHTML = '<div class="banner danger"><div class="banner-body">' +
        ("<strong>" + t('jail.logFailedTitle') + "</strong>") + esc(localizeErr(err)) + '</div></div>' +
        ("<div class=\"help\">" + t('jail.logFailedHelp1')) +
        (t('jail.logFailedHelp2') + "</div>");
    });
  }

  function rawSnippet(jail) {
    var lines = ['[' + jail.name + ']'];
    var override = jail.override || {};
    Object.keys(override).forEach(function (key) { lines.push(key + ' = ' + override[key]); });
    if (lines.length === 1) lines.push(t('jail.unmanagedComment'));
    return lines.join('\n');
  }

  function fieldHtml(field, values, override, inherited, sources) {
    var key = field.key;
    var hasOverride = Object.prototype.hasOwnProperty.call(override || {}, key);
    var value = values[key] === undefined ? '' : String(values[key]);
    var inh = (inherited || {})[key] || '';
    var source = (sources || {})[key];
    var sourceName = source ? String(source).split(/[\\/]/).pop() : '';

    var hint = '';
    if (hasOverride) {
      hint = inh ? t('jail.overriddenHint', { value: esc(inh) }) : (t('jail.overridden') + t('jail.overriddenSuffix'));
    } else if (inh) {
      hint = t('jail.inheritedFrom', { name: esc(sourceName), value: esc(inh) });
    } else if (sourceName) {
      hint = t('jail.sourcePrefix') + esc(sourceName);
    }

    var control;
    if (field.type === 'bool') {
      var on = isOn(value);
      control = '<div class="switch">' +
        switchToggle('data-toggle', key, on, tSrc(field.label)) +
        '<span class="hint" data-bool-label="' + esc(key) + '">' + (on ? t('common.enabled') : t('common.close')) +
        (hasOverride ? '' : t('common.inherited')) + '</span>' +
        (hasOverride ? '<button type="button" class="btn sm ghost" data-clear="' + esc(key) + ("\">" + t('jail.restoreInherit') + "</button>") : '') +
        '<input type="hidden" data-field="' + esc(key) + '" data-orig="' + esc(value) + '" value="' + esc(value) + '">' +
        '</div>';
    } else if (field.type === 'select') {
      control = '<select data-field="' + esc(key) + '" data-orig="' + esc(value) + '">' +
        '<option value=""' + (value === '' ? ' selected' : '') + (">" + t('common.inherited') + "</option>") +
        (field.options || []).map(function (opt) {
          return '<option value="' + esc(opt) + '"' + (String(value) === String(opt) ? ' selected' : '') +
            '>' + esc(opt) + '</option>';
        }).join('') + '</select>';
    } else {
      control = '<input type="' + (field.type === 'int' ? 'number' : 'text') + '" ' +
        'data-field="' + esc(key) + '" data-orig="' + esc(value) + '" value="' + esc(value) + '" ' +
        'placeholder="' + esc(tSrc(field.placeholder || '')) + '">';
    }

    return '<div class="field"><label>' + esc(tSrc(field.label)) + ' <span class="mono">' + esc(key) + '</span></label>' +
      control +
      (field.help ? '<div class="help">' + esc(tSrc(field.help)) + '</div>' : '') +
      (hint ? '<div class="merged-hint">' + hint + '</div>' : '') +
      '</div>';
  }

  function collectFields(root) {
    var values = {};
    Array.prototype.forEach.call(root.querySelectorAll('[data-field]'), function (input) {
      var key = input.dataset.field;
      var orig = input.dataset.orig === undefined ? '' : input.dataset.orig;
      var now = input.value === undefined ? '' : String(input.value);
      if (now !== orig) values[key] = now;
    });
    return values;
  }

  /* --------------------------------------------------------- 封禁管理 */

  // 「全部 Jail」汇总视图的哨兵值，避免与真实 Jail 名冲突
  var BAN_ALL = '__all__';

  function loadBanStatus(name) {
    if (!name) { state.bans.status = null; return Promise.resolve(); }
    if (name === BAN_ALL) {
      return api('bans').then(function (data) { state.bans.all = data; });
    }
    return api('jails/' + encodeURIComponent(name)).then(function (data) {
      state.bans.status = data.status || null;
    });
  }

  // 封禁列表的搜索 / 排序 / 勾选都在前端做：数据量小，不值得为它加接口参数
  function banSearchHit(text) {
    var needle = (state.bans.search || '').trim().toLowerCase();
    if (!needle) return true;
    return String(text || '').toLowerCase().indexOf(needle) >= 0;
  }

  function sortBanRecords(records, sort) {
    var dir = sort.dir === 'desc' ? -1 : 1;
    return records.slice().sort(function (a, b) {
      var result;
      if (sort.key === 'ip') result = cmpValues(ipSortKey(a.ip), ipSortKey(b.ip));
      else if (sort.key === 'expires') result = cmpValues(Number(a.expiresAt) || 0, Number(b.expiresAt) || 0);
      else result = cmpValues(String(a.ip || ''), String(b.ip || ''));
      if (!result) result = cmpValues(ipSortKey(a.ip), ipSortKey(b.ip));
      return result * dir;
    });
  }

  function banToolbarHtml(total, shown) {
    var sort = state.bans.sort;
    return '<div class="toolbar">' +
      '<input type="text" id="banSearch" class="toolbar-search" ' +
      ("placeholder=\"" + t('ban.searchPlaceholder') + "\" value=\"") + esc(state.bans.search) + '">' +
      '<div class="spacer" style="flex:1"></div>' +
      '<span class="hint">' + (shown === total ? t('common.count', { n: total })
        : t('ban.matched', { shown: shown, total: total })) + '</span>' +
      sortControl('ban', [
        ['ip', t('ban.colIp')],
        ['expires', t('ban.colExpires')]
      ], sort) +
      '</div>';
  }

  // 单 Jail 视图的 IP -> {bannedAt, expiresAt} 映射。
  // 后端给 status.banInfo（主）与 status.bans（备），字段缺失一律当没有。
  function singleBanMap(status) {
    var map = {};
    if (!status) return map;
    var info = status.banInfo;
    if (info && typeof info === 'object' && !Array.isArray(info)) {
      Object.keys(info).forEach(function (ip) {
        var rec = info[ip];
        if (rec && typeof rec === 'object') map[ip] = rec;
      });
    }
    if (Array.isArray(status.bans)) {
      status.bans.forEach(function (rec) {
        if (!rec || !rec.ip) return;
        if (!map[rec.ip]) map[rec.ip] = { bannedAt: rec.bannedAt, expiresAt: rec.expiresAt };
      });
    }
    return map;
  }

  // 单 Jail 的封禁列表与监控信息
  function renderBanList(status) {
    var st = status || state.bans.status;
    if (!st) {
      return t('ban.jailNotRunningInline');
    }
    if (!st.ok) {
      return t('err.loadFailedInline', { detail: esc(st.error || t('common.unknownError')) });
    }
    var jailName = String(state.bans.jail || '');
    var banMap = singleBanMap(st);
    var all = (st.bannedIps || []).map(function (ip) {
      return {
        ip: ip,
        jails: [jailName],
        bannedAt: (banMap[ip] || {}).bannedAt,
        expiresAt: (banMap[ip] || {}).expiresAt
      };
    });
    var records = sortBanRecords(all.filter(function (item) {
      return banSearchHit(item.ip + ' ' + jailName);
    }), state.bans.sort);

    var html = banToolbarHtml(all.length, records.length);
    // 卡片与标题一起输出：i18n 改写时这里丢掉过 <div class="card"><h3> 前缀
    html += ('<div class="card"><h3>' + t('ban.currentList', { shown: records.length, total: all.length })) + "</h3>";
    if (!all.length) {
      html += t('ban.noneBannedInline');
    } else if (!records.length) {
      html += t('ban.noMatchInline', { q: esc(state.bans.search) });
    } else {
      html += '<div class="table-wrap"><table class="fixed ban-table"><thead><tr>' +
        '<th class="c-ip">' + t('ban.colIp') + '</th>' +
        ("<th class=\"c-time\">" + t('ban.colBannedAt') + "</th>") +
        ("<th class=\"c-time\">" + t('ban.colExpires') + "</th>") +
        ("<th class=\"c-time\">" + t('ban.colRemaining') + "</th>") +
        '<th class="c-acts"></th></tr></thead><tbody>' +
        records.map(function (item) {
          return '<tr><td class="c-ip mono">' + esc(item.ip) +
            '<button type="button" class="copy-btn" data-act="copy-ip" data-ip="' + esc(item.ip) +
            ("\" title=\"" + t('common.copyIp') + "\">" + t('common.copy') + "</button></td>") +
            banTimeCells(item) +
            '<td class="c-acts"><div class="cell-actions">' +
            '<button class="btn sm danger" data-act="unban" ' +
            'data-name="' + esc(jailName) + '" data-ip="' + esc(item.ip) + ("\">" + t('common.unban') + "</button>") +
            '</div></td></tr>';
        }).join('') + '</tbody></table></div>';
    }
    html += '</div>';

    var files = st.fileList || [];
    html += ("<div class=\"card\"><h3>" + t('ban.monitorTitle') + "</h3><dl class=\"kv\">") +
      kv(t('ban.currentFailed'), t('unit.times', { n: String(st.currentlyFailed || 0) })) +
      kv(t('ban.totalFailed'), t('unit.times', { n: String(st.totalFailed || 0) })) +
      kv(t('ban.totalBanned'), t('unit.times', { n: String(st.totalBanned || 0) })) +
      kv(t('ban.monitorFiles'), '<span class="mono">' + esc(files.join(t('common.dunhao')) || '—') + '</span>') +
      '</dl></div>';
    return html;
  }

  // 汇总视图里勾选的 IP；数据刷新后要剔除已经不存在的条目
  function syncBanSelection() {
    var data = state.bans.all;
    var entries = (data && data.entries) || [];
    var selected = state.bans.selected;
    var alive = {};
    entries.forEach(function (item) { alive[item.ip] = true; });
    Object.keys(selected).forEach(function (ip) {
      if (!alive[ip]) delete selected[ip];
    });
    return selected;
  }

  function selectedIps() {
    return Object.keys(state.bans.selected);
  }

  // 汇总视图里当前可见（搜索过滤后）的条目，用于「全选」与批量解封
  function filteredBanRecords() {
    var data = state.bans.all;
    var entries = (data && data.entries) || [];
    return sortBanRecords(entries.filter(function (item) {
      return banSearchHit(item.ip + ' ' + (item.jails || []).join(' '));
    }), state.bans.sort);
  }

  // 所有运行中 Jail 的封禁 IP 汇总：同一个 IP 命中多个 Jail 只显示一行
  function renderBanSummary(data) {
    if (!data) return t('ban.allEmptyInline');

    var entries = data.entries || [];
    var selected = syncBanSelection();
    var picked = selectedIps();
    var records = filteredBanRecords();
    var allPicked = records.length > 0 && records.every(function (item) {
      return !!selected[item.ip];
    });

    var html = banToolbarHtml(entries.length, records.length);
    if (picked.length) {
      html += '<div class="toolbar">' +
        t('ban.selectedInline', { n: picked.length }) +
        ("<button class=\"btn danger\" data-act=\"unban-selected\">" + t('ban.unbanSelected') + "</button>") +
        ("<button class=\"btn\" data-act=\"clear-selected\">" + t('ban.clearSelection') + "</button>") +
        '</div>';
    }

    html += ('<div class="card"><h3>' + t('ban.allTitle', { shown: records.length, total: entries.length })) + "</h3>";
    if (!entries.length) {
      html += '<div class="empty">' + (data.running
        ? t('ban.allEmptyDisabled')
        : t('ban.allEmptyStopped')) + '</div>';
    } else if (!records.length) {
      html += t('ban.noMatchInline', { q: esc(state.bans.search) });
    } else {
      html += '<div class="table-wrap"><table class="fixed ban-table wide"><thead><tr>' +
        '<th class="row-check"><input type="checkbox" id="banSelectAll" data-act="select-all"' +
        (allPicked ? ' checked' : '') + ' aria-label="' + t('ban.selectAll') + '"></th>' +
        '<th class="c-ip">' + t('ban.colIp') + '</th>' +
        ("<th class=\"c-jails\">" + t('ban.colJail') + "</th>") +
        ("<th class=\"c-time\">" + t('ban.colBannedAt') + "</th>") +
        ("<th class=\"c-time\">" + t('ban.colExpires') + "</th>") +
        ("<th class=\"c-time\">" + t('ban.colRemaining') + "</th>") +
        '<th class="c-acts"></th></tr></thead><tbody>' +
        records.map(function (item) {
          var jails = item.jails || [];
          var on = !!selected[item.ip];
          return '<tr' + (on ? ' class="sel"' : '') + '>' +
            '<td class="row-check"><input type="checkbox" data-act="select-ip" data-ip="' +
            esc(item.ip) + '" data-jails="' + esc(jails.join(',')) + '"' +
            (on ? ' checked' : '') + t('ban.selectIpAriaPrefixFull') + esc(item.ip) + '"></td>' +
            '<td class="c-ip mono">' + esc(item.ip) +
            '<button type="button" class="copy-btn" data-act="copy-ip" data-ip="' + esc(item.ip) +
            ("\" title=\"" + t('common.copyIp') + "\">" + t('common.copy') + "</button></td>") +
            '<td class="c-jails">' + jails.map(function (jail) {
              return '<span class="badge muted">' + esc(jail) + '</span>';
            }).join(' ') + '</td>' +
            banTimeCells(item) +
            '<td class="c-acts"><div class="cell-actions"><button class="btn sm danger" data-act="unban-multi" ' +
            'data-ip="' + esc(item.ip) + '" data-jails="' + esc(jails.join(',')) + ("\">" + t('common.unban') + "</button>") +
            '</div></td></tr>';
        }).join('') + '</tbody></table></div>';
    }
    html += '</div>';

    var jails = data.jails || [];
    if (jails.length) {
      html += ("<div class=\"card\"><h3>" + t('ban.perJailTitle') + "</h3><dl class=\"kv\">") +
        jails.map(function (item) {
          return kv(item.name, item.ok
            ? t('unit.count', { n: String(item.count) })
            : ("<span class=\"badge danger\">" + t('err.loadFailed') + "</span> <span class=\"hint\">") +
              esc(item.error || '') + '</span>');
        }).join('') +
        ("</dl><div class=\"help\">" + t('ban.perJailHelp') + "</div></div>");
    }
    return html;
  }

  function renderBans() {
    var host = $('#view');
    var active = state.jails.filter(function (j) { return j.active; });
    var all = state.bans.jail === BAN_ALL;
    // 这一页头部原本有一个与顶栏重复的「刷新」，去掉之后整行就空了。
    // 页面标题由顶栏统一呈现，空着的 .view-head 只会白占 32px 一行高度，
    // 所以整块不再输出。
    var html = '';

    if (!active.length && !all) {
      host.innerHTML = html + '<div class="card"><div class="empty">' +
        (t('ban.noRunningJail') + "</div></div>");
      return;
    }
    // 切到具体 Jail 时汇总视图的勾选不再有意义
    if (!all) state.bans.selected = {};

    // 顶部一整行：Jail 选择器 + 手动封禁输入与按钮 + 解封全部。
    // 「解封全部」是这一页唯一的破坏性操作，推到最右并用淡红描边分开，
    // 不再让它单独占一行、也不再用它与卡片标题拼成两块。
    var banTarget = all ? (active.length ? active[0].name : '') : state.bans.jail;
    html += '<div class="toolbar ban-bar">' +
      '<select id="banJail" style="width:auto;min-width:220px">' +
      option(BAN_ALL, t('ban.allJailsOption'), state.bans.jail) +
      active.map(function (j) { return option(j.name, j.name, state.bans.jail); }).join('') +
      '</select>' +
      ("<input type=\"text\" id=\"banIp\" placeholder=\"" + t('ban.manualPlaceholder') + "\">") +
      '<button class="btn primary" data-act="ban" data-name="' + esc(banTarget) + ("\">" + t('common.ban') + "</button>") +
      '<div class="spacer" style="flex:1"></div>' +
      (all
        ? ("<button class=\"btn unban-all\" data-act=\"unban-all-jails\">" + t('ban.unbanAllJails') + "</button>")
        : '<button class="btn unban-all" data-act="unban-all" data-name="' + esc(state.bans.jail) + ("\">" + t('ban.unbanAll') + "</button>")) +
      '</div>';

    html += all ? renderBanSummary(state.bans.all) : renderBanList(state.bans.status);
    host.innerHTML = html;
  }

  /* ---------------------------------------------------------- 过滤器 */

  function renderFilters() {
    var host = $('#view');
    var sel = state.filterSel;
    var html = '<div class="view-head"><div class="spacer"></div>' +
      ("<button class=\"btn primary\" data-act=\"filter-new\">+ " + t('filter.newTitle') + "</button>") +
      (state.filterTemplates.length
        ? ("<button class=\"btn\" data-act=\"filter-template\">" + t('filter.fromTemplate') + "</button>")
        : '') +
      '</div>';

    html += '<div class="grid cols-2" style="align-items:start">';

    var opts = state.filterOpts;
    var all = state.filters || [];
    var counts = {
      enabled: all.filter(function (item) { return item.enabled; }).length,
      disabled: all.filter(function (item) { return !item.enabled; }).length,
      all: all.length
    };
    // 「已启用」= 至少被一个已启用的 Jail 引用（与 fail2ban 的 filter 默认规则一致）
    var listed = all.filter(function (item) {
      if (opts.status === 'enabled') return !!item.enabled;
      if (opts.status === 'disabled') return !item.enabled;
      return true;
    });

    html += t('filter.listInline', { n: listed.length }) +
      ("<div class=\"segmented\" id=\"filterStatus\" role=\"group\" aria-label=\"" + t('filter.filterByStatus') + "\">") +
      [['enabled', t('jail.scopeEnabled')], ['disabled', t('notify.pollDisabled')], ['all', t('jail.scopeAll')]].map(function (pair) {
        var value = pair[0];
        var on = opts.status === value;
        return '<button type="button" class="' + (on ? 'active' : '') + '"' +
          ' data-act="filter-status" data-status="' + value + '"' +
          ' aria-pressed="' + (on ? 'true' : 'false') + '">' + esc(pair[1]) +
          '<span class="seg-count">' + counts[value] + '</span></button>';
      }).join('') +
      '</div>' +
      ("<input type=\"text\" id=\"filterSearch\" placeholder=\"" + t('filter.searchPlaceholder') + "\" value=\"") +
      esc(opts.search) + '" style="margin-bottom:10px">' +
      '<div style="max-height:52vh;overflow:auto">';
    if (!listed.length) {
      html += '<div class="empty">' + (opts.status === 'all'
        ? t('filter.noneAtAll')
        : t('filter.noneInScopeText', { n: counts.all })) + '</div>';
    } else {
      html += '<table><tbody id="filterList">';
      listed.forEach(function (item) {
        var active = item.name === sel;
        var owners = item.usedBy || [];
        html += '<tr data-act="filter-edit" data-name="' + esc(item.name) + '" ' +
          'style="cursor:pointer' + (active ? ';background:var(--accent-soft)' : '') + '" ' +
          'data-search="' + esc(item.name.toLowerCase()) + '">' +
          '<td><strong>' + esc(item.name) + '</strong>' +
          (item.custom ? t('filter.badgeCustom') : '') +
          (!item.preset ? t('filter.badgeCustomOnly') : '') +
          (item.enabled
            ? t('jail.badgeEnabled')
            : t('filter.badgeDisabled')) +
          (owners.length ? t('filter.usedByInline', { owners: esc(owners.join(t('common.dunhao'))) }) : '') +
          '</td></tr>';
      });
      html += '</tbody></table>';
    }
    html += '</div></div>';

    html += '<div class="card"><h3>' + (sel ? t('filter.editTitle', { name: esc(sel) }) : t('filter.detailTitle')) + '</h3>';
    if (!sel) {
      html += t('filter.pickOneInline');
    } else if (!state.filterData) {
      html += t('fnk.loadingInline');
    } else {
      var d = state.filterData;
      html += t('filter.failregexLabelField') +
        '<textarea id="filterFailregex" spellcheck="false">' + esc((d.failregex || []).join('\n')) + '</textarea></div>' +
        t('filter.ignoreregexLabelField') +
        '<textarea id="filterIgnoreregex" spellcheck="false">' + esc((d.ignoreregex || []).join('\n')) + '</textarea></div>' +
        '<div class="toolbar" style="margin-bottom:0">' +
        '<button class="btn primary" data-act="filter-save" data-name="' + esc(sel) + ("\">" + t('common.save') + "</button>") +
        (d.custom
          ? '<button class="btn danger" data-act="filter-delete" data-name="' + esc(sel) + ("\">" + t('filter.deleteCustom') + "</button>")
          : ("<span class=\"hint\">" + t('filter.onlyBasePrefix') + " ") + esc(sel) + '.local</span>') +
        '</div>' +
        t('filter.writePathInline', { path: esc(d.localPath || t('common.notCreated')) }) +
        ("<details class=\"raw\"><summary>" + t('filter.baseReadonly') + "</summary><pre>") +
        esc(d.baseContent || t('common.none')) + '</pre></details>' +
        ("<details class=\"raw\"><summary>" + t('filter.customCurrent') + "</summary><pre>") +
        esc(d.localContent || t('common.none')) + '</pre></details>';
    }
    html += '</div></div>';

    host.innerHTML = html;
    applyFilterSearch();
  }

  // 关键字过滤是纯前端的行级隐藏：整块重绘后要按当前关键字重新执行一次
  function applyFilterSearch() {
    var needle = (state.filterOpts.search || '').toLowerCase();
    Array.prototype.forEach.call(document.querySelectorAll('#filterList tr'), function (row) {
      var hit = !needle || (row.dataset.search || '').indexOf(needle) >= 0;
      row.style.display = hit ? '' : 'none';
    });
  }

  function openFilter(name) {
    state.filterSel = name;
    state.filterData = null;
    renderFilters();
    api('filters/' + encodeURIComponent(name)).then(function (data) {
      state.filterData = data;
      if (state.view === 'filters' && state.filterSel === name) renderFilters();
    }).catch(function (err) {
      toast(localizeErr(err), 'err');
    });
  }

  /* --------------------------------------------------- 过滤器模板 */

  function filterTemplateGroups() {
    var groups = [];
    (state.filterTemplates || []).forEach(function (item) {
      var group = null;
      for (var i = 0; i < groups.length; i++) {
        if (groups[i].name === item.category) { group = groups[i]; break; }
      }
      if (!group) { group = { name: item.category, items: [] }; groups.push(group); }
      group.items.push(item);
    });
    return groups;
  }

  function filterTemplateById(id) {
    var list = state.filterTemplates || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return list[i];
    }
    return null;
  }

  function filterTemplateInfoHtml(tpl) {
    var html = '<dl class="kv">' +
      kv(t('filter.tplPurpose'), esc(tSrc(tpl.desc) || '—')) +
      kv(t('filter.tplLogpath'), '<span class="mono">' + esc(tpl.logpath || '—') + '</span>') +
      (tpl.port ? kv(t('filter.tplPort'), '<span class="mono">' + esc(tpl.port) + '</span>') : '') +
      '</dl>';
    if (tpl.sample) {
      html += ("<details class=\"raw\" open><summary>" + t('filter.tplSample') + "</summary><pre>") +
        esc(tpl.sample) + '</pre></details>';
    }
    if (tpl.note) {
      html += '<div class="field" style="margin:10px 0 0"><div class="merged-hint">' +
        esc(tSrc(tpl.note)) + '</div></div>';
    }
    return html;
  }

  var BLANK_FILTER_HINT = '<dl class="kv">' +
    kv(t('filter.tplOrigin'), t('filter.tplOriginBlank')) +
    kv(t('filter.tplSyntax'), t('filter.tplAnchor')) +
    '</dl>' +
    '<div class="field" style="margin:10px 0 0"><div class="help">' +
    t('filter.tplHostNote') +
    t('filter.tplRegexNote') +
    t('filter.tplRegexCmd') +
    '</div></div>';

  function applyFilterTemplate(id) {
    var info = $('#ftInfo');
    var name = $('#ftName');
    var failregex = $('#ftFailregex');
    var ignoreregex = $('#ftIgnoreregex');

    if (!id) {
      if (info) info.innerHTML = BLANK_FILTER_HINT;
      if (name) name.value = '';
      if (failregex) failregex.value = '';
      if (ignoreregex) ignoreregex.value = '';
      updateFilterTemplateWarn();
      return;
    }

    var tpl = filterTemplateById(id);
    if (!tpl) return;
    if (info) info.innerHTML = filterTemplateInfoHtml(tpl);
    if (name) name.value = tpl.name || '';
    if (failregex) failregex.value = (tpl.failregex || []).join('\n');
    if (ignoreregex) ignoreregex.value = (tpl.ignoreregex || []).join('\n');
    updateFilterTemplateWarn();
  }

  function updateFilterTemplateWarn() {
    var box = $('#ftWarn');
    if (!box) return;
    var name = ($('#ftName') || {}).value.trim();
    box.innerHTML = '';
    if (!name) return;
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(name)) {
      box.innerHTML = t('filter.nameRuleInline');
      return;
    }
    var exists = false;
    (state.filters || []).forEach(function (item) { if (item.name === name) exists = true; });
    if (exists) {
      box.innerHTML = t('filter.nameTakenInline');
    }
  }

  // templateId 传 '' 表示空白新建；传 null / undefined 表示选中第一个模板
  function openFilterCreateDrawer(templateId) {
    var groups = filterTemplateGroups();
    var firstId = groups.length ? groups[0].items[0].id : '';
    var initial = (templateId === null || templateId === undefined) ? firstId : templateId;
    var dir = (state.meta && state.meta.paths && state.meta.paths.filterD) || '/etc/fail2ban/filter.d';

    var body = ("<div class=\"field\"><label>" + t('filter.tplOrigin') + "</label><select id=\"ftTemplate\">") +
      ("<option value=\"\">" + t('filter.blankOption') + "</option>") +
      groups.map(function (group) {
        return '<optgroup label="' + esc(tSrc(group.name)) + '">' +
          group.items.map(function (item) {
            return '<option value="' + esc(item.id) + '">' + esc(tSrc(item.label)) + '</option>';
          }).join('') +
          '</optgroup>';
      }).join('') + '</select>' +
      ("<div class=\"help\">" + t('filter.blankHelp') + "</div></div>") +
      '<div id="ftInfo"></div>' +
      ("<div class=\"section-title\">" + t('filter.defTitle') + "</div>") +
      ("<div class=\"field\"><label>" + t('filter.nameLabel') + "</label>") +
      ("<input type=\"text\" id=\"ftName\" spellcheck=\"false\" placeholder=\"" + t('filter.namePlaceholder') + "\">") +
      ("<div class=\"help\">" + t('filter.saveHint') + "</div>") +
      '<div id="ftWarn"></div></div>' +
      t('filter.failregexLabelMustField') +
      '<textarea id="ftFailregex" spellcheck="false"></textarea></div>' +
      t('filter.ignoreregexLabelField') +
      '<textarea id="ftIgnoreregex" spellcheck="false" style="min-height:70px"></textarea></div>' +
      ("<div class=\"help\">" + t('filter.createHint')) +
      (dir ? ("<br>" + t('filter.actualPathPrefix') + "<span class=\"mono\">") + esc(dir) + '</span>' : '') + '</div>';

    var foot = '<div class="spacer"></div>' +
      ("<button class=\"btn\" data-act=\"drawer-close\">" + t('common.cancel') + "</button>") +
      ("<button class=\"btn primary\" data-act=\"filter-create\">" + t('filter.createBtn') + "</button>");

    openDrawer(t('filter.newTitle'), body, foot);

    var select = $('#ftTemplate');
    if (select) select.value = initial;
    applyFilterTemplate(initial);

    var nameInput = $('#ftName');
    if (nameInput) {
      nameInput.addEventListener('input', updateFilterTemplateWarn);
      nameInput.focus();
    }
  }

  /* ------------------------------------------------------------ 备份 */

  var BACKUP_INTERVALS = [1, 3, 6, 12, 24, 72, 168];

  function backupSettingsFromForm() {
    var settings = JSON.parse(JSON.stringify(state.backup.settings));
    var directory = $('#bkDirectory');
    var interval = $('#bkInterval');
    var keep = $('#bkKeep');
    if (directory) settings.directory = directory.value.trim();
    if (interval) settings.intervalHours = parseInt(interval.value, 10) || 24;
    if (keep) settings.keep = parseInt(keep.value, 10) || 10;
    return settings;
  }

  /* 维护分区：导出 / 自动备份 / 备份文件，三张卡片竖排。
     结构照维护页的通行做法：每张卡一副标题 + 说明 + 动作，
     动作放卡片里而不是塞进标题行，标题行只留右侧的单个主按钮。 */
  function renderBackupCard() {
    var data = state.backup;
    if (!data) return '<div class="card">' + t('fnk.loadingInline') + '</div>';
    var settings = data.settings;
    var status = settings.lastStatus;

    // ① 导出
    var html = '<div class="card">' + cardHead(t('maint.exportTitle')) +
      '<p class="dim card-desc">' + esc(t('maint.exportDesc')) + '</p>' +
      '<div class="card-acts top">' +
      '<button class="btn" data-act="backup-export">' + t('maint.exportDownload') + '</button>' +
      '<button class="btn" data-act="backup-run">' + t('maint.exportToDir') + '</button>' +
      '</div>' +
      '<div class="card-hint">' + esc(t('maint.exportHint')) + '</div>' +
      '<div class="card-note">' + esc(t('maint.exportScope')) + '</div>' +
      '<div class="sep"></div>' +
      '<dl class="kv">' +
      kv(t('maint.dirLabel'), '<span class="mono">' + esc(data.directory || '—') + '</span>') +
      kv(t('maint.defaultDir'), '<span class="mono">' + esc(data.defaultDirectory || '—') + '</span>') +
      '</dl></div>';

    // ② 自动备份
    html += '<div class="card">' + cardHead(t('set.backupTitle')) +
      '<p class="dim card-desc">' + esc(t('set.backupEnableHelp')) + '</p>' +
      '<div class="swrow">' +
      '<div class="swtext"><div class="swname">' + esc(t('set.backupEnable')) + '</div>' +
      '<div class="hint">' + esc(t('maint.autoDesc')) + '</div></div>' +
      switchToggle('data-toggle-backup', 'enabled', settings.enabled, t('set.backupEnable')) +
      '</div>' +
      '<div class="form-grid">' +
      '<div class="field"><label>' + t('set.backupInterval') + '</label><select id="bkInterval">' +
      BACKUP_INTERVALS.map(function (hours) {
        return option(hours, hours < 24 ? t('unit.hours', { n: hours })
          : t('unit.days', { n: hours / 24 }), settings.intervalHours);
      }).join('') + '</select></div>' +
      '<div class="field"><label>' + t('set.backupKeep') + '</label>' +
      '<input type="number" id="bkKeep" min="1" max="200" value="' + esc(settings.keep) + '">' +
      '<div class="hint">' + t('set.backupKeepHelp') + '</div></div>' +
      '</div>' +
      '<div class="field"><label>' + t('set.backupDir') + '</label>' +
      '<input type="text" id="bkDirectory" list="bkLocations" value="' +
      esc(data.directory || '') + '" placeholder="' + esc(t('set.backupDirPlaceholder')) + '">' +
      '<datalist id="bkLocations">' +
      (data.locations || []).map(function (item) {
        // 后端给 kind + 路径变量，标签在本地按语言拼，英文界面才不会出现中文
        var label = item.label;
        if (item.kind === 'default') {
          label = t('set.backupLocDefault', { path: item.defaultDir || '' });
        } else if (item.kind === 'volume') {
          label = t('set.backupLocVolume', { volume: item.volume || item.path });
        } else if (item.kind === 'share') {
          label = t('set.backupLocShare', { name: item.name || '', volume: item.volume || '' });
        }
        return '<option value="' + esc(item.path) + '">' + esc(tSrc(label)) + '</option>';
      }).join('') + '</datalist>' +
      '<div class="hint">' + t('set.backupDirHelp') + '</div></div>' +
      '<div class="swrow last">' +
      '<div class="swtext"><div class="swname">' + esc(t('set.backupIncludeNotif')) + '</div>' +
      '<div class="hint">' + esc(t('set.backupIncludeNotifHelp')) + '</div></div>' +
      switchToggle('data-toggle-backup', 'includeNotifications', settings.includeNotifications,
        t('set.backupIncludeNotif')) +
      '</div>' +
      '<div class="sep"></div>' +
      '<dl class="kv">' +
      kv(t('maint.lastSuccess'), status && status.ok
        ? '<span class="mono">' + esc(fmtTime(status.time)) + '</span>'
        : '<span class="faint">' + t('maint.none') + '</span>') +
      kv(t('maint.lastError'), status && !status.ok
        ? '<span style="color:var(--danger)">' + esc(localizeStatusMessage(status)) + '</span>'
        : '<span class="faint">' + t('maint.none') + '</span>') +
      '</dl>' +
      '<div class="card-acts">' +
      '<button class="btn primary" data-act="backup-save">' + t('common.saveSettings') + '</button>' +
      '<button class="btn" data-act="backup-run">' + t('set.backupNow') + '</button>' +
      '</div></div>';

    // ③ 备份文件：三列（创建时间 / 大小 / 恢复·删除），表头右侧挂条数。
    // 文件名只是归档命名规则，用户关心的是「什么时候备的」，所以把时间戳
    // 拆出来单独成列并按时间倒序，最新的那份永远在第一行。
    var backups = (data.backups || []).slice().sort(function (a, b) {
      return (Number(b.mtime) || 0) - (Number(a.mtime) || 0);
    });
    html += '<div class="card">' + cardHead(t('set.backupFiles'),
      '<span class="chip">' + backups.length + '</span>');
    if (!backups.length) {
      html += '<div class="faint">' + t('set.backupEmpty') + '</div>';
    } else {
      html += '<div class="bscroll"><div class="table-wrap"><table class="ttable tbackup">' +
        '<thead><tr>' +
        '<th>' + t('set.backupCreatedAt') + '</th>' +
        '<th class="num">' + t('common.size') + '</th>' +
        '<th class="acts-col"></th>' +
        '</tr></thead><tbody>';
      backups.forEach(function (item) {
        html += '<tr>' +
          '<td class="bcname"><div class="mono ellipsis bname" title="' + esc(item.name) + '">' +
          esc(backupLabel(item.name)) + '</div></td>' +
          '<td class="num mono">' + esc(fmtSize(item.size)) + '</td>' +
          '<td class="acts-col"><div class="acts">' +
          '<button class="btn sm" data-act="backup-restore" data-name="' +
          esc(item.name) + '">' + t('set.backupRestore') + '</button>' +
          '<button class="btn sm danger" data-act="backup-delete" data-name="' +
          esc(item.name) + '">' + t('common.delete') + '</button>' +
          '</div></td></tr>';
      });
      html += '</tbody></table></div></div>';
    }
    html += '</div>';
    return html;
  }

  /* 备份文件名是 fn-fail2ban-auto-20261108-191750.tar.gz，直接显示一列
     长得几乎一样的字符串认不出哪条最新。把时间戳抽出来单独排版。 */
  function backupLabel(name) {
    var match = /(\d{8})-(\d{6})/.exec(String(name || ''));
    if (!match) return name;
    var d = match[1];
    var t6 = match[2];
    return d.slice(0, 4) + '-' + d.slice(4, 6) + '-' + d.slice(6, 8) + ' ' +
      t6.slice(0, 2) + ':' + t6.slice(2, 4) + ':' + t6.slice(4, 6);
  }

  /* ------------------------------------------------------ 白名单编辑 */

  // ignoreip 在 fail2ban 里是空格分隔的一整串，界面上拆成「输入框 + 已添加 Chip 列表」。
  // 提交时重新拼回空格分隔字符串，格式与后端既有契约完全一致。
  function splitIgnoreIp(value) {
    return String(value || '').split(/[\s,]+/).filter(function (item) { return item; });
  }

  function ignoreIpState() {
    if (!state.settings) return [];
    if (!Array.isArray(state.ignoreIpDraft)) {
      state.ignoreIpDraft = splitIgnoreIp(state.settings.values.ignoreip);
    }
    return state.ignoreIpDraft;
  }

  // ignoreip 字段在全局设置里要换成自定义控件，其它字段仍走 fieldHtml
  function ignoreIpFieldHtml() {
    return ("<div class=\"field\"><label>" + t('set.ignoreipLabel') + "</label>") +
      '<div id="ignoreIpWrap">' + ignoreIpChipsHtml(ignoreIpState()) + '</div>' +
      '<div class="row" data-chip-add>' +
      ("<input type=\"text\" id=\"ignoreIpInput\" placeholder=\"" + t('set.ignoreipPlaceholder') + "\">") +
      ("<button type=\"button\" class=\"btn\" data-act=\"ignoreip-add\">" + t('common.add') + "</button>") +
      '</div>' +
      ("<div class=\"help\">" + t('set.ignoreipHelp1')) +
      t('set.ignoreipHelp2') +
      (t('set.ignoreipHelp3') + "</div></div>");
  }

  function addIgnoreIpFromInput() {
    var ipInput = $('#ignoreIpInput');
    var raw = ipInput ? ipInput.value : '';
    if (!splitIgnoreIp(raw).length) { toast(t('err.fillIpOrCidr'), 'warn'); return; }
    var added = addIgnoreIp(raw);
    if (ipInput) { ipInput.value = ''; ipInput.focus(); }
    renderIgnoreIpList();
    if (!added) toast(t('err.dupIgnoreIp'), 'warn', 2400);
  }

  function renderIgnoreIpList() {
    var items = ignoreIpState();
    var host = $('#ignoreIpWrap');
    if (!host) return;
    host.innerHTML = ignoreIpChipsHtml(items);
  }

  function ignoreIpChipsHtml(items) {
    if (!items.length) {
      return t('set.ignoreipEmptyInline');
    }
    return '<div class="ip-list" id="ignoreIpList">' +
      items.map(function (value, index) {
        return '<span class="ip-chip">' + esc(value) +
          '<button type="button" data-act="ignoreip-remove" data-index="' + index +
          ("\" title=\"" + t('set.remove') + "\" aria-label=\"" + t('set.remove') + " ") + esc(value) + '">✕</button></span>';
      }).join('') + '</div>';
  }

  function addIgnoreIp(raw) {
    var added = 0;
    splitIgnoreIp(raw).forEach(function (value) {
      var list = ignoreIpState();
      if (list.indexOf(value) >= 0) return;
      list.push(value);
      added++;
    });
    return added;
  }

  /* -------------------------------------------------------- 全局设置 */

  /* ------------------------------------------------------- 设置页 */

  // 每个分区只拉自己需要的接口：进「关于」不必把通知渠道也读一遍。
  // 已缓存的数据不再重复请求，切分区是瞬时的。
  function loadSettingsTab(tab) {
    var tasks = [];
    // policy 与 about 都要读「全局设置」，缺了才拉，已缓存就直接用
    function needSettings() {
      if (state.settings) return;
      tasks.push(api('settings').then(function (d) {
        state.settings = d;
        // 服务端数据变了：白名单草稿要以返回值为准重新拆分
        state.ignoreIpDraft = splitIgnoreIp(d.values && d.values.ignoreip);
      }));
    }
    if (tab === 'policy' || tab === 'about') {
      needSettings();
    } else if (tab === 'notify') {
      tasks.push(api('notifications').then(function (d) {
        state.notifications = d;
        restartBrowserNotify();
      }), loadDeliveries());
    } else if (tab === 'maint') {
      tasks.push(api('backup').then(function (d) { state.backup = d; }));
    } else if (tab === 'storage') {
      tasks.push(api('storage').then(function (d) { state.storage = d; }));
    } else if (tab === 'update') {
      // 读缓存（后端未命中时自己会查一次）；不打网络风暴
      tasks.push(api('update').then(function (d) {
        state.update.info = d;
        state.update.lastCheckAt = Date.now();
        state.update.lastFailAt = 0;
        paintUpdateDot();
      }));
    } else if (tab === 'guide') {
      // 「说明」列的是真实路径与端口：settings 给写入位置，fnknock 给敲门那几张
      needSettings();
      tasks.push(api('fnknock').then(function (d) { state.fnknock = d; }));
      if (!state.overview) {
        tasks.push(api('service').then(function (info) {
          state.overview = { fail2ban: info, jails: [], summary: {}, system: {} };
        }));
      }
    }
    return tasks;
  }

  // 一次只展开一个分区，用顶部标签条切换。
  function renderSettings() {
    var host = $('#view');
    var html = settingsWorkbarHtml();
    html += '<div id="setPanel">' + settingsTabHtml(state.setTab) + '</div>';
    host.innerHTML = html;
    paintUpdateDot();
    paintNotifyDot();
  }

  function settingsWorkbarHtml() {
    var html = '<div class="workbar" id="setWorkbar" role="tablist">';
    SET_TABS.forEach(function (id) {
      var on = state.setTab === id;
      html += '<button type="button" role="tab" data-set-tab="' + esc(id) + '"' +
        ' aria-selected="' + (on ? 'true' : 'false') + '"' +
        (on ? ' class="on"' : '') + '>' + esc(setTabLabel(id)) +
        (id === 'notify' ? '<i class="workbar-dot" id="setNotifyDot" hidden></i>' : '') +
        (id === 'update' ? '<i class="workbar-dot" id="setUpdateDot" hidden></i>' : '') +
        '</button>';
    });
    return html + '</div>';
  }

  // 设置页里各分区的调度。数据没到就先给一行占位，不整页白屏。
  function settingsTabHtml(tab) {
    switch (tab) {
      case 'appearance': return renderSetAppearance();
      case 'policy': return renderSetPolicy();
      case 'notify': return renderNotifications(true);
      case 'maint': return renderSetBackup();
      case 'storage': return renderStorage(true);
      case 'update': return renderUpdate(true);
      case 'guide': return renderSetGuide();
      case 'about': return renderSetAbout();
      default: return renderSetPanel();
    }
  }

  /* ------------------------------------------------- 设置页各分区 */

  // 卡片底部动作条：主按钮在最右、危险按钮在最左，只有一个按钮时也靠右。
  function cardFoot(buttons) {
    return '<div class="card-foot"><div class="spacer"></div>' + (buttons || '') + '</div>';
  }

  // 卡片标题行 + 行内动作（如「立即备份」「新增渠道」）
  function cardHead(title, actions) {
    return '<div class="card-head"><h3>' + esc(title) + '</h3>' +
      (actions ? '<div class="spacer"></div>' + actions : '') + '</div>';
  }

  /* 分区内容。分区标题由 .set-sec-title 承担，这里不再输出第二行灰字说明：
     一行「影响面板本身的操作习惯，不改动 fail2ban 配置」这类话既占高度，
     也没有告诉用户具体能改什么，卡片里的字段标签已经说清了。
     hint 参数保留在签名上，避免各调用点逐个改。 */
  function setSection(hint, bodyHtml) {
    return bodyHtml || '';
  }

  function prefsOf() {
    return state.prefs || {};
  }

  function renderSetPanel() {
    var prefs = prefsOf();
    var body = '<div class="card">' + cardHead(t('set.panelTitle')) +
      '<div class="field"><label>' + t('set.logLinesLabel') + '</label>' +
      '<input type="number" id="prefLogLines" min="20" max="5000" value="' +
      esc(prefs.logLines || 400) + '">' +
      '<div class="help">' + t('set.logLinesHelp') + '</div></div>' +
      '<div class="field"><label>' + t('set.autoReloadLabel') + '</label>' +
      '<div class="switch">' +
      switchToggle('data-toggle-pref', 'autoReload', prefs.autoReload, t('set.autoReloadLabel')) +
      '<span class="hint">' + t('set.autoReloadHint') + '</span></div></div>' +
      '<div class="field"><label>' + t('set.confirmDangerLabel') + '</label>' +
      '<div class="switch">' +
      switchToggle('data-toggle-pref', 'confirmDanger', prefs.confirmDanger,
        t('set.confirmDangerLabel')) +
      '<span class="hint">' + t('set.confirmDangerHint') + '</span></div></div>' +
      cardFoot('<button class="btn primary" data-act="prefs-save">' +
        t('common.savePrefs') + '</button>') +
      '</div>';
    return setSection(t('set.panelHint'), body);
  }

  function renderSetAppearance() {
    var prefs = prefsOf();
    var lang = typeof I18N_ !== 'undefined' && I18N_ ? I18N_.currentLang() : 'zh';
    var body = '<div class="card">' + cardHead(t('set.appearanceTitle')) +
      '<div class="field"><label>' + t('set.themeLabel') + '</label><select id="prefTheme">' +
      option('auto', t('common.themeAuto'), prefs.theme) +
      option('dark', t('common.themeDark'), prefs.theme) +
      option('light', t('common.themeLight'), prefs.theme) +
      '</select></div>' +
      '<div class="field"><label>' + t('set.langLabel') + '</label><select id="prefLang">' +
      option('zh', t('common.langZh'), lang) +
      option('en', t('common.langEn'), lang) +
      '</select><div class="help">' + t('set.langHelp') + '</div></div>' +
      '<div class="field"><label>' + t('set.sidebarLabel') + '</label><select id="prefSidebar">' +
      option('auto', t('set.sidebarAuto'), prefs.sidebar) +
      option('expanded', t('set.sidebarExpanded'), prefs.sidebar) +
      option('collapsed', t('set.sidebarCollapsed'), prefs.sidebar) +
      '</select><div class="help">' + t('set.sidebarHelp') + '</div></div>' +
      '<div class="field"><label>' + t('set.densityLabel') + '</label><select id="prefDensity">' +
      option('comfortable', t('set.densityComfort'), prefs.density) +
      option('compact', t('set.densityCompact'), prefs.density) +
      '</select><div class="help">' + t('set.densityHint') + '</div></div>' +
      cardFoot('<button class="btn primary" data-act="prefs-save">' +
        t('common.savePrefs') + '</button>') +
      '</div>';
    return setSection(t('set.appearanceHint'), body);
  }

  function renderSetPolicy() {
    var data = state.settings;
    if (!data) return '<div class="card">' + t('fnk.loadingInline') + '</div>';
    // ignoreip 用「输入框 + Chip 列表」渲染，其余字段照旧
    var globalFields = (state.meta.globalFields || []).map(function (f) {
      if (f.key === 'ignoreip') return ignoreIpFieldHtml();
      return fieldHtml(f, data.values, data.override, data.inherited, {});
    }).join('');
    var body = '<div class="card">' + cardHead(t('set.policyTitle')) +
      '<form id="settingsForm">' + globalFields + '</form>' +
      '<div class="help">' + t('set.defaultsHint') + '</div>' +
      cardFoot('<button class="btn" data-act="refresh">' + t('set.discard') + '</button>' +
        '<button class="btn primary" data-act="settings-save">' +
        t('common.saveReload') + '</button>') +
      '</div>';
    return setSection(t('set.policyHint'), body);
  }

  function renderSetBackup() {
    if (!state.backup) return '<div class="card">' + t('fnk.loadingInline') + '</div>';
    return setSection(t('set.backupHint'), renderBackupCard());
  }

  function renderSetAbout() {
    var meta = state.meta || {};
    var app = meta.app || {};
    var paths = meta.paths || {};
    var info = (state.overview && state.overview.fail2ban) || {};
    var version = app.version || (state.session && state.session.version) || '—';
    var repo = 'https://github.com/Smi13zZ/fail2ban';
    var body = '<div class="card">' + cardHead(t('set.aboutTitle')) +
      '<p class="dim about-desc">' + esc(t('set.aboutDesc')) + '</p>' +
      '<dl class="kv">' +
      kv(t('set.versionLabel'), '<span class="mono">v' + esc(version) + '</span>') +
      kv(t('set.aboutEngine'), '<span class="mono">' + esc(info.version || '—') + '</span>') +
      kv(t('set.aboutPorts'), '<span class="mono">' + esc(app.port || '—') + '</span>') +
      kv(t('set.aboutRepo'), '<a class="mono" href="' + esc(repo) +
        '" target="_blank" rel="noopener noreferrer">Smi13zZ/fail2ban</a>') +
      kv(t('set.aboutLicense'), 'MIT') +
      '</dl>' +
      '<div class="help">' + esc(t('set.aboutHint')) + '</div>' +
      '</div>';
    return body;
  }

  /* ------------------------------------------------------ 设置 · 说明 */

  // 一条路径行：标签 + 等宽路径 + 复制按钮。路径可能很长（@appdata 下多层目录），
  // 单独占一行而不是挤在 dt/dd 里，窄屏才不会把目录名折成两三段。
  function pathRow(label, value, note) {
    if (!value) return '';
    return '<div class="pathrow">' +
      '<div class="pathrow-k">' + esc(label) +
      (note ? '<span class="pathrow-note">' + esc(note) + '</span>' : '') + '</div>' +
      '<div class="pathrow-v"><span class="mono">' + esc(value) + '</span>' +
      '<button type="button" class="btn sm" data-act="copy-path" data-path="' +
      esc(value) + '">' + t('set.guideCopy') + '</button></div></div>';
  }

  // 一条只读信息行：没有复制按钮，值本身就是一句人话（读取顺序、版本号等）
  function infoRow(label, valueHtml) {
    if (!valueHtml) return '';
    return '<div class="pathrow"><div class="pathrow-k">' + esc(label) + '</div>' +
      '<div class="pathrow-v">' + valueHtml + '</div></div>';
  }

  function guideCard(title, rows, hint) {
    if (!rows) return '';
    return '<div class="card">' + cardHead(title) + rows +
      (hint ? '<div class="help">' + esc(hint) + '</div>' : '') + '</div>';
  }

  // 「说明」分区：把原先散落在「关于」与各页角落的文件位置、端口、接口地址收在一处。
  // 分成三张卡：本面板自己写的、fail2ban 系统自带的（只读）、敲门联动写的。
  // 每张卡的标题就是归属，行内不再重复「这是谁的文件」。
  function renderSetGuide() {
    var meta = state.meta || {};
    var paths = meta.paths || {};
    var settings = state.settings || {};
    var app = meta.app || {};
    var info = (state.overview && state.overview.fail2ban) || {};
    var fnk = (state.fnknock && state.fnknock.detected) || {};
    var fnkGw = (state.fnknock && state.fnknock.gateway) || {};
    // 封禁动作文件的路径挂在 banaction 上，不在 gateway 里：
    // gateway 只描述「网关过滤器 + 联动 Jail」，动作文件是另一件东西。
    var fnkBa = (state.fnknock && state.fnknock.banaction) || {};

    var panelRows =
      infoRow(t('path.panelPort'), '<span class="mono">' +
        esc((app.bind || '0.0.0.0') + ':' + (app.port || '—')) + '</span>') +
      pathRow(t('path.web'), paths.webDir) +
      pathRow(t('path.var'), paths.varDir || paths.dataDir) +
      pathRow(t('path.etc'), paths.etcDir) +
      pathRow(t('path.backup'), paths.backupDir) +
      pathRow(t('path.log'), paths.logFile || info.logFile);

    var systemRows =
      pathRow(t('path.managed'), settings.managedFile) +
      infoRow(t('path.readOrder'), '<span class="mono">' +
        esc((settings.files || []).join('  →  ') || '—') + '</span>') +
      pathRow(t('path.jailConf'), paths.jailConf, t('path.system')) +
      pathRow(t('path.jailLocal'), paths.jailLocal, t('path.system')) +
      pathRow(t('path.jailD'), paths.jailD) +
      pathRow(t('path.filterD'), paths.filterD) +
      pathRow(t('path.actionD'), paths.actionD) +
      infoRow(t('path.socket'), '<span class="mono">' + esc(info.socket || '—') + '</span>') +
      infoRow(t('path.client'), '<span class="mono">' + esc(info.client || '—') + '</span>');

    var fnkRows = fnk.installed || fnk.varDir
      ? pathRow(t('path.fnkBaseUrl'), fnk.baseUrl || '') +
        pathRow(t('path.fnkVar'), fnk.varDir) +
        pathRow(t('path.fnkAction'), fnkBa.actionFile) +
        pathRow(t('path.fnkFilter'), fnkGw.filterPath) +
        pathRow(t('path.fnkJail'), fnkGw.logPath)
      : '<div class="empty">' + esc(t('fnk.notDetectedTitle')) + '</div>';

    // 第三张卡不带尾注：它的标题已经说清归属，再挂一句「把封禁同步过去」是功能说明，
    // 不是位置说明，混在同一张卡里会让人以为那句话在讲路径。
    return setSection(t('set.guideHint'),
      guideCard(t('path.panel'), panelRows) +
      guideCard(t('path.system'), systemRows) +
      guideCard(t('path.fnknock'), fnkRows));
  }

  /* ---------------------------------------------------------- 通知设置 */

  var notifTimer = null;
  var lastEventId = 0;

  // 后端下发的渠道元数据：label/hint 是中文源文，双语走 labels_i18n / hints_i18n。
  // 前端不硬编码任何渠道字段，新增渠道只改后端一处。
  var SECRET_MARK = '__SET__';

  function channelTypeMeta(value) {
    var list = (state.notifications && state.notifications.channelTypes) || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].value === value) return list[i];
    }
    return { value: value, label: value, hint: '', fields: [], secretFields: [] };
  }

  function channelFields(typeValue) {
    return channelTypeMeta(typeValue).fields || [];
  }

  // 双语元数据：{key: {zh, en}}。当前语言没有就退中文原文，最后退后端下发的 label。
  function metaText(map, key, fallback) {
    if (!map) return fallback || '';
    var item = map[key];
    if (typeof item === 'string' && item) return item;
    if (item && typeof item === 'object') {
      var lang = currentLang() === 'en' ? 'en' : 'zh';
      if (item[lang]) return item[lang];
      if (item.zh) return item.zh;
      if (item.en && currentLang() !== 'en') return item.en;
    }
    return fallback || '';
  }

  // 渠道类型名：优先 type 级键 __label__，再按源文反查，最后用 value
  function channelTypeLabel(meta) {
    if (!meta) return '';
    var text = metaText(meta.labels_i18n, '__label__', '');
    if (!text && currentLang() === 'en' && meta.label_en) text = meta.label_en;
    return text || tSrc(meta.label) || meta.label || meta.value || '';
  }

  function channelTypeHint(meta) {
    if (!meta) return '';
    var text = metaText(meta.hints_i18n, '__hint__', '');
    if (!text && currentLang() === 'en' && meta.hint_en) text = meta.hint_en;
    return text || tSrc(meta.hint) || meta.hint || '';
  }

  // 字段双语表挂在字段自己身上（fields[].labels_i18n / hints_i18n）；
  // 渠道级 labels_i18n 只放类型级的 __label__ / __hint__，所以这里先读字段级。
  function fieldI18n(field, kind) {
    var map = field && field[kind];
    if (!map) return '';
    var lang = currentLang() === 'en' ? 'en' : 'zh';
    if (typeof map === 'string') return map;
    return map[lang] || map.zh || map.en || '';
  }

  function fieldLabel(meta, field) {
    return fieldI18n(field, 'labels_i18n') ||
      metaText(meta.labels_i18n, field.key, '') ||
      tSrc(field.label) || field.label || field.key;
  }

  function fieldHelp(meta, field) {
    return fieldI18n(field, 'hints_i18n') ||
      metaText(meta.hints_i18n, field.key, '') ||
      tSrc(field.hint) || field.hint || '';
  }

  // 密钥字段：后端用 secretFields 标注；没给就按 type=password 兜底
  function isSecretField(meta, field) {
    if (field && field.type === 'password') return true;
    var list = (meta && meta.secretFields) || [];
    return list.indexOf(field.key) >= 0;
  }

  // 各渠道类型需要的字段不同（Webhook 地址 / corpid+Secret+AgentId …），
  // 表单按后端元数据动态渲染，新增渠道类型时后端改配置即可。
  // 密钥字段：GET 回传的是 __SET__ 掩码，输入框必须留空 + 提示「已保存，留空保留原值」，
  // 绝不能把掩码回填进输入框 —— 否则整份设置保存时会被当成新密钥写盘。
  function renderChannelFields(typeValue, values) {
    var host = $('#chFields');
    if (!host) return;
    var meta = channelTypeMeta(typeValue);
    var fields = meta.fields || [];
    if (!fields.length) { host.innerHTML = ''; return; }
    host.innerHTML = fields.map(function (field) {
      var raw = values ? values[field.key] : undefined;
      var secret = isSecretField(meta, field);
      // 后端只要回传了掩码就按「已保存」处理，不管 secretFields 有没有标到它
      var storedMasked = (raw === SECRET_MARK) || (secret && raw && raw.stored);
      var value = (raw === null || raw === undefined || raw === SECRET_MARK)
        ? (field['default'] || '') : String(raw);
      if (storedMasked) value = '';
      var label = fieldLabel(meta, field) + (field.required ? t('notify.badgeRequired') : '');
      var help = fieldHelp(meta, field);
      var hint = help ? '<div class="help">' + esc(help) + '</div>' : '';
      if (storedMasked) {
        hint += '<div class="help secret-hint">' + esc(t('notify.secretSet')) + '</div>';
      }
      var control;
      var common = ' data-ch="' + esc(field.key) + '"' +
        (secret ? ' data-secret="1" autocomplete="new-password"' : '') +
        ' placeholder="' + esc(field.placeholder || '') + '"';
      if (field.type === 'select') {
        control = '<select' + common + '>' +
          (field.options || []).map(function (opt) {
            var optLabel = metaText(opt, 'labels_i18n', '') ||
              tSrc(opt.label) || opt.label || opt.value;
            return option(opt.value, optLabel, value);
          }).join('') + '</select>';
      } else {
        control = '<input type="' + (secret ? 'password' : 'text') + '"' + common +
          ' value="' + esc(value) + '" spellcheck="false">';
      }
      return '<div class="field"><label>' + esc(label) + '</label>' + control + hint + '</div>';
    }).join('');
  }

  // 摘要只用来区分渠道，密钥字段一律不显示：GET 回传的掩码绝不能漏到界面上
  function channelSummary(channel) {
    var shown = function (value) {
      var text = String(value === undefined || value === null ? '' : value).trim();
      return (text && text !== SECRET_MARK && text !== 'true') ? text : '';
    };
    if (channel.type === 'wecom_app') {
      var parts = [];
      var corpid = shown(channel.corpid);
      if (corpid) parts.push(corpid.slice(0, 8) + '…');
      var agentid = shown(channel.agentid);
      if (agentid) parts.push('AgentId ' + agentid);
      var touser = shown(channel.touser);
      if (touser) parts.push(t('notify.receivePrefix') + ' ' + touser);
      return parts.join(' · ') || t('common.notSet');
    }
    if (channel.type === 'telegram') {
      var chat = shown(channel.chatId);
      return chat ? 'Chat ' + chat : t('common.notSet');
    }
    if (channel.type === 'wxpusher') {
      // 后端字段名是 uids / topics（多个用逗号分隔）
      var uids = shown(channel.uids);
      if (uids) return 'UID ' + uids.slice(0, 12) + '…';
      var topics = shown(channel.topics);
      return topics ? 'Topic ' + topics : t('common.notSet');
    }
    var url = shown(channel.url) || shown(channel.apiBase);
    if (url) return shortenUrl(url);
    // 地址本身就是密钥（如 Bark key、Webhook）：掩码说明已经保存，不能显示成「未填写」
    if (channel.url === SECRET_MARK || channel.apiBase === SECRET_MARK) return t('notify.secretSet');
    return t('common.notFilled');
  }

  function shortenUrl(url) {
    url = String(url || '');
    return url.length > 52 ? url.slice(0, 30) + '…' + url.slice(-18) : url;
  }

  // 开关统一用真正的 <button role="switch">：Tab 可达、Enter/Space 可切换、读屏能读出开关状态
  function switchToggle(attr, key, checked, label) {
    return '<button type="button" class="switch-track' + (checked ? ' on' : '') + '"' +
      ' role="switch" aria-checked="' + (checked ? 'true' : 'false') + '"' +
      ' aria-label="' + esc(label || key) + '" ' +
      attr + '="' + esc(key) + '"></button>';
  }

  function setSwitch(node, on) {
    if (!node) return;
    node.classList.toggle('on', !!on);
    node.setAttribute('aria-checked', on ? 'true' : 'false');
  }

  function switchRowFor(attr, key, label, checked, help) {
    return '<div class="field">' +
      '<div class="switch">' +
      switchToggle('data-toggle-' + attr, key, checked, label) +
      '<span>' + esc(label) + '</span>' +
      '</div>' +
      (help ? '<div class="help">' + esc(help) + '</div>' : '') +
      '</div>';
  }

  function switchRow(key, label, checked, help) {
    return switchRowFor('notif', key, label, checked, help);
  }

  function fmtSize(bytes) {
    if (typeof bytes !== 'number' || isNaN(bytes)) return '—';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    if (bytes < 1024 * 1024 * 1024) return (bytes / 1024 / 1024).toFixed(2) + ' MB';
    return (bytes / 1024 / 1024 / 1024).toFixed(2) + ' GB';
  }

  /* ------------------------------------------------------- 投递记录 */

  // 投递记录的事件名是后端的 ASCII 标识，界面按当前语言渲染
  var DELIVERY_EVENT_KEYS = {
    ban: 'notify.dvBan',
    unban: 'notify.dvUnban',
    serviceDown: 'notify.dvServiceDown',
    serviceRecover: 'notify.dvServiceRecover',
    test: 'notify.dvTest'
  };

  function deliveryEventLabel(event) {
    var key = DELIVERY_EVENT_KEYS[event];
    return key ? t(key) : t('notify.dvOther');
  }

  // 通知历史里后端下发的 type 也是同样五个 ASCII 值
  function eventTypeLabel(type) {
    return deliveryEventLabel(type);
  }

  // 推送正文是「中文 / English」与「中文段 + 空行 + 英文段」的中英双语文本。
  // 英文界面取英文那一段，中文界面原样保留完整双语（用户可能转发给别人看）。
  function bilingualPick(text) {
    var raw = String(text === undefined || text === null ? '' : text);
    if (currentLang() !== 'en') return raw;
    var parts = raw.split('\n\n');
    if (parts.length > 1) return parts.slice(1).join('\n\n').trim();
    var slash = raw.indexOf(' / ');
    if (slash > 0) return raw.slice(slash + 3).trim();
    return raw;
  }

  function deliveryTime(at) {
    var seconds = Number(at) || 0;
    if (!seconds) return '—';
    // 后端给的是 epoch 秒；万一某处给了毫秒也别显示成 1970 年
    if (seconds > 1e11) seconds = Math.floor(seconds / 1000);
    return fmtTime(seconds);
  }

  function deliveryPayload() {
    if (state.deliv.data) return state.deliv.data;
    var data = (state.notifications && state.notifications.deliveries) || null;
    if (!data) return null;
    return (data.items ? data : { items: data, total: data.length, kept: data.length });
  }

  function deliveryRowHtml(item) {
    var ok = item.ok !== false;
    var typeMeta = channelTypeMeta(item.type);
    var channel = item.channel || item.channel_id || t('common.unnamed');
    var detail = String(item.detail || '');
    var html = '<div class="deliv-row' + (ok ? '' : ' fail') + '">' +
      '<span class="dot' + (ok ? ' on' : ' err') + '"></span>' +
      '<div class="deliv-body">' +
      '<div class="deliv-title">' + esc(channel) +
      (item.type ? ' <span class="chip">' + esc(channelTypeLabel(typeMeta)) + '</span>' : '') +
      '</div>' +
      '<div class="deliv-meta">' +
      '<span>' + esc(deliveryEventLabel(item.event)) + '</span>' +
      '<span>' + esc(ok ? t('notify.delivOkBadge') : t('notify.delivFailBadge')) + '</span>' +
      '</div>';
    if (detail) {
      html += '<div class="deliv-detail">' +
        (ok ? '' : esc(t('notify.delivReason')) + ': ') + esc(detail) + '</div>';
    }
    html += '</div>' +
      '<span class="deliv-time">' + esc(deliveryTime(item.at)) + '</span>' +
      '</div>';
    return html;
  }

  function deliveryCardHtml() {
    var payload = deliveryPayload() || { items: [], total: 0, kept: 0 };
    var items = payload.items || [];
    // 卡片头一行放「标题 → 筛选分段 → 刷新 → 清空」：四个都在同一条 flex 上，
    // 筛选不再单独占一行，刷新与清空也不再掉到卡片底部去。
    var html = '<div class="card">' + cardHead(t('notify.delivTitle'),
      '<div class="seg">' +
      [['all', 'notify.delivFilterAll'], ['ok', 'notify.delivFilterOk'], ['fail', 'notify.delivFilterFail']]
        .map(function (pair) {
          return '<button type="button" data-act="deliv-filter" data-status="' + pair[0] + '"' +
            (state.deliv.status === pair[0] ? ' class="on"' : '') + '>' +
            t(pair[1]) + '</button>';
        }).join('') +
      '</div>' +
      '<button class="btn sm" data-act="deliv-refresh">' + t('common.refresh') + '</button>' +
      '<button class="btn sm danger" data-act="deliv-clear">' + t('notify.delivClear') + '</button>') +
      '<p class="dim card-desc">' + esc(t('notify.delivHint')) +
      (payload.kept ? ' ' + esc(t('notify.delivKept', { n: payload.kept })) : '') +
      '</p>';

    if (!items.length) {
      html += '<div class="empty">' + t('notify.delivEmpty') + '</div>';
    } else {
      html += '<div class="deliv-list">' + items.map(deliveryRowHtml).join('') + '</div>';
    }
    return html + '</div>';
  }

  function loadDeliveries() {
    var status = state.deliv.status || 'all';
    return api('notifications/deliveries?status=' + encodeURIComponent(status) + '&limit=60')
      .then(function (data) { state.deliv.data = data; })
      .catch(function () { /* 记录接口不可用不影响通知页其它部分 */ });
  }

  /* ---------------------------------------------------------- 通知设置 */

  // embedded=true 时嵌入设置页：不输出页面级 view-head，也不自己写宿主，
  // 直接把 HTML 串返回给调用方拼装。
  function renderNotifications(embedded) {
    var body = notificationsHtml();
    if (embedded) return body;
    var host = $('#view');
    host.innerHTML = '<div class="view-head"><h2>' + t('nav.notifications') + '</h2></div>' + body;
  }

  function notificationsHtml() {
    var data = state.notifications;
    if (!data) return t('fnk.loadingInline');
    var s = data.settings;

    // 三张竖排卡片：通知（总开关 + 子开关）→ 通知渠道 → 投递记录。
    // 卡片头「标题 + 间隙 + 行内动作」，动作紧贴标题右侧，不再单独占一行。
    var html = '';

    html += '<div class="card">' + cardHead(t('notify.masterTitle'),
      '<button class="btn sm primary" data-act="notif-save">' +
      t('common.saveSettings') + '</button>') +
      '<div class="help help-tight">' + esc(t('notify.hint')) + '</div>' +
      '<div class="swrow">' +
      '<div class="swtext"><div class="swname">' + esc(t('notify.enablePush')) + '</div>' +
      '<div class="hint">' + esc(t('notify.enablePushHelp')) + '</div></div>' +
      switchToggle('data-toggle-notif', 'enabled', s.enabled, t('notify.enablePush')) +
      '</div>' +
      '<div class="swrow">' +
      '<div class="swtext"><div class="swname">' + esc(t('notify.browserNotify')) + '</div>' +
      '<div class="hint">' + esc(t('notify.browserNotifyHelp')) + '</div></div>' +
      switchToggle('data-toggle-notif', 'browserNotify', s.browserNotify, t('notify.browserNotify')) +
      '</div>';

    // 事件开关逐个成行，与上面两个开关同一种排版
    (data.eventTypes || []).forEach(function (item) {
      var on = !!s.events[item.key];
      html += '<div class="swrow">' +
        '<div class="swtext"><div class="swname">' + esc(tSrc(item.label)) + '</div>' +
        (item.help ? '<div class="hint">' + esc(tSrc(item.help)) + '</div>' : '') +
        '</div>' +
        switchToggle('data-toggle-notif', 'events.' + item.key, on, tSrc(item.label)) +
        '</div>';
    });

    html += '<div class="swrow last">' +
      '<div class="swtext"><div class="swname">' + esc(t('notify.pollInterval')) + '</div>' +
      '<div class="hint">' + esc(t('notify.pollHelp')) + '</div></div>' +
      '<input type="number" id="notifInterval" min="10" max="3600" value="' +
      esc(s.pollInterval) + '">' +
      '</div>';

    var notifyF2b = data.fail2ban || {};
    html += '<div class="sep"></div><dl class="kv">' +
      kv('fail2ban', notifyF2b.installed
        ? (notifyF2b.running
          ? t('notify.badgeRunning')
          : t('notify.badgeStopped'))
        : t('dash.badgeNotInstalled')) +
      kv(t('notify.pollState'), s.enabled || s.browserNotify
        ? t('notify.pollEvery', { n: s.pollInterval }) : t('notify.pollDisabled')) +
      '</dl></div>';

    // 通知渠道
    html += '<div class="card">' + cardHead(t('notify.channelsTitle'),
      '<button class="btn sm primary" data-act="channel-new">' +
      t('notify.addChannel') + '</button>');
    if (!s.channels.length) {
      html += '<div class="empty">' +
        '<span class="empty-ic">' + BELL_ICON + '</span>' +
        esc(t('notify.channelsEmpty1')) +
        '</div>';
    } else {
      html += '<div class="chlist">';
      s.channels.forEach(function (channel) {
        var typeLabel = channelTypeLabel(channelTypeMeta(channel.type));
        html += '<div class="chrow' + (channel.enabled ? '' : ' off') + '">' +
          '<span class="chico">' + BELL_ICON + '</span>' +
          '<div class="chin">' +
          '<div class="chname">' + esc(channel.name || t('common.unnamed')) + '</div>' +
          '<div class="chmeta">' + esc(typeLabel + ' · ' +
            (channel.enabled ? t('notify.badgeRunning') : t('notify.badgeDisabled'))) + '</div>' +
          '<div class="chsum">' + esc(channelSummary(channel)) + '</div>' +
          '</div>' +
          '<div class="spacer"></div>' +
          '<button class="btn sm" data-act="channel-test" data-id="' + esc(channel.id) + '">' +
          t('common.test') + '</button>' +
          '<button class="btn sm" data-act="channel-edit" data-id="' + esc(channel.id) + '">' +
          t('common.edit') + '</button>' +
          '<button class="btn sm danger" data-act="channel-delete" data-id="' +
          esc(channel.id) + '">' + t('common.delete') + '</button>' +
          '</div>';
      });
      html += '</div>';
    }
    html += '<div class="card-note">' + t('notify.channelsEmpty2') + '</div>' +
      '</div>';

    // 投递记录
    html += deliveryCardHtml();
    return html;
  }

  function openChannelEditor(channel) {
    var isNew = !channel;
    var types = (state.notifications && state.notifications.channelTypes) || [];
    var firstType = types.length ? types[0].value : 'dingtalk';
    var item = channel || { id: '', type: firstType, name: '', enabled: true };
    var meta = channelTypeMeta(item.type);

    var body = '<div class="section-title">' + t('notify.channelInfo') + '</div>' +
      '<div class="field"><label>' + t('notify.channelType') + '</label>' +
      '<select id="chType">' +
      types.map(function (o) { return option(o.value, channelTypeLabel(o), item.type); }).join('') +
      '</select><div class="help" id="chHint">' + esc(channelTypeHint(meta)) + '</div></div>' +
      '<div class="field"><label>' + t('jail.colName') + '</label>' +
      '<input type="text" id="chName" value="' + esc(item.name) +
      '" placeholder="' + t('notify.namePlaceholder') + '"></div>' +
      '<div class="channel-fields" id="chFields"></div>' +
      '<div class="field"><div class="switch">' +
      switchToggle('data-toggle', 'chEnabled', item.enabled, t('notify.enableChannel')) +
      '<span data-bool-label="chEnabled">' + (item.enabled ? t('common.enable') : t('common.disable')) + '</span>' +
      '<input type="hidden" data-field="chEnabled" data-orig="' + (item.enabled ? 'true' : 'false') +
      '" value="' + (item.enabled ? 'true' : 'false') + '"></div></div>';

    var foot = (isNew ? '' : t('notify.removeChannelButton')) +
      '<div class="spacer"></div>' +
      '<button class="btn" data-act="drawer-close">' + t('common.cancel') + '</button>' +
      '<button class="btn" data-act="channel-test-draft">' + t('notify.testSend') + '</button>' +
      '<button class="btn primary" data-act="channel-save">' + t('notify.saveChannel') + '</button>';

    openDrawer(isNew ? t('notify.addTitle') : t('notify.editTitle'), body, foot);
    drawerCtx.channelId = item.id || null;
    // 记住哪些密钥字段原本就有值（GET 回传的是掩码）：编辑时留空表示沿用，
    // 校验不能把它们当成「必填未填」。
    drawerCtx.channelSecrets = {};
    var fieldList = meta.fields || [];
    fieldList.forEach(function (field) {
      if (!isSecretField(meta, field)) return;
      var raw = item[field.key];
      if (raw === SECRET_MARK || raw === true) drawerCtx.channelSecrets[field.key] = true;
    });
    renderChannelFields(item.type, item);
  }

  function draftChannel() {
    var enabledInput = $('#drawerBody [data-field="chEnabled"]');
    var meta = channelTypeMeta(($('#chType') || {}).value || 'generic');
    var channel = {
      id: drawerCtx.channelId || '',
      type: ($('#chType') || {}).value || 'generic',
      name: ($('#chName') || {}).value || '',
      enabled: !enabledInput || isOn(enabledInput.value),
    };
    Array.prototype.forEach.call($('#drawerBody').querySelectorAll('[data-ch]'), function (input) {
      var value = String(input.value || '').trim();
      var key = input.dataset.ch;
      // 密钥字段留空 = 用户没改：整条字段都不提交，让服务端按 id 沿用旧值。
      // 提交空串也等价，但不提交更明确 —— 不会出现「空串覆盖」这种可能。
      if (!value && isSecretField(meta, { key: key, type: input.type })) return;
      channel[key] = value;
    });
    return channel;
  }

  function validateChannel(channel) {
    var meta = channelTypeMeta(channel.type);
    var fields = meta.fields || [];
    for (var i = 0; i < fields.length; i++) {
      if (!fields[i].required) continue;
      if (channel[fields[i].key]) continue;
      // 编辑已有渠道时，原本已保存的密钥留空不算未填
      if (isSecretField(meta, fields[i]) &&
          (drawerCtx.channelSecrets || {})[fields[i].key]) continue;
      return t('err.fillField', { field: fieldLabel(meta, fields[i]) });
    }
    if (channel.type === 'wecom_app' && !/^\d+$/.test(channel.agentid || '')) {
      return t('err.agentIdNumeric');
    }
    return null;
  }

  function notifSettingsFromForm() {
    var settings = JSON.parse(JSON.stringify(state.notifications.settings));
    var input = $('#notifInterval');
    if (input) settings.pollInterval = parseInt(input.value, 10) || 30;
    return settings;
  }

  function saveNotifications(settings, button, message) {
    busy(button, true);
    return api('notifications', { method: 'PUT', body: { settings: settings } })
      .then(function (data) {
        state.notifications.settings = data.settings;
        if (message) toast(message, 'ok');
        restartBrowserNotify();
        return refresh();
      })
      .catch(function (err) { toast(localizeErr(err), 'err'); })
      .finally(function () { busy(button, false); });
  }

  function ensureNotificationPermission(callback) {
    if (!('Notification' in window)) {
      toast(t('err.browserNoNotify'), 'warn');
      callback(false);
      return;
    }
    if (Notification.permission === 'granted') { callback(true); return; }
    if (Notification.permission === 'denied') {
      toast(t('err.notifyDenied'), 'warn');
      callback(false);
      return;
    }
    Notification.requestPermission().then(function (result) {
      callback(result === 'granted');
    }).catch(function () { callback(false); });
  }

  function stopBrowserNotify() {
    if (notifTimer) { clearInterval(notifTimer); notifTimer = null; }
  }

  function pollNotifEvents(prime) {
    api('notifications/events?since=' + lastEventId).then(function (data) {
      var events = data.events || [];
      if (prime) {
        if (events.length) lastEventId = events[events.length - 1].id;
        return;
      }
      events.forEach(function (event) {
        if (event.id > lastEventId) lastEventId = event.id;
        if (event.type === 'test') return;
        try {
          new Notification(event.title, { body: event.text, tag: 'f2b-' + event.id });
        } catch (e) { /* 某些浏览器在无 SW 时会抛错，忽略 */ }
      });
    }).catch(function () { /* 忽略轮询失败 */ });
  }

  function restartBrowserNotify() {
    stopBrowserNotify();
    var settings = state.notifications && state.notifications.settings;
    if (!settings || !settings.browserNotify) return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    pollNotifEvents(true);
    notifTimer = setInterval(function () { pollNotifEvents(false); }, 15000);
  }

  /* ------------------------------------------------------------- 存储 */

  // 后端条目的 label 是中文源文。优先用 key 映射：EN 界面靠源文反查也能命中，
  // 但 key 映射不依赖字典顺序，新增条目时更稳。
  var STORAGE_KEY_MAP = {
    update_dir: 'stor.item.updateDir',
    backup_dir: 'stor.item.snapshotDir',
    server_log: 'stor.item.serverLog',
    server_log1: 'stor.item.serverLog1',
    install_log: 'stor.item.installLog',
    deliveries: 'stor.item.deliveries',
    events: 'stor.item.events',
    notifications: 'stor.item.notifications',
    backup_settings: 'stor.item.backupSettings',
    prefs: 'stor.item.prefs',
    fnknock: 'stor.item.fnknock',
    fnknock_log: 'stor.item.fnknockLog'
  };

  function storageItemLabel(item) {
    var key = STORAGE_KEY_MAP[item.key];
    if (key) return t(key);
    return tSrc(item.label) || item.label || item.key || '';
  }

  function storageReport() {
    return state.storage || null;
  }

  // 清理回收量按 target 估算：日志 = 日志类条目；备份 = 保留 keep 个之外的部分。
  // 后端才是最终答案（返回实测 freed），这里只用来在确认框里说清楚「预计回收多少」。
  function storageEstimate(target) {
    var data = storageReport();
    if (!data) return { bytes: 0, removed: 0, keep: 0 };
    // 后端至少保留 1 份备份，估算不能按 0 算成「全删」
    var keep = Math.max(1, Number(data.keep) || 0);
    // 日志清理会截断 server.log 与升级安装日志、并删掉轮转副本。
    // 安装日志位于 update/ 目录内部（nested），总量统计时不计，但清理时会真的回收。
    var logsBytes = 0;
    (data.items || []).forEach(function (item) {
      if (item.key === 'server_log' || item.key === 'server_log1' || item.key === 'install_log') {
        logsBytes += Number(item.bytes) || 0;
      }
    });
    var backups = (data.backups || []).slice().sort(function (a, b) {
      return (Number(b.mtime) || 0) - (Number(a.mtime) || 0);
    });
    var doomed = backups.slice(keep);
    var backupBytes = 0;
    doomed.forEach(function (item) { backupBytes += Number(item.size) || 0; });
    // 「全部清理」还会裁掉多余的安装包缓存（后端保留 updates_keep 份并豁免运行版本）
    var pkgBytes = 0;
    var pkgCount = 0;
    var updateKeep = Number(data.updates_keep) || 0;
    var packages = (data.packages || []).slice().sort(function (a, b) {
      return (Number(b.t) || 0) - (Number(a.t) || 0);
    });
    var currentVersion = String(data.current_version || '');
    packages.slice(updateKeep).forEach(function (item) {
      var name = String(item.name || '');
      // rollback.fpk 与当前运行版本的包后端永不删除，不能算进回收量
      if (name === 'rollback.fpk') return;
      if (currentVersion && name.indexOf(currentVersion) >= 0) return;
      pkgBytes += Number(item.bytes) || 0;
      pkgCount += 1;
    });
    if (target === 'logs') return { bytes: logsBytes, removed: 0, keep: keep };
    if (target === 'backups') return { bytes: backupBytes, removed: doomed.length, keep: keep };
    return { bytes: logsBytes + backupBytes + pkgBytes,
      removed: doomed.length + pkgCount, keep: keep };
  }

  function storageEstimateText(target) {
    var est = storageEstimate(target);
    if (!est.bytes) return { text: t('stor.estimateNone'), bytes: 0 };
    return { text: t('stor.estimate', { size: fmtSize(est.bytes) }), bytes: est.bytes };
  }

  function renderStorage(embedded) {
    var body = storageHtml();
    if (embedded) return body;
    $('#view').innerHTML = '<div class="view-head"><h2>' + t('nav.storage') + '</h2></div>' + body;
  }

  /* 存储分区：一张卡里放「合计 → 明细表 → 清理按钮 → 安装包缓存」。
     明细表的路径不再单独占一列（列一多，窄屏下整表要横向滚动），
     改成「项目名一行 + 路径与标签一行」的双行单元格。 */
  function storageHtml() {
    var data = storageReport();
    if (!data) return '<div class="card">' + t('fnk.loadingInline') + '</div>';

    var items = (data.items || []).slice().sort(function (a, b) {
      return (Number(b.bytes) || 0) - (Number(a.bytes) || 0);
    });
    var packages = (data.packages || []).slice().sort(function (a, b) {
      return (Number(b.t) || 0) - (Number(a.t) || 0);
    });
    var updateKeepN = Number(data.updates_keep) || 0;
    var logEst = storageEstimateText('logs');
    var backupEst = storageEstimateText('backups');
    var allEst = storageEstimateText('all');

    var html = '<div class="card">' + cardHead(t('stor.usageTitle'),
      '<button class="btn sm" data-act="storage-refresh">' + t('common.refresh') + '</button>') +
      '<p class="dim card-desc">' + esc(t('stor.hint')) + '</p>';

    // 合计条：标签 / 数值 / 目录三段同一行基线对齐
    html += '<div class="storage-total">' +
      '<span class="k">' + t('stor.total') + '</span>' +
      '<b class="mono">' + esc(fmtSize(data.total || 0)) + '</b>' +
      '<span class="faint mono">' + esc(data.var_dir || '—') + '</span>' +
      '</div>';

    if (!items.length) {
      html += '<div class="faint">' + t('stor.empty') + '</div>';
    } else {
      html += '<div class="bscroll storage-scroll"><div class="table-wrap">' +
        '<table class="ttable tstorage"><thead><tr>' +
        '<th>' + t('stor.colItem') + '</th>' +
        '<th class="num" style="width:96px">' + t('common.size') + '</th>' +
        '<th class="num hide-sm" style="width:78px">' + t('stor.colFiles') + '</th>' +
        '</tr></thead><tbody>';
      items.forEach(function (item) {
        var files = (item.files === undefined || item.files === null) ? '—' : String(item.files);
        html += '<tr><td class="sname">' +
          '<div class="ellipsis">' + esc(storageItemLabel(item)) +
          (item.nested ? ' <span class="chip">' + esc(t('stor.nested')) + '</span>' : '') +
          '</div>' +
          '<div class="sub mono ellipsis" title="' + esc(item.path || '') + '">' +
          esc(item.path || '') + '</div>' +
          '</td>' +
          '<td class="num mono">' + esc(item.bytes ? fmtSize(item.bytes) : '—') + '</td>' +
          '<td class="num mono hide-sm">' + esc(files) + '</td>' +
          '</tr>';
      });
      html += '</tbody></table></div></div>';
    }

    // 清理动作：三个常规清理在左，「全部清理」用危险态推到最右
    html += '<div class="sep"></div>' +
      '<div class="row wrap" style="gap:8px">' +
      '<button class="btn" data-act="storage-clean" data-target="backups" title="' +
      esc(backupEst.text) + '">' + t('stor.cleanBackups') + '</button>' +
      '<span class="faint" style="font-size:12.5px">' + esc(t('stor.keepNote', { n: data.keep || 2 })) +
      '</span>' +
      '<button class="btn" data-act="storage-clean" data-target="logs" title="' +
      esc(logEst.text) + '">' + t('stor.cleanLogs') + '</button>' +
      '<div class="spacer"></div>' +
      '<button class="btn danger" data-act="storage-clean" data-target="all" title="' +
      esc(allEst.text) + '">' + t('stor.cleanAll') + '</button>' +
      '</div>' +
      '<div class="card-note">' + esc(t('stor.noteKeep')) + '</div>';

    // 安装包缓存
    html += '<h4 class="storage-h4">' + t('stor.packagesTitle') + '</h4>';
    if (!packages.length) {
      html += '<div class="faint">' + t('stor.packagesEmpty') + '</div>';
    } else {
      html += '<div class="bscroll storage-scroll"><div class="table-wrap">' +
        '<table class="ttable tstorage"><thead><tr>' +
        '<th>' + t('common.name') + '</th>' +
        '<th class="num" style="width:96px">' + t('common.size') + '</th>' +
        '</tr></thead><tbody>';
      packages.forEach(function (item) {
        html += '<tr><td class="sname">' +
          '<div class="mono ellipsis" title="' + esc(item.name) + '">' + esc(item.name) + '</div>' +
          '<div class="sub">' +
          (item.partial
            ? '<span class="chip err">' + esc(t('stor.partialBadge')) + '</span>'
            : '<span class="faint">' + esc(fmtTime(item.t)) + '</span>') +
          '</div></td>' +
          '<td class="num mono">' + esc(fmtSize(item.bytes || 0)) + '</td>' +
          '</tr>';
      });
      html += '</tbody></table></div></div>';
    }
    if (updateKeepN) {
      html += '<div class="card-note">' + esc(t('stor.keepNote', { n: updateKeepN })) + '</div>';
    }
    html += '</div>';

    // 备份文件清单不在这里：存储页只回答「谁占了空间、能回收多少」，
    // 文件的恢复 / 删除在「维护」分区里做，同一份清单只出现一次。
    return html;
  }

  function shortenPath(path) {
    path = String(path || '');
    if (path.length <= 64) return path;
    return path.slice(0, 30) + '…' + path.slice(-30);
  }

  function cleanStorage(target, button) {
    var data = storageReport() || {};
    // keep 只作用于归档备份；配置快照（20）与安装包（2）由后端固定
    var keep = Math.max(1, Number(data.keep) || 0);
    var est = storageEstimate(target);
    var message;
    if (target === 'logs') {
      message = t('stor.confirmLogs', { size: fmtSize(est.bytes) });
    } else if (target === 'backups') {
      message = t('stor.confirmBackups', {
        n: keep, snapshot: Number(data.snapshot_keep) || 0, size: fmtSize(est.bytes)
      });
    } else {
      message = t('stor.confirmAll', {
        n: keep,
        snapshot: Number(data.snapshot_keep) || 0,
        packages: Number(data.updates_keep) || 0,
        size: fmtSize(est.bytes)
      });
    }
    if (!confirmDanger(message)) return;
    busy(button, true);
    api('storage/cleanup', { method: 'POST', body: { target: target, keep: keep } })
      .then(function (result) {
        // 「升级下载/安装进行中」后端可能回 200 + {ok:false, busy:true}，
        // 也可能被路由映射成 409，两种都要认。
        if (result && (result.busy === true || result.ok === false)) {
          if (result.busy === true || result.error === 'busy') {
            toast(t('stor.busy'), 'warn');
            return null;
          }
          toast(localizeErr({ message: result.detail }), 'err');
          return refresh();
        }
        // 后端顺手带回了清理后的最新报告，省一次往返
        if (result && result.storage) state.storage = result.storage;
        var freed = Number(result && result.freed) || 0;
        var removed = Number(result && result.removed) || 0;
        if (!freed && !removed) toast(t('stor.cleanedNothing'), 'ok');
        else toast(t('stor.cleaned', { size: fmtSize(freed), removed: removed }), 'ok', 5000);
        // detail 只放失败的路径，不当文案用
        if (result && result.detail) toast(t('stor.cleanFailed', { detail: result.detail }), 'warn', 8000);
        return refresh();
      })
      .catch(function (err) {
        if (err && err.status === 409) { toast(t('stor.busy'), 'warn'); return; }
        toast(localizeErr(err), 'err');
      })
      .finally(function () { busy(button, false); });
  }

  /* ------------------------------------------------------------- 更新 */

  // 状态条只有「发现新版本」着色，其余走中性色；图标沿用更新语义
  var VS_ICONS = {
    idle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.7 4.4v5h-5"/></svg>',
    fresh: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.5l2.1 5.2 5.6.4-4.3 3.6 1.4 5.4-4.8-3-4.8 3 1.4-5.4-4.3-3.6 5.6-.4z"/></svg>'
  };

  // 后端的 reason 是机器码（not_checked / no_release / rate_limited …），
  // 用本地模板翻成人话；认不出来的码再把 detail 原样显示，绝不把码直接甩给用户。
  var UPDATE_REASON_KEYS = {
    not_checked: 'update.reasonNotChecked',
    no_release: 'update.reasonNoRelease',
    not_found: 'update.reasonNotFound',
    rate_limited: 'update.reasonRateLimited',
    timeout: 'update.reasonTimeout',
    network: 'update.reasonNetwork',
    parse: 'update.reasonParse',
    http: 'update.reasonHttp',
    error: 'update.reasonError',
    unknown_current: 'update.reasonUnknownCurrent'
  };

  // 数值段比较：26.11.10 要判为高于 26.11.9（字符串比较会判反）。
  function compareVersions(a, b) {
    var pa = String(a || '').replace(/^v/i, '').split('.');
    var pb = String(b || '').replace(/^v/i, '').split('.');
    var n = Math.max(pa.length, pb.length);
    for (var i = 0; i < n; i += 1) {
      var x = parseInt(pa[i], 10);
      var y = parseInt(pb[i], 10);
      if (isNaN(x)) x = 0;
      if (isNaN(y)) y = 0;
      if (x !== y) return x > y ? 1 : -1;
    }
    return 0;
  }

  function updateInfo() {
    return state.update.info || null;
  }

  // 后端的 error / reason / notes 都是中文源文。英文界面能反查到译文就用译文，
  // 查不到就退到本地通用文案，绝不能把中文漏到英文界面上。
  function localizedSource(text, fallbackKey) {
    text = String(text === undefined || text === null ? '' : text).trim();
    if (!text) return '';
    var translated = tSrc(text);
    if (translated && translated !== text) return translated;
    if (currentLang() === 'en' && /[\u4e00-\u9fff]/.test(text)) return t(fallbackKey);
    return text;
  }

  // 状态条图标：已最新用四角星，发现新版本用星芒，检查失败用感叹号。
  function updateStatusIcon(kind) {
    var open = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round">';
    if (kind === 'fresh') {
      return open + '<path d="M12 3.5l2.1 5.2 5.6.4-4.3 3.6 1.4 5.4-4.8-3-4.8 3 1.4-5.4-4.3-3.6 5.6-.4z"/></svg>';
    }
    if (kind === 'failed') {
      return open + '<circle cx="12" cy="12" r="8.5"/><path d="M12 8v5"/><path d="M12 16.2v.01"/></svg>';
    }
    return open + '<path d="M12 3.5l1.9 5.1 5.1 1.9-5.1 1.9L12 17.5l-1.9-5.1L5 10.5l5.1-1.9z"/>' +
      '<path d="M18 15.5l.8 2.2 2.2.8-2.2.8-.8 2.2-.8-2.2-2.2-.8 2.2-.8z"/></svg>';
  }

  function updateAssetsHtml(info) {
    // Release 里可能挂着别的东西（校验文件、源码包），只有 .fpk 才是安装包
    var assets = (info.assets || []).filter(function (item) {
      if (!item) return false;
      var name = String(item.name || '');
      return /.fpk$/i.test(name) || (!!item.url && /\.fpk$/i.test(String(item.url)));
    });
    if (!assets.length) return '';
    // size 为 0 表示「没拿到大小」，不是「文件是空的」：
    // GitHub 接口被限流后走的是发布订阅源，那条路径只有下载链接没有字节数。
    return '<div class="section-title">' + t('update.assets') + '</div>' +
      '<div class="assetlist">' +
      assets.map(function (item) {
        var size = Number(item.size) || 0;
        return '<div class="assetrow"><span class="aname mono">' +
          esc(item.name || '—') + '</span><span class="asize dim">' +
          (size > 0 ? esc(fmtSize(size)) : esc(t('update.sizeUnknown'))) +
          '</span></div>';
      }).join('') + '</div>';
  }

  function renderUpdate(embedded) {
    var body = updateHtml();
    if (embedded) return body;
    $('#view').innerHTML = '<div class="view-head"><h2>' + t('nav.update') + '</h2></div>' + body;
  }

  // 一句话说明 + 单张卡片：卡片头带「打开发布页面」，版本对比与状态条在卡片里，
  // 说明与仓库信息在下面 —— 整页只有一张卡，不散成多个 fragment。
  function updateHtml() {
    var info = updateInfo();
    var current = (info && info.current) ||
      (state.meta && state.meta.app && state.meta.app.version) || '—';
    var hasUpdate = !!(info && info.has_update);
    var latest = (info && info.latest) || (info && info.ok ? current : '—');
    // 本地版本可能高于线上最新（装了还没发布的内测包）：这时「最新版本」
    // 显示线上那个更低的号会让人误以为要降级，直接跟当前版本对齐。
    if (latest !== '—' && compareVersions(latest, current) < 0) latest = current;
    // not_checked 是「还没查过」，不是失败：面板刚启动时会短暂出现
    var failed = !!(info && info.ok === false && info.reason && info.reason !== 'not_checked');
    var checking = !!state.update.checking;

    var html = '<div class="help">' + esc(t('update.hint')) + '</div>';

    var releaseUrl = (info && info.html_url) || '';
    if (!releaseUrl) {
      var repoSlug = (info && info.repo) || 'Smi13zZ/fail2ban';
      releaseUrl = 'https://github.com/' + repoSlug + '/releases';
    }
    html += '<div class="card">' + cardHead(t('update.title'),
      '<a class="btn sm" href="' + esc(releaseUrl) + '" target="_blank" ' +
      'rel="noopener noreferrer">' + t('update.release') + '</a>') +
      '<div class="help help-tight">' + esc(t('update.desc')) + '</div>';

    // 当前 -> 最新
    html += '<div class="vhero">' +
      '<div class="vcol"><div class="vk">' + t('update.current') + '</div>' +
      '<div class="vv mono">v' + esc(current) + '</div></div>' +
      '<div class="varrow" aria-hidden="true">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M13 6l6 6-6 6"/></svg>' +
      '</div>' +
      '<div class="vcol"><div class="vk">' + t('update.latest') + '</div>' +
      '<div class="vv mono' + (hasUpdate ? ' fresh' : '') + '">' +
      (latest && latest !== '—' ? 'v' + esc(latest) : '—') + '</div></div>' +
      '</div>';

    // 状态条：标题 + 副标题 + 右侧两个动作（检查 / 一键更新）
    var title, sub, tone;
    if (failed) {
      title = t('update.checkFailed');
      var reasonKey = UPDATE_REASON_KEYS[String(info.reason || '')];
      sub = reasonKey ? t(reasonKey) : localizedSource(info.detail || info.error, 'common.unknownError');
      tone = '';
    } else if (hasUpdate) {
      title = t('update.newTitle');
      sub = t('update.newSub');
      tone = ' has';
    } else {
      title = t('update.upTitle');
      sub = t('update.upSub');
      tone = '';
    }

    html += '<div class="vstatus' + tone + '">' +
      '<span class="vsic">' + updateStatusIcon(failed ? 'failed' : (hasUpdate ? 'fresh' : 'idle')) + '</span>' +
      '<div class="vstext"><b>' + esc(title) + '</b>' +
      (sub ? '<span>' + esc(sub) + '</span>' : '') + '</div>' +
      '<div class="vactions">' +
      '<button class="btn sm" data-act="update-check"' + (checking ? ' disabled' : '') + '>' +
      (checking ? t('update.checking') : t('update.check')) + '</button>' +
      (hasUpdate
        ? '<button class="btn sm primary" data-act="update-install">' + t('update.install') + '</button>'
        : '') +
      '</div></div>';

    // 版本更新说明：可滚动、限高，长 changelog 不会把卡片撑成一屏多
    html += '<div class="sep"></div><div class="section-title">' + t('update.notes') + '</div>';
    if (info && info.notes) {
      html += '<div class="relnotes">' + esc(info.notes) + '</div>';
    } else {
      html += '<div class="faint">' + t('update.notesEmpty') + '</div>';
    }

    html += updateAssetsHtml(info || {});

    // 仓库 / 上次检查 / 发布时间
    html += '<div class="sep"></div><dl class="kv">' +
      kv(t('update.repo'), '<span class="mono">' + esc((info && info.repo) || '—') + '</span>') +
      kv(t('update.checkedLabel'), info && info.checked_at
        ? esc(t('update.checkedAt', { t: fmtTime(Number(info.checked_at)) })) +
          (info && info.cached ? ' · ' + esc(t('update.cachedHint')) : '')
        : esc(t('update.neverChecked'))) +
      kv(t('update.publishedAt'), esc(info && info.published_at
        ? String(info.published_at).slice(0, 10) : '—')) +
      '</dl>';

    html += '<div class="help">' + esc(t('update.autoCheck')) + '</div>';
    html += '</div>';
    return html;
  }


  function paintUpdateDot() {
    var dot = $('#navUpdateDot');
    if (!dot) return;
    var info = updateInfo();
    dot.hidden = !(info && info.has_update);
  }

  // 版本检查有独立节流：成功查过 30 分钟内不再自动查，失败只冷却 5 分钟
  // （失败也压半小时的话，用户刚断网恢复就看不到红点）。手动点不受节流限制。
  var UPDATE_REFRESH_MS = 30 * 60 * 1000;
  var UPDATE_RETRY_MS = 5 * 60 * 1000;
  var updateTimer = null;

  function updateCheckDue() {
    var info = state.update;
    if (info.checking) return false;
    if (info.lastCheckAt && Date.now() - info.lastCheckAt < UPDATE_REFRESH_MS) return false;
    if (info.lastFailAt && Date.now() - info.lastFailAt < UPDATE_RETRY_MS) return false;
    return true;
  }

  function checkUpdate(force, button) {
    if (state.update.checking) return Promise.resolve();
    state.update.checking = true;
    if (inSettingsTab('update')) renderSettings();
    if (button) busy(button, true);
    return api('update' + (force ? '?force=1' : ''))
      .then(function (info) {
        state.update.info = info;
        // 时间戳只在查成功时更新：一次断网失败不该让下次补查再等 30 分钟
        state.update.lastCheckAt = Date.now();
        state.update.lastFailAt = 0;
        paintUpdateDot();
        if (force && info && info.has_update) {
          toast(t('update.foundToast', { version: info.latest }), 'ok', 4000);
        } else if (force && info && info.ok !== false) {
          toast(t('update.toastUpToDate'), 'ok', 2400);
        }
        return info;
      })
      .catch(function (err) {
        state.update.lastFailAt = Date.now();
        if (inSettingsTab('update')) {
          toast(t('update.failed', { detail: localizeErr(err) }), 'err', 6000);
        }
        return null;
      })
      .finally(function () {
        state.update.checking = false;
        if (button) busy(button, false);
        if (inSettingsTab('update')) renderSettings();
      });
  }

  // 升级遮罩：进度区高度固定，百分比只增不减；没有总量可测的阶段显示「准备中…」
  var UP_PHASE_KEYS = {
    idle: 'update.stagePreparing',
    preparing: 'update.stagePreparing',
    download: 'update.phaseDownload',
    downloading: 'update.phaseDownload',
    verify: 'update.phaseVerify',
    verifying: 'update.phaseVerify',
    uninstall: 'update.phaseUninstall',
    uninstalling: 'update.phaseUninstall',
    install: 'update.phaseInstall',
    installing: 'update.phaseInstall',
    start: 'update.phaseStart',
    starting: 'update.phaseStart',
    rollback: 'update.phaseRollback',
    done: 'update.phaseDone',
    finished: 'update.phaseDone',
    failed: 'update.phaseFailed',
    error: 'update.phaseFailed'
  };

  // 后端真实阶段只有 idle / downloading / installing / failed 四个；
  // 表里其余词是为安装脚本将来细分阶段留的前向兼容。认不出来的阶段不假装在
  // 「准备中」，直接把后端给的 detail（中文句子）显示出来。
  function upPhaseText(phase, detail) {
    var raw = String(phase || '').trim();
    var key = UP_PHASE_KEYS[raw.toLowerCase()];
    if (key) return t(key);
    if (detail) return tSrc(detail) || detail;
    return raw || t('update.stagePreparing');
  }
  // 升级遮罩的各个函数按「下载中 / 无总量可测」两种形态切换：
  // 前者显示进度条 + 大小 + 百分比，后者把同一块空间留给一句「准备中…」。
  // 两张子块高度合计固定，所以切换阶段时弹窗不变高矮。

  function paintUpStage(text) {
    var stage = $("#upStage");
    if (stage) stage.textContent = text || t("update.stagePreparing");
  }

  // shape: "track" 有可测总量，显示进度条；"ready" 没有，只显示一句状态。
  function paintUpShape(shape) {
    var track = $("#upProgTrack");
    var ready = $("#upProgReady");
    if (track) track.hidden = shape !== "track";
    if (ready) ready.hidden = shape === "track";
  }

  // 遮罩上的旋转图标：SVG 由 JS 注入，样式里靠 .upspin svg 控制尺寸与旋转。
  // 只写一个空的 span 的话，图标不会出现 —— 遮罩上看不到任何进行中的提示。
  var UP_SPIN_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M20.5 12a8.5 8.5 0 1 1-2.6-6.1"/><path d="M20.7 4.4v5h-5"/></svg>';

  function openUpOverlay(title) {
    var overlay = $("#upOverlay");
    if (!overlay) return;
    overlay.hidden = false;
    var spin = $("#upSpin");
    if (spin) {
      spin.classList.remove("done");
      if (!spin.querySelector("svg")) spin.innerHTML = UP_SPIN_ICON;
    }
    var reload = $("#upReload");
    if (reload) reload.hidden = true;
    var keep = $("#upKeep");
    if (keep) keep.hidden = false;
    var titleNode = $("#upTitle");
    if (titleNode) titleNode.textContent = title || t("update.upgrading");
    var fill = $("#upProgFill");
    if (fill) fill.style.width = "0%";
    var pct = $("#upProgPct");
    if (pct) pct.textContent = "";
    var bytes = $("#upProgBytes");
    if (bytes) bytes.textContent = "";
    paintUpStage(t("update.stagePreparing"));
    paintUpShape("ready");
    state.update.maxPct = 0;
    state.update.done = false;
    state.update.phase = "";
  }

  function closeUpOverlay() {
    var overlay = $("#upOverlay");
    if (overlay) overlay.hidden = true;
  }

  // 进度区：done/total 都有才算百分比；没有总量就只留阶段文案。
  // 百分比取历史最大值，避免服务端瞬时快照回跳造成进度倒退。
  function paintUpProgress(prog) {
    if (!prog) return;
    var phase = String(prog.phase || "").toLowerCase();
    paintUpStage(upPhaseText(prog.phase, prog.detail));
    state.update.phase = prog.phase || "";

    var done = Number(prog.done) || 0;
    var total = Number(prog.total) || 0;
    var serverPct = Number(prog.percent);
    if (isNaN(serverPct)) serverPct = 0;

    if (total > 0) {
      paintUpShape("track");
      var pct = Math.min(100, Math.floor((done / total) * 100));
      pct = Math.max(pct, Math.min(100, serverPct));
      if (pct > state.update.maxPct) state.update.maxPct = pct;
      var fill = $("#upProgFill");
      if (fill) fill.style.width = state.update.maxPct + "%";
      var pctLabel = $("#upProgPct");
      if (pctLabel) pctLabel.textContent = state.update.maxPct + "%";
      var bytesLabel = $("#upProgBytes");
      if (bytesLabel) {
        bytesLabel.textContent = t("update.bytes", { done: fmtSize(done), total: fmtSize(total) });
      }
      return;
    }

    // 没有可测总量（准备 / 安装 / 服务重启）：不假装有百分比。
    // 下载已经到 100% 时保留进度条满格，别让它看起来像是退回去了。
    if (state.update.maxPct >= 100) {
      paintUpShape("track");
      var keepFill = $("#upProgFill");
      if (keepFill) keepFill.style.width = "100%";
      var keepPct = $("#upProgPct");
      if (keepPct) keepPct.textContent = "100%";
      var keepBytes = $("#upProgBytes");
      if (keepBytes) keepBytes.textContent = "";
      return;
    }
    paintUpShape("ready");
    // 从「下载中」切过来时百分比位要清空：留着一个上一阶段的数字会让人以为
    // 安装阶段也有百分比可看。
    var clearPct = $("#upProgPct");
    if (clearPct) clearPct.textContent = "";
    var ready = $("#upProgReady");
    if (ready) {
      ready.textContent = prog.detail
        ? (tSrc(prog.detail) || t("update.stagePreparing"))
        : t("update.stagePreparing");
    }
  }


  function stopUpdatePoll() {
    if (state.update.poll) {
      clearInterval(state.update.poll);
      state.update.poll = null;
    }
  }

  // 装完的判据：面板版本号变了（升级过程中服务会停止，接口连续失败属正常）。
  // 找不到版本变化就一直容忍，直到次数上限。
  function pollUpdateProgress() {
    var tries = 0;
    var fails = 0;
    stopUpdatePoll();
    state.update.poll = setInterval(function () {
      tries += 1;
      api('update/progress').then(function (prog) {
        fails = 0;
        paintUpProgress(prog);
        var phase = String((prog && prog.phase) || '').toLowerCase();
        if (phase === 'failed' || phase === 'error') {
          stopUpdatePoll();
          finishUpgrade(false, prog, 'failed');
          return;
        }
        // progress 里就带 current（已装版本），先用它判，取不到再问 meta
        if (state.update.before && prog && prog.current &&
            String(prog.current) !== state.update.before) {
          stopUpdatePoll();
          finishUpgrade(true, prog);
          return null;
        }
        return api('meta').then(function (meta) {
          var version = (meta && meta.app && meta.app.version) || '';
          if (state.update.before && version && version !== state.update.before) {
            stopUpdatePoll();
            finishUpgrade(true, prog);
          }
        }).catch(function () { /* 升级中 meta 也可能短暂失败 */ });
      }).catch(function () {
        fails += 1;
        // 升级期间服务会重启：容忍到 180 次，之后才判定超时
        if (fails >= 180) {
          stopUpdatePoll();
          finishUpgrade(false, null, 'timeout');
        }
      });
      if (tries > 900) {
        stopUpdatePoll();
        finishUpgrade(false, null, 'timeout');
      }
    }, 1000);
  }
  // mode: "ok" 装完 / "failed" 后端明确报错 / "timeout" 我们等不到了（不代表升级失败）
  function finishUpgrade(ok, prog, mode) {
    var kind = ok ? "ok" : (mode || "failed");
    state.update.done = true;
    var spin = $("#upSpin");
    if (spin && ok) spin.classList.add("done");
    if (ok) {
      paintUpShape("track");
      var fill = $("#upProgFill");
      if (fill) fill.style.width = "100%";
      var pct = $("#upProgPct");
      if (pct) pct.textContent = "100%";
      var bytes = $("#upProgBytes");
      if (bytes) bytes.textContent = "";
      paintUpStage(t("update.done"));
    } else if (kind === "timeout") {
      paintUpStage(t("update.pollTimeout"));
    } else {
      paintUpStage(t("update.phaseFailed"));
    }
    // 超时也要给一个重载入口：面板可能只是慢，用户刷新就能确认结果
    var reload = $("#upReload");
    if (reload) reload.hidden = false;
    if (ok) {
      toast(t("update.done"), "ok", 4000);
      setTimeout(function () { location.reload(); }, 1500);
    } else if (kind === "timeout") {
      toast(t("update.pollTimeout"), "warn", 9000);
    } else {
      var detail = (prog && (prog.detail || prog.error)) || "";
      toast(detail
        ? t("update.failed", { detail: localizedSource(detail, "common.unknownError") })
        : t("update.phaseFailed"), "err", 9000);
    }
  }

  function startUpgrade(button) {
    var info = updateInfo() || {};
    var target = info.latest || "";
    if (!confirmDanger(t("update.installConfirm", { version: target || "—" }))) return;
    state.update.before = (state.meta && state.meta.app && state.meta.app.version) || "";
    openUpOverlay(target ? t("update.installingTitle", { v: target }) : t("update.upgrading"));
    paintUpStage(t("update.installingDownload"));
    if (button) busy(button, true);
    // 轮询必须排在阻塞请求之前：后端在这个请求里同步下载安装包，
    // await 会一直挂到下载结束，等它返回才起轮询就全程零进度。
    pollUpdateProgress();
    api("update/install", { method: "POST", body: {} })
      .then(function (data) {
        // 后端没起来（缺安装包、权限不足等）会直接回 ok:false，别让轮询空转到超时
        if (data && data.ok === false) {
          stopUpdatePoll();
          closeUpOverlay();
          // 后端在忙时回 {ok:false, error:"busy"}，这是「已在升级」不是失败
          if (data.error === "busy" || data.busy === true) {
            toast(t("update.installBlocked"), "warn", 5000);
            return;
          }
          toast(localizedSource(data.detail || data.error, "update.phaseFailed"), "err", 9000);
          return;
        }
        if (data && data.phase) paintUpProgress(data);
        toast(t("update.installing"), "ok", 3000);
      })
      .catch(function (err) {
        stopUpdatePoll();
        if (err && err.status === 409) {
          closeUpOverlay();
          toast(t("update.installBlocked"), "warn");
        } else {
          // 升级期间服务会重启，首个请求失败是常态，留着轮询等它回来
          toast(localizeErr(err, "update.openFailed"), "warn", 6000);
        }
      })
      .finally(function () { if (button) busy(button, false); });
  }



  function onUpdateTick() {
    if (document.hidden) return;
    if (!updateCheckDue()) return;
    checkUpdate(false);
  }

  /* -------------------------------------------------------------- 日志 */

  var logTimer = null;

  function stopLogTimer() {
    if (logTimer) { clearInterval(logTimer); logTimer = null; }
  }

  function loadLogs() {
    var params = 'logs?lines=' + encodeURIComponent(state.logOpts.lines);
    if (state.logOpts.keyword) params += '&keyword=' + encodeURIComponent(state.logOpts.keyword);
    return api(params).then(function (data) { state.logs = data; });
  }

  /* 日志级别：fail2ban.log 每行形如
     「2026-11-06 00:07:12,345 fail2ban.actions [123]: NOTICE [sshd] Ban 1.2.3.4」。
     归类只看级别词与动作词，不解析时间戳 —— 时间戳在这里只用于显示。 */
  var LOG_LEVELS = ['all', 'ban', 'unban', 'found', 'warn', 'error'];

  function logLevelLabel(id) {
    var map = {
      all: t('log.levelAll'), ban: t('log.levelBan'), unban: t('log.levelUnban'),
      found: t('log.levelFound'), warn: t('log.levelWarn'), error: t('log.levelError')
    };
    return map[id] || id;
  }

  function logLevelOf(line) {
    if (/\bERROR\b|\bCRITICAL\b/.test(line)) return 'error';
    if (/\bUnban\b/.test(line)) return 'unban';
    if (/\bBan\b/.test(line)) return 'ban';
    if (/\bWARNING\b/.test(line)) return 'warn';
    if (/\bFound\b/.test(line)) return 'found';
    return 'all';
  }

  /* 一行日志拆成「时间戳 + 消息」。前缀是固定的
     「YYYY-MM-DD HH:MM:SS,mmm 」，拆不出来时整行当消息、时间留空。

     fail2ban 会把整条内部命令（连同里面的换行）原样写进日志，
     于是一条记录里可能嵌着几十行、上万字符 —— 直接按一个 .logline 渲染，
     会变成一整片挤在卡片里的等宽长文本，完全看不出是一条记录。
     这里只取首行做「摘要」，其余内容折叠起来，展开按钮才显示全文。 */
  function splitLogLine(line) {
    var match = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})/.exec(line);
    if (!match) return { date: '', time: '', message: line };
    return {
      date: match[1],
      time: match[2],
      message: line.slice(match[0].length).replace(/^[,.]\d+\s+/, '').trim()
    };
  }

  // 单行日志的摘要长度上限：超出部分收起，避免一条记录占满整屏
  var LOG_SUMMARY_MAX = 260;

  // 只命中当前级别筛选的行；「全部」不做过滤。
  // 返回时倒序：日志文件是从旧到新追加的，界面把最新的放在最上面，
  // 一眼就能看到刚刚发生了什么，不用滚到底。
  function visibleLogLines() {
    var data = state.logs;
    if (!data || !data.lines) return [];
    var level = state.logOpts.level;
    var picked = (!level || level === 'all')
      ? data.lines.slice()
      : data.lines.filter(function (line) { return logLevelOf(line) === level; });
    return picked.reverse();
  }

  // 级别 → 行样式类：与列表配色一一对应（封禁红、解封绿、失败琥珀）
  function logRowClass(level) {
    if (level === 'ban' || level === 'error') return 'err';
    if (level === 'unban') return 'ok';
    if (level === 'found' || level === 'warn') return 'warn';
    return '';
  }

  function logLineHtml(line) {
    var level = logLevelOf(line);
    var parts = splitLogLine(line);
    var message = parts.message;
    var truncated = message.length > LOG_SUMMARY_MAX;
    var shown = truncated ? message.slice(0, LOG_SUMMARY_MAX) + '…' : message;
    return '<div class="logline ' + logRowClass(level) + '">' +
      '<span class="t"><span class="d">' + esc(parts.date) + '</span>' +
      '<span class="h">' + esc(parts.time) + '</span></span>' +
      '<span class="m">' + esc(shown) +
      (truncated
        // 全文放 title：悬停就能读完，不用为了看一眼把页面撑爆
        ? '<button type="button" class="log-more" title="' + esc(message) + '"' +
          ' data-act="log-more">' + t('log.expand') + '</button>'
        : '') +
      '</span></div>';
  }

  // resetScroll：筛选条件变了就回到顶部 —— 那是一次全新的查询，
  // 沿用上一次的滚动位置会落在毫不相干的地方。自动刷新不走这条路。
  function renderLogs(resetScroll) {
    var host = $('#view');
    var data = state.logs;
    var rows = visibleLogLines();

    // 工具栏只有一条：左边按级别筛选，右边依次是关键字、行数、导出、刷新、自动刷新。
    // 这四项原先挂在页面底部，翻日志时视线要来回跳；越靠右越接近「重新取一次」，
    // 所以刷新与自动刷新挨在一起，参数类的关键字与行数排在它们左边。
    var html = '<div class="toolbar log-bar">' +
      '<div class="seg" id="logLevelSeg">' +
      LOG_LEVELS.map(function (id) {
        return '<button type="button" data-act="log-level" data-level="' + esc(id) + '"' +
          (state.logOpts.level === id ? ' class="on"' : '') + '>' +
          esc(logLevelLabel(id)) + '</button>';
      }).join('') +
      '</div><div class="spacer"></div>' +
      '<input type="text" id="logKeyword" class="log-search" placeholder="' +
        esc(t('log.keywordPlaceholder')) + '" value="' + esc(state.logOpts.keyword) + '">' +
      '<select id="logLines" class="log-lines">' +
      [100, 200, 400, 800, 2000].map(function (n) {
        return option(n, t('log.lineCount', { n: n }), state.logOpts.lines);
      }).join('') + '</select>' +
      '<button class="btn sm" data-act="logs-export">' + t('common.export') + '</button>' +
      '<button class="btn sm" data-act="logs-refresh">' + t('common.refresh') + '</button>' +
      '<label class="log-auto"><input type="checkbox" id="logAuto"' +
        (state.logOpts.auto ? ' checked' : '') + '><span>' +
        esc(t('log.autoRefreshShort')) + '</span></label>' +
      '</div>';

    // 重绘前记住滚动位置：自动刷新每 5 秒整块重绘一次，归零会让正在读的位置跳走。
    // 新的行出现在最上面，所以除了 scrollTop 还要记住总高度 —— 顶进来几行，
    // 原来的内容就往下挪多少，按差值补回去才停在原处。
    var prevBox = $('.card.flush', host);
    var keepTop = (prevBox && !resetScroll) ? prevBox.scrollTop : 0;
    var keepHeight = (prevBox && !resetScroll) ? prevBox.scrollHeight : 0;

    html += '<div class="card flush" style="max-height:72vh;overflow:auto">';
    if (!data) {
      html += '<div class="empty">' + t('fnk.loadingInline') + '</div>';
    } else if (data.error) {
      html += '<div class="empty">' + esc(data.error) + '</div>';
    } else if (!data.lines.length) {
      html += '<div class="empty">' + t('log.noMatch') + '</div>';
    } else if (!rows.length) {
      html += '<div class="empty">' + t('log.noMatch') + '</div>';
    } else {
      html += rows.map(logLineHtml).join('');
    }
    html += '</div>';

    host.innerHTML = html;
    var box = $('.card.flush', host);
    if (box) {
      // 本来就停在最上面：跟着最新的一行走，不往回缩
      if (keepTop <= 4) box.scrollTop = 0;
      // 停在中间：新行顶进来多少就往下让多少，正在读的那几行留在原地
      else if (keepHeight) box.scrollTop = keepTop + (box.scrollHeight - keepHeight);
    }

    if (state.logOpts.auto && !logTimer) {
      logTimer = setInterval(function () {
        if (state.view !== 'logs') { stopLogTimer(); return; }
        loadLogs().then(function () {
          if (state.view === 'logs') renderLogs();
        }).catch(function () { stopLogTimer(); });
      }, 5000);
    }
  }

  /* ------------------------------------------------------- fnknock 联动 */

  // fn-knock（敲门）自带「通用黑名单」，本页面把 fail2ban 的封禁/解封同步过去，
  // 让经过敲门网关的流量在应用层也被拦下。
  function fnkSettings() {
    return (state.fnknock && state.fnknock.settings) || {};
  }

  function loadFnkBlacklist() {
    var opts = state.fnkOpts;
    var query = 'fnknock/blacklist?page=' + opts.page + '&limit=' + opts.limit +
      '&search=' + encodeURIComponent(opts.search || '');
    return api(query).then(function (data) {
      state.fnkBlacklist = data;
    }).catch(function (err) {
      // 对端未安装 / 未配置时不应影响整页渲染
      state.fnkBlacklist = { error: localizeErr(err), page: opts.page, limit: opts.limit };
    });
  }

  function fnkBlacklistItems() {
    var payload = (state.fnkBlacklist && state.fnkBlacklist.data) || {};
    if (Array.isArray(payload)) return payload;
    return payload.items || payload.list || payload.records || payload.entries || [];
  }

  function renderFnknock() {
    var host = $('#view');
    var data = state.fnknock;
    if (!data) { host.innerHTML = t('fnk.loadingInline'); return; }

    var s = data.settings || {};
    var detected = data.detected || {};
    // 后端偶尔会少带这一个字段（例如请求被截断），这里绝不能因此整页崩掉
    var f2bState = data.fail2ban || {};
    var log = data.log || [];
    var pollInterval = (state.notifications && state.notifications.settings &&
      state.notifications.settings.pollInterval) || 30;

    // 「测试连接 / 保存设置」放到分区标签条的右端：标签条这一行本来右侧就空着，
    // 而这两个动作作用于整页六块，贴着标签条比另起一行更贴合它们的适用范围。
    var html = '';

    if (!detected.installed && !detected.running) {
      html += '<div class="banner danger"><div class="banner-body">' +
        ("<strong>" + t('fnk.notDetectedTitle') + "</strong>") +
        t('fnk.notDetectedBody1') + t('fnk.notDetectedBody2') +
        '</div></div>';
    } else if (detected.installed && !detected.running) {
      html += '<div class="banner"><div class="banner-body">' +
        t('fnk.notRespondingTitle') + t('fnk.noResponseBody') +
        '</div></div>';
    }

    html += fnkWorkbarHtml();
    html += fnkTabHtml(state.fnkTab, data, s, detected, f2bState, log, pollInterval);
    host.innerHTML = html;
  }

  // 与设置页同一套标签条：六块一次只展开一块，窄屏横向滚动。
  // 「测试连接 / 保存设置」留在标签条右侧、但在灰底之外 —— 灰底是「这是一组标签」
  // 的视觉边界，把两个动作包进去，会让人以为它们也属于某个分区。
  function fnkWorkbarHtml() {
    var html = '<div class="fnk-bar-row">' +
      '<div class="workbar fnk-bar" id="fnkWorkbar" role="tablist">';
    FNK_TABS.forEach(function (id) {
      var on = state.fnkTab === id;
      html += '<button type="button" role="tab" data-fnk-tab="' + esc(id) + '"' +
        ' aria-selected="' + (on ? 'true' : 'false') + '"' +
        (on ? ' class="on"' : '') + '>' + esc(fnkTabLabel(id)) + '</button>';
    });
    html += '</div>' +
      '<button type="button" class="btn sm" data-act="fnk-test">' +
      t('common.testConnection') + '</button>' +
      '<button type="button" class="btn sm primary" data-act="fnk-save">' +
      t('common.saveSettings') + '</button>' +
      '</div>';
    return html;
  }

  function fnkTabHtml(tab, data, s, detected, f2bState, log, pollInterval) {
    switch (tab) {
      case 'sync': return fnkSyncPanel(data, s);
      case 'conn': return fnkConnPanel(data, s, detected);
      case 'status': return fnkStatusPanel(data, s, detected, f2bState, pollInterval);
      case 'list': return fnkListPanel();
      case 'log': return fnkLogPanel(log, s);
      default: return fnkModePanel(data, s);
    }
  }

  // ---- 封禁方式：决定 fail2ban 封禁一个 IP 时由谁来执行 ----
  function fnkModePanel(data, s) {
    var ba = data.banaction || {};
    var g = data.gateway || {};
    return '<div class="card">' + cardHead(t('fnk.modeTitle')) +
      switchRowFor('fnk', 'banViaFnknock', t('fnk.enableLabel'), s.banViaFnknock,
        t('fnk.enableHelp1') + t('fnk.enableHelp2') + t('fnk.enableHelp3')) +
      (s.banViaFnknock && !s.keepFirewall
        ? '<div class="merged-hint">' + t('fnk.onlyKnockHint1') + t('fnk.onlyKnockHint2') + '</div>'
        : '') +
      // 高级选项，默认关闭（即只走敲门）
      '<div class="field" id="fnkKeepFirewallWrap"' + (s.banViaFnknock ? '' : ' hidden') + '>' +
      '<div class="switch">' +
      switchToggle('data-toggle-fnk', 'keepFirewall', s.keepFirewall, t('fnk.keepFirewallLabel')) +
      '<span>' + esc(t('fnk.keepFirewallLabel')) + '</span></div>' +
      '<div class="help">' + esc(t('fnk.keepFirewallHelp1')) + esc(t('fnk.keepFirewallHelp2')) + '</div>' +
      '</div>' +
      '<div class="help">' + esc(t('fnk.modulesHint')) + '</div>' +
      '</div>' +

      '<div class="card">' + cardHead(t('fnk.effectiveAction')) +
      '<dl class="kv">' +
      kv(t('fnk.effNow'), '<span class="mono">' +
        esc(ba.effectiveBanaction || t('fnk.inheritedDefault')) + '</span>') +
      kv(t('fnk.inheritedAction'), '<span class="mono">' + esc(ba.inheritedBanaction || '—') + '</span>') +
      kv(t('fnk.actionFile'), ba.actionInstalled
        ? '<span class="badge ok">' + t('fnk.badgeInstalled') + '</span>'
        : '<span class="badge muted">' + t('fnk.badgeNotInstalled') + '</span>') +
      kv(t('fnk.filterRow'), g.filterInstalled
        ? '<span class="badge ok">' + t('common.installed') + '</span> <span class="mono">' +
          esc(g.filterName) + '.local</span>'
        : '<span class="badge muted">' + t('fnk.badgeNotInstalled') + '</span>') +
      kv(t('fnk.jailRow'), g.jailInstalled
        ? '<span class="badge ok">' + t('fnk.joined') + '</span> <span class="mono">' +
          esc(g.jailName) + '</span> <span class="hint">' + esc(g.logPath) + '</span>'
        : '<span class="badge muted">' + t('fnk.badgeNotJoined') + '</span>') +
      kv(t('fnk.ignoreRow'), g.jailInstalled && g.ignoreip
        ? '<span class="mono">' + esc(g.ignoreip) + '</span>'
        : '<span class="badge muted">—</span>') +
      '</dl>' +
      (g.jailInstalled && g.logDirExists === false
        ? '<div class="help warn-text">' + t('fnk.logDirMissingPrefix') + ' <span class="mono">' +
          esc(g.logDir) + '</span> ' + t('fnk.logDirMissingMid') + t('fnk.logDirMissing2') + '</div>'
        : '') +
      (ba.actionInstalled
        ? '<div class="help"><span class="mono">' + esc(ba.actionFile) + '</span></div>'
        : '') +
      '</div>' +

      '<div class="card">' + cardHead(t('fnk.howCardTitle')) +
      '<div class="help help-tight">' + t('fnk.howText') + '</div></div>';
  }

  // ---- 同步：把 fail2ban 的封禁 / 解封镜像进敲门黑名单 ----
  function fnkSyncPanel(data, s) {
    return '<div class="card">' + cardHead(t('fnk.syncTitle')) +
      switchRowFor('fnk', 'enabled', t('fnk.syncEnable'), s.enabled, t('fnk.syncEnableHelp')) +
      switchRowFor('fnk', 'removeOnUnban', t('fnk.removeOnUnban'), s.removeOnUnban,
        t('fnk.removeOnUnbanHelp')) +
      '<div class="field"><label>' + t('fnk.sourceLabel') + '</label><select id="fnkSource">' +
      (data.sources || []).map(function (item) {
        return option(item.value, tSrc(item.label), s.source);
      }).join('') + '</select><div class="help">' + esc(t('fnk.sourceHelpPrefix')) + '</div></div>' +
      '<div class="field"><label>' + t('fnk.noteLabel') + '</label>' +
      '<input type="text" id="fnkComment" value="' + esc(s.comment || 'fail2ban') + '">' +
      '<div class="help">' + esc(t('fnk.noteHelpPrefix')) + '</div></div>' +
      '<div class="help">' + esc(t('fnk.instantHelp1')) + esc(t('fnk.instantHelp2')) + '</div>' +
      '</div>' +

      '<div class="card">' + cardHead(t('fnk.manualTitle')) +
      '<div class="field"><label>' + t('fnk.ipLabel') + '</label>' +
      '<input type="text" id="fnkManualIps" placeholder="' + esc(t('fnk.manualIpsPlaceholder')) + '">' +
      '<div class="help">' + esc(t('fnk.loopbackNote1')) + esc(t('fnk.loopbackNote2b')) + '</div></div>' +
      cardFoot(
        '<button class="btn" data-act="fnk-push-all">' + t('fnk.pushAll') + '</button>' +
        '<button class="btn" data-act="fnk-push">' + t('fnk.addToBlacklist') + '</button>' +
        '<button class="btn danger" data-act="fnk-remove">' + t('fnk.removeFromBlacklist') + '</button>') +
      '</div>';
  }

  // ---- 连接：敲门管理接口的地址、鉴权与超时 ----
  function fnkConnPanel(data, s, detected) {
    return '<div class="card">' + cardHead(t('fnk.connTitle')) +
      '<div class="field"><label>' + t('fnk.baseUrlLabel') + '</label>' +
      '<input type="text" id="fnkBaseUrl" value="' + esc(s.baseUrl || '') +
      '" placeholder="' + esc(detected.baseUrl || 'http://127.0.0.1:7998') + '">' +
      '<div class="help">' + t('fnk.baseUrlHelpText') + '</div></div>' +
      '<div class="field"><label>' + t('fnk.authLabel') + '</label><select id="fnkAuthMode">' +
      (data.authModes || []).map(function (item) {
        return option(item.value, tSrc(item.label), s.authMode);
      }).join('') + '</select><div class="help">' + t('fnk.authHelpText') + '</div></div>' +
      '<div class="field"><label>' + t('fnk.headerNameLabel') + '</label>' +
      '<input type="text" id="fnkAuthHeader" value="' + esc(s.authHeader || 'x-fn-knock-admin-auth') + '">' +
      '<div class="help">' + esc(t('fnk.headerNameHelpText')) + '</div></div>' +
      '<div class="field"><label>' + t('fnk.credentialLabel') + '</label>' +
      '<input type="text" id="fnkAuthValue" value="' + esc(s.authValue || '') +
      '" placeholder="' + esc(t('fnk.credentialPlaceholder')) + '">' +
      '<div class="help">' + esc(t('fnk.credentialHelpText')) + '</div></div>' +
      '<div class="field"><label>' + t('fnk.timeoutLabel') + '</label>' +
      '<input type="number" id="fnkTimeout" min="2" max="60" value="' + esc(s.timeout || 8) + '"></div>' +
      cardFoot('<button class="btn" data-act="fnk-test">' + t('common.testConnection') + '</button>') +
      '</div>';
  }

  // ---- 运行状态：本机探测结果 ----
  function fnkStatusPanel(data, s, detected, f2bState, pollInterval) {
    return '<div class="card">' + cardHead(t('fnk.probeTitle')) +
      '<dl class="kv">' +
      kv(t('fnk.installState'), detected.installed
        ? '<span class="badge ok">' + t('fnk.badgeInstalled') + '</span>'
        : '<span class="badge muted">' + t('fnk.badgeNotDetected') + '</span>') +
      kv(t('fnk.varDir'), '<span class="mono">' + esc(detected.varDir || '—') + '</span>') +
      kv(t('fnk.backend'), detected.running
        ? '<span class="badge ok">' + t('fnk.listening') + '</span> <span class="mono">127.0.0.1:' +
          esc(detected.port) + '</span>'
        : '<span class="badge muted">' + t('fnk.badgeNoResponse') + '</span>') +
      kv(t('fnk.localSecret'), detected.secretFound
        ? '<span class="badge ok">' + t('fnk.badgeReadable') + '</span>'
        : '<span class="badge muted">' + t('fnk.badgeNotFound') + '</span>') +
      kv('fail2ban', f2bState.installed
        ? (f2bState.running
          ? '<span class="badge ok">' + t('notify.badgeRunning') + '</span>'
          : '<span class="badge muted">' + t('notify.badgeStopped') + '</span>')
        : '<span class="badge muted">' + t('dash.badgeNotInstalled') + '</span>') +
      kv(t('fnk.pollIntervalRow'), esc(t('fnk.pollIntervalValue', { n: pollInterval }))) +
      '</dl>' +
      cardFoot('<button class="btn" data-act="fnk-test">' + t('common.testConnection') + '</button>') +
      '</div>';
  }

  // ---- 黑名单：敲门那边的通用黑名单分页表 ----
  function fnkListPanel() {
    var bl = state.fnkBlacklist;
    var html = '<div class="card">' + cardHead(t('fnk.blacklistTitle'),
      '<button class="btn sm" data-act="fnk-reload">' + t('common.refreshList') + '</button>') +
      '<div class="toolbar">' +
      '<input type="text" id="fnkSearch" class="toolbar-search" placeholder="' +
      esc(t('fnk.searchPlaceholder')) + '" value="' + esc(state.fnkOpts.search) + '">' +
      '</div>';

    if (!bl) {
      html += t('fnk.loadingInline');
    } else if (bl.error) {
      html += '<div class="empty">' + esc(bl.error) + '</div>';
    } else {
      var items = fnkBlacklistItems();
      if (!items.length) {
        html += t('fnk.blacklistEmptyInline');
      } else {
        html += '<div class="table-wrap"><table class="fixed fnk-table"><thead><tr>' +
          '<th class="c-ip">IP</th><th class="c-source">' + t('fnk.colSource') + '</th>' +
          '<th class="c-note">' + t('fnk.colNote') + '</th>' +
          '<th class="c-exp">' + t('ban.colExpires') + '</th><th class="c-acts"></th>' +
          '</tr></thead><tbody>';
        items.forEach(function (item) {
          var ip = item.ip || item.address || item.client_ip || '';
          html += '<tr>' +
            '<td class="c-ip mono">' + esc(ip) + '</td>' +
            '<td class="c-source">' + esc(item.source || '—') + '</td>' +
            '<td class="c-note">' + esc(item.comment || '—') + '</td>' +
            '<td class="c-exp">' + esc(item.expires_at || item.expiresAt || t('common.permalink')) + '</td>' +
            '<td class="c-acts"><div class="cell-actions">' +
            '<button class="btn sm danger" data-act="fnk-remove-one" data-ip="' + esc(ip) + '">' +
            t('set.remove') + '</button></div></td></tr>';
        });
        html += '</tbody></table></div>';
      }
      var payload = bl.data || {};
      var total = payload.total !== undefined ? payload.total : payload.totalCount;
      if (total !== undefined) {
        html += '<div class="help">' + esc(t('fnk.totalCount', { n: total })) + '</div>';
      }
    }
    return html + '</div>';
  }

  // ---- 联动日志：最近的同步 / 测试记录 ----
  function fnkLogPanel(log, s) {
    var html = '<div class="card">' + cardHead(t('fnk.logTitle'),
      log.length ? '<button class="btn sm" data-act="fnk-log-clear">' +
        t('notify.clearHistory') + '</button>' : '');
    if (!log.length) {
      html += '<div class="empty">' + t('fnk.logEmpty') +
        (s.enabled ? t('fnk.logEmptyOn') : t('fnk.logEmptyOff')) + '</div>';
    } else {
      html += '<div class="event-list">' +
        log.slice().reverse().map(function (entry) {
          var label = entry.action === 'unban' ? t('fnk.logUnban')
            : (entry.action === 'test' ? t('fnk.logTest') : t('fnk.logBan'));
          return '<div class="event-item ' + (entry.action === 'unban' ? 'unban' : 'ban') + '">' +
            '<span class="ev-dot"></span>' +
            '<div class="ev-body"><div class="ev-title">' + esc(label) +
            (entry.jail ? ' · ' + esc(entry.jail) : '') + ' ' +
            (entry.ok ? t('fnk.badgeSuccess') : t('fnk.badgeFailed')) +
            '</div><div class="ev-text">' +
            esc((entry.ips && entry.ips.length ? entry.ips.join(', ') : t('fnk.noIps')) +
              (entry.detail ? t('fnk.sep') + entry.detail : '')) +
            '</div></div>' +
            '<span class="ev-time">' + esc(fmtTime(entry.time)) + '</span>' +
            '</div>';
        }).join('') + '</div>';
    }
    return html + '</div>';
  }

  function draftFnknockSettings() {
    var s = fnkSettings();
    var pick = function (id, fallback) {
      var node = $(id);
      return node ? node.value.trim() : fallback;
    };
    return {
      // 开关由 onToggle 直接改内存中的 settings
      enabled: !!s.enabled,
      removeOnUnban: !!s.removeOnUnban,
      banViaFnknock: !!s.banViaFnknock,
      keepFirewall: s.keepFirewall === true,
      baseUrl: pick('#fnkBaseUrl', s.baseUrl || ''),
      authMode: pick('#fnkAuthMode', s.authMode || 'auto'),
      authHeader: pick('#fnkAuthHeader', s.authHeader || ''),
      authValue: pick('#fnkAuthValue', s.authValue || ''),
      source: pick('#fnkSource', s.source || 'manual'),
      comment: pick('#fnkComment', s.comment || 'fail2ban'),
      timeout: parseInt(pick('#fnkTimeout', s.timeout || 8), 10) || 8
    };
  }

  function saveFnknock(button) {
    busy(button, true);
    api('fnknock', { method: 'PUT', body: { settings: draftFnknockSettings() } })
      .then(function (data) {
        state.fnknock.settings = data.settings;
        state.fnknock.detected = data.detected;
        state.fnknock.banaction = data.banaction;
        var message = t('fnk.saved');
        if (data.applied && data.applied.enabled) {
          message += data.applied.keptFirewall
            ? (t('fnk.savedSwitchedPrefix') + " ") + data.applied.banaction
            : t('fnk.savedKnockOnlySuffix');
          if (data.reloaded === false) message += t('fnk.reloadFailedInline', { detail: (data.reloadError || t('common.unknown')) });
          else message += t('fnk.restartHint');
        } else if (data.applied) {
          message += t('fnk.savedRestoredSuffix');
        }
        toast(message, data.reloadError ? 'warn' : 'ok', 7000);
        return loadFnkBlacklist();
      })
      .then(function () { if (state.view === 'fnknock') renderFnknock(); })
      .catch(function (err) { toast(localizeErr(err), 'err', 8000); })
      .finally(function () { busy(button, false); });
  }

  function testFnknock(button) {
    busy(button, true);
    api('fnknock/test', { method: 'POST', body: { settings: draftFnknockSettings() } })
      .then(function (data) {
        toast(t('fnk.connected', { url: data.baseUrl, header: data.authHeader,
          n: (data.total || 0) }), 'ok', 6000);
        return api('fnknock').then(function (d) { state.fnknock = d; });
      })
      .then(function () { if (state.view === 'fnknock') renderFnknock(); })
      .catch(function (err) { toast(localizeErr(err), 'err', 9000); })
      .finally(function () { busy(button, false); });
  }

  function fnknockSync(payload, button) {
    busy(button, true);
    api('fnknock/sync', { method: 'POST', body: payload })
      .then(function (data) {
        toast(localizeMsg(data.message, 'common.completed'), 'ok', 5000);
        return Promise.all([
          api('fnknock').then(function (d) { state.fnknock = d; }),
          loadFnkBlacklist()
        ]);
      })
      .then(function () { if (state.view === 'fnknock') renderFnknock(); })
      .catch(function (err) { toast(localizeErr(err), 'err', 9000); })
      .finally(function () { busy(button, false); });
  }

  function manualFnknockSync(action, button) {
    var raw = ($('#fnkManualIps') || {}).value || '';
    var ips = raw.split(/[\s,;，；]+/).filter(function (item) { return item; });
    if (!ips.length) { toast(t('fnk.needIps'), 'err'); return; }
    fnknockSync({ action: action, ips: ips }, button);
  }

  /* ------------------------------------------------- 手机端选择器 */

  // 原生 <select> 在手机上弹出的是系统自带列表：主题、行高、触控尺寸都与面板
  // 不是一套。窄屏下拦住它的默认弹层，改用底部选择条（与面板同一套令牌）。
  // 选中后仍然派发一次真正的 change 事件，页面里原有的 onChange 逻辑照旧生效 ——
  // 这样以后新增下拉时不必再写一遍手机端分支。
  var pickerCtx = null;

  function mobilePickerOn() {
    return window.innerWidth <= 720;
  }

  // 选择条标题取字段的 label：没有就退回 aria-label，都没有就不显示标题行
  function pickerLabelOf(select) {
    var field = select.closest ? select.closest(".field") : null;
    var label = field ? field.querySelector("label") : null;
    var text = label ? String(label.textContent || "").trim() : "";
    if (!text) text = String(select.getAttribute("aria-label") || "").trim();
    return text;
  }

  function closePicker() {
    var mask = $("#pickerMask");
    if (mask) mask.hidden = true;
    pickerCtx = null;
  }

  function openPicker(select) {
    var mask = $("#pickerMask");
    var list = $("#pickerList");
    var title = $("#pickerTitle");
    if (!mask || !list || !title || !select) return;
    var current = String(select.value);
    var html = "";
    for (var i = 0; i < select.options.length; i += 1) {
      var opt = select.options[i];
      var value = String(opt.value);
      html += '<button type="button" class="picker-item" data-pick="' + esc(value) + '"' +
        (opt.disabled ? " disabled" : "") + ">" +
        '<span class="lbl">' + esc(opt.textContent || value) + "</span>" +
        '<i class="dot' + (value === current ? " on" : "") + '"></i></button>';
    }
    list.innerHTML = html;
    title.textContent = pickerLabelOf(select);
    title.hidden = !title.textContent;
    pickerCtx = select;
    mask.hidden = false;
    list.scrollTop = 0;
  }

  function pickValue(value) {
    var select = pickerCtx;
    closePicker();
    if (!select) return;
    if (String(select.value) === String(value)) return;
    select.value = value;
    // 派发真正的 change：换排序字段要重置方向、切 Jail 要重拉封禁状态，
    // 全在既有的 onChange 里，这里不重复实现一遍。
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  /* ------------------------------------------------------------ 抽屉 */

  function openDrawer(title, body, foot) {
    $('#drawerTitle').textContent = title;
    $('#drawerBody').innerHTML = body;
    $('#drawerFoot').innerHTML = foot || '';
    $('#drawer').classList.add('open');
    $('#drawerMask').classList.add('open');
  }

  function closeDrawer() {
    $('#drawer').classList.remove('open');
    $('#drawerMask').classList.remove('open');
    drawerCtx.jail = null;
    drawerCtx.channelId = null;
    drawerCtx.jailLog = null;
    drawerCtx.channelSecrets = {};
    // 滑出动画（.24s）结束后清空内容。残留的隐藏表单会和页面上的输入框抢同一个 id，
    // 例如抽屉里的 #manualIp 会盖住「封禁管理」页的 #banIp，导致点封禁总提示未填 IP。
    setTimeout(function () {
      if ($('#drawer').classList.contains('open')) return;  // 期间又打开了新抽屉
      $('#drawerBody').innerHTML = '';
      $('#drawerFoot').innerHTML = '';
    }, 300);
  }

  /* --------------------------------------------------------- 交互动作 */

  function doService(action, button) {
    busy(button, true);
    api('service', { method: 'POST', body: { action: action } }).then(function (data) {
      toast(localizeMsg(data.message, 'common.completed'), 'ok');
      return refresh();
    }).catch(function (err) {
      toast(localizeErr(err), 'err');
    }).finally(function () { busy(button, false); });
  }

  function confirmDanger(message) {
    if (state.prefs && state.prefs.confirmDanger === false) return true;
    return window.confirm(message);
  }

  function onClick(event) {
    var trigger = event.target.closest('[data-act]');
    if (!trigger) return;
    var act = trigger.dataset.act;
    var name = trigger.dataset.name;
    var ip = trigger.dataset.ip;

    if (act === 'drawer-close') { closeDrawer(); return; }

    /* --- 设置页标签条 --------------------------------------------------- */

    // 标签按钮自带 data-set-tab，不走 data-act；这里兜一层是为了键盘/未来扩展
    var setTabBtn = event.target.closest('[data-set-tab]');
    if (setTabBtn) { setSettingsTab(setTabBtn.dataset.setTab); return; }

    if (act === 'copy-ip') { copyText(ip, trigger); return; }
    if (act === 'copy-path') { copyText(trigger.dataset.path, trigger); return; }

    // 行复选框的勾选由 onChange 处理（点击时 checked 已更新，这里什么也不做）
    if (act === 'select-ip') return;

    /* --- 排序控件 ------------------------------------------------------- */

    if (act === 'sort-dir') {
      var sorted = sortStateOf(trigger.dataset.scope);
      sorted.dir = sorted.dir === 'asc' ? 'desc' : 'asc';
      rerenderSort(trigger.dataset.scope);
      return;
    }

    /* --- 单 Jail 日志 --------------------------------------------------- */

    if (act === 'jail-log') { openJailLog(name); return; }
    if (act === 'jail-log-refresh') { loadJailLog(name || drawerCtx.jailLog); return; }

    /* --- 白名单 Chip ---------------------------------------------------- */

    if (act === 'ignoreip-add') { addIgnoreIpFromInput(); return; }

    if (act === 'ignoreip-remove') {
      var index = parseInt(trigger.dataset.index, 10);
      var items = ignoreIpState();
      if (!isNaN(index) && index >= 0 && index < items.length) items.splice(index, 1);
      renderIgnoreIpList();
      return;
    }

    if (act === 'install') {
      if (!confirmDanger(t('dash.installConfirm'))) return;
      busy(trigger, true);
      api('install', { method: 'POST', body: {} }).then(function (data) {
        toast(localizeMsg(data.message, 'dash.installDone'), 'ok');
        return refresh();
      }).catch(function (err) { toast(localizeErr(err), 'err', 9000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'svc') { doService(trigger.dataset.svc, trigger); return; }
    if (act === 'refresh') { refresh(); return; }
    if (act === 'reload-config') { doService('reload', trigger); return; }

    if (act === 'fnk-save') { saveFnknock(trigger); return; }
    if (act === 'fnk-test') { testFnknock(trigger); return; }
    if (act === 'fnk-reload') {
      loadFnkBlacklist().then(function () { if (state.view === 'fnknock') renderFnknock(); });
      return;
    }
    if (act === 'fnk-push-all') { fnknockSync({ action: 'push-all' }, trigger); return; }
    if (act === 'fnk-push') { manualFnknockSync('push', trigger); return; }
    if (act === 'fnk-remove') { manualFnknockSync('remove', trigger); return; }
    if (act === 'fnk-remove-one') {
      if (!confirmDanger(t('fnk.removeConfirm', { ip: ip }))) return;
      fnknockSync({ action: 'remove-one', ips: [ip] }, trigger);
      return;
    }
    if (act === 'fnk-log-clear') {
      api('fnknock/log/clear', { method: 'POST', body: {} })
        .then(function () { return api('fnknock'); })
        .then(function (data) {
          state.fnknock = data;
          toast(t('fnk.logCleared'), 'ok');
          if (state.view === 'fnknock') renderFnknock();
        })
        .catch(function (err) { toast(localizeErr(err), 'err'); });
      return;
    }

    if (act === 'jail-edit') { openJailEditor(name); return; }

    if (act === 'view-bans') {
      state.bans.jail = name;
      setView('bans');
      return;
    }

    if (act === 'jail-new') { openJailCreator(); return; }

    if (act === 'jail-save') {
      var values = collectFields($('#drawerBody'));
      if (!Object.keys(values).length) { toast(t('err.noChanges'), 'warn'); return; }
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name), { method: 'PUT', body: { values: values, reload: reloadFlag() } })
        .then(function (data) {
          toast(data.reloaded ? t('set.savedReloaded') : t('jail.saved'), data.reloadError ? 'warn' : 'ok');
          if (data.reloadError) toast(t('err.reloadFailedPrefix') + data.reloadError, 'err');
          closeDrawer();
          return refresh();
        }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'jail-create') {
      var newName = ($('#newJailName') || {}).value || '';
      newName = newName.trim();
      if (!newName) { toast(t('err.fillJailName'), 'warn'); return; }
      var newValues = collectFields($('#drawerBody'));
      newValues.enabled = newValues.enabled || 'true';
      busy(trigger, true);
      api('jails', { method: 'POST', body: { name: newName, values: newValues, reload: reloadFlag() } })
        .then(function () {
          toast((t('jail.createdPrefix') + " ") + newName, 'ok');
          closeDrawer();
          return refresh();
        }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'jail-reset') {
      if (!confirmDanger(t('jail.unmanageConfirm'))) return;
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name), { method: 'DELETE', body: { reload: reloadFlag() } })
        .then(function () { toast(t('jail.unmanaged'), 'ok'); closeDrawer(); return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'jail-start' || act === 'jail-stop') {
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name) + '/actions', {
        method: 'POST',
        body: { action: act === 'jail-start' ? 'start' : 'stop' }
      }).then(function (data) {
        toast(localizeMsg(data.message, 'common.done'), 'ok');
        return refresh();
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'ban') {
      // 「配置 Jail」抽屉与「封禁管理」页各有一个手动封禁输入框，按按钮所在位置取，
      // 不能用 #manualIp || #banIp 这种顺序盲取 —— 抽屉关闭后它的 DOM 仍会短暂存在
      var input = trigger.closest('#drawerBody') ? $('#manualIp') : $('#banIp');
      var value = input ? input.value.trim() : '';
      if (!value) { toast(t('err.fillIp'), 'warn'); return; }
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name) + '/actions', { method: 'POST', body: { action: 'ban', ip: value } })
        .then(function (data) { toast(localizeMsg(data.message, 'ban.banned'), 'ok'); if (input) input.value = ''; return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'unban') {
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name) + '/actions', { method: 'POST', body: { action: 'unban', ip: ip } })
        .then(function (data) { toast(localizeMsg(data.message, 'ban.unbanned'), 'ok'); return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'unban-all') {
      if (!confirmDanger(t('ban.confirmUnbanJail'))) return;
      busy(trigger, true);
      api('jails/' + encodeURIComponent(name) + '/actions', { method: 'POST', body: { action: 'unban-all' } })
        .then(function (data) { toast(localizeMsg(data.message, 'ban.unbanAllDone'), 'ok'); return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    // 汇总视图：一个 IP 可能同时落在多个 Jail 里，逐个解封
    if (act === 'unban-multi') {
      var unbanJails = String(trigger.dataset.jails || '').split(',').filter(function (item) { return item; });
      if (!unbanJails.length) { toast(t('err.orphanRecord'), 'err'); return; }
      if (!confirmDanger(t('ban.confirmUnbanFrom', { jails: unbanJails.join(t('common.dunhao')), ip: ip }))) return;
      busy(trigger, true);
      Promise.all(unbanJails.map(function (jail) {
        return api('jails/' + encodeURIComponent(jail) + '/actions',
          { method: 'POST', body: { action: 'unban', ip: ip } });
      })).then(function () {
        toast((t('ban.unbanned') + " ") + ip, 'ok');
        return refresh();
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    // 汇总视图：勾选多个 IP 后一次性解封（逐条调 unban-multi 的后端动作，最后汇总结果）
    if (act === 'unban-selected') {
      var picks = selectedIps();
      if (!picks.length) { toast(t('ban.pickFirst'), 'warn'); return; }
      var plan = {};
      var entries = (state.bans.all && state.bans.all.entries) || [];
      entries.forEach(function (item) {
        if (!state.bans.selected[item.ip]) return;
        plan[item.ip] = (item.jails || []).filter(function (j) { return j; });
      });
      if (!picks.length) { toast(t('err.selectionGone'), 'warn'); return; }
      if (!confirmDanger(t('ban.confirmUnbanSelected', { n: picks.length }))) return;
      busy(trigger, true);
      var done = 0;
      var failed = [];
      var queue = Promise.resolve();
      picks.forEach(function (targetIp) {
        var jails = plan[targetIp] || [];
        queue = queue.then(function () {
          if (!jails.length) { failed.push(targetIp + t('err.orphanJail')); return null; }
          return Promise.all(jails.map(function (jail) {
            return api('jails/' + encodeURIComponent(jail) + '/actions',
              { method: 'POST', body: { action: 'unban', ip: targetIp } });
          })).then(function () { done++; })
            .catch(function (err) { failed.push(targetIp + t('common.colon') + localizeErr(err)); });
        });
      });
      queue.then(function () {
        if (failed.length) {
          toast(t('ban.unbannedPartial', { done: done, failed: failed.length }) +
            failed.slice(0, 3).join(t('common.semi')) + (failed.length > 3 ? ' …' : ''), 'warn', 8000);
        } else {
          toast(t('ban.unbannedCount', { n: done }), 'ok', 4000);
        }
        state.bans.selected = {};
        return refresh();
      }).finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'clear-selected') {
      state.bans.selected = {};
      renderBans();
      return;
    }

    if (act === 'select-all') {
      var visible = filteredBanRecords();
      var selectAll = !!trigger.checked;
      visible.forEach(function (item) {
        if (selectAll) state.bans.selected[item.ip] = true;
        else delete state.bans.selected[item.ip];
      });
      // 全选只影响当前筛选出来的行，其余已勾选的保持不动
      renderBans();
      return;
    }

    // 汇总视图：一次清空所有运行中 Jail 的封禁列表
    if (act === 'unban-all-jails') {
      var allJails = (state.jails || []).filter(function (j) { return j.active; })
        .map(function (j) { return j.name; });
      if (!allJails.length) { toast(t('err.noJailRunning'), 'err'); return; }
      if (!confirmDanger(t('ban.confirmUnbanAllJails', { n: allJails.length }))) return;
      busy(trigger, true);
      Promise.all(allJails.map(function (jail) {
        return api('jails/' + encodeURIComponent(jail) + '/actions',
          { method: 'POST', body: { action: 'unban-all' } });
      })).then(function () {
        toast(t('ban.unbannedAllJails', { n: allJails.length }), 'ok');
        return refresh();
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'notif-save') {
      saveNotifications(notifSettingsFromForm(), trigger, t('notify.saved'));
      return;
    }

    if (act === 'notif-clear') {
      api('notifications/events/clear', { method: 'POST', body: {} })
        .then(function () { toast(t('notify.historyCleared'), 'ok'); return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); });
      return;
    }

    /* --- 通知事件开关 --------------------------------------------------- */

    if (act === 'event-toggle') {
      var evKey = trigger.dataset.key;
      var settings = state.notifications && state.notifications.settings;
      if (!evKey || !settings || !settings.events) return;
      // 点到的是开关本身时，onToggle 已经翻过值了，这里不要再翻回去
      if (event.target.closest('[data-toggle-notif]')) {
        if (inSettingsTab('notify')) renderSettings();
        return;
      }
      settings.events[evKey] = !settings.events[evKey];
      if (inSettingsTab('notify')) renderSettings();
      return;
    }

    /* --- 投递记录 ------------------------------------------------------- */

    if (act === 'deliv-filter') {
      var nextStatus = trigger.dataset.status || 'all';
      if (state.deliv.status === nextStatus) return;
      state.deliv.status = nextStatus;
      state.deliv.data = null;
      loadDeliveries().then(function () { if (inSettingsTab('notify')) renderSettings(); });
      return;
    }

    if (act === 'deliv-refresh') {
      busy(trigger, true);
      loadDeliveries().then(function () {
        if (inSettingsTab('notify')) renderSettings();
      }).finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'deliv-clear') {
      if (!confirmDanger(t('notify.delivClearConfirm'))) return;
      busy(trigger, true);
      api('notifications/deliveries/clear', { method: 'POST', body: {} })
        .then(function () {
          toast(t('notify.delivCleared'), 'ok');
          state.deliv.data = null;
          return loadDeliveries();
        })
        .then(function () { if (inSettingsTab('notify')) renderSettings(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    /* --- 存储 ----------------------------------------------------------- */

    if (act === 'storage-clean') {
      cleanStorage(trigger.dataset.target || 'logs', trigger);
      return;
    }

    /* --- 更新 ----------------------------------------------------------- */

    if (act === 'update-check') { checkUpdate(true, trigger); return; }
    if (act === 'update-install') { startUpgrade(trigger); return; }

    if (act === 'channel-new') { openChannelEditor(null); return; }

    if (act === 'channel-edit') {
      var editId = trigger.dataset.id;
      var found = (state.notifications.settings.channels || []).filter(function (item) {
        return item.id === editId;
      })[0];
      if (!found) { toast(t('err.channelNotFound'), 'err'); return; }
      openChannelEditor(found);
      return;
    }

    if (act === 'channel-delete' || act === 'channel-remove') {
      var removeId = act === 'channel-remove' ? drawerCtx.channelId : trigger.dataset.id;
      if (!removeId) { closeDrawer(); return; }
      if (!confirmDanger(t('notify.deleteConfirm'))) return;
      var afterDelete = notifSettingsFromForm();
      afterDelete.channels = afterDelete.channels.filter(function (item) {
        return item.id !== removeId;
      });
      saveNotifications(afterDelete, trigger, t('notify.channelDeleted')).then(closeDrawer);
      return;
    }

    if (act === 'channel-save') {
      var draft = draftChannel();
      var invalid = validateChannel(draft);
      if (invalid) { toast(invalid, 'warn'); return; }
      var collection = notifSettingsFromForm();
      var replaced = false;
      collection.channels = collection.channels.map(function (item) {
        if (draft.id && item.id === draft.id) { replaced = true; return draft; }
        return item;
      });
      if (!replaced) collection.channels.push(draft);
      saveNotifications(collection, trigger, t('notify.channelSaved')).then(closeDrawer);
      return;
    }

    if (act === 'channel-test' || act === 'channel-test-draft') {
      var payload = act === 'channel-test-draft'
        ? { channel: draftChannel() }
        : { channelId: trigger.dataset.id };
      if (payload.channel) {
        var problem = validateChannel(payload.channel);
        if (problem) { toast(problem, 'warn'); return; }
      }
      busy(trigger, true);
      api('notifications/test', { method: 'POST', body: payload })
        .then(function (data) { toast(t('notify.testSent', { detail: (data.detail || t('common.success')) }), 'ok'); })
        .catch(function (err) { toast(localizeErr(err), 'err', 7000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'filter-edit') { openFilter(name); return; }

    // 过滤器列表：切换「已启用 / 未启用 / 全部」分类
    if (act === 'filter-status') {
      var nextStatus = trigger.dataset.status;
      if (!nextStatus || state.filterOpts.status === nextStatus) return;
      state.filterOpts.status = nextStatus;
      renderFilters();
      return;
    }

    if (act === 'filter-new') { openFilterCreateDrawer(''); return; }
    if (act === 'filter-template') { openFilterCreateDrawer(null); return; }

    if (act === 'filter-create') {
      var ftName = ($('#ftName') || {}).value.trim();
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(ftName)) {
        toast(t('err.filterNameChars'), 'err');
        return;
      }
      var ftFailregex = splitLines(($('#ftFailregex') || {}).value);
      if (!ftFailregex.length) { toast(t('err.needFailregex'), 'err'); return; }
      busy(trigger, true);
      api('filters/' + encodeURIComponent(ftName), {
        method: 'PUT',
        body: {
          failregex: ftFailregex,
          ignoreregex: splitLines(($('#ftIgnoreregex') || {}).value),
          reload: reloadFlag()
        }
      }).then(function (data) {
        toast(data.reloaded ? t('filter.createdReloaded', { name: ftName }) : t('filter.created', { name: ftName }),
          data.reloadError ? 'warn' : 'ok');
        if (data.reloadError) toast(t('err.reloadFailedPrefix') + data.reloadError, 'err');
        closeDrawer();
        return refresh().then(function () { openFilter(ftName); });
      }).catch(function (err) { toast(localizeErr(err), 'err', 8000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'filter-save') {
      busy(trigger, true);
      api('filters/' + encodeURIComponent(name), {
        method: 'PUT',
        body: {
          failregex: splitLines($('#filterFailregex').value),
          ignoreregex: splitLines($('#filterIgnoreregex').value),
          reload: reloadFlag()
        }
      }).then(function (data) {
        toast(data.reloaded ? t('filter.savedReloaded') : t('filter.saved'), data.reloadError ? 'warn' : 'ok');
        if (data.reloadError) toast(t('err.reloadFailedPrefix') + data.reloadError, 'err');
        return openFilter(name);
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'filter-delete') {
      if (!confirmDanger(t('filter.deleteConfirmInline', { name: name }))) return;
      busy(trigger, true);
      api('filters/' + encodeURIComponent(name), { method: 'DELETE', body: { reload: reloadFlag() } })
        .then(function () { toast(t('filter.deleted'), 'ok'); return openFilter(name); })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'settings-save') {
      var settingsValues = collectFields($('#settingsForm'));
      // ignoreip 是自定义控件，不进 data-field；在这里拼回空格分隔字符串，值没变就不提交
      if (state.settings) {
        var original = String((state.settings.values && state.settings.values.ignoreip) || '')
          .split(/\s+/).filter(function (item) { return item; }).join(' ');
        var draft = ignoreIpState().join(' ');
        if (draft !== original) settingsValues.ignoreip = draft;
      }
      if (!Object.keys(settingsValues).length) { toast(t('err.noChanges'), 'warn'); return; }
      busy(trigger, true);
      api('settings', { method: 'PUT', body: { values: settingsValues, reload: reloadFlag() } })
        .then(function (data) {
          toast(data.reloaded ? t('set.savedReloaded') : t('jail.saved'), data.reloadError ? 'warn' : 'ok');
          if (data.reloadError) toast(t('err.reloadFailedPrefix') + data.reloadError, 'err');
          return refresh();
        }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'backup-save') {
      busy(trigger, true);
      api('backup', { method: 'PUT', body: { settings: backupSettingsFromForm() } })
        .then(function (data) {
          state.backup.settings = data.settings;
          state.backup.directory = data.directory;
          state.backup.backups = data.backups;
          toast(t('set.backupSaved'), 'ok');
          return refresh();
        })
        .catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'backup-run') {
      busy(trigger, true);
      api('backup/run', { method: 'POST', body: {} })
        .then(function (data) {
          toast(t('set.backupCreatedPrefix') + data.name, 'ok', 5000);
          return refresh();
        })
        .catch(function (err) { toast(localizeErr(err), 'err', 8000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    // 「导出配置」：先生成一份归档，再直接把这一份下载下来。
    // 生成与下载分成两步是后端契约决定的（/api/backup/run 只写盘、不回字节），
    // 前端把两步串起来，用户看到的是一次「导出」。
    if (act === 'backup-export') {
      busy(trigger, true);
      api('backup/run', { method: 'POST', body: {} })
        .then(function (data) {
          var name = data && data.name;
          if (!name) throw new Error(t('common.unknownError'));
          var link = document.createElement('a');
          link.href = 'api/backup/download?name=' + encodeURIComponent(name);
          link.download = name;
          document.body.appendChild(link);
          link.click();
          link.remove();
          toast(t('set.backupCreatedPrefix') + name, 'ok', 5000);
          return refresh();
        })
        .catch(function (err) { toast(localizeErr(err), 'err', 8000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'backup-delete') {
      if (!confirmDanger(t('set.backupDeleteConfirmInline', { name: name }))) return;
      api('backup/delete', { method: 'POST', body: { name: name } })
        .then(function () { toast(t('set.backupDeleted'), 'ok'); return refresh(); })
        .catch(function (err) { toast(localizeErr(err), 'err'); });
      return;
    }

    // 恢复是覆盖性操作：先确认，成功后无论 reload 成没成功都要把整页数据重拉，
    // 因为 jail 配置可能已经整片换掉了。reload 失败单独提示，不当作恢复失败。
    if (act === 'backup-restore') {
      if (!confirmDanger(t('set.backupRestoreConfirm', { name: backupLabel(name) }))) return;
      busy(trigger, true);
      api('backup/restore', { method: 'POST', body: { name: name } })
        .then(function (data) {
          toast(t('set.backupRestoreDone'), 'ok', 5000);
          if (data && data.reload && data.reload.ok === false) {
            toast(t('set.backupRestoreReloadFailed') + (data.reload.error || ''), 'warn', 9000);
          }
          // 配置换了，缓存里那些按旧配置渲染的数据全部作废
          state.settings = null;
          state.ignoreIpDraft = null;
          state.jails = [];
          state.filters = [];
          state.overview = null;
          return refresh();
        })
        .catch(function (err) { toast(localizeErr(err), 'err', 9000); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'prefs-save') {
      // 面板行为 / 外观两个分区都可能触发保存：缺哪个控件就沿用当前值，
      // 不能因为「这一屏没有那个输入框」把别的偏好重置掉。
      var cur = prefsOf();
      var prefs = {
        theme: $('#prefTheme') ? $('#prefTheme').value : (cur.theme || 'auto'),
        lang: $('#prefLang') ? $('#prefLang').value : (cur.lang || ''),
        sidebar: $('#prefSidebar') ? $('#prefSidebar').value : (cur.sidebar || 'auto'),
        density: $('#prefDensity') ? $('#prefDensity').value : (cur.density || 'comfortable'),
        logLines: $('#prefLogLines')
          ? (parseInt($('#prefLogLines').value, 10) || 400) : (cur.logLines || 400),
        autoReload: state.prefs.autoReload !== false,
        confirmDanger: state.prefs.confirmDanger !== false
      };
      busy(trigger, true);
      api('prefs', { method: 'PUT', body: { prefs: prefs } }).then(function (data) {
        state.prefs = data.prefs;
        state.logOpts.lines = data.prefs.logLines;
        applyTheme(data.prefs.theme);
        applyDensity(data.prefs.density);
        applySidebarPref(data.prefs.sidebar);
        applyLangPref(data.prefs.lang);
        toast(t('set.prefsSaved'), 'ok');
        render();
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'storage-refresh') {
      busy(trigger, true);
      api('storage').then(function (d) {
        state.storage = d;
        renderSettings();
      }).catch(function (err) { toast(localizeErr(err), 'err'); })
        .finally(function () { busy(trigger, false); });
      return;
    }

    if (act === 'log-level') {
      // 纯前端筛选：日志已经全部拉下来了，切级别不再发请求
      state.logOpts.level = trigger.dataset.level || 'all';
      renderLogs(true);
      return;
    }

    if (act === 'logs-refresh') { refresh(); return; }

    if (act === 'logs-export') {
      var lines = (state.logs && state.logs.lines) || [];
      if (!lines.length) { toast(t('err.nothingToExport'), 'warn'); return; }
      var blob = new Blob([lines.join('\n')], { type: 'text/plain;charset=utf-8' });
      var url = URL.createObjectURL(blob);
      var link = document.createElement('a');
      link.href = url;
      link.download = 'fail2ban-' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '') + '.log';
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 4000);
      return;
    }
  }

  function splitLines(text) {
    return String(text || '').split('\n').map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });
  }

  function reloadFlag() {
    return !(state.prefs && state.prefs.autoReload === false);
  }

  function openJailCreator() {
    var fields = (state.meta.jailFields || []).map(function (f) {
      var copy = Object.assign({}, f);
      if (f.key === 'enabled') {
        return '<div class="field"><label>' + esc(tSrc(f.label)) + '</label>' +
          '<div class="switch">' + switchToggle('data-toggle', 'enabled', true, tSrc(f.label)) +
          ("<span class=\"hint\" data-bool-label=\"enabled\">" + t('common.enabled') + "</span>") +
          '<input type="hidden" data-field="enabled" data-orig="false" value="true"></div></div>';
      }
      return fieldHtml(copy, {}, {}, {}, {});
    }).join('');

    var body = ("<div class=\"field\"><label>Jail " + t('jail.colName') + "</label>") +
      ("<input type=\"text\" id=\"newJailName\" placeholder=\"" + t('jail.namePlaceholder') + "\"></div>") +
      ("<div class=\"section-title\">" + t('jail.paramsTitle') + "</div>") + fields +
      ("<div class=\"help\">" + t('jail.createHint') + "</div>");

    var foot = '<div class="spacer"></div>' +
      ("<button class=\"btn\" data-act=\"drawer-close\">" + t('common.cancel') + "</button>") +
      ("<button class=\"btn primary\" data-act=\"jail-create\">" + t('common.create') + "</button>");

    openDrawer(t('jail.newTitle'), body, foot);
  }

  function onToggle(event) {
    var boolTrack = event.target.closest('[data-toggle]');
    if (boolTrack) {
      var key = boolTrack.dataset.toggle;
      var input = $('#drawerBody [data-field="' + key + '"]') ||
        $('#settingsForm [data-field="' + key + '"]');
      var next = input && isOn(input.value) ? 'false' : 'true';
      if (input) input.value = next;
      setSwitch(boolTrack, next === 'true');
      var label = document.querySelector('[data-bool-label="' + key + '"]');
      if (label) label.textContent = next === 'true' ? t('common.enabled') : t('common.close');
      return;
    }

    var notifTrack = event.target.closest('[data-toggle-notif]');
    if (notifTrack && state.notifications) {
      var path = notifTrack.dataset.toggleNotif.split('.');
      var target = state.notifications.settings;
      for (var i = 0; i < path.length - 1; i++) target = target[path[i]];
      var leaf = path[path.length - 1];
      if (notifTrack.dataset.toggleNotif === 'browserNotify') {
        if (target[leaf]) {
          target[leaf] = false;
          setSwitch(notifTrack, false);
          return;
        }
        ensureNotificationPermission(function (granted) {
          if (!granted) return;
          target[leaf] = true;
          setSwitch(notifTrack, true);
          toast(t('jail.toggleOnHint'), 'warn', 4200);
        });
        return;
      }
      target[leaf] = !target[leaf];
      setSwitch(notifTrack, target[leaf]);
      return;
    }

    var backupTrack = event.target.closest('[data-toggle-backup]');
    if (backupTrack && state.backup) {
      var backupKey = backupTrack.dataset.toggleBackup;
      state.backup.settings[backupKey] = !state.backup.settings[backupKey];
      setSwitch(backupTrack, state.backup.settings[backupKey]);
      return;
    }

    var fnkTrack = event.target.closest('[data-toggle-fnk]');
    if (fnkTrack && state.fnknock) {
      var fnkKey = fnkTrack.dataset.toggleFnk;
      state.fnknock.settings[fnkKey] = !state.fnknock.settings[fnkKey];
      setSwitch(fnkTrack, !!state.fnknock.settings[fnkKey]);
      if (fnkKey === 'banViaFnknock') {
        // 这一块里「保留系统防火墙」子项与「只走敲门」的提示都跟着它变，
        // 直接重绘这块最省事，也避免两种状态各写一套显隐逻辑
        renderFnknock();
      }
      return;
    }

    var prefTrack = event.target.closest('[data-toggle-pref]');
    if (prefTrack) {
      var prefKey = prefTrack.dataset.togglePref;
      state.prefs[prefKey] = !(state.prefs[prefKey] !== false);
      setSwitch(prefTrack, state.prefs[prefKey]);
      return;
    }

    // 日志页的「自动刷新」是一个普通复选框，直接读它的勾选状态
    if (event.target && event.target.id === 'logAuto') {
      state.logOpts.auto = !!event.target.checked;
      if (!state.logOpts.auto) stopLogTimer();
      else loadLogs().then(function () { if (state.view === 'logs') renderLogs(); })
        .catch(function () { stopLogTimer(); });
      return;
    }

    var clearBtn = event.target.closest('[data-clear]');
    if (clearBtn) {
      var clearKey = clearBtn.dataset.clear;
      var clearInput = $('#drawerBody [data-field="' + clearKey + '"]') ||
        $('#settingsForm [data-field="' + clearKey + '"]');
      if (clearInput) clearInput.value = '';
      var clearLabel = document.querySelector('[data-bool-label="' + clearKey + '"]');
      if (clearLabel) clearLabel.textContent = t('jail.clearOverride');
      toast(t('jail.clearOverrideToast'), 'warn');
    }
  }

  function onChange(event) {
    // 排序字段下拉：换字段时重置方向（文本升序、计数降序）
    if (event.target.dataset && event.target.dataset.sortkey) {
      var sScope = event.target.dataset.sortkey;
      var sState = sortStateOf(sScope);
      sState.key = event.target.value;
      sState.dir = defaultSortDir(sScope, sState.key);
      rerenderSort(sScope);
      return;
    }

    // 行复选框 / 全选：勾选态变化要重绘，好让「解封选中」按钮与行高亮跟着更新
    var check = event.target.closest('[data-act="select-ip"]');
    if (check) {
      if (check.checked) state.bans.selected[check.dataset.ip] = true;
      else delete state.bans.selected[check.dataset.ip];
      renderBans();
      return;
    }

    if (event.target.id === 'ftTemplate') {
      applyFilterTemplate(event.target.value);
      return;
    }
    if (event.target.id === 'jailScope') {
      state.jailOpts.scope = event.target.value;
      renderJails();
      return;
    }
    if (event.target.id === 'banJail') {
      state.bans.jail = event.target.value;
      state.bans.status = null;
      renderBans();
      loadBanStatus(state.bans.jail).then(function () {
        if (state.view === 'bans') renderBans();
      }).catch(function (err) { toast(localizeErr(err), 'err'); });
      return;
    }
    if (event.target.id === 'logLines') {
      state.logOpts.lines = parseInt(event.target.value, 10) || 400;
      refresh();
      return;
    }
    if (event.target.id === 'prefTheme') {
      applyTheme(event.target.value);
      return;
    }
    if (event.target.id === 'chType') {
      var hint = $('#chHint');
      if (hint) hint.textContent = channelTypeHint(channelTypeMeta(event.target.value));
      // 切换类型时保留同名字段的值，其余字段按新类型重建
      renderChannelFields(event.target.value, draftChannel());
    }
  }

  var searchTimer = null;

  function onInput(event) {
    if (event.target.id === 'jailSearch') {
      clearTimeout(searchTimer);
      var value = event.target.value;
      searchTimer = setTimeout(function () {
        state.jailOpts.search = value;
        var focus = document.activeElement;
        renderJails();
        var box = $('#jailSearch');
        if (box && focus && focus.id === 'jailSearch') { box.focus(); }
      }, 260);
      return;
    }
    if (event.target.id === 'banSearch') {
      // 300ms 防抖：整表重绘，太快会打断连续输入
      clearTimeout(searchTimer);
      var banNeedle = event.target.value;
      searchTimer = setTimeout(function () {
        state.bans.search = banNeedle;
        if (state.view !== 'bans') return;
        renderBans();
        var box = $('#banSearch');
        if (box) {
          box.focus();
          if (box.setSelectionRange) box.setSelectionRange(box.value.length, box.value.length);
        }
      }, 300);
      return;
    }
    if (event.target.id === 'fnkSearch') {
      clearTimeout(searchTimer);
      var fnkNeedle = event.target.value;
      searchTimer = setTimeout(function () {
        state.fnkOpts.search = fnkNeedle;
        state.fnkOpts.page = 1;
        loadFnkBlacklist().then(function () {
          if (state.view !== 'fnknock') return;
          renderFnknock();
          var box = $('#fnkSearch');
          if (box) {
            box.focus();
            box.setSelectionRange(box.value.length, box.value.length);
          }
        });
      }, 320);
      return;
    }
    if (event.target.id === 'filterSearch') {
      state.filterOpts.search = event.target.value.trim().toLowerCase();
      applyFilterSearch();
      return;
    }
    if (event.target.id === 'logKeyword') {
      clearTimeout(searchTimer);
      var text = event.target.value;
      searchTimer = setTimeout(function () {
        state.logOpts.keyword = text;
        loadLogs().then(function () {
          if (state.view !== 'logs') return;
          renderLogs(true);
          // 整页重绘会让输入框失焦，光标也回到开头 —— 重新聚焦并摆到末尾
          var box = $('#logKeyword');
          if (box) {
            box.focus();
            if (box.setSelectionRange) box.setSelectionRange(box.value.length, box.value.length);
          }
        });
      }, 320);
    }
  }

  // 当前页的搜索框（没有搜索框的页面返回 null，此时 / 不做任何事）
  function currentSearchInput() {
    switch (state.view) {
      case 'jails': return $('#jailSearch');
      case 'bans': return $('#banSearch');
      case 'filters': return $('#filterSearch');
      case 'logs': return $('#logKeyword');
      case 'fnknock': return $('#fnkSearch');
      default: return null;
    }
  }

  function onKeydown(event) {
    if (event.key === 'Enter') {
      if (event.target.id === 'logKeyword') {
        event.preventDefault();
        state.logOpts.keyword = event.target.value;
        refresh();
        return;
      }
      // 白名单输入框里回车 = 点「添加」
      if (event.target.id === 'ignoreIpInput') {
        event.preventDefault();
        addIgnoreIpFromInput();
        return;
      }
    }
  }

  /* ------------------------------------------------------------ 启动 */

  function bind() {
    $('#tabs').addEventListener('click', function (event) {
      var btn = event.target.closest('button[data-view]');
      if (btn) setView(btn.dataset.view);
    });

    $('#view').addEventListener('click', onClick);
    $('#drawerFoot').addEventListener('click', onClick);
    $('#drawerBody').addEventListener('click', onClick);
    $('#view').addEventListener('change', onChange);
    $('#view').addEventListener('input', onInput);
    $('#view').addEventListener('keydown', onKeydown);
    $('#drawerBody').addEventListener('click', onToggle);
    $('#view').addEventListener('click', onToggle);
    $('#drawerBody').addEventListener('change', onChange);

    $('#drawerMask').addEventListener('click', closeDrawer);
    $('#drawerClose').addEventListener('click', closeDrawer);

    // 手机端选择条：点「取消」或遮罩关闭，点某一项即选中。
    // 用 mousedown 而不是 click 拦截 select 的默认弹层：click 时系统弹层
    // 可能已经开始展开，拦不住。
    var pickerMask = $('#pickerMask');
    if (pickerMask) {
      pickerMask.addEventListener('click', function (event) {
        if (event.target.closest('#pickerCancel') || event.target === pickerMask) {
          event.preventDefault();
          closePicker();
          return;
        }
        var item = event.target.closest('[data-pick]');
        if (item && !item.disabled) {
          event.preventDefault();
          pickValue(item.dataset.pick);
        }
      });
    }
    document.addEventListener('mousedown', function (event) {
      if (!mobilePickerOn()) return;
      var select = event.target.closest ? event.target.closest('select') : null;
      if (!select || select.disabled) return;
      // 只拦「单选、选项已就绪」的普通下拉；多选与非 select 一律放行
      if (select.multiple) return;
      event.preventDefault();
      openPicker(select);
    }, true);
    document.addEventListener('touchstart', function (event) {
      if (!mobilePickerOn()) return;
      var select = event.target.closest ? event.target.closest('select') : null;
      if (!select || select.disabled || select.multiple) return;
      event.preventDefault();
      openPicker(select);
    }, { capture: true, passive: false });

    $('#menuToggle').addEventListener('click', openSidebar);
    $('#scrim').addEventListener('click', closeSidebar);
    $('#btnCollapse').addEventListener('click', toggleCollapsed);

    document.addEventListener('keydown', function (event) {
      if (event.key === 'Escape') {
        // 选择条盖在页面之上时优先关它，别把下面的抽屉一起关掉
        if (pickerCtx) { closePicker(); return; }
        closeDrawer();
        return;
      }

      // 按 / 聚焦当前页的搜索框（在输入框里打字时不抢焦点）
      if (event.key === '/' && !event.ctrlKey && !event.metaKey && !event.altKey) {
        var tag = (event.target.tagName || '').toLowerCase();
        var typing = tag === 'input' || tag === 'textarea' || tag === 'select' ||
          event.target.isContentEditable;
        if (typing) return;
        if ($('#drawer').classList.contains('open')) return;
        var box = currentSearchInput();
        if (!box) return;
        event.preventDefault();
        box.focus();
        if (box.select) box.select();
      }
    });

    // 后台标签页不轮询；切回前台时若已超期立刻补刷
    document.addEventListener('visibilitychange', onVisibilityChange);

    // 浏览器前进 / 后退与深链：解析 #view 或 #settings/<tab>。
    // 旧 hash（#notifications 等）在这里一次性重定向，不留死链。
    window.addEventListener('hashchange', function () {
      var parsed = parseHash(location.hash);
      if (parsed.legacy) {
        // 用 replace 改写地址栏，不往历史里再塞一条
        try {
          history.replaceState(null, '', '#' + viewHash('settings', parsed.tab));
        } catch (e) { /* 忽略 */ }
      }
      if (!parsed.view) return;
      if (!Object.prototype.hasOwnProperty.call(VIEW_TITLES, parsed.view)) return;
      if (state.view === parsed.view) {
        // 同一个视图内切设置分区：只重绘分区，不重拉整页
        if (parsed.view === 'settings' && parsed.tab && parsed.tab !== state.setTab) {
          state.setTab = parsed.tab;
          renderSettings();
          paintPageTitle();
        }
        return;
      }
      setView(parsed.view, parsed.tab);
    });

    // 标签条用事件委托绑一次，避免每次重绘都要重新绑
    $('#view').addEventListener('click', function (event) {
      var tabBtn = event.target.closest('[data-set-tab]');
      if (tabBtn) { setSettingsTab(tabBtn.dataset.setTab); return; }
      var fnkBtn = event.target.closest('[data-fnk-tab]');
      if (fnkBtn) setFnkTab(fnkBtn.dataset.fnkTab);
    });

    // 升级遮罩：「立即重新加载」与超时后的手动兜底
    var upReloadBtn = $('#upReload');
    if (upReloadBtn) upReloadBtn.addEventListener('click', function () { location.reload(); });
    // 页面开着时每 30 分钟查一次新版本；visibilitychange 变可见且已过期时补查
    updateTimer = setInterval(onUpdateTick, 60000);

    $('#btnRefresh').addEventListener('click', function () { refresh(); });
    $('#btnReload').addEventListener('click', function (event) { doService('reload', event.currentTarget); });
    $('#btnTheme').addEventListener('click', function () {
      var order = ['auto', 'dark', 'light'];
      var next = order[(order.indexOf(currentTheme()) + 1) % order.length];
      applyTheme(next);
      toast(t('common.themeSuffix') + THEME_LABELS[next], 'ok', 1600);
      if (state.prefs) {
        state.prefs.theme = next;
        api('prefs', { method: 'PUT', body: { prefs: { theme: next } } }).catch(function () {});
      }
    });

    // 中英文切换：点一下在 zh / en 之间切
    $('#btnLang').addEventListener('click', function () {
      if (!window.I18N) return;
      var next = I18N.currentLang() === 'zh' ? 'en' : 'zh';
      I18N.setLang(next);
      repaintForLang();
      toast(t('common.langSwitchToast'), 'ok', 1600);
    });
  }

  // 侧栏底部两个图标按钮与版本行前的图标：一次画齐，切语言时不用重画
  // （它们是纯图形，没有随语言变化的文案）。
  function paintSideIcons() {
    var langIcon = $('#langIcon');
    if (langIcon) langIcon.innerHTML = LANG_ICON;
    var verIcon = $('#verIcon');
    if (verIcon) verIcon.innerHTML = GLOBE_ICON;
    applyCollapseButton(readCollapsed());
  }

  // 语言按钮的悬停提示与 <html lang>：切换语言后要刷新
  function paintLangTag() {
    if (!window.I18N) return;
    var lang = I18N.currentLang();
    var langBtn = $('#btnLang');
    if (langBtn) {
      var label = lang === 'zh' ? t('common.langZh') : t('common.langEn');
      langBtn.title = label;
      langBtn.setAttribute('aria-label', label);
    }
    var themeBtn = $('#btnTheme');
    if (themeBtn) {
      themeBtn.title = t('common.themeTip', { theme: THEME_LABELS[currentTheme()] || THEME_LABELS.auto });
      themeBtn.setAttribute('aria-label', themeBtn.title);
    }
    var verLine = $('#verLine');
    if (verLine && !verLine.title) verLine.title = 'Fail2ban';
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
  }

  // 外壳里的静态文案（侧栏导航、顶栏按钮、抽屉标题）写在 index.html 里，
  // 不在 app.js 的字符串里，所以按 data-i18n 标记统一回填。
  function paintStaticI18n() {
    var nodes = document.querySelectorAll('[data-i18n]');
    for (var i = 0; i < nodes.length; i += 1) {
      var node = nodes[i];
      var key = node.getAttribute('data-i18n');
      if (!key) continue;
      var text = t(key);
      if (text && text !== key) node.textContent = text;
    }
  }

  // 切语言后把所有由 JS 渲染的文案重刷一遍：页面标题、侧栏导航、当前视图。
  function repaintForLang() {
    paintLangTag();
    paintStaticI18n();
    paintPageTitle();
    applySidebarPref((state.prefs && state.prefs.sidebar) || 'auto');
    applyTheme(currentTheme());
    render();
  }

  function boot() {
    applyTheme(currentTheme());
    applyDensity('comfortable');
    applySidebarPref('auto');
    paintSideIcons();
    paintLangTag();
    paintStaticI18n();
    // 首屏还原：#jails / #settings/notify / 旧 #notifications 都要能落到正确位置
    var parsed = parseHash(location.hash);
    if (parsed.legacy) {
      try {
        history.replaceState(null, '', '#' + viewHash('settings', parsed.tab));
      } catch (e) { /* 忽略 */ }
    }
    if (parsed.view && Object.prototype.hasOwnProperty.call(VIEW_TITLES, parsed.view)) {
      state.view = parsed.view;
      if (parsed.view === 'settings' && SET_TABS.indexOf(parsed.tab) >= 0) {
        state.setTab = parsed.tab;
      }
      Array.prototype.forEach.call($('#tabs').children, function (btn) {
        btn.classList.toggle('active', btn.dataset.view === state.view);
      });
      paintPageTitle();
    }
    // 面板不再需要登录，直接进主界面；只读会话信息用于版本号回落显示
    api('session').then(function (session) {
      state.session = session;
    }).catch(function () { /* 取不到会话不影响进入 */ })
      .then(enterApp)
      .then(function () {
        // 侧栏「更新」红点：读缓存（不发网络），再节流补一次真实检查
        return api('update').then(function (info) {
          state.update.info = info;
          state.update.lastCheckAt = Date.now();
          paintUpdateDot();
        }).catch(function () { /* 更新接口不可用不影响主流程 */ });
      });
  }

  // 被飞牛桌面以 iframe 嵌入时收紧布局，独立页面打开则保持宽松边距
  if (window.self !== window.top) {
    document.documentElement.classList.add('embedded');
  }

  bind();
  boot();
})();
