# 开发说明

这份文档写给要改这个面板的人：怎么搭环境、改完怎么验、打包和发布怎么走、
以及踩过哪些只在真机上才会出现的坑。

## 一、项目结构

```
fn-fail2ban-upgrade/
├─ src/pkg/fn-fail2ban/      打包源（真正进安装包的内容）
│  ├─ manifest               版本、端口、显示名、changelog
│  ├─ cmd/                   生命周期脚本（install / config / upgrade / uninstall / main）
│  ├─ config/                privilege、resource
│  ├─ wizard/                安装向导与卸载向导的字段定义
│  ├─ wizard/install         首次安装时问哪些问题
│  ├─ ICON.PNG ICON_256.PNG  外层图标，文件名必须大写（fnpack 硬要求）
│  └─ app/
│     ├─ bin/                后端（纯 Python 标准库）
│     ├─ ui/                 ui/config 与九档图标
│     └─ web/                前端（原生 JS + CSS，零构建）
├─ tools/                   质量闸门、打包、图标生成
├─ output/                  打好的 .fpk
├─ image/                   截图与图标母版
├─ report/                  改造规范、上下文恢复卡、历次交付说明
└─ CHANGELOG.md             版本记录
```

两条不能破的边界：后端只用 Python 标准库（不引入任何第三方包），
前端零构建（不引入 npm、CDN、打包器）。

## 二、环境

本机没有全局的 python 与 node，用便携运行时：

```powershell
$py   = "C:\Users\PVE\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\python\python.exe"
$node = "C:\Users\PVE\.dsh\dsh-runtimes\dsh-next-primary-runtime\dependencies\node\bin\node.exe"
```

跑闸门时不需要手动指定 node：`preflight.py` 按这个顺序找，找到一个就用 ——

1. 环境变量 `DSH_NODE`（其次 `NODE_BIN`）；
2. PATH 上的 `node` / `node.exe`；
3. 上面那个便携运行时里的固定路径（用 `os.path.isfile` 判断存在）。

三者都找不到时，前端语法检查判**失败**而不是「跳过」：静默跳过在日志里和通过长得
一模一样，而前端恰恰是最容易改坏的部分。同样的顺序也适用于 `check_web.py` 单独运行。

## 三、改完先跑闸门

```
python tools/preflight.py
```

一条命令跑齐本地闸门，顺序与 CI 一致。全绿才算改完。也可以用单条：

| 命令 | 查什么 |
| --- | --- |
| `tools/check_structure.py` | 方法缩进跑偏掉了类、同类方法重名、读到从未赋值的属性、调用了不存在的方法、用了没定义的名字 |
| `tools/check_i18n.py` | 中英文词条一一对应、词条的值必须是非空字符串字面量、en 不能直接抄中文、代码引用的 key 存在、占位符两边一致 |
| `tools/check_css.py` | 花括号配平、CSS 变量先定义后使用且必须定义在 `:root` / `[data-theme]` 里、页面引用的静态资源存在、结构契约要求的类齐全 |
| `tools/check_web.py` | JS 语法（node --check）、中文原文反查表引用的 key 存在、index.html 真的引到样式表与两个脚本、设置页范围内没有内联间距补丁 |
| `tools/check_docs.py` | Markdown 里的相对路径能否解析到（按文档所在目录算） |
| `tools/inspect_fpk.py` | 打好的包装不装得上、功能全不全 |

### 闸门自己的体检

闸门最坏的形态不是漏报，是「改过之后什么都不报、但一直绿」—— 看起来和通过一模一样。
所以 `check_structure.py` 内置了一组变异样本：

```
python tools/check_structure.py --self-test
```

六个用例：方法缩进跑偏掉出类、读到从未赋值的属性、用了没定义的名字、同类方法重名、
调用不存在的方法，以及一个**必须不报**的干净类（只会误报的闸门一样不能用）。
样本在临时目录里现写现删，不进仓库。`preflight.py` 把它作为第一道闸门跑 ——
闸门本身被改坏了，后面的全绿没有意义。

i18n 那道也有：`python tools/check_i18n.py --self-test`，八个用例覆盖「en 值写成
undefined / null / 数字」「en 直接抄中文」「抄中文但夹着术语」五种必须抓到的形态，
以及「纯术语相同」「术语 + 符号相同」「纯符号相同」三种不该误报的形态。

前端那道同样：临时放一个语法坏掉的 .js 进 `app/web/`，`check_web.py` 应当报错并以
非 0 退出，然后删掉它。

### i18n 的两条硬断言

词条的值必须是非空字符串字面量。值写成 `undefined` 时不会报错 —— 键还在，取词条
照样能取到「一个不是字符串的东西」，页面于是把这个 key 名直接印出来。这类问题在
中文界面完全看不出来，切到英文才露馅。所以 `zh` 与 `en` 两侧都查，并打印 key 与行号。

