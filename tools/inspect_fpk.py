#!/usr/bin/env python3
"""解包自检：不安装就能看出这个 .fpk 装上去会不会出事。

历史上真正让安装失败或功能残缺的问题，都在包结构里，而打包命令本身是成功返回的：

  * cmd/* 丢了可执行位 —— 安装报 10111；
  * 九档图标缺档 —— 应用中心某个位置显示空白，不报任何错；
  * __pycache__ / *.pyc / 临时扫描结果被打进去 —— 体积变大，且会把开发机的路径带上；
  * manifest 里的 checksum 与 app.tgz 实际 md5 不一致 —— 安装器拒绝，但报错信息很含糊；
  * 包里的版本和源码 manifest 不一致 —— 典型原因是改了源码忘了重新打包；
  * 生命周期脚本与官方 1.7.0 出现计划外的字节差异 —— 那正是「原地升级不改配置」的保证。

用法：

    python tools/inspect_fpk.py                    # 自动取 output/ 里最新的包
    python tools/inspect_fpk.py output/xxx.fpk
    python tools/inspect_fpk.py xxx.fpk --strict   # 警告也算失败
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import sys
import tarfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

ICON_SIZES = [16, 24, 48, 64, 72, 96, 128, 256, 512]
REQUIRED_OUTER = ["manifest", "app.tgz", "config/resource", "config/privilege",
                  "ICON.PNG", "ICON_256.PNG"]
REQUIRED_INNER = ["ui/config", "web/index.html"]
REQUIRED_SCRIPTS = ["cmd/main", "cmd/install_init", "cmd/install_callback",
                    "cmd/config_init", "cmd/config_callback",
                    "cmd/upgrade_init", "cmd/upgrade_callback",
                    "cmd/uninstall_init", "cmd/uninstall_callback"]
CRUD_SUFFIXES = (".pyc", ".pyo", ".log", ".orig", ".rej", ".DS_Store")
BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "baseline-files.json")


def human(size):
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return "%.1f %s" % (size, unit) if unit != "B" else "%d B" % size
        size /= 1024.0


def md5(data):
    return hashlib.md5(data).hexdigest()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def manifest_fields(raw):
    fields = {}
    for line in raw.decode("utf-8", "replace").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, value = line.split("=", 1)
            fields[key.strip()] = value.strip()
    return fields


def newest_fpk():
    found = sorted(glob.glob(os.path.join(G.ROOT, "output", "*.fpk")),
                   key=os.path.getmtime, reverse=True)
    return found[0] if found else ""


def main(argv=None):
    G.setup_stdio()
    parser = argparse.ArgumentParser(description="检查一个已打好的 fnOS 安装包")
    parser.add_argument("path", nargs="?", default=None, help="fpk 路径（默认取 output/ 最新）")
    parser.add_argument("--strict", action="store_true", help="警告也判失败")
    parser.add_argument("--no-baseline", action="store_true", help="跳过与 1.7.0 的脚本比对")
    args = parser.parse_args(argv)

    path = args.path or newest_fpk()
    if not path or not os.path.isfile(path):
        print("找不到安装包（先构建，或者显式给出路径）")
        print("RESULT: FAIL")
        return 1
    path = os.path.abspath(path)
    report = G.Report("安装包检查")
    raw = open(path, "rb").read()
    print("=== %s（%s，md5 %s）===" % (os.path.basename(path), human(len(raw)), md5(raw)))

    try:
        outer = tarfile.open(fileobj=io.BytesIO(raw))
        members = {m.name: m for m in outer.getmembers()}
    except tarfile.TarError as exc:
        print("不是有效的 tar 包：%s" % exc)
        print("RESULT: FAIL")
        return 1

    for name in REQUIRED_OUTER:
        if name not in members:
            report.fail(name, "缺少必需成员")
    for name in REQUIRED_SCRIPTS:
        if name not in members:
            report.fail(name, "缺少生命周期脚本")

    noexec = [n for n, m in sorted(members.items())
              if n.startswith("cmd/") and m.isfile() and not (m.mode & 0o111)]
    for name in noexec:
        report.fail(name, "没有可执行位 —— 安装会报 10111")
    if not noexec:
        count = len([n for n in members if n.startswith("cmd/")])
        print("  cmd/* 可执行位：%d 个脚本全部 0755" % count)

    # fnpack 要求外层图标固定尺寸；尺寸对不上时安装器不一定报错，
    # 但应用中心会按 64/256 去缩放，图标看起来发糊。
    for name, want, colour in (("ICON.PNG", 64, "RGB"), ("ICON_256.PNG", 256, "RGB")):
        member = members.get(name)
        if member is None:
            continue
        head = outer.extractfile(member).read(33)
        if len(head) < 26 or head[:8] != b"\x89PNG\r\n\x1a\n":
            report.fail(name, "不是有效的 PNG")
            continue
        width = int.from_bytes(head[16:20], "big")
        height = int.from_bytes(head[20:24], "big")
        if (width, height) != (want, want):
            report.fail(name, "尺寸是 %dx%d，fnpack 要求 %dx%d"
                        % (width, height, want, want))
        else:
            print("  %s：%dx%d（%s）" % (name, width, height, colour))

    manifest = {}
    if "manifest" in members:
        manifest = manifest_fields(outer.extractfile("manifest").read())
        version = manifest.get("version", "")
        print("  manifest：%s %s / platform %s / service_port %s"
              % (manifest.get("appname", "?"), version or "（缺 version）",
                 manifest.get("platform", "?"), manifest.get("service_port", "?")))
        if not re.match(r"^\d{2}\.\d{1,2}\.\d+$", version):
            report.fail("manifest", "version 不是「年.月.序号」格式：%r" % version)
        for key in ("appname", "display_name", "platform", "checksum"):
            if not manifest.get(key):
                report.fail("manifest", "缺少字段 %s" % key)

    if "app.tgz" in members:
        inner_raw = outer.extractfile("app.tgz").read()
        packed_md5 = md5(inner_raw)
        if manifest.get("checksum") and manifest["checksum"] != packed_md5:
            report.fail("manifest", "checksum %s 与 app.tgz 实际 md5 %s 不一致 —— "
                                    "安装器会拒绝这个包"
                        % (manifest["checksum"][:12], packed_md5[:12]))
        else:
            print("  app.tgz md5 与 manifest 的 checksum 一致（%s）" % packed_md5[:12])

        inner = tarfile.open(fileobj=io.BytesIO(inner_raw))
        imembers = {m.name: m for m in inner.getmembers()}
        print("  app.tgz：%d 个成员，解包 %s" % (len(imembers), human(len(inner_raw))))

        for name in REQUIRED_INNER:
            if name not in imembers:
                report.fail("app.tgz", "缺少 %s" % name)

        server = imembers.get("bin/server.py")
        if server is None:
            report.fail("app.tgz", "缺少 bin/server.py（面板主程序）")
        elif not (server.mode & 0o111):
            report.warn("bin/server.py", "没有可执行位（cmd/main 用 python3 调用，通常不影响，"
                                        "但与既有包不一致）")

        uicfg = imembers.get("ui/config")
        if uicfg is not None:
            text = inner.extractfile(uicfg).read().decode("utf-8", "replace")
            template = re.search(r'"icon"\s*:\s*"([^"]+)"', text)
            if not template:
                report.fail("ui/config", "没有 icon 字段")
            else:
                tpl = template.group(1)
                missing = [size for size in ICON_SIZES
                           if "ui/" + tpl.replace("{0}", str(size)) not in imembers]
                if missing:
                    report.fail("ui/images", "缺 %s 档图标（应用中心对应位置会显示空白）"
                                % missing)
                else:
                    print("  图标：%s 九档齐全" % tpl)
            launch = re.search(r'"([\w.\-]+\.Application)"', text)
            if launch:
                expected = manifest.get("desktop_applaunchname", "")
                if expected and expected != launch.group(1):
                    report.fail("ui/config", "桌面入口 %r 与 manifest 的 %r 不一致"
                                % (launch.group(1), expected))

        crud = [n for n in imembers
                if "__pycache__" in n or n.endswith(CRUD_SUFFIXES)]
        if crud:
            report.fail("app.tgz", "打进了不该出货的文件：%s" % crud[:6])
        else:
            print("  包内无 __pycache__ / .pyc / .log 等临时文件")

        # 包里的版本号与源码 manifest 必须一致（典型症状：改了源码忘了重新打包）
        src_manifest = os.path.join(G.PKG, "manifest")
        if os.path.isfile(src_manifest):
            src = manifest_fields(open(src_manifest, "rb").read())
            if src.get("version") != manifest.get("version"):
                report.fail("manifest", "包内版本 %s 与源码 manifest %s 不一致 —— 包是旧的"
                            % (manifest.get("version"), src.get("version")))
            else:
                print("  包内版本与源码 manifest 一致：%s" % manifest.get("version"))

        # 生命周期脚本与官方 1.7.0 的逐字节比对
        if not args.no_baseline and os.path.isfile(BASELINE):
            with open(BASELINE, encoding="utf-8") as fh:
                base = json.load(fh)
            expected = base.get("expected_diff", {})
            for name, digest in sorted(base.get("files", {}).items()):
                member = members.get(name)
                if member is None:
                    report.fail(name, "基线里有的文件在包里不见了")
                    continue
                data = outer.extractfile(member).read()
                actual = sha256(data)
                if actual == digest:
                    continue
                norm = sha256(data.replace(b"\r\n", b"\n"))
                if norm == digest:
                    continue    # 只差行尾：fnOS 安装时会归一化，不算改动
                if name in expected:
                    print("  %s 与 1.7.0 有已知差异（%s）" % (name, expected[name]))
                else:
                    report.fail(name, "与 1.7.0 官方包不一致，且不在已知差异名单里 —— "
                                      "这会影响原地升级，必须是有意改动才能接受")
            if "output" not in path:
                pass

    return report.finish(strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())