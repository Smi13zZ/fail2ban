#!/usr/bin/env python3
"""i18n 闸门：词条的值必须真的能用，中英两本字典必须一一对应。

i18n 出问题时的表现都非常安静 —— 没有任何一处会报错，只有人眼在设备上翻一遍
才看得见：

  * t('nav.xxx') 查不到 key，页面直接把 key 名印出来（切到英文才看得到）；
  * 某条 en 词条的值写成 undefined / null，键在、值是空的，页面印的还是 key 名；
  * 某条 en 词条直接抄了中文，英文界面里冒出一句中文；
  * 少一条 en 词条，那一行永远是中文；
  * 英文漏写占位符，变量被吞掉。

所以逐条查：

  1. zh / en 两本字典的 key 集合完全相同；
  2. 同一本书里 key 重复（后一个静默覆盖前一个）；
  3. 每条词条的值必须是「非空的字符串字面量」—— 值是 undefined / null / 空串 /
     数字 / 对象 / 数组一律失败，打印 key 与行号；
  4. en 词条的值与 zh 同 key 的值逐字相同、且 zh 里确实是中文，判失败（漏翻）；
  5. app.js / index.html 里以字面量引用的 key 都能在字典里查到；
  6. 两本书里同 key 的占位符集合一致。

用法：

    python tools/check_i18n.py                 # 查仓库里的 i18n.js
    python tools/check_i18n.py 别的/i18n.js     # 指定文件（变异测试时用）
    python tools/check_i18n.py --self-test     # 用变异样本检验本闸门还能抓到问题
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _gatelib as G  # noqa: E402

I18N = os.path.join(G.WEB, "i18n.js")
APP_JS = os.path.join(G.WEB, "app.js")
INDEX = os.path.join(G.WEB, "index.html")

# 词条行：四个空格缩进 + "key" + 冒号，其余是值。
# 必须带 re.M：否则 ^ 只匹配整个字典块的第一个字符，一条也匹配不到，
# 于是所有检查都「通过」—— 这正是这道闸门本来要防的那种假绿。
KEY_LINE = re.compile(r'^\s{4}"((?:[^"\\]|\\.)*)"\s*:\s*(.*)$', re.M)
PLACEHOLDER = re.compile(r"\{(\w+)\}")

# 值必须是被引号包起来的单行字面量。JS 里值写成 undefined、null、123、{}、[...]
# 或拼一段表达式都不合格：要么让页面印出 key 名，要么让 t() 走「查不到」分支。
STRING_LITERAL = re.compile(r'^(?:"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\')$')

# 值「与中文逐字相同」时可接受的技术术语。判断方式见 same_as_zh_is_benign()：
# 去掉占位符、符号、数字之后，剩下的词全在这个集合里，说明整条本来就是
# 「术语 + 符号」，中英文相同是对的，不算漏翻。
BENIGN_TERMS = {
    "jail", "jails", "ban", "bans", "unban", "unbans", "banned",
    "filter", "filters", "bantime", "findtime", "maxretry", "ignoreip",
    "ip", "ips", "ipv4", "ipv6", "cidr", "port", "url", "uri", "api",
    "http", "https", "ssh", "sshd", "ftp", "smtp", "json", "csv", "ui",
    "fail2ban", "fnknock", "knock", "nftables", "iptables",
    "log", "logs", "logpath", "action", "backend", "systemd", "python",
    "nas", "fnos", "ok", "on", "off", "id", "uid", "gid", "pid",
}

# 与中文逐字相同但已知合理、又不属于「纯术语」判定的词条。
# 每一条都必须写原因；不加原因的放行等于把闸门关掉。
SAME_AS_ZH_ALLOWED = {}

# 值为空串但有意为之的词条。默认空串视为「等于没翻译」，只有这里列出的放行。
# 它们都是「句子片段」：中文靠量词和标点把碎片粘起来，英文不需要，所以英文侧
# 就该是空的。放行不等于不管 —— 运行时仍会打进 NOTE 列表，改坏了看得见。
EMPTY_ALLOWED = {
    "en:filter.usedBySuffix": "句子片段：英文是 Used by + 名称，没有后缀",
    "en:unit.countSuffix": "句子片段：中文用量词「个」，英文的数字后面不加量词",
    "en:fnk.comma": "句子片段：中文用「，」，英文用空格分隔",
}

CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


def line_of(src, offset):
    return src.count("\n", 0, offset) + 1


def block_span(src, lang):
    """定位 DICT 里某个语言的字典块，返回 (start, end) 或 None。

    按「顶层标记行 + 花括号配平」定位：值里会出现花括号（比如内嵌的 HTML 片段），
    所以不能靠正则一路贪到结尾，必须数括号、并且跳过字符串里的花括号。
    """
    marker = re.search(r"^\s{4}%s:\s*\{" % lang, src, re.M)
    if not marker:
        return None
    start = marker.end() - 1      # 指向左花括号
    depth, i, in_str, escape = 0, start, None, False
    while i < len(src):
        ch = src[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return (start, i + 1)
        i += 1
    return None


def parse(src, span):
    """-> [(key, 原始值文本, 行号)]，保留重复项以便暴露覆盖问题。"""
    rows = []
    start, end = span
    block = src[start:end]
    for match in KEY_LINE.finditer(block):
        raw = match.group(2).strip()
        if raw.endswith(","):
            raw = raw[:-1].rstrip()
        rows.append((match.group(1), raw, line_of(src, start + match.start())))
    return rows


def unquote(raw):
    """把字符串字面量取成内容；不是字面量时返回 None。"""
    if not STRING_LITERAL.match(raw):
        return None
    body = raw[1:-1]
    # 只还原影响判定的转义；判定只关心「有没有中文」
    return body.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


def same_as_zh_is_benign(value):
    """这条「与中文逐字相同」的值是不是本来就该一样。

    做法是「把不该翻译的东西全部抠掉，看还剩不剩中文」：先去掉占位符，再整词去掉
    技术术语（jail / ban / filter… 这些本来就没有中文说法），剩下的中文才是真正
    漏翻的部分。

    注意不能反过来写「一个 ASCII 词都没有就放行」—— 一句纯中文的漏翻恰恰没有
    ASCII 词，那样写会把最典型的漏翻放过去。
    """
    stripped = PLACEHOLDER.sub(" ", value)
    for term in sorted(BENIGN_TERMS, key=len, reverse=True):
        stripped = re.sub(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])",
                          " ", stripped, flags=re.IGNORECASE)
    return not CJK.search(stripped)


def check_values(report, rel, lang, rows):
    """每条词条的值必须是非空字符串字面量。"""
    for key, raw, line in rows:
        where = "%s:%d" % (rel, line)
        if not raw:
            report.fail(where, "%s 的 %r 没有值" % (lang, key))
            continue
        value = unquote(raw)
        if value is None:
            report.fail(where, "%s 的 %r 不是字符串字面量（写成了 %s）—— "
                               "值不是字符串时，取词条会退化成把 key 名印在页面上"
                        % (lang, key, raw[:40]))
            continue
        if not value.strip():
            reason = EMPTY_ALLOWED.get("%s:%s" % (lang, key))
            if reason:
                report.note(where, "%s 的 %r 是空串，已知有意为之：%s" % (lang, key, reason))
            else:
                report.fail(where, "%s 的 %r 是空串，等于没翻译" % (lang, key))


def check_untranslated(report, rel, zh_rows, en_rows):
    """en 与 zh 逐字相同、且 zh 确实是中文 —— 那是直接把中文抄过去了。"""
    zh = {key: raw for key, raw, _line in zh_rows}
    for key, raw, line in en_rows:
        if key not in zh or raw != zh[key]:
            continue
        value = unquote(raw)
        if value is None or not CJK.search(value):
            continue
        if key in SAME_AS_ZH_ALLOWED:
            continue
        if same_as_zh_is_benign(value):
            continue
        report.fail("%s:%d" % (rel, line),
                    "en 的 %r 与 zh 逐字相同且含中文，看起来是漏翻：%r"
                    % (key, value[:40]))


def check_references(report, known, app_js, index):
    """app.js / index.html 里以字面量引用的 key 必须存在。

    只查字面量：t(变量) 这种动态拼接查不出来，硬猜只会刷假警报。
    """
    used = set()
    for path in (app_js, index):
        if not path or not os.path.isfile(path):
            continue
        text = G.read_text(path)
        for match in re.finditer(r"""\bt\(\s*["']([A-Za-z][\w.]*)["']""", text):
            used.add((match.group(1), path, text.count("\n", 0, match.start()) + 1))
        for match in re.finditer(r"""\bdata-i18n=["']([A-Za-z][\w.]*)["']""", text):
            used.add((match.group(1), path, text.count("\n", 0, match.start()) + 1))
    for key, path, line in sorted({(k, p, l) for k, p, l in used if k not in known}):
        report.fail("%s:%d" % (G.rel(path), line), "引用了字典里没有的 key %r" % key)
    print("代码里引用的 key %d 个，字典 %d 条" % (len(used), len(known)))


# 两处细节都不能错，错一处自测就变成假绿：
#   1) 词条必须四个空格缩进 + 引号，与真实字典一致（八空格的话 KEY_LINE 匹配不到，
#      一个用例都不会跑，全绿）；
#   2) 值按「原始 JS 文本」传入，不在这里补引号 —— 补了引号，undefined 就变成
#      字符串 "undefined"，这条用例反而验不到东西。
SELF_TEST_TEMPLATE = '''    zh: {
    "t.same": %(zh)s,
    "t.ok": "正常词条",
    },
    en: {
    "t.same": %(en)s,
    "t.ok": "Normal entry",
    },
'''

# 变异样本：(名字, zh 值, en 值, 期望是否报错, 期望报错里出现的片段)
# 前四条对应真机踩过的形态；后三条是「必须不报」，用来防误报。
SELF_TEST_CASES = [
    ("en 值写成 undefined", '"检查更新失败"', "undefined", True, "不是字符串字面量"),
    ("en 值写成 null", '"检查更新失败"', "null", True, "不是字符串字面量"),
    ("en 值写成数字", '"检查更新失败"', "123", True, "不是字符串字面量"),
    ("en 值直接抄中文", '"检查更新失败"', '"检查更新失败"', True, "看起来是漏翻"),
    ("en 值抄中文但夹着术语", '"Jail 管理"', '"Jail 管理"', True, "看起来是漏翻"),
    ("术语 + 符号相同（不该报）", '"Jail / ban"', '"Jail / ban"', False, ""),
    ("纯术语相同（不该报）", '"Jail"', '"Jail"', False, ""),
    ("纯符号相同（不该报）", '"—"', '"—"', False, ""),
]


def self_test():
    """用几个已知形态的样本检验本闸门还能抓到问题。

    闸门最坏的形态不是漏报，是「改过之后什么都不报、但一直绿的」。所以给一组
    「必须抓到」与「必须不报」的样本，样本在临时目录里现写现删，不进仓库。
    """
    G.setup_stdio()
    import tempfile
    import shutil
    workdir = tempfile.mkdtemp(prefix="f2b-i18n-selftest-")
    failures = 0
    try:
        for index, (title, zh, en, should_fail, expect) in enumerate(SELF_TEST_CASES, 1):
            path = os.path.join(workdir, "case%d.js" % index)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("(function (global) {\n  var DICT = {\n")
                fh.write(SELF_TEST_TEMPLATE % {"zh": zh, "en": en})
                fh.write("  };\n})(window);\n")
            report = G.Report("自测用例 %d" % index)
            # 自测时不要把逐条 FAIL 喷到屏幕上，只看结论
            report.fail = lambda where, message, _r=report: _r.failures.append(
                "%s: %s" % (where, message))
            src = G.read_text(path)
            rows = {}
            for lang in ("zh", "en"):
                span = block_span(src, lang)
                rows[lang] = parse(src, span) if span else []
            for lang in ("zh", "en"):
                check_values(report, G.rel(path), lang, rows[lang])
            check_untranslated(report, G.rel(path), rows["zh"], rows["en"])
            caught = " | ".join(report.failures)
            ok = (expect in caught) if should_fail else not report.failures
            print("%s  用例 %d：%s" % ("ok  " if ok else "FAIL", index, title))
            if not ok:
                failures += 1
                print("       期望：%s；实际：%s"
                      % ("报错且包含 %r" % expect if should_fail else "不报错",
                         caught or "（什么都没报）"))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print("")
    if failures:
        print("%d 个自测用例未按预期工作 —— 闸门本身需要修" % failures)
        print("RESULT: FAIL")
        return 1
    print("RESULT: PASS（%d 个用例）" % len(SELF_TEST_CASES))
    return 0


def resolve_targets(argv):
    """给了显式路径就只查那个文件，其余检查项跳过。"""
    if not argv:
        return I18N, APP_JS, INDEX
    path = os.path.abspath(argv[0])
    if not os.path.isfile(path):
        raise SystemExit("找不到：%s" % argv[0])
    return path, "", ""


def main(argv=None):
    G.setup_stdio()
    argv = sys.argv[1:] if argv is None else argv
    if "--self-test" in argv:
        return self_test()
    target, app_js, index = resolve_targets(argv)
    rel = G.rel(target)

    report = G.Report("i18n 检查")
    if not os.path.isfile(target):
        print("找不到 %s" % rel)
        print("RESULT: FAIL")
        return 1
    src = G.read_text(target)

    tables = {}
    for lang in ("zh", "en"):
        span = block_span(src, lang)
        if span is None:
            report.fail(rel, "找不到 %s 字典块" % lang)
            continue
        rows = parse(src, span)
        seen = set()
        for key, _raw, line in rows:
            if key in seen:
                report.fail("%s:%d" % (rel, line),
                            "%s 里 key %r 出现了不止一次（后一个会静默覆盖前一个）"
                            % (lang, key))
            seen.add(key)
        tables[lang] = rows
        print("  %s 词条 %d 条" % (lang, len(rows)))
    if report.failures:
        return report.finish()

    for lang, rows in tables.items():
        check_values(report, rel, lang, rows)

    zh_rows, en_rows = tables["zh"], tables["en"]
    zh_keys = {key for key, _raw, _line in zh_rows}
    en_keys = {key for key, _raw, _line in en_rows}
    for key in sorted(zh_keys - en_keys):
        report.fail(rel, "en 缺少 %r" % key)
    for key in sorted(en_keys - zh_keys):
        report.fail(rel, "zh 缺少 %r" % key)

    check_untranslated(report, rel, zh_rows, en_rows)

    zh_values = {key: raw for key, raw, _line in zh_rows}
    en_values = {key: raw for key, raw, _line in en_rows}
    for key in sorted(zh_keys & en_keys):
        first = set(PLACEHOLDER.findall(zh_values[key]))
        second = set(PLACEHOLDER.findall(en_values[key]))
        if first != second:
            report.fail(rel, "%r 的占位符不一致：zh %s / en %s"
                        % (key, sorted(first) or "无", sorted(second) or "无"))

    check_references(report, zh_keys | en_keys, app_js, index)
    return report.finish()


if __name__ == "__main__":
    sys.exit(main())