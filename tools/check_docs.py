#!/usr/bin/env python3
"""文档闸门：所有 *.md 里指向本地文件的链接与图片必须解析得到。

只在网页上才暴露的问题：Markdown 的相对路径是按「文档所在目录」解析的，
写在 docs/DEVELOPMENT.md 里的 images/x.png 指的是 docs/images/x.png，
不是仓库根下的 images/x.png。本地编辑器会「帮你找图」，预览一切正常，
推到 GitHub 之后整篇文档全是 404。

检查范围：README / README_EN / CHANGELOG / docs/ / report/ 下的中文文档。
仓外链接（http、mailto、以 / 开头的站点路径）与锚点一律跳过。

用法： python tools/check_docs.py
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

SKIP_DIRS = {".git", "__pycache__", "node_modules", "output", "src", "tools", "tmp"}
LINK = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HTML_REF = re.compile(r'<img[^>]+src="([^"]+)"')
# 围栏代码块与行内代码里的内容不参与检查：那里的路径是示例或代码片段，
# 例如表格里写的 <img src="logo.svg"> 只是在描述某段 HTML，不是真的引用文件。
# 反引号用 chr(96) 拼出来，避免正则里出现一串转义后读不懂的符号。
_TICK = chr(96)
FENCE = re.compile(_TICK * 3 + ".*?" + _TICK * 3, re.S)
INLINE_CODE = re.compile("(?<!" + _TICK + ")" + _TICK + "[^" + _TICK + "\n]+" + _TICK + "(?!" + _TICK + ")")


def markdown_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(G.ROOT):
        rel_dir = os.path.relpath(dirpath, G.ROOT).replace("\\", "/")
        top = rel_dir.split("/")[0]
        if top in SKIP_DIRS:
            dirnames[:] = []
            continue
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            if name.endswith(".md"):
                out.append(os.path.join(dirpath, name))
    return out


def check(path, report):
    rel = G.rel(path)
    text = G.read_text(path)
    text = FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    text = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), text)
    base = os.path.dirname(path)
    refs = LINK.findall(text) + HTML_REF.findall(text)
    seen = set()
    for ref in refs:
        ref = ref.strip()
        if ref in seen:
            continue
        seen.add(ref)
        if ref.startswith(("http://", "https://", "mailto:", "#", "data:", "//", "/")):
            continue
        target = ref.split("#", 1)[0]
        if not target:
            continue
        resolved = os.path.normpath(os.path.join(base, target.replace("%20", " ")))
        if not os.path.exists(resolved):
            report.fail(rel, "链接指向的文件不存在：%s" % ref)
    return len(seen)


def main():
    G.setup_stdio()
    report = G.Report("文档检查")
    files = markdown_files()
    if not files:
        report.fail("docs", "没有找到任何 .md 文档")
    total = 0
    for path in files:
        total += check(path, report)
    print("检查了 %d 篇文档，%d 个本地引用" % (len(files), total))
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())