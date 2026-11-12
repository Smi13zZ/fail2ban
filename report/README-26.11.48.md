# Fail2ban 26.11.48 交付说明

概览「Fail2ban 服务」卡里的 systemd 一行改用状态徽章。

## 一、为什么改

上一版把这张卡里的状态都改成了绿字（运行中 / 已启用），只有 systemd 一行
还是纯黑正文，值是 `active`。它其实是这张卡里最直接的运行依据 ——
status 显示 fail2ban 在跑，systemd 显示 unit 本身活着 —— 却看着像个普通字段。

## 二、映射规则

| systemctl is-active 的输出 | 颜色 | 理由 |
| --- | --- | --- |
| active / reloading / activating | 绿 | unit 正在工作 |
| inactive / failed / deactivating | 橙 | unit 没在工作 |
| 空 / unknown / not-installed | 灰 | 面板没拿到答复 |

「拿不到值」用灰而不是橙：那说明面板没问到 systemd（权限、路径、非 systemd 环境），
不是服务本身有问题。套橙会把「面板没权限」说成「服务挂了」。

## 三、值不翻译

仍显示 systemctl 的原样输出（active / inactive / failed）。
排查故障时用户要拿界面上的字去和命令行输出对照，换成「运行中 / 未运行」
反而对不上 —— 这张卡里的其它状态词是给人看的，这一项是给排查用的。

## 四、实测

| 项 | 结果 |
| --- | --- |
| 概览 → systemd | `badge ok` / `rgb(22,163,74)`，与「状态 = 运行中」同色 |
| 文案 | 仍为 `active`（未翻译） |
| 其它字段 | 版本号 / 路径 / 数字保持正文色，不受影响 |

八道本地闸门全通过。

## 五、验证

- 桌面 1440 与手机 390 两套视口逐页截图 42 张，无白屏 / 无控制台报错。
- 打包后 `inspect_fpk.py --strict` 通过。

## 六、发布

已按流程提交、打 tag `v26.11.48` 并推送，由 CI 构建并发布 Release。

## 七、生产机

**未动**。升级由你在应用中心 Web UI 自己操作。
