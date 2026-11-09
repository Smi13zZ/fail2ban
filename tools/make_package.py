#!/usr/bin/env python3
"""在 Linux 上把 src/pkg/fn-fail2ban 打成 .fpk（CI 与 fnos-test 都用这条）。

为什么打包必须在 Linux 上：fnOS 要求 cmd/ 下的生命周期脚本带可执行位，
Windows 上没有这个概念，从 Windows 打出来的包安装时报 10111。所以这里只做
「在符合条件的环境里一件件摆好再交给 fnpack」，本身不做任何平台相关的魔改。

与 tools/build_fpk.py 的分工：
  * build_fpk.py 是「Windows 开发机 → scp 到 fnos-test → 那边打包 → scp 回来」，
    本机没有 Linux 时用它；
  * 本脚本是「已经在一台 Linux 上」，把打包前后的固定动作（清干净、摆权限、
    校验图标、命名、解包自检）固化成一条命令，CI 与测试机跑的是同一份逻辑。

用法：

    python tools/make_package.py                 # 打包 + 自检，产物落到 output/
    python tools/make_package.py --stage-only    # 只做打包目录，不调用 fnpack（本地预习）
    python tools/make_package.py --skip-gates    # 跳过结构/i18n 闸门（不推荐）

没有 fnpack 时脚本会明确报出来并回退到 --stage-only 的行为，不会假装成功。
"""
import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

FNPACK_HINT = ("没有找到 fnpack。它是 fnOS 官方打包工具，只提供 Linux 版本：\n"
               "  curl -fsSL -o /tmp/fnpack https://static2.fnnas.com/fnpack/fnpack-1.2.3-linux-amd64\n"
               "  chmod +x /tmp/fnpack && sudo mv /tmp/fnpack /usr/local/bin/fnpack\n"
               "Windows 开发机上没有它属正常，改用 tools/build_fpk.py --host fnos-test 打包。")

PY = sys.executable


