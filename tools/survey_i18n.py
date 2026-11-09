import re, os, collections
p = r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/src/pkg/fn-fail2ban/app/web/app.js"
s = open(p, encoding="utf-8").read()
# all CJK-bearing string literals give a rough measure of how much needs translating
lits = re.findall(r"'([^'\\\n]*[\u4e00-\u9fff][^'\\\n]*)'", s)
lits += re.findall(r'"([^"\\\n]*[\u4e00-\u9fff][^"\\\n]*)"', s)
uniq = sorted(set(lits))
print("js  lines:", s.count(chr(10)))
print("CJK string literals:", len(lits), "| unique:", len(uniq))
print("with interpolated vars:", sum(1 for x in uniq if "+" in x))
for x in uniq[:12]:
    print("   ", x[:70])
html = open(r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/src/pkg/fn-fail2ban/app/web/index.html", encoding="utf-8").read()
hl = re.findall(r">([^<>]*[\u4e00-\u9fff][^<>]*)<", html)
print("html CJK text nodes:", len(set(x.strip() for x in hl)))
