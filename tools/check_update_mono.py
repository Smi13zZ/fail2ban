#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""升级遮罩阶段收敛规则的静态闸门（Fail2ban）。

  1. 阶段只允许前进（UP_PHASE_ORDER）
  2. 单调性只在同一 attempt 内成立
  3. 不拿安装响应体绘制进度（install() 的返回体没有 done/total）

用法：python3 tools/check_update_mono.py
"""
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
APP_JS = os.path.join(REPO, "src", "pkg", "fn-fail2ban", "app", "web", "app.js")


def main():
    src = io.open(APP_JS, encoding="utf-8").read()
    fails = []

    if "UP_PHASE_ORDER" not in src:
        fails.append("规则 1 缺少阶段顺序表 UP_PHASE_ORDER")
    if not re.search(r"checking\s*:\s*0", src):
        fails.append("规则 1 顺序表里没有 checking: 0")
    if "nextRank < prevRank" not in src:
        fails.append("规则 1 缺少「后退快照丢弃」")
    if "state.update.monoAttempt" not in src:
        fails.append("规则 3 缺少 attempt 基线")
    if not re.search(r"attempt !== state\.update\.monoAttempt", src):
        fails.append("规则 3 缺少「attempt 变了就重置」")
    # 安装响应不得用于绘制进度
    if re.search(r"if \(data && data\.phase\) paintUpProgress\(data\)", src):
        fails.append("规则 3 仍在用安装响应里的 phase 绘制进度")
    if "update.installingReady" not in src:
        fails.append("缺少安装阶段文案")
    if "update.phaseVerify" not in src:
        fails.append("缺少校验阶段文案")

    if fails:
        for f in fails:
            print("FAIL", f)
        return 1
    print("OK   升级遮罩阶段收敛规则齐备")
    return 0


if __name__ == "__main__":
    sys.exit(main())
