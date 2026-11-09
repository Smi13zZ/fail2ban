import os
WEB = r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/src/pkg/fn-fail2ban/app/web"
# Vector twin of the app icon: light tile, black shield, two white slashes.
# The panel logo and the tab icon must be the same mark as the launcher icon.
svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <clipPath id="shield">
      <path d="M32 9.5c5.6 2.4 9.7 3.6 11.6 4.1v14.6c0 9.6-5.6 16.4-11.6 19.9-6-3.5-11.6-10.3-11.6-19.9V13.6C22.3 13.1 26.4 11.9 32 9.5z"/>
    </clipPath>
  </defs>
  <path d="M32 9.5c5.6 2.4 9.7 3.6 11.6 4.1v14.6c0 9.6-5.6 16.4-11.6 19.9-6-3.5-11.6-10.3-11.6-19.9V13.6C22.3 13.1 26.4 11.9 32 9.5z" fill="#121212"/>
  <g clip-path="url(#shield)">
    <path d="M-6 26 L14 -2 H26 L6 30 Z" fill="#fff"/>
    <path d="M6 46 L30 2 H42 L18 50 Z" fill="#fff"/>
  </g>
</svg>
"""
open(os.path.join(WEB, "logo.svg"), "w", encoding="utf-8", newline="\n").write(svg)
open(os.path.join(WEB, "favicon.svg"), "w", encoding="utf-8", newline="\n").write(svg)
print("logo.svg + favicon.svg written")