def manifest_fields():
    fields = {}
    with open(os.path.join(G.PKG, "manifest"), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                fields[key.strip()] = value.strip()
    return fields


def run(argv, cwd=None):
    print("  $ " + " ".join(argv))
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        print(proc.stdout[-4000:])
        print(proc.stderr[-4000:])
        raise SystemExit("命令失败（退出码 %d）：%s" % (proc.returncode, " ".join(argv)))
    return proc.stdout


def png_size(path):
    with open(path, "rb") as fh:
        head = fh.read(26)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit("%s 不是 PNG" % path)
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


def stage(dest):
    """把源码树摆成 fnpack 需要的样子。"""
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(G.PKG, dest)

    # 1) 清掉不该出货的东西
    for dirpath, dirnames, filenames in os.walk(dest):
        for name in list(dirnames):
            if name == "__pycache__":
                shutil.rmtree(os.path.join(dirpath, name))
                dirnames.remove(name)
        for name in filenames:
            if name.endswith((".pyc", ".pyo", ".log", ".orig", ".rej")) or name.startswith("_"):
                os.remove(os.path.join(dirpath, name))

    # 2) 权限：目录 755、文件 644，再把该可执行的挑出来
    for dirpath, dirnames, filenames in os.walk(dest):
        os.chmod(dirpath, 0o755)
        for name in filenames:
            os.chmod(os.path.join(dirpath, name), 0o644)
    for name in sorted(os.listdir(os.path.join(dest, "cmd"))):
        os.chmod(os.path.join(dest, "cmd", name), 0o755)
    os.chmod(os.path.join(dest, "app", "bin", "server.py"), 0o755)

    # 3) 外层图标的尺寸是 fnpack 的硬要求，尺寸不对安装器不一定报错，
    #    但应用中心会按 64/256 硬缩，图看起来是糊的
    for name, want in (("ICON.PNG", 64), ("ICON_256.PNG", 256)):
        path = os.path.join(dest, name)
        if not os.path.isfile(path):
            raise SystemExit("缺少 %s（fnpack 必需）" % name)
        width, height = png_size(path)
        if (width, height) != (want, want):
            raise SystemExit("%s 是 %dx%d，fnpack 要求 %dx%d" % (name, width, height, want, want))
        print("  ok  %s %dx%d" % (name, width, height))

    # 4) 九档图标缺一档，应用中心对应位置就是空白
    sizes = [16, 24, 48, 64, 72, 96, 128, 256, 512]
    missing = [s for s in sizes
               if not os.path.isfile(os.path.join(dest, "app", "ui", "images", "icon_%d.png" % s))]
    if missing:
        raise SystemExit("app/ui/images 缺 %s 档图标" % missing)
    print("  ok  app/ui/images 九档齐全")
    return dest


def gates():
    for name, argv in (
        ("结构检查", [PY, os.path.join(G.ROOT, "tools", "check_structure.py")]),
        ("i18n 字典", [PY, os.path.join(G.ROOT, "tools", "check_i18n.py")]),
        ("CSS 与资源", [PY, os.path.join(G.ROOT, "tools", "check_css.py")]),
    ):
        print("== 闸门：" + name)
        run(argv)


def main(argv=None):
    G.setup_stdio()
    parser = argparse.ArgumentParser(description="在 Linux 上打包 fnOS 安装包")
    parser.add_argument("--stage-only", action="store_true", help="只准备打包目录，不调用 fnpack")
    parser.add_argument("--skip-gates", action="store_true", help="跳过打包前的闸门")
    parser.add_argument("--keep-stage", action="store_true", help="保留打包目录便于排查")
    parser.add_argument("--out", default=os.path.join(G.ROOT, "output"), help="产物目录")
    args = parser.parse_args(argv)

    fields = manifest_fields()
    version = fields.get("version", "")
    platform = fields.get("platform", "all")
    appname = fields.get("appname", "fn-fail2ban")
    if not version:
        raise SystemExit("manifest 里没有 version")
    print("打包 %s %s（platform=%s）" % (appname, version, platform))

    # 与 build_fpk.py 的 check_tree() 同一条规则：以 _ 开头的散落文件和 .pyc/.log 这类
    # 临时产物不许出货。这里是「中止并指名道姓」，不是悄悄过滤掉 —— 悄悄过滤会让
    # 开发者以为文件进包了，实际上没有。
    blockers = G.ship_blockers(G.PKG)
    if blockers:
        print("拒绝打包以下文件（以 _ 开头或属于临时产物）：")
        for path in blockers:
            print("  " + G.rel(path))
        print("确认它们不是误放的临时文件；确实是产物的一部分就改名。")
        return 1

    if not args.skip_gates:
        gates()

    fnpack = shutil.which("fnpack")
    if not fnpack and not args.stage_only:
        print(FNPACK_HINT)
        print("")
        print("已回退到「只准备打包目录」，没有产出安装包。")
        args.stage_only = True

    workdir = tempfile.mkdtemp(prefix="f2b-build-")
    try:
        dest = stage(os.path.join(workdir, appname))
        if args.stage_only:
            print("打包目录：%s" % dest)
            return 0

        print("== fnpack build")
        run([fnpack, "build"], cwd=dest)
        built = os.path.join(dest, appname + ".fpk")
        if not os.path.isfile(built):
            built = next(iter(glob.glob(os.path.join(dest, "*.fpk"))), "")
        if not built:
            raise SystemExit("fnpack 报告成功但目录里没有 .fpk")

        os.makedirs(args.out, exist_ok=True)
        final = os.path.join(args.out, "%s-%s-%s.fpk" % (appname, version, platform))
        shutil.copy2(built, final)
        print("")
        print("产物：%s（%d 字节）" % (G.rel(final), os.path.getsize(final)))

        print("")
        print("== 解包自检")
        run([PY, os.path.join(G.ROOT, "tools", "inspect_fpk.py"), final, "--strict"])
        return 0
    finally:
        if args.keep_stage:
            print("保留打包目录：%s" % workdir)
        else:
            shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())