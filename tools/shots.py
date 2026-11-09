#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail2ban 面板真机多页面截图工具（零第三方依赖，只用 Python 3 标准库）。

用途
----
把已装在测试机上的面板逐页、逐视口截成 PNG，供改版前后逐页对照验收。

为什么不用 chromium 的 --screenshot 一把梭
----------------------------------------
这台设备上的 headless chromium 有一个坑：--window-size 小于约 500 时，
**布局仍按 500 CSS px 算**，截图只是把 500 宽的图裁到 390 —— 出来的是
「被裁掉的桌面布局」，不是真正的手机布局（--dump-dom 同样如此）。
所以手机端必须走 CDP 的 Emulation.setDeviceMetricsOverride 设真实视口，
再 Page.captureScreenshot 截图。本工具两个视口都走同一条 CDP 路径。

用法
----
    python tools/shots.py                          # 桌面 + 手机，截全部页面
    python tools/shots.py --viewport mobile        # 只截手机
    python tools/shots.py --only dashboard,jails   # 只截指定页
    python tools/shots.py --out image/shots-261117 # 指定输出目录
    python tools/shots.py --repeat 2               # 连跑两遍，比对稳定性
    python tools/shots.py --theme dark --lang en   # 换主题 / 语言
    python tools/shots.py --fnk-tabs               # 连 fnknock 页内分区一起截

产物
----
    <out>/desktop-<page>.png / <out>/mobile-<page>.png
    <out>/_meta/shots.json     每张图的尺寸、页面指标、控制台报错
    <out>/INDEX.md             逐图一行：文件 + 肉眼可见的关键元素

