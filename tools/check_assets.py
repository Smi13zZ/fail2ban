import os, re, sys
pkg = sys.argv[1]
css = open(os.path.join(pkg, "app/web/style.css"), encoding="utf-8").read()
print("braces:", css.count("{") - css.count("}"))
used = set(re.findall(r"var\(\s*(--[\w-]+)", css))
defined = set(re.findall(r"^\s*(--[\w-]+)\s*:", css, re.M))
print("missing vars:", sorted(used - defined) or "none")
web = os.path.join(pkg, "app/web")
html = open(os.path.join(web, "index.html"), encoding="utf-8").read()
for ref in re.findall(r'(?:href|src)="([^"#]+)"', html):
    if ref.startswith(("http", "data:")):
        continue
    print(("  OK      " if os.path.exists(os.path.join(web, ref)) else "  MISSING ") + ref)