en 词条与 zh 同 key 逐字相同且含中文，判失败（漏翻）。为避免误报，先按下面规则判断
「本来就该一样」：把占位符去掉、整词去掉技术术语（jail / ban / filter / bantime 等，
它们本来就没有中文说法），如果这时已经不剩中文，就算合理；否则报错。

空串默认也判失败 —— 空串等于没翻译。只有三处英文片段键是有意留空的
（`Used by ` 没有后缀、数字后面不需要量词、逗号用空格），列在 `EMPTY_ALLOWED` 里
并写明原因，运行时仍会打印 NOTE，改坏了看得见。要往这个名单里加东西，必须写原因。

## 四、打包

**必须在 Linux 上打包**。Windows 打出来的包会丢 `cmd/*` 的可执行位，安装报 10111。

两个脚本分工不同，产物等价：

| 脚本 | 在哪跑 | 什么时候用 |
| --- | --- | --- |
| `tools/build_fpk.py --host fnos-test` | Windows 开发机 | 日常改完要一个能装的包：把源码树 scp 到测试机，那边 fnpack 打包再 scp 回来 |
| `tools/make_package.py` | 任意 Linux（含 CI） | 已经在 Linux 上时用：摆目录、摆权限、校验图标、调 fnpack、跑解包自检，产物直接进 `output/` |

前者多一步网络往返，好处是开发机上一条命令就能出包；后者不含任何传输动作，
所以 CI 与测试机本地都用它。两边的打包前处理（清理、权限、图标校验、命名）是
同一套规则，改的时候两处要一起改 —— 否则「CI 打出来的」和「本地打出来的」会悄悄不同。

`make_package.py` 不带 fnpack 时会明确说明并退化成「只准备打包目录」，不会假装成功。

打包前会跑三道闸门（结构 / i18n / CSS）；打包后会跑 `inspect_fpk.py --strict`。

### 安装包里必须成立的事

- `cmd/*` 十个脚本全部 0755；
- `ICON.PNG` 64×64、`ICON_256.PNG` 256×256，文件名大写；
- `app/ui/images/` 下九档图标（16/24/48/64/72/96/128/256/512）一个不缺；
- 包里没有 `__pycache__`、`.pyc`、`.log`，也没有以 `_` 开头的临时文件；
- `manifest` 的 `checksum` 等于 `app.tgz` 的实际 md5（fnpack 自动重算，不要手填）；
- 包内版本等于源码 `manifest` 的版本（不等就是改了源码忘了重打）；
- `cmd/` 与官方 1.7.0 的差异只有三处已知有意改动，其余必须逐字节一致。

最后一条是「生产机原地升级不改配置」的机械保证：`tools/baseline-files.json` 存的是
1.7.0 官方包里这些文件的 sha256，`inspect_fpk.py` 逐项核对。新增有意改动要同步这份
名单并在里面写明原因，否则自检会失败 —— 这是有意设计的，别为了让它变绿去改判定。

## 五、发布

```
1. 改 src/pkg/fn-fail2ban/manifest 的 version（月内序号 +1，不跳号不复用）
2. 改 CHANGELOG.md，把新版本小节写在最上面
3. 本地跑 python tools/preflight.py 全绿
4. 在 fnos-test 上覆盖安装，逐页点一遍（中英文各一轮）
5. commit → 打 v<version> 标签 → push 标签
6. CI 构建；标签名等于 v + manifest 版本时才发 Release
```

CI 里打包用的是 `tools/make_package.py`，与测试机上跑的是同一份逻辑，
所以「本地打好能装」和「CI 打出来的能装」是同一件事。

## 六、在测试机上验证

测试机 `fnos-test`（192.168.1.13）可自由装、卸、重启、造数据；生产机只读。

```bash
# 装/卸/重启（没有 systemctl 单元，用 appcenter-cli）
appcenter-cli stop fn-fail2ban
appcenter-cli start fn-fail2ban      # 会转一会儿，属正常
```

页面验证用无头 chromium，把结果写进页面再读回来：

```bash
/usr/bin/chromium --headless=new --no-sandbox --disable-gpu \
  --window-size=1440,1000 --screenshot=/tmp/shot.png --virtual-time-budget=9000 URL
```

- `--dump-dom` **忽略** `--window-size`（三个尺寸都报同一个 innerWidth），
  所以量手机布局要看截图，不要信 dump-dom 里的尺寸；
- 需要驱动界面状态时，复制一份 `index.html` 注入探针脚本（点按钮、读 DOM），
  跑完**务必删掉**注入的临时页面，别留在 `web/` 里被打进下一个包。

### 造数据

```bash
# 封禁一个 IP（注意不是 /api/ban，那个路由不存在）
curl -s -X POST http://127.0.0.1:18752/api/jails/sshd/actions \
     -H 'Content-Type: application/json' -d '{"action":"ban","ip":"203.0.113.7"}'
```

## 七、踩过的坑

### 1. 语法完全合法，但装上去才炸

