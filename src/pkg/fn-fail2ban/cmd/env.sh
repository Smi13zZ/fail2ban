#!/bin/bash
# ---------------------------------------------------------------------------
# fail2ban · fnOS 原生应用
# 公共环境变量，所有 cmd/ 生命周期脚本统一 source 本文件
# ---------------------------------------------------------------------------

F2B_APPNAME="${TRIM_APPNAME:-fn-fail2ban}"
F2B_TARGET="${TRIM_APPDEST:-/var/apps/${F2B_APPNAME}/target}"
F2B_VAR="${TRIM_PKGVAR:-/var/apps/${F2B_APPNAME}/var}"
F2B_ETC="${TRIM_PKGETC:-/var/apps/${F2B_APPNAME}/etc}"
F2B_HOME="${TRIM_PKGHOME:-/var/apps/${F2B_APPNAME}/home}"
F2B_TMP="${TRIM_PKGTMP:-/var/apps/${F2B_APPNAME}/tmp}"
F2B_PORT="${TRIM_SERVICE_PORT:-18752}"

F2B_PIDFILE="${F2B_VAR}/server.pid"
F2B_LOGFILE="${F2B_VAR}/server.log"
F2B_READYFILE="${F2B_VAR}/server.ready"

# fail2ban 自身的配置位置（宿主机系统目录）
F2B_SYS_ETC="/etc/fail2ban"
F2B_JAILD="${F2B_SYS_ETC}/jail.d"
F2B_MANAGED="${F2B_JAILD}/50-fn-fail2ban.local"

# ---- 飞牛统一网关 ---------------------------------------------------------
# socket 必须位于应用 target 目录下；前缀由框架以 FNNAS_GATEWAY_* 注入，
# 未注入时回退为约定的默认值。二者须与 ui/config 中的 gatewaySocket /
# gatewayPrefix 完全一致，否则网关能找到 socket 但路由仍然 404。
F2B_GATEWAY_SOCKET="${FNNAS_GATEWAY_SOCKET:-${F2B_TARGET}/app.sock}"
F2B_GATEWAY_PREFIX="${FNNAS_GATEWAY_PREFIX:-/app/${F2B_APPNAME}}"
FNNAS_GATEWAY_SOCKET="${F2B_GATEWAY_SOCKET}"
FNNAS_GATEWAY_PREFIX="${F2B_GATEWAY_PREFIX}"

# 应用自身配置文件（登录凭证等），由应用管理，勿与 /etc/fail2ban 混淆
F2B_CREDFILE="${F2B_ETC}/credentials.json"

if command -v python3 >/dev/null 2>&1; then
    F2B_PYTHON="$(command -v python3)"
elif [ -x /usr/bin/python3 ]; then
    F2B_PYTHON="/usr/bin/python3"
else
    F2B_PYTHON="python3"
fi

export F2B_APPNAME F2B_TARGET F2B_VAR F2B_ETC F2B_HOME F2B_TMP F2B_PORT
export F2B_PIDFILE F2B_LOGFILE F2B_READYFILE F2B_PYTHON
export F2B_SYS_ETC F2B_JAILD F2B_MANAGED F2B_CREDFILE
export F2B_GATEWAY_SOCKET F2B_GATEWAY_PREFIX
export FNNAS_GATEWAY_SOCKET FNNAS_GATEWAY_PREFIX
