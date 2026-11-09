#!/usr/bin/env python3
"""Rebuild fn-fail2ban.fpk from the extracted project tree.

Why the build runs on the NAS:
  * fnpack exists only on fnOS; a Windows-side repack loses the exec bit on
    cmd/* and fnOS then refuses the package with code 10111.
  * The tree below is byte-identical to what is installed on the production
    box (verified by sha256 against /var/apps/fn-fail2ban and
    /vol1/@appcenter/fn-fail2ban), so a rebuild reproduces the running app.

Usage:  python tools/build_fpk.py [--host fnos-test]
"""
import argparse
import os
import subprocess
import tarfile
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_NAME = "fn-fail2ban"
SRC = os.path.join(ROOT, "src", "pkg", PKG_NAME)
OUT = os.path.join(ROOT, "output")

SKIP_DIRS = {"__pycache__", ".pytest_cache", ".git"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".orig", ".rej"}


def manifest_field(key, default=""):
    with open(os.path.join(SRC, "manifest"), encoding="utf-8") as fh:
        for line in fh:
            if line.strip().startswith(key):
                return line.split("=", 1)[1].strip()
    return default


def _skip(ti):
    norm = ti.name.replace("\\", "/")
    if any(part in SKIP_DIRS for part in norm.split("/")):
        return None
    if os.path.splitext(norm)[1] in SKIP_EXT:
        return None
    # 以 _ 开头的散落文件是临时产物（调试输出、扫描结果），不应进入安装包
    if os.path.basename(norm).startswith("_"):
        return None
    return ti


def check_tree():
    """打包前拒绝明显不该出货的文件。

    曾经把英文残留扫描的输出（_missed.json 等）打进过安装包：它们以 _ 开头、
    放在 app/ 根目录，tar 的过滤规则当时只按扩展名筛，于是照样进了 app.tgz。
    这里做一次显式检查，命中就中止构建，而不是等打包完才发现。
    """
    bad = []
    for root, dirs, files in os.walk(SRC):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            if name.startswith("_"):
                bad.append(os.path.join(root, name))
            elif os.path.splitext(name)[1] in SKIP_EXT:
                bad.append(os.path.join(root, name))
    if bad:
        raise SystemExit("拒绝打包以下文件：\n  " + "\n  ".join(bad))


def run(argv):
    proc = subprocess.run(argv, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("command failed: %s\n%s" % (" ".join(argv), proc.stderr))
    return proc.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="fnos-test")
    ap.add_argument("--workdir", default="/tmp/f2b-rebuild")
    args = ap.parse_args()

    check_tree()
    version = manifest_field("version", "0.0.0")
    platform = manifest_field("platform", "all")
    os.makedirs(OUT, exist_ok=True)

    tar_local = os.path.join(tempfile.gettempdir(), "%s-src.tar.gz" % PKG_NAME)
    with tarfile.open(tar_local, "w:gz") as tf:
        tf.add(SRC, arcname=PKG_NAME, filter=_skip)

    remote_tar = "/tmp/%s-src.tar.gz" % PKG_NAME
    run(["scp", "-o", "BatchMode=yes", tar_local, "%s:%s" % (args.host, remote_tar)])

    # cmd/* must stay 0755 (fnOS runs them directly); everything else 0644.
    script = (
        "set -e; rm -rf {wd}; mkdir -p {wd}; tar -xzf {tar} -C {wd}; cd {wd}/{app}; "
        "find . -type d -exec chmod 755 {{}} \\;; "
        "find . -type f -exec chmod 644 {{}} \\;; "
        "chmod 755 cmd/*; chmod 755 app/bin/server.py; "
        "fnpack build; ls -la {app}.fpk"
    ).format(wd=args.workdir, tar=remote_tar, app=PKG_NAME)
    print(run(["ssh", "-o", "BatchMode=yes", args.host, script]))

    out_path = os.path.join(OUT, "%s-%s-%s.fpk" % (PKG_NAME, version, platform))
    run(["scp", "-o", "BatchMode=yes",
         "%s:%s/%s/%s.fpk" % (args.host, args.workdir, PKG_NAME, PKG_NAME), out_path])
    print("built: %s (%d bytes)" % (out_path, os.path.getsize(out_path)))


if __name__ == "__main__":
    main()