工作方式
--------
本脚本在 Windows 本机跑：通过 ssh 把「自己」传到测试机的 /tmp 下，
以 --on-device 再跑一次，由设备侧驱动 chromium（--remote-debugging-pipe），
截完用 tar 流把 PNG 取回本机，最后在本机做校验并写 INDEX.md。
设备侧只读面板页面，不往 /var/apps 里写任何东西。
"""

import argparse
import base64
import hashlib
import io
import json
import os
import select
import shutil
import struct
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

MARKER = '@@F2B_SHOTS@@'
BT = chr(96)  # 反引号；INDEX.md 里包文件名用
DEFAULT_HOST = 'fnos-test'
DEFAULT_URL = 'http://127.0.0.1:18752/'
DEFAULT_CHROME = '/usr/bin/chromium'
REMOTE_DIR = '/tmp/f2b-shots'

# 视口定义：dsf 是截图倍率（手机用 2 倍，字口更清楚），mobile 决定是否启用移动模拟
VIEWPORTS = {
    'desktop': {'width': 1440, 'height': 900, 'dsf': 1, 'mobile': False},
    'mobile': {'width': 390, 'height': 844, 'dsf': 2, 'mobile': True},
}

# 页面清单（顺序即验收顺序）。kind 决定怎么进这一页：
#   view     直接换 hash
#   fnknock  换 hash，可选再点页内分区标签
#   settings 按发现的设置分区逐个换 hash
PAGES = [
    {'id': 'dashboard', 'kind': 'view'},
    {'id': 'jails', 'kind': 'view'},
    {'id': 'bans', 'kind': 'view'},
    {'id': 'filters', 'kind': 'view'},
    {'id': 'fnknock', 'kind': 'fnknock'},
    {'id': 'settings', 'kind': 'settings'},
    {'id': 'logs', 'kind': 'view'},
]

# 页面就绪判定：首屏占位已撤 + 视图区有内容
READY_JS = r"""(function(){
  var s=document.getElementById('bootSplash');
  var v=document.getElementById('view');
  if(!s||!s.hidden) return {r:0,why:'splash'};
  if(!v) return {r:0,why:'noview'};
  var t=(v.innerText||'').trim();
  if(t.length<10) return {r:0,why:'view-empty'};
  if(document.readyState!=='complete') return {r:0,why:'doc-'+document.readyState};
  return {r:1,len:t.length};
})()"""

READY_NO_LOADING_JS = r"""(function(){
  var v=document.getElementById('view');
  var t=v?(v.innerText||''):'';
  if(t.indexOf('\u52a0\u8f7d\u4e2d\u2026')>=0) return 0;
  if(t.indexOf('\u6b63\u5728\u8fde\u63a5\u9762\u677f')>=0) return 0;
  return 1;
})()"""

# 页面指标：这张图里「肉眼能数出来的东西」，写进 INDEX.md 与 _meta
METRICS_JS = r"""(function(){
  var out={};
  var v=document.getElementById('view');
  function q(sel){return document.querySelectorAll(sel).length;}
  var scrollable=function(node){
    var n=node;
    while(n && n.nodeType===1){
      var cs=window.getComputedStyle(n);
      var ox=cs.overflowX;
      if((ox==='auto'||ox==='scroll'||ox==='hidden') && n!==document.body && n!==document.documentElement) return true;
      n=n.parentElement;
    }
    return false;
  };
  out.pageTitle=((document.getElementById('pageTitle')||{}).innerText||'').trim();
  out.docTitle=document.title||'';
  var txt=v?((v.innerText||'').trim()):'';
  out.textLen=txt.length;
  out.textHead=txt.slice(0,300);
  out.cards=q('#view .card');
  out.stats=q('#view .stat');
  out.tables=q('#view table');
  out.tableRows=q('#view tbody tr');
  out.buttons=q('#view button');
  out.inputs=q('#view input, #view select, #view textarea');
  out.badges=q('#view .badge');
  out.empty=q('#view .empty');
  out.heads=[].slice.call(v?v.querySelectorAll('h2,h3,.card-head h3'):[]).map(function(n){
    return (n.innerText||'').trim().split('\n')[0];
  }).filter(function(s){return s;}).slice(0,10);
  out.navCount=q('#tabs button[data-view]');
  out.navItems=[].slice.call(document.querySelectorAll('#tabs button[data-view]')).map(function(b){
    return (b.innerText||'').trim();
  });
  out.setTabs=[].slice.call(document.querySelectorAll('#setWorkbar [data-set-tab]')).map(function(b){
    return b.dataset.setTab;
  });
  out.setTabOn=((document.querySelector('#setWorkbar button.on')||{}).innerText||'').trim();
  out.fnkTabs=[].slice.call(document.querySelectorAll('[data-fnk-tab]')).map(function(b){
    return b.dataset.fnkTab;
  });
  out.splashHidden=!!((document.getElementById('bootSplash')||{}).hidden);
  out.loadingLeft=(txt.indexOf('\u52a0\u8f7d\u4e2d\u2026')>=0)||(txt.indexOf('\u6b63\u5728\u8fde\u63a5')>=0);
  out.media={
    iw:window.innerWidth, ih:window.innerHeight, dpr:window.devicePixelRatio||1,
    hoverNone:window.matchMedia('(hover: none)').matches,
    pointerCoarse:window.matchMedia('(pointer: coarse)').matches
  };
  var de=document.documentElement;
  out.scrollW=de.scrollWidth; out.clientW=de.clientWidth; out.scrollH=de.scrollHeight;
  out.overflowX=de.scrollWidth>de.clientWidth+1;
  var over=[];
  if(out.overflowX){
    var nodes=document.querySelectorAll('#view *, .topbar *');
    var lim=de.clientWidth;
    for(var i=0;i<nodes.length && over.length<10;i++){
      var n=nodes[i];
      if(!n.getBoundingClientRect||scrollable(n)) continue;
      var r=n.getBoundingClientRect();
      if(r.width>0 && r.right>lim+1.5){
        over.push({tag:n.tagName.toLowerCase(),cls:String(n.className||'').slice(0,48),right:Math.round(r.right)});
      }
    }
  }
  out.overflowNodes=over;
  return out;
})()"""


# ------------------------------------------------------------- 升级遮罩两态 ---
# 一键升级的遮罩无法靠普通访问触发：要点「一键更新」，而那个按钮只在
# has_update 时出现。这里在页面里先替换 window.fetch 把两个接口伪造掉，
# 让遮罩按真实代码路径渲染出来 —— 不碰设备上的任何文件，也不会真的安装。
#
# 安全前提：这个脚本在**页面**的作用域里替换 fetch，
#   /api/update              -> 返回假的 has_update（让「一键更新」出现）
#   /api/update/install      -> 立刻返回 {ok:true, phase:'downloading'}（绝不触达设备）
#   /api/update/progress     -> 按脚本给的帧序列返回，用来停在指定阶段
#   /api/meta                -> 版本号固定成"升级前"那个，避免轮询判定装完而收尾
# 任何一条不匹配的请求都走原始 fetch，所以其余接口仍是真实数据。
FAKE_UPDATE_JS = r"""(function(){
  if (window.__f2bFaked) return;
  window.__f2bFaked = true;
  var orig = window.fetch;
  window.__f2bFrames = window.__f2bFrames || [];
  window.__f2bFrameIndex = 0;
  window.__f2bInstallHits = 0;
  var cur = '%(current)s';
  function json(obj){
    return Promise.resolve(new Response(JSON.stringify(obj), {
      status: 200, headers: {'Content-Type':'application/json'}
    }));
  }
  window.fetch = function(input, init){
    var url = String((input && input.url) || input || '');
    var path = url.split('?')[0];
    if (/\/api\/update$/.test(path)) {
      return json({ok:true, current:cur, latest:'26.11.19', has_update:true,
        repo:'Smi13zZ/fail2ban', notes:'（截图用假数据）', assets:[
          {name:'fn-fail2ban-26.11.19-all.fpk', size:366074}],
        checked_at: Math.floor(Date.now()/1000), published_at:'2026-11-09'});
    }
    if (/\/api\/update\/progress$/.test(path)) {
      var frames = window.__f2bFrames || [];
      var idx = Math.min(window.__f2bFrameIndex, Math.max(0, frames.length - 1));
      window.__f2bFrameIndex += 1;
      return json(frames.length ? frames[idx] : {phase:'preparing', done:0, total:0});
    }
    if (/\/api\/update\/install$/.test(path)) {
      window.__f2bInstallHits += 1;
      return json({ok:true, phase:'downloading', done:0, total:366074});
    }
    if (/\/api\/meta$/.test(path)) {
      return orig.apply(this, arguments).then(function(res){
        return res.clone().json().then(function(data){
          if (data && data.app) data.app.version = cur;
          return new Response(JSON.stringify(data), {
            status:200, headers:{'Content-Type':'application/json'}});
        });
      });
    }
    return orig.apply(this, arguments);
  };
})()"""

# 两态各自的进度帧：下载中有字节数、其余阶段没有可测总量
OVERLAY_STATES = [
    {
        'id': 'update-overlay-download',
        'note': '升级遮罩 · 下载中（有真实字节数）',
        'frames': [
            {'phase': 'downloading', 'done': 146841, 'total': 366074, 'percent': 40},
            {'phase': 'downloading', 'done': 146841, 'total': 366074, 'percent': 40},
        ],
        'settle': 1.6,
    },
    {
        # 无总量态：安装/重启阶段后端不给字节数。注意不能先喂一个 100% 的下载帧 —
        # 那样进度条会按「只增不减」保住满格（那是另一条设计分支，见下面的 third 态）。
        'id': 'update-overlay-preparing',
        'note': '升级遮罩 · 无总量态（准备中…）',
        'frames': [
            {'phase': 'installing', 'done': 0, 'total': 0},
            {'phase': 'installing', 'done': 0, 'total': 0},
            {'phase': 'installing', 'done': 0, 'total': 0},
        ],
        'settle': 2.8,
    },
    {
        # 下载到 100% 后切安装：进度条按「只增不减」保满格，百分比保留、字节数清空。
        'id': 'update-overlay-installing',
        'note': '升级遮罩 · 下载满格后切安装（保留 100%）',
        'frames': [
            {'phase': 'downloading', 'done': 366074, 'total': 366074, 'percent': 100},
            {'phase': 'installing', 'done': 0, 'total': 0},
            {'phase': 'installing', 'done': 0, 'total': 0},
        ],
        'settle': 2.8,
    },
]

# ------------------------------------------------------- 说明页专项（可核对）---
# 说明页是纯静态的三张路径卡，最容易出现「某行值空了」这种只有肉眼能发现的毛病，
# 所以单独把每张卡的每一行读出来，交给 INDEX.md 与 _meta 做逐条核对。
GUIDE_JS = r"""(function(){
  var cards = [].slice.call(document.querySelectorAll('#view .card')).map(function(card){
    var h = card.querySelector('.card-head h3') || card.querySelector('h3');
    var rows = [].slice.call(card.querySelectorAll('.pathrow')).map(function(row){
      var k = row.querySelector('.pathrow-k');
      var v = row.querySelector('.pathrow-v');
      return {
        k: k ? (k.innerText||'').trim() : '',
        v: v ? (v.innerText||'').trim() : '',
        mono: !!(v && v.querySelector('.mono'))
      };
    });
    return {title: h ? (h.innerText||'').trim() : '', rows: rows,
            textLen: (card.innerText||'').trim().length,
            emptyBlocks: [].slice.call(card.querySelectorAll('.empty')).length};
  });
  var empty = [];
  cards.forEach(function(c){
    c.rows.forEach(function(r){
      if (!r.v || r.v === '—' || r.v === '-' || r.v === 'undefined') {
        empty.push(c.title + ' / ' + r.k);
      }
    });
  });
  return {cards: cards, emptyRows: empty,
          cardCount: cards.length,
          rowTotal: cards.reduce(function(n,c){return n + c.rows.length;}, 0)};
})()"""

VARROW_JS = r"""(function(){
  var row = document.querySelector('#view .vhero');
  var arrow = row ? row.querySelector('.varrow') : null;
  var svg = arrow ? arrow.querySelector('svg') : null;
  var cols = row ? [].slice.call(row.querySelectorAll('.vcol')) : [];
  var box = function(n){
    if(!n) return null;
    var r = n.getBoundingClientRect();
    var cs = window.getComputedStyle(n);
    return {x:Math.round(r.x), y:Math.round(r.y),
            w:Math.round(r.width), h:Math.round(r.height),
            display:cs.display, visibility:cs.visibility, opacity:cs.opacity};
  };
  var a = box(arrow), s2 = box(svg);
  var between = null;
  if (a && cols.length >= 2) {
    var r0 = cols[0].getBoundingClientRect(), r1 = cols[1].getBoundingClientRect();
    between = a.x >= Math.floor(r0.right) - 2 && (a.x + a.w) <= Math.ceil(r1.left) + 2;
  }
  return {present: !!arrow, arrow: a, svg: s2, betweenCols: between,
          colCount: cols.length,
          versions: cols.map(function(c){
            var v = c.querySelector('.vv');
            return v ? (v.innerText||'').trim() : '';
          })};
})()"""

# --------------------------------------------------- 本版重点核对（--expect）---
# 这些是「这一轮改了什么」的硬断言，跑完直接告诉你要看的那几处对不对。
# 用 --expect 打开；数值按当前版本的设计值写死，改版后如果设计变了就要一起改。
EXPECT_DEFAULT = {
    'guideCards': 3,
    'guideRows': 20,
    'guideRequiredKeys': ['封禁动作文件', '过滤器文件', '联动 Jail 日志',
                          '封禁动作文件', 'action.d'],
    'navCount': 7,
    'navOrder': ['概览', 'Jail 管理', '封禁管理', '过滤器', 'fn-knock 联动', '日志', '设置'],
}

# 封禁管理：顶栏下面应该直接就是 Jail 选择器，中间不该留空行（曾经那里有整整一行
# 被占掉，手机上把选择器顶下一大截）。
BANS_TOPBAR_JS = r"""(function(){
  var v = document.getElementById('view');
  if (!v) return null;
  var topbar = document.querySelector('.topbar');
  var sel = v.querySelector('#banJail') || v.querySelector('select');
  if (!sel) return {found:false};
  var rect = function(n){ return n ? n.getBoundingClientRect() : null; };
  var rb = rect(topbar);
  var rs = rect(sel);
  // 顶栏底边 -> 选择器上沿之间，实际夹着哪些块。以前这里有一整行被白占掉，
  // 那一行的 .workbar / .view-head 会有非零高度，就是我们要抓的东西。
  var main = document.querySelector('main') || v;
  var between = [];
  var nodes = [].slice.call(main.children);
  for (var i = 0; i < nodes.length; i++) {
    var n = nodes[i];
    var r = n.getBoundingClientRect();
    if (r.bottom <= rs.top + 1 && r.top >= (rb ? rb.bottom : 0) - 1) {
      between.push({tag: n.tagName.toLowerCase(),
                    cls: String(n.className || '').slice(0, 40),
                    h: Math.round(r.height)});
    }
  }
  var pad = 0;
  if (rb) {
    var cs = window.getComputedStyle(main);
    pad = parseFloat(cs.paddingTop) || 0;
  }
  return {
    found: true,
    gap: rb ? Math.round(rs.top - rb.bottom) : null,
    mainPaddingTop: Math.round(pad),
    selectRect: {x: Math.round(rs.x), y: Math.round(rs.y),
                 w: Math.round(rs.width), h: Math.round(rs.height)},
    between: between,
    // 真正的判据：顶栏与选择器之间除了 main 的内边距，不该有别的块
    extraBlocks: between.length
  };
})()"""

OVERLAY_JS = r"""(function(){
  var btn = document.querySelector('[data-act="update-install"]');
  if (!btn) return {clicked:false, why:'no-install-button'};
  btn.click();
  return {clicked:true};
})()"""

OVERLAY_METRICS_JS = r"""(function(){
  var ov = document.getElementById('upOverlay');
  var track = document.getElementById('upProgTrack');
  var ready = document.getElementById('upProgReady');
  var fill = document.getElementById('upProgFill');
  var pct = document.getElementById('upProgPct');
  var bytes = document.getElementById('upProgBytes');
  var keep = document.getElementById('upKeep');
  var stage = document.getElementById('upStage');
  var spin = document.getElementById('upSpin');
  var rect = function(n){
    if(!n) return null;
    var r=n.getBoundingClientRect();
    return {w:Math.round(r.width), h:Math.round(r.height), x:Math.round(r.x), y:Math.round(r.y)};
  };
  var visible = function(n){
    if(!n || n.hidden) return false;
    var cs=window.getComputedStyle(n);
    if(cs.display==='none'||cs.visibility==='hidden') return false;
    var r=n.getBoundingClientRect();
    return r.width>0 && r.height>0;
  };
  return {
    overlayVisible: visible(ov),
    overlayRect: rect(ov),
    stageText: ((stage||{}).innerText||'').trim(),
    trackVisible: visible(track),
    trackRect: rect(track),
    readyVisible: visible(ready),
    readyText: ((ready||{}).innerText||'').trim(),
    fillWidthPct: fill?String(fill.style.width||''):'',
    pctText: ((pct||{}).innerText||'').trim(),
    bytesText: ((bytes||{}).innerText||'').trim(),
    keepVisible: visible(keep),
    keepText: ((keep||{}).innerText||'').trim(),
    spinRect: rect(spin),
    installHits: window.__f2bInstallHits || 0,
    boxRect: rect(ov ? ov.querySelector('.upoverlay-box') : null)
  };
})()"""


def log(msg):
    sys.stderr.write(str(msg) + '\n')
    sys.stderr.flush()


# --------------------------------------------------------------- CDP 管道 ---
# chromium --remote-debugging-pipe 走文件描述符 3/4，每条消息以 \0 分隔，
# 省掉 WebSocket 握手与分帧，纯标准库够用。

class CdpPipe(object):
    def __init__(self, rfd, wfd):
        self.rfd = rfd
        self.wfd = wfd
        self.buf = b''
        self.next_id = 0
        self.events = []
        self.event_hook = None   # 事件到达时同步回调（用于自动关掉 confirm 之类的原生弹窗）

    def send(self, method, params=None, session=None):
        self.next_id += 1
        msg = {'id': self.next_id, 'method': method, 'params': params or {}}
        if session:
            msg['sessionId'] = session
        data = json.dumps(msg).encode('utf-8')
        while data:
            written = os.write(self.wfd, data)
            data = data[written:]
        os.write(self.wfd, b'\0')
        return self.next_id

    def _read(self, timeout):
        deadline = time.time() + timeout
        while b'\0' not in self.buf:
            remain = deadline - time.time()
            if remain <= 0:
                raise RuntimeError('CDP read timeout')
            ready, _, _ = select.select([self.rfd], [], [], remain)
            if not ready:
                raise RuntimeError('CDP read timeout')
            chunk = os.read(self.rfd, 1 << 20)
            if not chunk:
                raise RuntimeError('CDP pipe closed')
            self.buf += chunk
        raw, self.buf = self.buf.split(b'\0', 1)
        return json.loads(raw.decode('utf-8'))

    def call(self, method, params=None, session=None, timeout=30):
        mid = self.send(method, params, session)
        deadline = time.time() + timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise RuntimeError('CDP timeout: ' + method)
            msg = self._read(remain)
            if msg.get('id') is None:
                self.events.append(msg)
                if self.event_hook:
                    self.event_hook(msg)
                continue
            if msg['id'] != mid:
                continue
            if 'error' in msg:
                raise RuntimeError('%s -> %s' % (method, json.dumps(msg['error'], ensure_ascii=False)))
            return msg.get('result', {})

    def pump(self, seconds):
        """把这段时间里到达的事件收进 self.events，不做别的事。"""
        deadline = time.time() + seconds
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                return
            try:
                msg = self._read(remain)
            except RuntimeError:
                return
            if msg.get('id') is None:
                self.events.append(msg)
                if self.event_hook:
                    self.event_hook(msg)

    def drain_events(self):
        out = self.events
        self.events = []
        return out


class Chrome(object):
    def __init__(self, binary, workdir, extra_args=None):
        self.profile = os.path.join(workdir, 'chrome-profile')
        self.logfile = os.path.join(workdir, 'chrome.log')
        args = [
            binary,
            '--headless=new',
            '--no-sandbox',
            '--disable-gpu',
            '--disable-dev-shm-usage',
            '--remote-debugging-pipe',
            '--user-data-dir=' + self.profile,
            '--no-first-run',
            '--no-default-browser-check',
            '--disable-extensions',
            '--disable-background-networking',
            '--disable-background-timer-throttling',
            '--disable-backgrounding-occluded-windows',
            '--disable-renderer-backgrounding',
            '--hide-scrollbars',
            '--force-color-profile=srgb',
            '--font-render-hinting=none',
            'about:blank',
        ] + list(extra_args or [])
        r_in, w_in = os.pipe()   # 浏览器读 fd3
        r_out, w_out = os.pipe()  # 浏览器写 fd4

        def preexec():
            os.dup2(r_in, 3)
            os.dup2(w_out, 4)

        self._log = open(self.logfile, 'wb')
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=self._log,
            stderr=self._log,
            close_fds=True,
            pass_fds=(r_in, w_in, r_out, w_out),
            preexec_fn=preexec,
        )
        os.close(r_in)
        os.close(w_out)
        self.cdp = CdpPipe(r_out, w_in)
        version = self.cdp.call('Browser.getVersion', {})
        self.version = version.get('product', '')
        self.dialogSeen = False
        targets = self.cdp.call('Target.getTargets', {}).get('targetInfos', [])
        page = [t for t in targets if t.get('type') == 'page']
        if not page:
            raise RuntimeError('chromium 没有 page target')
        attached = self.cdp.call('Target.attachToTarget',
                                 {'targetId': page[0]['targetId'], 'flatten': True})
        self.session = attached['sessionId']
        self.cdp.event_hook = self._on_event
        for domain in ('Page.enable', 'Runtime.enable', 'Log.enable'):
            self.cdp.call(domain, {}, self.session)

    def _on_event(self, msg):
        """原生 confirm()/alert() 会阻塞渲染进程，CDP 也就跟着一起卡住。
        截图场景一律「确定」，否则点「一键更新」会永远停在对话框上。"""
        if msg.get('method') == 'Page.javascriptDialogOpening':
            try:
                self.cdp.send('Page.handleJavaScriptDialog', {'accept': True}, self.session)
                self.dialogSeen = True
            except Exception:
                pass

    def close(self):
        try:
            self.cdp.call('Browser.close', {}, timeout=5)
        except Exception:
            pass
        try:
            self.proc.wait(timeout=10)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        try:
            self._log.close()
        except Exception:
            pass

    # -- 便捷封装 ---------------------------------------------------------
    def eval(self, expression, timeout=20):
        res = self.cdp.call('Runtime.evaluate',
                            {'expression': expression, 'returnByValue': True,
                             'awaitPromise': True},
                            self.session, timeout=timeout)
        if res.get('exceptionDetails'):
            raise RuntimeError('JS 异常: ' + json.dumps(res['exceptionDetails'], ensure_ascii=False)[:300])
        return res.get('result', {}).get('value')

    def navigate(self, url, timeout=40):
        self.cdp.call('Page.navigate', {'url': url}, self.session, timeout=timeout)

    def set_viewport(self, width, height, dsf, mobile):
        self.cdp.call('Emulation.setDeviceMetricsOverride', {
            'width': width, 'height': height,
            'deviceScaleFactor': dsf, 'mobile': bool(mobile),
        }, self.session)

    def set_theme(self, theme):
        if theme not in ('light', 'dark'):
            return
        self.cdp.call('Emulation.setEmulatedMedia', {
            'features': [{'name': 'prefers-color-scheme', 'value': theme}]
        }, self.session)

    def seed_script(self, script):
        if script:
            self.cdp.call('Page.addScriptToEvaluateOnNewDocument',
                          {'source': script}, self.session)


def wait_until(fn, timeout, interval=0.15):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = fn()
        except RuntimeError:
            raise
        if last:
            return True, last
        time.sleep(interval)
    return False, last


def wait_ready(chrome, timeout):
    """等首屏占位撤掉 + 视图区有内容。返回 (ok, why, waited_s)。"""
    started = time.time()

    def probe():
        state = chrome.eval(READY_JS)
        if isinstance(state, dict) and state.get('r') == 1:
            return state
        return None

    ok, state = wait_until(probe, timeout)
    waited = time.time() - started
    if not ok:
        state = chrome.eval(READY_JS) or {}
        return False, str(state.get('why', 'timeout')), waited

    # 内容稳定：连续两次采样 innerText 长度一致，避免截到「重绘一半」
    stable, last = 0, None
    for _ in range(30):
        value = chrome.eval('(function(){var v=document.getElementById("view");return v?(v.innerText||"").length:0;})()')
        if last is not None and value == last:
            stable += 1
            if stable >= 2:
                break
        else:
            stable = 0
        last = value
        time.sleep(0.18)

    # 视口内不再有 loading 占位（有就再等一会，等不到也不判失败，只记录）
    loading_gone, _ = wait_until(lambda: chrome.eval(READY_NO_LOADING_JS) == 1, 2.5, interval=0.2)
    waited = time.time() - started
    return True, ('' if loading_gone else 'loading-left'), waited


def png_size(data):
    if len(data) < 24 or data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('不是 PNG')
    return struct.unpack('>II', data[16:24])


def capture(chrome, path, viewport, max_height):
    """按视口宽 + 内容高截整页（超高按 max_height 截断并标记）。"""
    metrics = chrome.cdp.call('Page.getLayoutMetrics', {}, chrome.session)
    size = metrics.get('cssContentSize') or metrics.get('contentSize') or {}
    css_w = int(size.get('width') or viewport['width'])
    css_h = int(size.get('height') or viewport['height'])
    clip_h = min(max(css_h, viewport['height']), max_height)
    shot = chrome.cdp.call('Page.captureScreenshot', {
        'format': 'png',
        'captureBeyondViewport': True,
        'clip': {'x': 0, 'y': 0,
                 'width': max(viewport['width'], min(css_w, viewport['width'])),
                 'height': clip_h,
                 'scale': 1},
    }, chrome.session, timeout=60)
    data = base64.b64decode(shot['data'])
    with open(path, 'wb') as fh:
        fh.write(data)
    return {'cssW': viewport['width'], 'cssH': clip_h,
            'contentH': css_h, 'truncated': css_h > clip_h,
            'pngW': png_size(data)[0], 'pngH': png_size(data)[1]}


# ------------------------------------------------------------- 设备侧主流程 ---

def discover(chrome, url, timeout):
    """从 DOM 里发现导航项与各页分区标签，避免把清单写死在新旧版本上。"""
    found = {'views': [], 'setTabs': [], 'fnkTabs': []}

    def walk(item):
        target = url + '?discover=' + str(int(time.time() * 1000)) + '#' + item['hash']
        chrome.navigate(target)
        ok, why, _ = wait_ready(chrome, timeout)
        if not ok:
            log('  ! 发现阶段 %s 未就绪: %s' % (item['hash'], why))
        return ok

    walk({'hash': 'dashboard'})
    metrics = chrome.eval(METRICS_JS) or {}
    found['views'] = metrics.get('navItems') or []

    if walk({'hash': 'settings'}):
        metrics = chrome.eval(METRICS_JS) or {}
        found['setTabs'] = metrics.get('setTabs') or []

    if walk({'hash': 'fnknock'}):
        metrics = chrome.eval(METRICS_JS) or {}
        found['fnkTabs'] = metrics.get('fnkTabs') or []

    return found


def build_shots(found, opts):
    shots = []
    known = set()
    for page in PAGES:
        pid = page['id']
        known.add(pid)
        if page['kind'] == 'settings':
            tabs = found.get('setTabs') or []
            if not tabs:
                shots.append({'id': pid, 'hash': '#settings', 'kind': 'view'})
            for tab in tabs:
                shots.append({'id': pid + '-' + tab, 'hash': '#settings/' + tab,
                              'kind': 'settings-tab', 'tab': tab})
        elif page['kind'] == 'fnknock':
            shots.append({'id': pid, 'hash': '#fnknock', 'kind': 'view'})
            if opts.get('fnkTabs'):
                for tab in (found.get('fnkTabs') or []):
                    shots.append({'id': pid + '-' + tab, 'hash': '#fnknock',
                                  'kind': 'fnk-tab', 'tab': tab})
        else:
            shots.append({'id': pid, 'hash': '#' + pid, 'kind': 'view'})
    if opts.get('only'):
        wanted = set(opts['only'])
        shots = [s for s in shots if s['id'] in wanted or s['id'].split('-')[0] in wanted]
    return shots


def device_main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('--on-device', action='store_true')
    ap.add_argument('--plan', required=True)
    args = ap.parse_args(argv)
    plan = json.loads(Path(args.plan).read_text(encoding='utf-8'))

    workdir = plan['remoteDir']
    if os.path.isdir(workdir):
        shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(workdir)
    shots_dir = os.path.join(workdir, 'shots')
    os.makedirs(shots_dir)

    url = plan['url']
    timeout = plan.get('timeout', 30)
    max_height = plan.get('maxHeight', 5000)
    results = []

    chrome = Chrome(plan.get('chrome', DEFAULT_CHROME), workdir)
    try:
        seed = []
        lang = plan.get('lang') or 'zh'
        seed.append('try{localStorage.setItem("f2b-lang",%s);}catch(e){}'
                    % json.dumps(lang))
        if plan.get('theme') in ('light', 'dark'):
            chrome.set_theme(plan['theme'])
        chrome.seed_script('(function(){%s})();' % ';'.join(seed))

        # 遮罩两态需要在页面里伪造 update 接口；其余截图不受影响
        if plan.get('extras'):
            meta_now = {}
            try:
                import urllib.request
                with urllib.request.urlopen(url + 'api/meta', timeout=10) as resp:
                    meta_now = json.loads(resp.read().decode('utf-8'))
            except Exception:
                meta_now = {}
            cur_ver = (meta_now.get('app') or {}).get('version', '')
            chrome.seed_script(FAKE_UPDATE_JS % {'current': cur_ver})

        log('[1/3] 发现页面结构 ...')
        found = discover(chrome, url, timeout)
        log('      导航 %d 项 / 设置分区 %d 个 / fnknock 分区 %d 个'
            % (len(found['views']), len(found['setTabs']), len(found['fnkTabs'])))

        shots = build_shots(found, plan)
        log('[2/3] 共 %d 页 x %d 视口 = %d 张'
            % (len(shots), len(plan['viewports']), len(shots) * len(plan['viewports'])))

        seq = 0
        for vp_name in plan['viewports']:
            vp = VIEWPORTS[vp_name]
            chrome.set_viewport(vp['width'], vp['height'], vp['dsf'], vp['mobile'])
            for shot in shots:
                seq += 1
                tag = '%s-%s' % (vp_name, shot['id'])
                out_path = os.path.join(shots_dir, tag + '.png')
                entry = {'file': tag + '.png', 'viewport': vp_name,
                         'id': shot['id'], 'kind': shot['kind'],
                         'tab': shot.get('tab', ''), 'hash': shot['hash'],
                         'cssW': vp['width'], 'cssH': vp['height'],
                         'dsf': vp['dsf'], 'mobile': vp['mobile']}
                started = time.time()
                try:
                    chrome.cdp.drain_events()
                    nav_url = '%s?shot=%d%s' % (url, seq, shot['hash'])
                    chrome.navigate(nav_url)
                    ok, why, waited = wait_ready(chrome, timeout)
                    entry['ready'] = ok
                    entry['readyWhy'] = why
                    entry['waitedMs'] = int(waited * 1000)
                    if ok and shot['kind'] == 'fnk-tab':
                        clicked = chrome.eval(
                            '(function(){var b=document.querySelector("[data-fnk-tab=\\"%s\\"]");'
                            'if(!b) return false; b.click(); return true;})()' % shot['tab'])
                        entry['clicked'] = bool(clicked)
                        if clicked:
                            time.sleep(0.5)
                            ok2, why2, _ = wait_ready(chrome, timeout)
                            entry['ready'] = entry['ready'] and ok2
                    chrome.cdp.pump(0.35)
                    metrics = chrome.eval(METRICS_JS) or {}
                    entry['metrics'] = metrics
                    # 两处专项：说明页逐行核对；更新页量版本对比箭头
                    if shot.get('tab') == 'guide':
                        entry['guide'] = chrome.eval(GUIDE_JS) or {}
                    if shot.get('tab') == 'update':
                        entry['varrow'] = chrome.eval(VARROW_JS) or {}
                    if shot.get('id') == 'bans':
                        entry['bansHead'] = chrome.eval(BANS_TOPBAR_JS) or {}
                    entry.update(capture(chrome, out_path, vp, max_height))
                    entry['bytes'] = os.path.getsize(out_path)
                    entry['ok'] = bool(entry['ready'])
                    events = chrome.cdp.drain_events()
                    errs = []
                    for ev in events:
                        method = ev.get('method')
                        params = ev.get('params') or {}
                        if method == 'Runtime.exceptionThrown':
                            detail = params.get('exceptionDetails') or {}
                            errs.append('exception: ' + str(detail.get('text', ''))[:200])
                        elif method == 'Runtime.consoleAPICalled' and params.get('type') == 'error':
                            args_ = params.get('args') or []
                            text = ' '.join(str(a.get('value', a.get('description', ''))) for a in args_)
                            errs.append('console.error: ' + text[:200])
                        elif method == 'Log.entryAdded':
                            e = params.get('entry') or {}
                            if e.get('level') in ('error', 'warning'):
                                errs.append('%s: %s' % (e.get('level'), str(e.get('text', ''))[:200]))
                    entry['console'] = errs[:12]
                except Exception as exc:  # 单页失败不拖垮整批
                    entry['ok'] = False
                    entry['error'] = str(exc)[:300]
                    entry.setdefault('metrics', {})
                    log('      x %s: %s' % (tag, entry['error']))
                entry['ms'] = int((time.time() - started) * 1000)
                results.append(entry)
                if entry.get('ok'):
                    log('      %-34s %4dpx 高 %5dms' % (tag, entry.get('pngH', 0), entry['ms']))

        # ---- 升级遮罩两态（可选）：伪造 update 接口后在页面里点「一键更新」----
        # 遮罩只能由点击触发，所以先让页面里的 fetch 返回假的 has_update，
        # 再点按钮，然后按脚本给出的进度帧停在指定阶段截图。
        if plan.get('extras'):
            log('      升级遮罩两态 ...')
            for state in OVERLAY_STATES:
                for vp_name in plan['viewports']:
                    seq += 1
                    vp = VIEWPORTS[vp_name]
                    tag = '%s-%s' % (vp_name, state['id'])
                    out_path = os.path.join(shots_dir, tag + '.png')
                    entry = {'file': tag + '.png', 'viewport': vp_name,
                             'id': state['id'], 'kind': 'overlay',
                             'note': state.get('note', ''), 'tab': '',
                             'hash': '#settings/update',
                             'cssW': vp['width'], 'cssH': vp['height'],
                             'dsf': vp['dsf'], 'mobile': vp['mobile']}
                    started = time.time()
                    chrome.set_viewport(vp['width'], vp['height'], vp['dsf'], vp['mobile'])
                    try:
                        chrome.cdp.drain_events()
                        chrome.navigate('%s?shot=%d#settings/update' % (url, seq))
                        ok, why, _ = wait_ready(chrome, timeout)
                        frames = json.dumps(state['frames'])
                        chrome.eval('window.__f2bFrames=%s;window.__f2bFrameIndex=0;' % frames)
                        clicked = chrome.eval(OVERLAY_JS) or {}
                        entry['clicked'] = bool(clicked.get('clicked'))
                        entry['ready'] = ok and entry['clicked']
                        entry['readyWhy'] = ('' if entry['ready'] else
                                             (why or clicked.get('why') or 'click-failed'))
                        if entry['ready']:
                            time.sleep(state.get('settle', 1.5))
                            om = chrome.eval(OVERLAY_METRICS_JS) or {}
                            entry['overlayMetrics'] = om
                            entry['ready'] = bool(om.get('overlayVisible'))
                            entry['readyWhy'] = '' if entry['ready'] else 'overlay-not-visible'
                            entry['metrics'] = {
                                'pageTitle': '升级遮罩',
                                'textLen': len((om.get('stageText') or '') +
                                               (om.get('bytesText') or '') +
                                               (om.get('readyText') or '')),
                                'cards': 0, 'stats': 0, 'tables': 0, 'tableRows': 0,
                                'buttons': 0, 'navCount': 0, 'heads': [],
                                'splashHidden': True, 'loadingLeft': False,
                                'overflowX': False,
                            }
                            chrome.cdp.pump(0.3)
                            entry.update(capture(chrome, out_path, vp, 1400))
                            entry['bytes'] = os.path.getsize(out_path)
                            entry['ok'] = bool(entry['ready'])
                            errs = []
                            for ev in chrome.cdp.drain_events():
                                if ev.get('method') == 'Runtime.exceptionThrown':
                                    d = (ev.get('params') or {}).get('exceptionDetails') or {}
                                    errs.append('exception: ' + str(d.get('text', ''))[:200])
                            entry['console'] = errs
                    except Exception as exc:
                        entry['ok'] = False
                        entry['error'] = str(exc)[:300]
                        entry.setdefault('metrics', {})
                    entry['ms'] = int((time.time() - started) * 1000)
                    results.append(entry)
                    log('      %-40s %s' % (tag, 'OK' if entry.get('ok')
                                            else 'FAIL: ' + str(entry.get('error') or entry.get('readyWhy'))))
    finally:
        try:
            chrome.close()
        except Exception:
            pass

    try:
        import urllib.request
        with urllib.request.urlopen(url + 'api/meta', timeout=10) as resp:
            meta = json.loads(resp.read().decode('utf-8'))
    except Exception:
        meta = {}

    manifest = {
        'version': (meta.get('app') or {}).get('version', ''),
        'chromium': chrome.version,
        'url': url,
        'theme': plan.get('theme'),
        'lang': plan.get('lang'),
        'viewports': plan['viewports'],
        'discovery': found,
        'shots': results,
        'remoteDir': workdir,
        'shotsDir': shots_dir,
    }
    sys.stdout.write(MARKER + json.dumps(manifest, ensure_ascii=False) + '\n')
    sys.stdout.flush()
    return 0


# --------------------------------------------------------------- 本机侧流程 ---

def _png_decode(data):
    """最小 PNG 解码：只支持 8bit / 非隔行 / 彩色(2) 或带 alpha(6)。
    chromium 截图正好只出这两种，省掉 Pillow 依赖。"""
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('不是 PNG')
    pos = 8
    width = height = depth = ctype = 0
    idat = []
    while pos < len(data):
        (length,) = struct.unpack('>I', data[pos:pos + 4])
        ctag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if ctag == b'IHDR':
            width, height, depth, ctype, _, _, interlace = struct.unpack('>IIBBBBB', body)
            if depth != 8 or interlace != 0 or ctype not in (2, 6):
                raise ValueError('不支持的 PNG 变体 depth=%s ctype=%s interlace=%s'
                                 % (depth, ctype, interlace))
        elif ctag == b'IDAT':
            idat.append(body)
        elif ctag == b'IEND':
            break
        elif ctag == b'PLTE':
            raise ValueError('调色板 PNG')
    import zlib
    raw = zlib.decompress(b''.join(idat))
    bpp = 3 if ctype == 2 else 4
    stride = width * bpp
    out = bytearray(height * stride)
    prev = bytearray(stride)
    off = 0
    for y in range(height):
        ftype = raw[off]
        off += 1
        line = bytearray(raw[off:off + stride])
        off += stride
        if ftype == 1:
            for i in range(bpp, stride):
                line[i] = (line[i] + line[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = line[i - bpp] if i >= bpp else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                a = line[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pr) & 0xFF
        elif ftype != 0:
            raise ValueError('未知 PNG 过滤类型 %s' % ftype)
        out[y * stride:(y + 1) * stride] = line
        prev = line
    return {'width': width, 'height': height, 'bpp': bpp, 'pixels': bytes(out)}


def image_stats(path):
    """算「这张图像不像白屏 / 空白」所需的统计量。"""
    data = Path(path).read_bytes()
    img = _png_decode(data)
    w, h, bpp, px = img['width'], img['height'], img['bpp'], img['pixels']
    colors = {}
    total = 0
    lum_sum = 0.0
    step_x = max(1, w // 220)
    step_y = max(1, h // 220)
    for y in range(0, h, step_y):
        base = y * w * bpp
        for x in range(0, w, step_x):
            i = base + x * bpp
            r, g, b = px[i], px[i + 1], px[i + 2]
            lum_sum += 0.2126 * r + 0.7152 * g + 0.0722 * b
            total += 1
            key = (r >> 2, g >> 2, b >> 2)
            colors[key] = colors.get(key, 0) + 1
    top_key, top_n = max(colors.items(), key=lambda kv: kv[1]) if colors else ((0, 0, 0), 0)
    return {
        'bytes': len(data),
        'width': w, 'height': h,
        'sampled': total,
        'uniqueColors': len(colors),
        'dominantFrac': round(top_n / float(total), 4) if total else 0.0,
        'dominantRGB': [top_key[0] << 2, top_key[1] << 2, top_key[2] << 2],
        'meanLuma': round(lum_sum / total, 1) if total else 0.0,
    }


def judge(stats, entry):
    """白屏 / 纯色 / 占位页判定。返回问题列表（空 = 正常）。"""
    if entry.get('kind') == 'overlay':
        return judge_overlay(entry, stats)
    problems = []
    if stats['uniqueColors'] < 6:
        problems.append('近乎纯色（唯一色 %d）' % stats['uniqueColors'])
    if stats['dominantFrac'] > 0.985:
        problems.append('单色占 %.1f%%（疑似白屏）' % (stats['dominantFrac'] * 100))
    metrics = entry.get('metrics') or {}
    if metrics.get('loadingLeft'):
        problems.append('视图内仍有「加载中…」占位')
    if not metrics.get('splashHidden'):
        problems.append('首屏占位未撤（截到 splash）')
    if (metrics.get('textLen') or 0) < 40:
        problems.append('视图文字量只有 %s 字' % metrics.get('textLen'))
    if not entry.get('ready'):
        problems.append('就绪判定失败：%s' % entry.get('readyWhy', '?'))
    # 说明页：每条路径行都必须有值（最容易出现的毛病是读错字段导致整行为空）
    g = entry.get('guide') or {}
    if g:
        if not g.get('cards'):
            problems.append('说明页没读到任何卡片')
        if (g.get('rowTotal') or 0) < 1:
            problems.append('说明页没有路径行')
        for lack in (g.get('emptyRows') or []):
            problems.append('路径行为空：' + lack)
    # 更新页：版本对比中间那个箭头必须真的占位置（曾量出 0×0 整个消失）
    v = entry.get('varrow') or {}
    if v:
        if not v.get('present'):
            problems.append('版本对比缺箭头元素')
        else:
            ar = v.get('arrow') or {}
            if not ar.get('w') or not ar.get('h'):
                problems.append('箭头量出 %sx%s（应为 20x20）'
                                % (ar.get('w'), ar.get('h')))
            if v.get('betweenCols') is False:
                problems.append('箭头没有落在两个版本号之间')

    want_w = entry['cssW'] * entry['dsf']
    want_h = entry['cssH'] * entry['dsf']
    if stats['width'] != want_w:
        problems.append('宽度 %d ≠ 期望 %d' % (stats['width'], want_w))
    if stats['height'] != want_h:
        problems.append('高度 %d ≠ 期望 %d' % (stats['height'], want_h))
    return problems


PAGE_NAMES = {
    'dashboard': '概览', 'jails': 'Jail 管理', 'bans': '封禁管理',
    'filters': '过滤器', 'fnknock': 'fn-knock 联动', 'logs': '日志',
}


def human_page(entry):
    if entry['kind'] == 'overlay':
        return '升级遮罩'
    if entry['kind'] == 'settings-tab':
        return '设置 · ' + (entry.get('tab') or '?')
    if entry['kind'] == 'fnk-tab':
        return 'fnknock · ' + (entry.get('tab') or '?')
    base = entry['id'].split('-')[0]
    return PAGE_NAMES.get(base, base)


def summarize(entry):
    """一句话说清这图里肉眼能看到什么。"""
    if entry['kind'] == 'overlay':
        return summarize_overlay(entry)
    m = entry.get('metrics') or {}
    bits = []
    if m.get('stats'):
        bits.append('%d 个统计卡' % m['stats'])
    if m.get('cards'):
        bits.append('%d 张卡片' % m['cards'])
    if m.get('tableRows'):
        bits.append('%d 行表格' % m['tableRows'])
    elif m.get('tables'):
        bits.append('%d 个表（0 行）' % m['tables'])
    if m.get('empty'):
        bits.append('%d 处空状态' % m['empty'])
    if m.get('buttons'):
        bits.append('%d 个按钮' % m['buttons'])
    if m.get('navCount'):
        bits.append('侧栏 %d 项' % m['navCount'])
    heads = [h for h in (m.get('heads') or []) if h][:4]
    if heads:
        bits.append('标题：' + ' / '.join(heads))
    title = m.get('pageTitle') or ''
    body = '、'.join(bits) if bits else '（无结构元素）'
    return '%s%s' % ((title + '：') if title else '', body)


def summarize_overlay(entry):
    """遮罩两态：列出可见的那几块（进度条有没有、字节数、阶段文案）。"""
    om = entry.get('overlayMetrics') or {}
    bits = []
    if om.get('stageText'):
        bits.append('阶段「%s」' % om['stageText'])
    if om.get('trackVisible'):
        fill = om.get('fillWidthPct') or ''
        bits.append('进度条可见（宽 %s）' % (fill or '?'))
        if om.get('bytesText'):
            bits.append('字节数「%s」' % om['bytesText'])
        if om.get('pctText'):
            bits.append('百分比「%s」' % om['pctText'])
    else:
        bits.append('无进度条')
        if om.get('readyText'):
            bits.append('占位「%s」' % om['readyText'])
    if om.get('pctText') == '' and om.get('trackVisible'):
        bits.append('百分比为空')
    if om.get('keepVisible'):
        bits.append('保留「%s」' % (om.get('keepText') or ''))
    box = om.get('boxRect') or {}
    if box:
        bits.append('弹窗 %d×%d' % (box.get('w', 0), box.get('h', 0)))
    return '：'.join([entry.get('note') or '升级遮罩', '、'.join(bits)])


def judge_overlay(entry, stats):
    """遮罩的专门断言：该出现的块必须出现，且不该有残留的百分比。"""
    problems = []
    om = entry.get('overlayMetrics') or {}
    if not om:
        problems.append('没取到遮罩结构')
        return problems
    if not om.get('overlayVisible'):
        problems.append('遮罩不可见')
    if not om.get('stageText'):
        problems.append('阶段文案为空')
    note = entry.get('note') or ''
    if '下载满格' in note:
        # 下载 100% 后切安装：进度条保住满格，百分比 100%，字节数清空
        if not om.get('trackVisible'):
            problems.append('100% 后应保留进度条，实际没有')
        if om.get('pctText') != '100%':
            problems.append('应保留 100%%，实际「%s」' % om.get('pctText'))
        if om.get('bytesText'):
            problems.append('安装阶段不该再显示字节数')
    elif '下载中' in note:
        if not om.get('trackVisible'):
            problems.append('下载态应有进度条，实际没有')
        if not om.get('bytesText'):
            problems.append('下载态应有字节数，实际为空')
        if not om.get('pctText'):
            problems.append('下载态应有百分比，实际为空')
        if om.get('readyVisible'):
            problems.append('下载态不该同时显示「准备中…」')
    else:
        if om.get('trackVisible'):
            problems.append('无总量态不该画进度条')
        if not om.get('readyVisible'):
            problems.append('无总量态应显示「准备中…」')
        if om.get('pctText'):
            problems.append('无总量态不该保留百分比「%s」' % om['pctText'])
        if not om.get('keepVisible'):
            problems.append('缺「升级期间请勿关闭页面」那一行')
    spin = om.get('spinRect') or {}
    if not spin or not spin.get('w'):
        problems.append('旋转图标宽度为 0')
    if om.get('installHits', 0) < 1:
        problems.append('「一键更新」没被点到（遮罩可能是残留状态）')
    return problems


def check_expectations(judged, manifest):
    """按本轮重点逐项硬核对。返回 [{item, ok, detail}]。"""
    out = []
    by_id = {}
    for r in judged:
        key = (r['viewport'], r['entry']['id'])
        by_id[key] = r
    vps = manifest.get('viewports') or []

    # 1) 说明页：卡片数与路径行数、关键行必须有值
    for vp in vps:
        r = by_id.get((vp, 'settings-guide'))
        if not r:
            out.append({'item': '说明页（%s）' % vp, 'ok': False, 'detail': '没截到'})
            continue
        g = r['entry'].get('guide') or {}
        cards = g.get('cardCount') or len(g.get('cards') or [])
        rows = g.get('rowTotal') or 0
        ok_cards = cards == EXPECT_DEFAULT['guideCards']
        ok_rows = rows == EXPECT_DEFAULT['guideRows']
        out.append({'item': '说明页卡片数（%s）' % vp, 'ok': ok_cards,
                    'detail': '%d 张（应 %d）' % (cards, EXPECT_DEFAULT['guideCards'])})
        out.append({'item': '说明页路径行数（%s）' % vp, 'ok': ok_rows,
                    'detail': '%d 条（应 %d）' % (rows, EXPECT_DEFAULT['guideRows'])})
        allkeys = []
        for c in (g.get('cards') or []):
            for row in (c.get('rows') or []):
                allkeys.append(row.get('k', ''))
        for need in EXPECT_DEFAULT['guideRequiredKeys']:
            hit = [k for k in allkeys if need in k]
            if not hit:
                out.append({'item': '说明页缺行「%s」（%s）' % (need, vp), 'ok': False,
                            'detail': '没找到这一行'})
        empties = g.get('emptyRows') or []
        out.append({'item': '说明页无空行（%s）' % vp, 'ok': not empties,
                    'detail': '空行：%s' % '、'.join(empties) if empties else '全部有值'})

    # 2) 侧栏 7 项且顺序正确（日志在设置之前）
    for vp in vps:
        r = by_id.get((vp, 'dashboard'))
        if not r:
            continue
        m = r['entry'].get('metrics') or {}
        nav = [x.strip() for x in (m.get('navItems') or [])]
        ok_n = len(nav) == EXPECT_DEFAULT['navCount']
        out.append({'item': '侧栏项数（%s）' % vp, 'ok': ok_n,
                    'detail': '%d 项：%s' % (len(nav), ' / '.join(nav))})
        ok_order = nav == EXPECT_DEFAULT['navOrder']
        out.append({'item': '侧栏顺序（%s）' % vp, 'ok': ok_order,
                    'detail': ' / '.join(nav)})

    # 3) 更新页箭头
    for vp in vps:
        r = by_id.get((vp, 'settings-update'))
        if not r:
            continue
        v = r['entry'].get('varrow') or {}
        ar = v.get('arrow') or {}
        ok = bool(ar.get('w')) and bool(ar.get('h')) and v.get('betweenCols') is not False
        out.append({'item': '版本对比箭头（%s）' % vp, 'ok': ok,
                    'detail': '%s×%s，落在两版本号之间：%s'
                              % (ar.get('w'), ar.get('h'),
                                 '是' if v.get('betweenCols') else '否')})

    # 4) 封禁管理顶栏下面不留空行
    for vp in vps:
        r = by_id.get((vp, 'bans'))
        if not r:
            continue
        bh = r['entry'].get('bansHead') or {}
        if not bh.get('found'):
            out.append({'item': '封禁页顶栏间距（%s）' % vp, 'ok': False,
                        'detail': '没找到 Jail 选择器'})
            continue
        extra = bh.get('extraBlocks')
        gap = bh.get('gap')
        pad = bh.get('mainPaddingTop')
        # 判据看结构：顶栏与选择器之间只允许 main 自己的内边距，不许夹别的块。
        ok = (extra == 0)
        detail = ('中间夹了 %s 个块' % extra) if extra else '中间无空行'
        if gap is not None:
            detail += '（顶栏到选择器 %s px，其中内边距 %s px）' % (gap, pad)
        out.append({'item': '封禁页顶栏下无空行（%s）' % vp, 'ok': ok,
                    'detail': detail})
    return out


def fmt_size(stats):
    return '%d×%d' % (stats['width'], stats['height'])


def write_index(out_dir, manifest, judged, expects=None):
    out_dir = Path(out_dir)
    version = manifest.get('version') or '未知'
    L = []
    L.append('# 截图索引（面板 %s）' % version)
    L.append('')
    L.append('- 生成时间：%s' % time.strftime('%Y-%m-%d %H:%M:%S'))
    L.append('- 面板地址：%s' % manifest.get('url'))
    L.append('- 浏览器：%s（CDP 直连，不走 --screenshot）' % manifest.get('chromium'))
    L.append('- 主题：%s ／ 语言：%s' % (manifest.get('theme') or '跟随系统（此环境解出浅色）',
                                         manifest.get('lang') or 'zh'))
    L.append('- 桌面视口 1440×900（1 倍）；手机视口 390×844（2 倍，移动模拟）')
    L.append('- 图高按内容自适应；超出上限的标「已截断」')
    L.append('')
    L.append('每行 = 一个文件 + 该图里肉眼可见的关键元素，改版后逐行对照差异。')
    L.append('')
    abnormal = [r for r in judged if r['problems']]
    if abnormal:
        L.append('## ⚠ 需要注意的图（%d 张）' % len(abnormal))
        L.append('')
        for item in abnormal:
            L.append('- ' + BT + item['file'] + BT + '：' + '；'.join(item['problems']))
        L.append('')
    else:
        L.append('## 全部截图通过「白屏 / 就绪 / 尺寸」断言')
        L.append('')

    for vp in manifest.get('viewports') or []:
        rows = [r for r in judged if r['viewport'] == vp]
        if not rows:
            continue
        label = '桌面 1440×900' if vp == 'desktop' else '手机 390×844'
        L.append('## %s（%s）' % (vp, label))
        L.append('')
        L.append('| 文件 | 页面 | 尺寸 | 肉眼可见 | 备注 |')
        L.append('| --- | --- | --- | --- | --- |')
        for r in rows:
            notes = []
            if r['entry'].get('truncated'):
                notes.append('已截断')
            if r['metrics'].get('overflowX'):
                notes.append('横向溢出')
            if r['entry'].get('console'):
                notes.append('控制台 %d 条' % len(r['entry']['console']))
            if r['problems']:
                notes.append('⚠ ' + r['problems'][0])
            if not notes:
                notes.append('—')
            L.append('| ' + BT + r['file'] + BT + ' | ' + human_page(r['entry']) + ' | '
                     + fmt_size(r['stats']) + ' | ' + summarize(r['entry']) + ' | '
                     + '；'.join(notes) + ' |')
        L.append('')

    # ---- 本轮重点核对（--expect）----
    if expects:
        L.append('## 本轮重点核对')
        L.append('')
        L.append('| 项 | 结果 | 实测 |')
        L.append('| --- | --- | --- |')
        for e in expects:
            L.append('| %s | %s | %s |' % (e.get('item', ''),
                                             '✅ 通过' if e['ok'] else '❌ 未通过',
                                             e.get('detail', '')))
        L.append('')

    # ---- 专项核对：说明页路径行 / 版本对比箭头 / 升级遮罩两态 ----
    L.append('## 专项核对')
    L.append('')
    guide_entry = None
    varrow_entry = None
    for r in judged:
        if r['entry'].get('guide') and not guide_entry:
            guide_entry = r
        if r['entry'].get('varrow') and not varrow_entry:
            varrow_entry = r

    if guide_entry:
        g = guide_entry['entry']['guide']
        cards = g.get('cards') or []
        L.append('### 说明页路径行（%s）' % guide_entry['entry']['viewport'])
        L.append('')
        L.append('- 卡片 %d 张，路径行合计 **%d** 条' % (len(cards), g.get('rowTotal') or 0))
        empties = g.get('emptyRows') or []
        if empties:
            L.append('- ⚠ 值为空的行 %d 条：%s' % (len(empties), '、'.join(empties)))
        else:
            L.append('- 所有路径行都有值（无空行 / 无「—」）')
        L.append('')
        for c in cards:
            L.append('- **%s**：%d 行' % (c.get('title') or '(无标题)', len(c.get('rows') or [])))
            for row in (c.get('rows') or []):
                L.append('  - %s → %s' % (row.get('k', ''), row.get('v') or '**（空）**'))
        L.append('')

    if varrow_entry:
        v = varrow_entry['entry']['varrow']
        ar = v.get('arrow') or {}
        svg = v.get('svg') or {}
        L.append('### 更新页版本对比箭头（%s）' % varrow_entry['entry']['viewport'])
        L.append('')
        L.append('- 两个版本号：%s' % ' → '.join(v.get('versions') or []))
        L.append('- 箭头容器：%s×%s，display=%s；内层 svg：%s×%s'
                 % (ar.get('w'), ar.get('h'), ar.get('display'), svg.get('w'), svg.get('h')))
        L.append('- 落点是否在两个版本号之间：%s' % ('是' if v.get('betweenCols') else '否'))
        L.append('')

    overlays = [r for r in judged if r['entry'].get('kind') == 'overlay']
    if overlays:
        L.append('### 升级遮罩')
        L.append('')
        L.append('三态靠页面内伪造 update 接口、再点真实「一键更新」触发；'
                 '**不会真的安装**，设备上的包与配置都没动。')
        L.append('')
        L.append('| 文件 | 状态 | 进度条 | 字节数 | 百分比 | 阶段 | 保留行 |')
        L.append('| --- | --- | --- | --- | --- | --- | --- |')
        for r in overlays:
            om = r['entry'].get('overlayMetrics') or {}
            L.append('| ' + BT + r['file'] + BT + ' | %s | %s | %s | %s | %s | %s |'
                     % (r['entry'].get('note') or '',
                        '有' if om.get('trackVisible') else '无',
                        om.get('bytesText') or '—',
                        om.get('pctText') or '—',
                        om.get('stageText') or '—',
                        '有' if om.get('keepVisible') else '无'))
        L.append('')

    disc = manifest.get('discovery') or {}
    L.append('## 结构快照')
    L.append('')
    L.append('- 侧栏导航（%d 项）：%s' % (len(disc.get('views') or []),
                                       ' / '.join(disc.get('views') or [])))
    L.append('- 设置分区（%d 个）：%s' % (len(disc.get('setTabs') or []),
                                       ' / '.join(disc.get('setTabs') or [])))
    L.append('- fnknock 分区（%d 个）：%s' % (len(disc.get('fnkTabs') or []),
                                            ' / '.join(disc.get('fnkTabs') or [])))
    L.append('')
    (out_dir / 'INDEX.md').write_text('\n'.join(L), encoding='utf-8')
    return '\n'.join(L)


# ----------------------------------------------------------------- 远程调度 ---

def ssh(host, command, timeout=600, retries=3):
    """ssh 调用带重试：测试机上偶尔在跑 chromium 时会有一次连接就断掉，
    这种瞬时失败重试一次就好，不该让整批截图白跑。"""
    last = None
    for attempt in range(retries):
        res = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=no',
                              '-o', 'ServerAliveInterval=15', host, command],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout)
        if res.returncode == 0:
            return res.stdout
        last = res
        if attempt < retries - 1:
            time.sleep(2 + attempt * 3)
    raise RuntimeError('ssh 失败(%d): %s\n%s'
                       % (last.returncode, command[:120],
                          (last.stderr or b'').decode('utf-8', 'replace')[-800:]))


def scp_to(host, local, remote, timeout=120, retries=3):
    last = None
    for attempt in range(retries):
        res = subprocess.run(['scp', '-q', '-o', 'BatchMode=yes', '-o',
                              'StrictHostKeyChecking=no', str(local),
                              '%s:%s' % (host, remote)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout)
        if res.returncode == 0:
            return
        last = res
        if attempt < retries - 1:
            time.sleep(2 + attempt * 3)
    raise RuntimeError('scp 上传失败(%s): %s'
                       % (last.returncode,
                          (last.stderr or b'').decode('utf-8', 'replace')[-400:]))


def run_remote(host, plan, script_path, tag, out_dir, timeout):
    remote_root = REMOTE_DIR
    remote_script = remote_root + '/shots.py'
    remote_plan = remote_root + '/plan-%s.json' % tag
    plan = dict(plan)
    plan['remoteDir'] = remote_root + '/work-' + tag

    ssh(host, 'mkdir -p %s' % remote_root, timeout=60)
    scp_to(host, script_path, remote_script)
    tmp = Path(tempfile.mkdtemp(prefix='f2b-plan-'))
    local_plan = tmp / 'plan.json'
    local_plan.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
    scp_to(host, local_plan, remote_plan)

    log('      设备侧驱动 chromium 中...')
    raw = ssh(host, 'python3 %s --on-device --plan %s' % (remote_script, remote_plan),
              timeout=timeout)
    text = raw.decode('utf-8', 'replace')
    manifest = None
    for line in text.splitlines():
        if line.startswith(MARKER):
            manifest = json.loads(line[len(MARKER):])
        elif line.startswith('[1/3]') or line.startswith('[2/3]'):
            log('      ' + line)
    if manifest is None:
        raise RuntimeError('设备侧没有返回清单，输出如下：\n' + text[-1500:])

    blob = ssh(host, 'tar -cf - -C %s shots' % manifest['remoteDir'], timeout=600)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(blob), mode='r:') as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = os.path.basename(member.name)
            if not name.endswith('.png'):
                continue
            fh = tar.extractfile(member)
            (out_dir / name).write_bytes(fh.read())
    shutil.rmtree(tmp, ignore_errors=True)
    return manifest


def plan_from_args(args):
    return {
        'url': args.url,
        'chrome': args.chrome,
        'viewports': args.viewport,
        'theme': args.theme,
        'lang': args.lang,
        'only': args.only,
        'fnkTabs': args.fnk_tabs,
        'extras': args.extras,
        'expect': args.expect,
        'timeout': args.timeout,
        'maxHeight': args.max_height,
    }


def main(argv):
    if '--on-device' in argv:
        return device_main(argv)

    ap = argparse.ArgumentParser(
        description='Fail2ban 面板真机多页面截图（桌面 + 手机）')
    ap.add_argument('--out', default='image/shots',
                    help='输出目录（默认 image/shots，相对项目根）')
    ap.add_argument('--viewport', default=['desktop', 'mobile'],
                    choices=['desktop', 'mobile'], nargs='+',
                    help='desktop=1440x900 / mobile=390x844，可多选')
    ap.add_argument('--only', default=None,
                    help='只截指定页，逗号分隔（如 dashboard,jails,settings）')
    ap.add_argument('--host', default=DEFAULT_HOST, help='SSH 主机别名（默认 fnos-test）')
    ap.add_argument('--url', default=DEFAULT_URL, help='面板本机地址（设备侧访问）')
    ap.add_argument('--chrome', default=DEFAULT_CHROME, help='设备上的 chromium 路径')
    ap.add_argument('--theme', default=None, choices=['light', 'dark'],
                    help='强制主题（默认跟随系统，此环境解出浅色）')
    ap.add_argument('--lang', default='zh', help='界面语言（zh / en）')
    ap.add_argument('--timeout', type=int, default=30, help='单页等待就绪上限（秒）')
    ap.add_argument('--max-height', type=int, default=5000, help='单图最大高度（CSS px）')
    ap.add_argument('--fnk-tabs', action='store_true', help='连 fnknock 页内分区一起截')
    ap.add_argument('--expect', action='store_true',
                    help='按本版重点逐项硬核对（说明页 3 卡 20 行 / 侧栏 7 项顺序 / 封禁页无空行）')
    ap.add_argument('--extras', action='store_true',
                    help='额外截升级遮罩两态（页面内伪造 update 接口，不会真的安装）')
    ap.add_argument('--repeat', type=int, default=1,
                    help='重复跑几遍做稳定性比对（默认 1；验收建议 2）')
    ap.add_argument('--keep-remote', action='store_true', help='保留设备上的临时目录')
    ap.add_argument('--no-index', action='store_true', help='不写 INDEX.md')
    args = ap.parse_args(argv)

    args.viewport = list(dict.fromkeys(args.viewport))
    if args.only:
        args.only = [x.strip() for x in str(args.only).split(',') if x.strip()]
    out_dir = Path(args.out)
    if not out_dir.is_absolute():
        out_dir = PROJECT_ROOT / out_dir

    log('== Fail2ban 面板截图 ==')
    log('   面板: %s   主机: %s' % (args.url, args.host))
    log('   视口: %s   输出: %s' % ('+'.join(args.viewport), out_dir))

    plan = plan_from_args(args)
    script_path = Path(__file__).resolve()
    started = time.time()
    budget = args.timeout * 40 + 420

    log('[1/3] 截取 ...')
    manifest = run_remote(args.host, plan, script_path, 'a', out_dir, budget)

    stability = None
    if args.repeat and args.repeat > 1:
        log('[2/3] 重复第 2 遍做稳定性比对 ...')
        tmp_dir = Path(tempfile.mkdtemp(prefix='f2b-shots-repeat-'))
        try:
            second = run_remote(args.host, plan, script_path, 'b', tmp_dir, budget)
            stability = compare_runs(manifest, second, out_dir, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    else:
        log('[2/3] 跳过稳定性比对（加 --repeat 2 可开）')

    log('[3/3] 校验与索引 ...')
    judged = []
    for entry in manifest['shots']:
        path = out_dir / entry['file']
        item = {'file': entry['file'], 'viewport': entry['viewport'],
                'entry': entry, 'metrics': entry.get('metrics') or {}, 'problems': []}
        if not path.exists():
            item['problems'] = ['文件缺失']
            item['stats'] = {'width': 0, 'height': 0, 'uniqueColors': 0,
                             'dominantFrac': 1.0, 'bytes': 0, 'meanLuma': 0}
        else:
            try:
                item['stats'] = image_stats(path)
                item['problems'] = judge(item['stats'], entry)
            except Exception as exc:
                item['stats'] = {'width': 0, 'height': 0, 'uniqueColors': 0,
                                 'dominantFrac': 0, 'bytes': path.stat().st_size,
                                 'meanLuma': 0}
                item['problems'] = ['图片解析失败: %s' % exc]
        judged.append(item)

    expects = check_expectations(judged, manifest) if args.expect else []

    meta_dir = out_dir / '_meta'
    meta_dir.mkdir(parents=True, exist_ok=True)
    report = {
        'generatedAt': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': manifest.get('version'),
        'chromium': manifest.get('chromium'),
        'url': manifest.get('url'),
        'theme': manifest.get('theme'),
        'lang': manifest.get('lang'),
        'viewports': manifest.get('viewports'),
        'discovery': manifest.get('discovery'),
        'stability': stability,
        'expectations': expects,
        'shots': [{'file': j['file'], 'viewport': j['viewport'],
                   'page': human_page(j['entry']), 'problems': j['problems'],
                   'stats': j['stats'], 'pngH': j['entry'].get('pngH'),
                   'waitedMs': j['entry'].get('waitedMs'),
                   'console': j['entry'].get('console') or [],
                   'overflowX': j['metrics'].get('overflowX'),
                   'truncated': j['entry'].get('truncated')} for j in judged],
    }
    (meta_dir / 'shots.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    if not args.no_index:
        write_index(out_dir, manifest, judged, expects)

    if not args.keep_remote:
        try:
            ssh(args.host, 'rm -rf %s' % REMOTE_DIR, timeout=60)
        except Exception:
            pass

    ok_count = len([j for j in judged if not j['problems']])
    log('')
    log('完成：%d 张，正常 %d，有问题 %d，用时 %.1fs'
        % (len(judged), ok_count, len(judged) - ok_count, time.time() - started))
    log('输出目录：%s' % out_dir)
    for j in judged:
        if j['problems']:
            log('  ! %s -> %s' % (j['file'], '；'.join(j['problems'])))
    if expects:
        bad = [e for e in expects if not e['ok']]
        log('重点核对：%d 项，通过 %d，未通过 %d'
            % (len(expects), len(expects) - len(bad), len(bad)))
        for e in expects:
            log('  %s %s -> %s' % ('OK  ' if e['ok'] else 'FAIL', e['item'], e['detail']))
    if stability:
        log('稳定性：%s' % stability['summary'])
        for d in (stability.get('diffs') or [])[:10]:
            log('  差异 %s：%s' % (d['file'], d['note']))
    bad_expect = [e for e in (expects or []) if not e['ok']]
    return 0 if (ok_count == len(judged) and not bad_expect) else 1


def compare_runs(first, second, dir_a, dir_b):
    a_map = {e['file']: e for e in first['shots']}
    b_map = {e['file']: e for e in second['shots']}
    diffs = []
    same = 0
    total = 0
    for name, ea in a_map.items():
        eb = b_map.get(name)
        total += 1
        if not eb:
            diffs.append({'file': name, 'note': '第二遍缺少这张'})
            continue
        ma = ea.get('metrics') or {}
        mb = eb.get('metrics') or {}
        keys = ['cards', 'stats', 'tables', 'tableRows', 'buttons', 'navCount', 'textLen']
        structural = [k for k in keys if ma.get(k) != mb.get(k)]
        pa = Path(dir_a) / name
        pb = Path(dir_b) / name
        note = []
        if structural:
            note.append('结构指标不一致：' + ', '.join(
                '%s %s→%s' % (k, ma.get(k), mb.get(k)) for k in structural))
        if pa.exists() and pb.exists():
            try:
                sa = image_stats(pa)
                sb = image_stats(pb)
                if (sa['width'], sa['height']) != (sb['width'], sb['height']):
                    note.append('尺寸不一致 %dx%d → %dx%d'
                                % (sa['width'], sa['height'], sb['width'], sb['height']))
                if abs(sa['dominantFrac'] - sb['dominantFrac']) > 0.02:
                    note.append('主色占比变化 %.3f → %.3f'
                                % (sa['dominantFrac'], sb['dominantFrac']))
                ratio = pixel_diff_ratio(pa, pb)
                if ratio > 0.02:
                    note.append('像素差异 %.1f%%' % (ratio * 100))
            except Exception as exc:
                note.append('比对失败: %s' % exc)
        if note:
            diffs.append({'file': name, 'note': '；'.join(note)})
        else:
            same += 1
    summary = ('%d/%d 张两遍一致' % (same, total)) if not diffs else \
        ('%d/%d 张一致，%d 张有差异' % (same, total, len(diffs)))
    return {'summary': summary, 'diffs': diffs, 'total': total}


def pixel_diff_ratio(path_a, path_b, tolerance=6):
    a = _png_decode(Path(path_a).read_bytes())
    b = _png_decode(Path(path_b).read_bytes())
    if (a['width'], a['height']) != (b['width'], b['height']):
        return 1.0
    pa, pb, bpp = a['pixels'], b['pixels'], a['bpp']
    w, h = a['width'], a['height']
    diff = 0
    total = 0
    for y in range(0, h, 5):
        base = y * w * bpp
        for x in range(0, w, 5):
            i = base + x * bpp
            if (abs(pa[i] - pb[i]) > tolerance or abs(pa[i + 1] - pb[i + 1]) > tolerance
                    or abs(pa[i + 2] - pb[i + 2]) > tolerance):
                diff += 1
            total += 1
    return diff / float(total) if total else 0.0


if __name__ == '__main__':
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        sys.exit(130)
