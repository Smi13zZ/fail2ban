# 真机多页面截图工具（tools/shots.py）

把测试机上**正在运行的面板**逐页、逐视口截成 PNG，供改版前后逐页对照验收。
纯 Python 3 标准库，零第三方依赖；本机跑，设备侧驱动 chromium。

    python tools/shots.py --out image/shots-<version> --fnk-tabs --extras --repeat 2

### 图片目录怎么用

| 目录 | 是什么 | 能当什么证据 |
| --- | --- | --- |
| `image/shots-<version>/` | 本工具出的图（CDP 真实视口） | **桌面与手机端的验收依据** |
| `image/v26118/` | 用 `--screenshot` 粗看的桌面图 | 只用于同图内对比「改之前 / 改之后」的桌面观感，**不当手机端证据** |

两批图不混用：手机端结论一律只认 `shots-<version>/mobile-*.png`。

---

## 1. 为什么不用 chromium 的 --screenshot

这台设备上的 headless chromium 有一个会误导人的行为：

**--window-size 小于约 500 时，布局仍按 500 CSS px 计算，截图只是把 500 宽的图裁到 390。**

也就是说 `--screenshot --window-size=390,844` 拿到的不是手机布局，而是
「被裁掉的桌面布局」：侧栏还是竖排的宽侧栏、四列统计卡还是四列。`--dump-dom` 同样如此
（实测两种模式都报 `innerWidth = 500`）。

本工具两个视口都走 **CDP `Emulation.setDeviceMetricsOverride` → `Page.captureScreenshot`**：

| 视口 | CSS 尺寸 | 截图倍率 | 移动模拟 | 输出像素 |
| --- | --- | --- | --- | --- |
| desktop | 1440×900 | 1× | 否 | 1440 宽起 |
| mobile | 390×844 | 2× | 是 | **780** 宽 |

手机端 780 宽是 390 CSS px × 2 倍图，像素密度和真机接近，字口清楚。
如果以后有人把视口参数改回 `--window-size`，请先看这一节。

CDP 走 `--remote-debugging-pipe`（fd 3/4，消息以 `\0` 分隔），
不需要 WebSocket、不需要 websocket-client 之类依赖。

---

## 2. 怎么跑

    # 全量：桌面 + 手机 × 全部页面与设置分区
    python tools/shots.py --out image/shots-<version> --fnk-tabs --extras --repeat 2

    # 只截手机端
    python tools/shots.py --viewport mobile --out image/shots-<version>

    # 只截某几页
    python tools/shots.py --only dashboard,jails,settings --out tmp/try

    # 换主题 / 语言
    python tools/shots.py --theme dark --lang en --out tmp/try

本机没有 python 时用 DSH 自带运行时：

    C:\Users\PVE\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe

### 参数

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `--out` | `image/shots` | 输出目录，相对项目根；不存在会建 |
| `--viewport` | `desktop mobile` | 可多选：`desktop`=1440×900、`mobile`=390×844 |
| `--only` | 全部 | 逗号分隔的页面 id；`settings` 会展开成它所有分区 |
| `--fnk-tabs` | 关 | 连 fnknock 页内 6 个分区一起截 |
| `--extras` | 关 | 额外截升级遮罩三态（见第 4 节） |
| `--repeat` | 1 | 跑几遍；`2` 会做稳定性比对（结构指标 + 像素差异） |
| `--theme` | 跟随系统 | `light` / `dark`；不指定时这台设备解出**浅色** |
| `--lang` | `zh` | 写进 localStorage 的 `f2b-lang` |
| `--host` | `fnos-test` | SSH 主机别名 |
| `--url` | `http://127.0.0.1:18752/` | **设备侧**访问面板用的地址 |
| `--timeout` | 30 | 单页等待就绪上限（秒） |
| `--max-height` | 5000 | 单图最大高度（CSS px），超出标「已截断」 |
| `--keep-remote` | 关 | 保留设备上 `/tmp/f2b-shots`，排障用 |
| `--no-index` | 关 | 不写 INDEX.md |

