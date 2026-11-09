# tools/

打包与质量闸门。所有脚本都是 Python 标准库，没有第三方依赖。

## 闸门

| 脚本 | 查什么 | 退出码 |
| --- | --- | --- |
| `preflight.py` | 一条命令跑齐下面全部闸门，顺序与 CI 一致 | 失败为 1 |
| `check_structure.py` | 方法缩进跑偏掉了类、同类方法重名、读到从未赋值的属性、调用了不存在的方法、用了没定义的名字 | 有问题为 1 |
| `check_i18n.py` | 中英文词条一一对应、值必须是非空字符串字面量、en 不能直接抄中文、代码引用的 key 存在、同 key 两边占位符一致 | 有问题为 1 |
| `check_css.py` | 花括号配平、CSS 变量先定义后使用且定义在主题块里、页面引用的静态资源存在、契约类齐全 | 有问题为 1 |
| `check_web.py` | JS 语法（node --check）、中文原文反查表引用的 key 存在、页面装配、设置页无内联间距补丁 | 有问题为 1 |
| `check_docs.py` | Markdown 里的相对路径能否解析到（按文档所在目录算） | 有问题为 1 |
| `inspect_fpk.py` | 打好的包装不装得上、功能全不全（详见下） | 有问题为 1 |

两道闸门各带一份自测，用变异样本检验它自己还能抓到问题（有必须抓到的，也有
必须不报的，防误报），样本在临时目录里现写现删、不进仓库：

- `check_structure.py --self-test`：六个用例；
- `check_i18n.py --self-test`：八个用例。

两份自测都是 `preflight.py` 的闸门，排在对应的常规检查之前 —— 闸门自己被改坏了，
后面的全绿没有意义。

`inspect_fpk.py --strict` 检查：必需成员、`cmd/*` 可执行位、外层图标尺寸、九档图标齐全、
包内无 `__pycache__` / `.pyc` / `.log`、`manifest` 的 checksum 与 `app.tgz` 实际 md5 一致、
包内版本与源码 manifest 一致，以及 `cmd/` `wizard/` `config/` 与 1.7.0 官方包的逐字节比对。

## 打包

| 脚本 | 在哪跑 | 用途 |
| --- | --- | --- |
| `build_fpk.py --host fnos-test` | Windows 开发机 | scp 源码到测试机，那边 fnpack 打包，再 scp 回来 |
| `make_package.py` | 任意 Linux（含 CI） | 摆目录与权限、校验图标、调 fnpack、跑解包自检，产物进 output/ |

两者产出的包等价。`make_package.py` 找不到 fnpack 时会说明原因并退化成「只准备打包目录」，
不会假装成功。

## 其他

| 脚本 | 用途 |
| --- | --- |
| `make_icons.py` | 从 1024px 母版重采样出九档图标 |
| `baseline-files.json` | 1.7.0 官方包里生命周期脚本的 sha256 基线，供 `inspect_fpk.py` 比对 |
| `_gatelib.py` | 各闸门共用的路径、UTF-8 输出、报告格式与 node 探测 |

`make_favicon.py` / `make_logo.py` / `tune_*.py` / `debug_*.py` / `dump_pixels.py` /
`check_masks.py` / `check_assets.py` / `survey_i18n.py` 是图标与文案改造期间的一次性
辅助脚本，保留作参考，不参与构建与闸门。