`py_compile` 只能证明文件能被解析。真正炸过的几类都是合法语法：方法体缩进少一级、
方法掉到模块级（类上少了一个方法，调用处 AttributeError）、`self.foo` 读了但从未赋值、
用了没 import 的名字、同类里方法重名后一个静默覆盖前一个。这就是 `check_structure.py`
存在的理由 —— 它按 AST 核查这几类，而不是再跑一遍编译。

### 2. 基类在标准库里时不能硬查

`PanelHandler` 继承 `BaseHTTPRequestHandler`，`self.path`、`self.wfile`、`send_header()`
全都在基类上。逐条报出来就是几十行假警报，真问题被埋掉。现在的做法是对这类类只出
一条汇总警告（说明有哪些成员看不到），其余仍然精确报错。

### 3. Windows 的代码页会吃掉闸门的错误原因

`subprocess.run(..., text=True)` 用系统区域编码解码：Windows 上是 GBK，
而闸门输出是 UTF-8 中文，读取线程会死在 UnicodeDecodeError 上，子进程的 stderr
整段丢失 —— 结果就是「闸门失败了，但什么原因都不显示」。所有子进程调用都显式
`encoding="utf-8", errors="replace"`。同理，脚本自己输出前也把 stdout 重设成 UTF-8。

### 4. 子进程找不到程序时抛异常，不是返回非 0

直接 `subprocess.run(["node", ...])` 在没有 node 的机器上会抛 FileNotFoundError，
把整个 preflight 崩成一句 traceback，看起来像闸门自己坏了。必须先 `shutil.which`
真检测；没有 node 时明确打「跳过」，不要静默跳过 —— 静默跳过和通过长得一模一样。

### 5. Markdown 的相对路径按文档所在目录解析

写在 `docs/DEVELOPMENT.md` 里的 `images/x.png` 指的是 `docs/images/x.png`。
本地编辑器会「帮你找图」，预览正常；推到网页上整篇文档全是 404。
`check_docs.py` 按 Markdown 的语义解析，并且跳过代码块与行内代码里的示例路径。

### 6. 反引号与模板字符串

写 Markdown 时注意：反引号与三反引号在生成脚本里是转义敏感字符，
用「先拼字符串再写盘」的方式容易把文档写坏（少了半篇）。写完要读回来核对行数。

### 7. 打包脚本里不要手工维护 checksum

`manifest` 的 `checksum` 是 `app.tgz` 的 md5，fnpack 打包时会重算。手工填的值在改过
任何一个文件之后就是错的，而安装器对 checksum 不匹配的报错很含糊。

## 八、后端结构速查

| 模块 | 职责 |
| --- | --- |
| `server.py` | HTTP 路由、处理器、启动入口、`--self-check` |
| `store.py` | 配置与数据文件的读写（数据目录、设置合并） |
| `f2b.py` | 调 `fail2ban-client`，解析 jail 状态与封禁列表 |
| `jailconf.py` | 读写 `/etc/fail2ban/jail.d/50-fn-fail2ban.local` |
| `banlog.py` | 解析 fail2ban.log，得到封禁时间与到期时间 |
| `notify.py` | 通知渠道定义、字段元数据、双语消息、投递 |
| `delivery.py` | 投递记录（环形缓冲、原子写、不含密钥） |
| `storage.py` | 目录占用统计与清理 |
| `update.py` | 版本检查（三层退路、缓存节流、后台轮询） |
| `installer.py` | 下载、校验、停止、卸载、安装、启动、回滚 |
| `fnknock.py` | 与敲门（fn-knock）联动的黑名单同步 |

`server.py --self-check` 打印运行环境（python 版本、网关 socket、fail2ban 是否安装、
数据目录、静态资源数量），设备上排查问题先看它。

## 九、前端结构速查

- `index.html`：静态外壳，文案用 `data-i18n` 标注 key；
- `i18n.js`：字典（中英各一本）、`t()` 查表、中文原文反查表 `SRC2KEY`、
  句子模板 `LINE_TPL`；
- `app.js`：视图渲染与请求（插件式，一个函数一个视图）；
- `style.css`：设计令牌 + 组件样式。

界面是原生 DOM：每个视图一个 `renderXxx()` 函数，直接把 HTML 字符串写进容器，
事件用委托挂在容器上。没有框架、没有构建步骤，改完刷新页面就生效。

两条经验：

1. 拼 HTML 时注意开标签与闭标签成对 —— 曾经在批量改文案时误删过一个卡片的开标签，
   只剩孤立的闭标签，整个卡片样式塌掉，而 JS 语法检查完全通过；
2. 切语言靠 `repaintForLang()` 重绘：静态外壳由 `paintStaticI18n()` 按 `data-i18n`
   回填，动态区重新调各自的 render 函数。新加的视图要在这里挂上，否则切语言不刷新。

改 i18n 时注意 key 名已冻结，改名必须同步字典与所有引用处；`check_i18n.py` 会查引用了
不存在的 key，但查不出「改了 key 名却只改了一半」的语义错误。