退出码：全部正常 `0`；有任何一张被判定有问题 `1`（问题会逐条打在 stderr）。

---

## 3. 产物

    <out>/desktop-<page>.png / <out>/mobile-<page>.png
    <out>/INDEX.md                 逐图一行：文件名 + 肉眼可见的关键元素
    <out>/_meta/shots.json         每张图的尺寸、页面指标、控制台报错、稳定性比对

命名规则：

- `desktop-dashboard.png` / `mobile-dashboard.png`
- 设置页按分区展开：`mobile-settings-guide.png`、`desktop-settings-notify.png`
- 带 `--fnk-tabs`：`mobile-fnknock-list.png`
- 带 `--extras`：`desktop-update-overlay-download.png` 等

**页面清单是从 DOM 里发现的，不是写死的**：导航项数、设置分区、fnknock 分区
都先跑一遍 `#dashboard` / `#settings` / `#fnknock` 读 DOM 再生成截图计划。
所以新增一个设置分区不用改这个脚本。

---

## 4. 升级遮罩三态（--extras）

遮罩靠普通访问触发不了：它是点「一键更新」后才出现的，而那个按钮只在
`has_update` 时渲染。做法是在**页面作用域**里先替换 `window.fetch`：

| 请求 | 伪造返回值 |
| --- | --- |
| `/api/update` | `has_update:true`，让「一键更新」按钮出现 |
| `/api/update/install` | 立刻 `{ok:true, phase:'downloading'}`，**绝不触达设备** |
| `/api/update/progress` | 按脚本给的帧序列返回，用来停在指定阶段 |
| `/api/meta` | 版本号固定成「升级前」那个，避免轮询判定装完而收尾 |

其余请求原样转发给真实 `fetch`，页面其余部分仍是真实数据。
**设备上的包、配置、服务都没动**，不会真的安装。

三态：

| 状态 | 应该看到 |
| --- | --- |
| `download` 下载中 | 进度条 + 「143.4 KB / 357.5 KB」+ 「40%」 |
| `preparing` 无总量 | **不画**进度条，只有一句「准备中…」+「升级期间请勿关闭页面。」 |
| `installing` 满格后切安装 | 进度条保住满格、百分比留「100%」、字节数清空 |

第三态容易漏：进度条按「只增不减」设计（`state.update.maxPct`），
下载到 100% 后进安装阶段不会退回 0。**注意 `preparing` 态不能先喂 100% 的下载帧**，
否则会命中这条保满格分支，看起来像「无总量却画了进度条」。

---

## 5. 判定与自检

每张图都会过这几道断言，任一失败就进 INDEX.md 的「⚠ 需要注意的图」：

- **不是白屏**：唯一色数 < 6、或单一颜色占比 > 98.5% 都判失败
- **不是占位**：视图文字量 < 40 字、视图里仍有「加载中…」、首屏 `#bootSplash` 未撤
- **尺寸对得上**：PNG 宽 = cssW × dsf，高 = cssH × dsf
- **真就绪**：`#bootSplash` 已 hidden + 视图区有内容，且连续两次采样文字长度稳定
  （避免截到「重绘一半」）

专项断言（默认开）：

- **说明页**：每条路径行的值都不能为空 / 不能是「—」，空的会点名报出来
- **更新页**：版本对比中间那个箭头必须真的占位置（20×20）且落在两个版本号之间

`--repeat 2` 会再跑一遍并把两遍逐张比对：结构指标（卡片数、表格行数、按钮数、
侧栏项数、文字量）必须一致，尺寸必须一致，像素差异 > 2% 就报出来。

---

## 5.1 本轮重点核对（--expect）

「这一轮改了什么」的硬断言，跑完直接把结果打出来并写进 INDEX.md 的「本轮重点核对」表。
当前核这些：

| 项 | 判据 |
| --- | --- |
| 说明页卡片数 | 3 张 |
| 说明页路径行数 | 20 条 |
| 说明页空行 | 一条都不能有（值不能为空、不能是「—」） |
| 侧栏项数与顺序 | 7 项，日志在设置之前 |
| 版本对比箭头 | 20×20，且落在两个版本号之间 |
| 封禁页顶栏下 | 顶栏与 Jail 选择器之间除内边距外不许夹别的块 |

