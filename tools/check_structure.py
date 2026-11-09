#!/usr/bin/env python3
"""结构闸门：抓 py_compile 抓不到的那类错误。

py_compile 只能证明「这段代码能被解析成语法树」。真正让设备上的面板在某个请求
打进来时才炸的，全是语法完全合法的写法：

  * 方法体缩进跑偏，方法掉出类外 —— 类上少了一个方法，调用处 AttributeError；
  * self.foo 读了一个从未赋值的属性 —— 只有走到那条分支才报错；
  * self.bar() 调用了类上不存在的方法 —— 同上；
  * 用了没导入、没定义的名字 —— 只有走到那条分支才报 NameError；
  * 同类里同名方法写了两遍 —— 后一个静默覆盖前一个，改动看起来像没生效。

前四类里，「基类不在本包内」（比如 BaseHTTPRequestHandler）的成员是看不到的，
硬查会刷出一屏假警报，把真问题埋掉。所以对这类类只做汇总报告，不逐条当成失败。

用法：

    python tools/check_structure.py            # 扫 app/bin 下全部模块
    python tools/check_structure.py 文件或目录  # 扫指定范围
    python tools/check_structure.py --self-test # 用变异样本检验本脚本还能抓到问题

退出码 1 表示发现确定的问题。
"""
import ast
import builtins
import importlib
import inspect
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

BUILTINS = set(dir(builtins)) | {
    "self", "cls", "__name__", "__file__", "__doc__", "__package__",
    "__builtins__", "__spec__", "__loader__", "__class__",
    "True", "False", "None", "WindowsError",
}


class ClassInfo:
    """一个类的可核查事实：方法名、属性赋值、属性读取、基类。"""

    def __init__(self, node):
        self.node = node
        self.name = node.name
        self.methods = {}
        self.duplicate_methods = []
        self.attr_assign = set()
        self.attr_read = {}          # attr -> 首次读取行号
        self.bases = [ast.unparse(b) for b in node.bases]
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name in self.methods:
                    self.duplicate_methods.append(item.name)
                self.methods.setdefault(item.name, item)
            elif isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        self.attr_assign.add(target.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                self.attr_assign.add(item.target.id)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Attribute) and isinstance(sub.value, ast.Name) \
                    and sub.value.id in ("self", "cls"):
                if isinstance(sub.ctx, (ast.Store, ast.Del)):
                    self.attr_assign.add(sub.attr)
                else:
                    self.attr_read.setdefault(sub.attr, sub.lineno)


def module_level_names(tree):
    """模块作用域里定义的名字：函数、类、赋值、导入，以及 if/try/with 分支里的同名定义。"""
    names = set()
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.If, ast.Try, ast.With, ast.For, ast.While)):
            names |= module_level_names(ast.Module(body=node.body, type_ignores=[]))
            for handler in getattr(node, "handlers", []):
                names |= module_level_names(ast.Module(body=handler.body, type_ignores=[]))
            names |= module_level_names(ast.Module(body=getattr(node, "orelse", []),
                                                  type_ignores=[]))
            names |= module_level_names(ast.Module(body=getattr(node, "finalbody", []),
                                                  type_ignores=[]))
    return names


