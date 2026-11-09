"""内置过滤器模板。

面板「过滤器 → 从模板新建」用这里的模板预填 failregex / ignoreregex、建议的
过滤器名与 logpath，用户确认后由 jailconf.write_filter_local() 写入
``/etc/fail2ban/filter.d/<name>.local``（不覆盖系统自带版本）。

约定
----
* 正则均为 fail2ban 语法：整行锚定（``^...$``）、必须含 ``<HOST>`` 占位符；
* ``desc`` / ``sample`` / ``note`` / ``logpath`` / ``port`` 只用于面板展示，
  不会写进 filter.d；
* 模板只是**起点**：启用前务必用真实日志校验
  ``fail2ban-regex <日志文件> <filter名或local路径>``。
"""

from __future__ import annotations

TEMPLATES: list[dict] = [
    # ---------------------------------------------------------------- fn-knock 网关
    {
        "id": "fnknock-gateway",
        "name": "fnknock-gateway",
        "label": "敲门网关请求日志（NDJSON）",
        "category": "fn-knock",
        "desc": "匹配 fn-knock（敲门）Go 网关的请求日志：登录类路径上的认证失败（401/403/429）、WAF 拦截、"
                "以及未命中路由的扫描请求。因为所有经过敲门的服务共用这一个入口，"
                "一条 Jail 就能覆盖全部被代理的站点（含挂在 Host 映射下的 Immich）。",
        "logpath": "/usr/local/etc/fn-knock/logs/*.log*",
        "port": "http,https",
        "sample": '{"level":"info","time":"2026-09-22T10:00:00+08:00","method":"POST","scheme":"https",'
                  '"host":"immich.118112.xyz:8888","path":"/api/auth/login","request_uri":"/api/auth/login",'
                  '"protocol":"HTTP/2.0","status":401,"duration_ms":12,"remote_ip":"203.0.113.9",'
                  '"remote_addr":"203.0.113.9:51234","client_ip":"203.0.113.9","user_agent":"immich-ios/3.2.1",'
                  '"logged_in":false,"auth_required":false,"auth_decision":"无需鉴权",'
                  '"route_type":"host_rule","route_key":"immich.118112.xyz","upstream":"http://127.0.0.1:2283",'
                  '"matched":true,"bytes_in":123,"bytes_out":456,"tls":true,"websocket":false,'
                  '"waf_blocked":false,"general_blacklist_blocked":false}',
        "failregex": [
            # 注意：fail2ban 要求每条 failregex 里 <HOST> 只能出现一次，
            # 所以「判定条件」全部放进前置 lookahead（零宽），后面只锚定一次客户端 IP。
            r'^(?:'
            # ① 认证失败：必须同时命中「登录类路径」与「401/403/429」，
            #    避免把 App 的 token 过期等正常 401 也算进去（那会误封自己人）。
            r'(?=.*?"status"\s*:\s*(?:401|403|429)(?!\d))'
            r'(?=.*?"path"\s*:\s*"[^"]*?(?:/login|/signin|/sign-in|/log-in|/wp-login\.php'
            r'|/administrator|/session))'
            # ② WAF 拦截 / 未命中路由被拦：与路径无关
            r'|(?=.*?"(?:waf_blocked"\s*:\s*true|route_type"\s*:\s*"unmatched_route_blocked"))'
            r')'
            r'.*?"(?:remote_ip|client_ip|remote_addr)"\s*:\s*"<HOST>.*$',
        ],
        "ignoreregex": [
            # 已经在敲门通用黑名单里的请求不再计数，避免「fail2ban 反复续封已拉黑的 IP」
            r'^.*"general_blacklist_blocked"\s*:\s*true.*$',
        ],
        "note": "日志位于敲门的「系统设置 → 日志」所指定的目录（默认就是网关运行目录下的 logs），"
                "文件名为 YYYY-MM-DD.log、一行一条 JSON。"
                "① 若想统计所有 401/403/429，把 path 那条 lookahead 删掉即可 —— "
                "但 Immich 等 App 在令牌过期时会正常收到 401，会把自己人算进去，务必同时调大 maxretry。"
                "② 封禁能否生效取决于目标服务是否只能经 8888 访问："
                "若该服务还有对公网的直连端口，黑名单会被绕过，需要改成系统防火墙封禁或关掉那个端口。",
    },
    # ---------------------------------------------------------------- Web 服务器
    {
        "id": "nginx-sensitive-scan",
        "name": "nginx-sensitive-scan",
        "label": "敏感文件 / 目录探测（Nginx · Apache 访问日志）",
        "category": "Web 服务器",
        "desc": "访问日志中出现 /.env、/.git、/wp-config.php、/actuator 等敏感路径且返回 4xx / 5xx —— 典型扫描器行为。",
        "logpath": "/var/log/nginx/access.log",
        "port": "http,https",
        "sample": '203.0.113.9 - - [22/Sep/2026 10:00:00 +0800] "GET /.env HTTP/1.1" 404 162 "-" "Mozilla/5.0"',
        "failregex": [
            r'^<HOST> \S+ \S+ \[[^\]]+\] "(?:GET|POST|HEAD|PUT|OPTIONS|PROPFIND) [^"]*?'
            r'(?:/\.env|/\.git|/\.svn|/\.hg|/\.aws|/\.ssh|/\.DS_Store|/wp-config\.php|/xmlrpc\.php'
            r'|/phpmyadmin|/pma/|/myadmin|/actuator|/vendor/phpunit|/eval-stdin\.php'
            r'|/docker-compose\.ya?ml|/config\.(?:json|php|ya?ml))[^"]*? HTTP/[\d.]+" (?:4\d\d|5\d\d) .*$',
        ],
        "ignoreregex": [],
        "note": "若站点前面还有一层反向代理，访问日志里记的是反代 IP，需要先用 real_ip 之类还原真实客户端 IP，否则封禁对象会错。",
    },
    {
        "id": "web-auth-bruteforce",
        "name": "web-auth-bruteforce",
        "label": "登录接口爆破（访问日志 401 / 403 / 429）",
        "category": "Web 服务器",
        "desc": "针对 /login、/signin、/api/auth、/wp-login.php 等认证路径，返回 401 / 403 / 429 时计入失败。",
        "logpath": "/var/log/nginx/access.log",
        "port": "http,https",
        "sample": '203.0.113.9 - - [22/Sep/2026 10:00:05 +0800] "POST /api/auth/login HTTP/1.1" 401 47 "-" "curl/8.5.0"',
        "failregex": [
            r'^<HOST> \S+ \S+ \[[^\]]+\] "(?:GET|POST|PUT) [^"]*?'
            r'(?:/login|/signin|/sign-in|/auth|/api/auth|/api/session|/wp-login\.php|/administrator)'
            r'[^"]*? HTTP/[\d.]+" (?:401|403|429) .*$',
        ],
        "ignoreregex": [],
        "note": "会误伤「忘记密码反复尝试」的正常用户，建议配合 ignoreip 白名单，且 maxretry 不要设得太小。",
    },
    # -------------------------------------------------------------- 反向代理
    {
        "id": "caddy-json-auth",
        "name": "caddy-json-auth",
        "label": "Caddy 访问日志（JSON）认证失败",
        "category": "反向代理",
        "desc": "Caddy 开启 access log（JSON 格式）后，匹配状态码 401 / 403 的请求。",
        "logpath": "/var/log/caddy/access.log",
        "port": "http,https",
        "sample": '{"level":"info","ts":1758500000.1,"logger":"http.log.access","msg":"handled request",'
                  '"request":{"remote_ip":"203.0.113.9","remote_port":"51234","method":"POST",'
                  '"uri":"/api/auth/login"},"status":401}',
        "failregex": [
            r'^.*?"remote_ip"\s*:\s*"<HOST>".*?"status"\s*:\s*(?:401|403).*$',
        ],
        "ignoreregex": [],
        "note": "Caddy 默认不写访问日志，需要在 Caddyfile 里配置 log 指令并保持默认 JSON 编码。",
    },
    {
        "id": "traefik-json-auth",
        "name": "traefik-json-auth",
        "label": "Traefik 访问日志（JSON）认证失败",
        "category": "反向代理",
        "desc": "Traefik 开启 JSON 格式 access log 后，匹配 DownstreamStatus 为 401 / 403 的请求。",
        "logpath": "/var/log/traefik/access.log",
        "port": "http,https",
        "sample": '{"ClientAddr":"203.0.113.9:51234","ClientHost":"203.0.113.9","RequestMethod":"POST",'
                  '"RequestPath":"/api/login","DownstreamStatus":401,"Duration":1234567}',
        "failregex": [
            r'^.*?"ClientHost"\s*:\s*"<HOST>".*?"DownstreamStatus"\s*:\s*(?:401|403).*$',
        ],
        "ignoreregex": [],
        "note": "如果 access log 用的是 common 格式，改用「Web 服务器 → 登录接口爆破」模板即可。",
    },
    # --------------------------------------------------------------- 容器
    {
        "id": "docker-json-auth",
        "name": "docker-json-auth",
        "label": "容器日志中的认证失败（Docker json-file 格式）",
        "category": "容器 / Docker",
        "desc": "匹配 Docker 默认 json-file 日志里「认证失败关键字」与「客户端 IP」同行的记录，适合容器化应用。",
        "logpath": "/var/lib/docker/containers/<容器ID>/<容器ID>-json.log",
        "port": "any",
        "sample": '{"log":"2026-09-22T10:00:00.000000000Z authentication failed for 203.0.113.9\\n",'
                  '"stream":"stdout","time":"2026-09-22T10:00:00.000000000Z"}',
        "failregex": [
            r'^.*"log":"[^"]*?(?:authentication failed|auth failed|login failed|failed login'
            r'|invalid credentials|invalid password|unauthorized|access denied)[^"]*?<HOST>[^"]*?".*$',
            r'^.*"log":"[^"]*?<HOST>[^"]*?(?:authentication failed|auth failed|login failed|failed login'
            r'|invalid credentials|invalid password|unauthorized|access denied)[^"]*?".*$',
        ],
        "ignoreregex": [],
        "note": "logpath 要换成实际容器的 ID 目录（`docker inspect -f '{{.LogPath}}' <容器名>` 可直接拿到完整路径）。容器被重建后该路径会变，需要同步改 jail 的 logpath。",
    },
    # --------------------------------------------------------------- 应用 / API
    {
        "id": "immich",
        "name": "immich",
        "label": "Immich 登录失败",
        "category": "应用 / API",
        "desc": "匹配 Immich 服务端日志中的登录失败记录（Docker json-file 格式），需要日志里带客户端 IP。",
        "logpath": "/var/lib/docker/containers/<immich-server 容器ID>/<容器ID>-json.log",
        "port": "http,https",
        "sample": '{"log":"[Nest] 1  - 09/22/2026, 10:00:00 AM   WARN [AuthService] '
                  'Failed to login with email user@example.com from 203.0.113.9\\n",'
                  '"stream":"stdout","time":"2026-09-22T10:00:00.000000000Z"}',
        "failregex": [
            r'^.*"log":"[^"]*?(?:Failed to login|Invalid user credentials|Invalid password'
            r'|Invalid credentials|Authentication failed)[^"]*?<HOST>[^"]*?".*$',
        ],
        "ignoreregex": [],
        "note": "Immich 只有在配置了可信代理（较新版本为 IMMICH_TRUSTED_PROXIES）时才会把真实客户端 IP 写进日志；否则日志里是反代/容器 IP，此时更应该在访问日志侧封禁。",
    },
    {
        "id": "api-json-unauthorized",
        "name": "api-json-unauthorized",
        "label": "JSON 应用日志：401 / 403 + 客户端 IP",
        "category": "应用 / API",
        "desc": "结构化日志里同时出现客户端 IP 字段与 401 / 403（或 unauthorized / forbidden）时计入，适合自建服务。",
        "logpath": "/var/log/myapp/app.log",
        "port": "any",
        "sample": '{"time":"2026-09-22T10:00:00Z","level":"warn","client_ip":"203.0.113.9",'
                  '"path":"/api/login","status":401,"msg":"Unauthorized"}',
        "failregex": [
            r'^.*?"(?:ip|client_ip|clientIp|client_addr|remote_addr|remoteAddr|ip_address|ipAddress'
            r'|source_ip|sourceIp)"\s*:\s*"<HOST>".*?(?:401|403|[Uu]nauthorized|[Ff]orbidden).*$',
        ],
        "ignoreregex": [],
        "note": "字段名不一致时，把前面那组 (?:ip|client_ip|...) 换成你日志里真实的 IP 字段名即可。",
    },
    {
        "id": "text-log-auth",
        "name": "app-auth-failure",
        "label": "通用文本日志：认证失败 + 客户端 IP（骨架）",
        "category": "应用 / API",
        "desc": "普通文本日志的起点模板：关键字与 IP 出现在同一行即计入。先替换关键字，再用真实日志校验。",
        "logpath": "/var/log/myapp/app.log",
        "port": "any",
        "sample": '2026-09-22 10:00:00 WARN auth: login failed for user "admin" from 203.0.113.9 (bad password)',
        "failregex": [
            r'^.*?(?:authentication failed|auth failed|login failed|failed login|invalid credentials'
            r'|invalid password|unauthorized|permission denied|access denied).*?<HOST>.*$',
            r'^.*?<HOST>.*?(?:authentication failed|auth failed|login failed|failed login|invalid credentials'
            r'|invalid password|unauthorized|permission denied|access denied).*$',
        ],
        "ignoreregex": [],
        "note": "这是骨架不是成品：务必把关键字换成你日志里的真实文案，并先用 fail2ban-regex 跑一遍真实日志再启用。",
    },
]


def list_templates() -> list[dict]:
    """返回模板列表（浅拷贝，避免调用方改到内置数据）。"""
    return [dict(item) for item in TEMPLATES]