数值写在 `EXPECT_DEFAULT`，设计变了要一起改。

**封禁页那条为什么按结构判而不是按像素间距判**：顶栏到选择器之间本来就有 `main` 的
页面内边距（桌面 16px / 手机 12px），拿一个固定的 px 阈值去卡必然误报。
真正的毛病是「中间多了一整行」——那是一个有高度的块，所以数 `main` 子元素里
夹在中间的那些块，只允许 0 个。

---

## 6. 常见坑

1. **手机端别用 `--window-size`**（见第 1 节）。这不只是「不准」，是**方向性错误**：
   会把手机截图当成合格证据，而它其实是裁过的桌面布局。
2. **`window.confirm()` 会把 CDP 整个卡死**。点「一键更新」会弹确认框，
   弹窗打开时渲染进程阻塞，后续 CDP 调用全部超时。脚本里挂了
   `Page.javascriptDialogOpening` 事件钩子自动 `accept`。
   自己写新交互时如果用到 `confirm/alert/prompt`，要想清楚这一点。
3. **主题取决于系统偏好，不是固定的**。这台设备 headless 解出**浅色**，
   和预想中的深色不一样。要深色得显式 `--theme dark`。
4. **`--timeout` 是单页上限**，整批的时间预算按页数自动放宽；
   整批跑满（桌面+手机 × 约 21 页 + 遮罩三态 × 2）约 5 分钟。
5. **设备侧不写 `/var/apps`**。工作目录固定在 `/tmp/f2b-shots`，跑完自动删。
   不往应用目录塞任何临时探针页。
6. **别在 `fnos-prod` 上跑**。默认 `--host fnos-test`；生产机只读巡检，
   不要拿它做截图目标。
7. **伪造脚本只在遮罩阶段注入**。用 `Page.addScriptToEvaluateOnNewDocument` 注入的
   脚本会对**之后所有导航**生效。如果一开始就注入，正常那张「设置 · 更新」截图会显示
   假的「发现新版本」和两个一样的版本号——看起来像界面 bug，其实是截图工具自己造的。
   现在只在遮罩阶段 `seed_script`，跑完立刻 `unseed_script` 摘掉。
   假数据里的「最新版本」也按当前版本自动 +1，避免对比区出现两个相同的号。
8. **本机路径含空格**：项目在 `D:\Workspace\DeepSeek Harness\` 下，
   PowerShell 里给脚本路径加引号；脚本内部一律用 `pathlib` 拼接，不受影响。

---

## 7. 实现要点（改脚本前先看）

- **本机负责调度与校验，设备侧负责驱动浏览器**：同一个文件用 `--on-device` 自调用，
  scp 到 `/tmp/f2b-shots/shots.py` 后在设备上跑，结果以一行
  `@@F2B_SHOTS@@{json}` 回传（stderr 留给进度日志）。
- **PNG 走 tar 流一次取回**，不逐张 scp（40+ 张逐张会慢很多）。
- **白屏检测自带最小 PNG 解码器**（8bit / 非隔行 / RGB 或 RGBA 四种过滤），
  不依赖 Pillow。chromium 截图只出这两种变体。
- **单页失败不拖垮整批**：每页 try/except，失败记进 `_meta/shots.json` 的 `error`。

---

## 8. 已知限制

- 只截静态首屏。抽屉、下拉、toast、移动端底部选择器这些交互态没覆盖；
  要截它们得在 `device_main` 里照着 `--extras` 的写法加一段「点击 + 等稳定 + 截图」。
- 遮罩三态是**伪造数据**驱动的，验证的是「布局与分支逻辑对不对」，
  不是「真实升级流程跑不跑得通」。真实升级流程要在设备上单独跑。
- 稳定性比对是采样像素差异（每 5 像素取一个），不是逐像素 diff；
  小范围抗锯齿差异不会报警。