def imported_names(tree):
    """本文件导入的名字 -> 来源（模块路径）。

    BaseHTTPRequestHandler 这类标准库基类的方法与属性在源码里看不到。把它们当成
    「不存在」会刷一屏假警报，把真问题埋掉；当成「没问题」等于放弃检查。
    折中做法：这类类只出汇总，人能一眼看出「这是本包之外的基类」。
    """
    out = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out[(alias.asname or alias.name).split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out[alias.asname or alias.name] = node.module or ""
    return out


def resolve_import(module, name):
    """把一个 from X import Y 解析成真实的类对象；解析不到返回 None。

    只用于标准库（http.server、socketserver 之类）：它们的方法与属性不在本包里，
    但源码就在本机的标准库里，读一遍就能知道都有什么，不必靠猜。
    """
    if module is None:
        return None
    try:
        mod = importlib.import_module(module)
    except Exception:      # noqa: BLE001 - 解析不到就走「无法判定」分支
        return None
    obj = getattr(mod, name, None)
    return obj if isinstance(obj, type) else None


def class_source_members(cls):
    """从类（含其 MRO 上的基类）的源码里收集方法名与 self 属性赋值。

    读源码而不是只看 dir(cls)：path / headers / rfile / wfile / command 这些是
    __init__ 或 setup() 里赋的实例属性，dir(cls) 上看不到，只有走一遍方法体才知道。
    源码取不到时返回 None，调用方据此走「无法判定」分支，而不是当成都存在。
    """
    methods, attrs = set(), set()
    try:
        mro = list(cls.__mro__)
    except Exception:      # noqa: BLE001
        return None
    for base in mro:
        if base is object:
            continue
        try:
            source = inspect.getsource(base)
            tree = ast.parse(textwrap.dedent(source))
        except (OSError, TypeError, SyntaxError, IndentationError):
            return None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.add(node.name)
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
                    and node.value.id == "self" and isinstance(node.ctx, (ast.Store, ast.Del)):
                attrs.add(node.attr)
    return methods, attrs


def external_bases(info, by_name, imported):
    """返回无法在本文件内解析的基类描述列表；空列表表示整个继承链都在本包内。"""
    out = []
    stack = list(info.bases)
    seen = set()
    while stack:
        text = stack.pop()
        short = text.split(".")[-1].split("[")[0]
        if short in seen:
            continue
        seen.add(short)
        base = by_name.get(short)
        if base is not None:
            stack.extend(base.bases)
            continue
        head = text.split(".")[0].split("[")[0]
        out.append("%s（来自 %s）" % (text, imported.get(head) or "未知"))
    return out


def params_of(fn):
    out = set()
    for arg in list(fn.args.args) + list(fn.args.kwonlyargs) + list(fn.args.posonlyargs):
        out.add(arg.arg)
    if fn.args.vararg:
        out.add(fn.args.vararg.arg)
    if fn.args.kwarg:
        out.add(fn.args.kwarg.arg)
    return out


def local_names(fn):
    """函数体内被绑定的名字：赋值、增强赋值、for 目标、with as、except as、
    嵌套 def/class、导入、walrus、global/nonlocal、推导式目标、match 绑定。"""
    out = set(params_of(fn))
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                out.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            out |= set(node.names)
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
        elif isinstance(node, ast.comprehension):
            for sub in ast.walk(node.target):
                if isinstance(sub, ast.Name):
                    out.add(sub.id)
        elif isinstance(node, ast.MatchAs) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.add(node.rest)
    return out


def inherited(info, by_name):
    """本包内继承链上的属性与全部方法名。"""
    attrs, methods, stack, seen = set(), set(), list(info.bases), set()
    while stack:
        text = stack.pop()
        short = text.split(".")[-1].split("[")[0]
        if short in seen:
            continue
        seen.add(short)
        base = by_name.get(short)
        if base is None:
            continue
        attrs |= base.attr_assign
        methods |= set(base.methods)
        stack.extend(base.bases)
    return attrs, methods


def analyse(path, report):
    src = G.read_text(path)
    rel = G.rel(path)
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as exc:
        report.fail("%s:%s" % (rel, exc.lineno), "语法错误：%s" % exc.msg)
        return

    module_names = module_level_names(tree) | BUILTINS
    imported = imported_names(tree)
    classes = [ClassInfo(node) for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
    by_name = {c.name: c for c in classes}

    # --- 1) 方法掉出了类：同名函数既在模块级、又是某个类的方法 ---------------
    class_method_names = set()
    for info in classes:
        class_method_names |= set(info.methods)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in class_method_names:
                owners = [c.name for c in classes if node.name in c.methods]
                report.fail("%s:%d" % (rel, node.lineno),
                            "%r 既定义在模块级、又是 %s 的方法 —— 缩进跑偏，类上少了一个方法"
                            % (node.name, "/".join(owners)))

    # --- 2) 同类里方法重名 ---------------------------------------------------
    for info in classes:
        for name in info.duplicate_methods:
            report.fail("%s:%d" % (rel, info.methods[name].lineno),
                        "%s.%s 定义了多次，后一个会静默覆盖前一个" % (info.name, name))

    # --- 3) self.xxx 读了从未赋值的属性 / 4) 调用不存在的方法 -----------------
    for info in classes:
        outside = external_bases(info, by_name, imported)
        inherited_attrs, inherited_methods = inherited(info, by_name)
        # 基类不在本包内时，先去标准库源码里把这些成员找出来；找得到就照常精确核查，
        # 找不到（只可能是非标准库或拿不到源码）才退化成一条汇总警告。
        resolved_unknown = []
        for text in outside:
            # text 形如 "BaseHTTPRequestHandler（来自 http.server）"：
            # 先用括号截掉来源说明，再取基类表达式的首段作为模块名
            expr = text.split("（")[0].strip()
            head = expr.split(".")[0].strip()
            module = imported.get(head, "")
            cls = resolve_import(module, expr.split(".")[-1].strip())
            found = class_source_members(cls) if cls is not None else None
            if found is None:
                resolved_unknown.append(text)
                continue
            inherited_methods |= found[0]
            inherited_attrs |= found[1]
        unknown_reads, unknown_calls = {}, {}
        for attr, line in sorted(info.attr_read.items(), key=lambda kv: kv[1]):
            if attr.startswith("__"):
                continue
            if attr in info.attr_assign or attr in inherited_attrs:
                continue
            if attr in info.methods or attr in inherited_methods:
                continue
            where = "%s:%d" % (rel, line)
            if resolved_unknown:
                unknown_reads.setdefault(attr, where)
            else:
                report.fail(where, "%s.%s 被读取但该类从未赋值 —— 只有走到这条分支才报错"
                            % (info.name, attr))
        for node in ast.walk(info.node):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            holder = node.func.value
            if not isinstance(holder, ast.Name) or holder.id not in ("self", "cls"):
                continue
            name = node.func.attr
            if name in info.methods or name in inherited_methods:
                continue
            if name in info.attr_assign:
                continue    # self.handler = fn; self.handler()
            where = "%s:%d" % (rel, node.lineno)
            if resolved_unknown:
                unknown_calls.setdefault(name, where)
            else:
                report.fail(where, "%s 上调用了不存在的 self.%s()" % (info.name, name))
        if resolved_unknown and (unknown_reads or unknown_calls):
            names = sorted(set(unknown_reads) | set(unknown_calls))
            shown = "、".join(names[:5])
            if len(names) > 5:
                shown += " 等 %d 项" % len(names)
            report.warn("%s" % rel,
                        "%s 继承自 %s，取不到它们的源码，%d 个成员未做核查：%s"
                        % (info.name, "、".join(resolved_unknown), len(names), shown))

    # --- 5) 用了没定义的名字 --------------------------------------------------
    def walk_fn(fn, enclosing):
        visible = enclosing | local_names(fn)
        for node in ast.walk(fn):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in visible or node.id in module_names:
                    continue
                report.fail("%s:%d" % (rel, node.lineno),
                            "%r 在 %s() 里使用了但没有定义" % (node.id, fn.name))
        for inner in ast.walk(fn):
            if inner is fn:
                continue
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                walk_fn(inner, visible)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            walk_fn(node, set())
        elif isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk_fn(item, set())

    # --- 6) 只供人看一眼的 ----------------------------------------------------
    if "except:" in src:
        report.note(rel, "存在裸 except:")


SELF_TEST_CASES = [
    # (名字, 源码, 期望抓到的问题里必须出现的片段)
    ("方法缩进跑偏，类上少了这个方法",
     "class Panel:\n"
     "    def handle(self):\n"
     "        return 1\n"
     "\n"
     "def handle(self):\n"
     "    return 0\n",
     "缩进跑偏"),
    ("读到从未赋值的属性",
     "class Store:\n"
     "    def __init__(self):\n"
     "        self.data = {}\n"
     "\n"
     "    def get(self, key):\n"
     "        return self.data.get(key) or self.fallback\n",
     "Store.fallback"),
    ("用了没定义的名字",
     "def render(rows):\n"
     "    out = []\n"
     "    for row in rows:\n"
     "        out.append(fmt(row))\n"
     "    return out\n",
     "fmt"),
    ("同类里方法重名",
     "class Api:\n"
     "    def go(self):\n"
     "        return 1\n"
     "\n"
     "    def go(self):\n"
     "        return 2\n",
     "定义了多次"),
    ("调用了不存在的方法",
     "class Api:\n"
     "    def run(self):\n"
     "        return self.validate()\n",
     "self.validate()"),
    # 这一条必须「不报」：只会误报的闸门一样不能用
    ("干净的类不能被误报",
     "class Good:\n"
     "    def __init__(self):\n"
     "        self.value = 1\n"
     "\n"
     "    def read(self):\n"
     "        return self.value\n",
     ""),
]


def self_test():
    """用几个已知有问题的片段检验本脚本还能抓到问题。

    闸门最坏的形态不是「漏报」，是「改过之后什么都不报、但一直是绿的」——
    看起来和通过一模一样。所以给它配一组变异样本：五个必须抓到、一个必须不报。
    这些样本在临时目录里现写现删，不进仓库。
    """
    G.setup_stdio()
    import tempfile
    workdir = tempfile.mkdtemp(prefix="f2b-structure-selftest-")
    failures = 0
    try:
        for index, (title, source, expect) in enumerate(SELF_TEST_CASES, 1):
            path = os.path.join(workdir, "case%d.py" % index)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(source)
            report = G.Report("自测用例 %d" % index)
            # 自测时不要把每个用例的逐条 FAIL 喷到屏幕上，只看结论
            report.fail = lambda where, message, _r=report: _r.failures.append(
                "%s: %s" % (where, message))
            analyse(path, report)
            caught = " | ".join(report.failures)
            if expect:
                ok = expect in caught
            else:
                ok = not report.failures
            print("%s  用例 %d：%s" % ("ok  " if ok else "FAIL", index, title))
            if not ok:
                failures += 1
                print("       期望包含 %r，实际：%s" % (expect or "（不该有报告）",
                                                       caught or "（什么都没报）"))
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    print("")
    if failures:
        print("%d 个自测用例未按预期工作 —— 闸门本身需要修" % failures)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS（%d 个用例）" % len(SELF_TEST_CASES))
    return 0


def target_files(args):
    if not args:
        return G.walk_files(G.BIN, [".py"])
    out = []
    for arg in args:
        path = os.path.abspath(arg)
        if os.path.isdir(path):
            out.extend(G.walk_files(path, [".py"]))
        elif os.path.isfile(path):
            out.append(path)
        else:
            raise SystemExit("找不到：%s" % arg)
    return sorted(set(out))


def main(argv):
    G.setup_stdio()
    if "--self-test" in argv:
        return self_test()
    files = target_files(argv)
    if not files:
        print("没有可检查的模块（%s）" % G.rel(G.BIN))
        print("RESULT: FAIL")
        return 1
    report = G.Report("结构检查")
    for path in files:
        analyse(path, report)
    print("检查了 %d 个模块" % len(files))
    return report.finish()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))