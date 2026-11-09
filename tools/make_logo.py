import os
WEB = r"D:/Workspace/DeepSeek Harness/fn-fail2ban-upgrade/src/pkg/fn-fail2ban/app/web"
# The sidebar mark is a 32px slot on a light rail; the app tile already carries
# its own rounded-square background, so the logo reuses that artwork directly.
svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="12" fill="#e8e9ea"/>
  <path d="M32 11.5c7.2 2.6 12.4 3.9 13.4 4.2v14.6c0 9.7-6.2 16.4-13.4 19.2-7.2-2.8-13.4-9.5-13.4-19.2V15.7c1-.3 6.2-1.6 13.4-4.2z"
        fill="#b4cfe1" stroke="#7996b0" stroke-width="2.4" stroke-linejoin="round"/>
  <path d="M32 13.6v34.1c6-2.7 10.7-8.7 11-16.4V16.3z" fill="#8eb6c7" opacity=".85"/>
</svg>
'''
open(os.path.join(WEB, "logo.svg"), "w", encoding="utf-8", newline="\n").write(svg)
print("logo.svg written")
