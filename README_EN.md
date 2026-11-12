# Fail2ban

A Fail2ban management panel for fnOS (Feiniu NAS). It installs as a native app and
opens straight into the management interface — there is no login page.

It brings the day-to-day work of fail2ban into the browser: which jails are running,
which addresses are banned and until when, editing ban policy, managing allow lists,
pushing notifications, and reading the log.

## What it does

**Overview** — service state, current ban count, active jails, cumulative blocks.
Refreshes every 30 seconds and again when you switch back to the tab.

**Jails** — every fail2ban jail with its state; edit bantime, max retry, ignore list,
log path and filter per jail, or create a custom jail from a template. Each jail can
open the tail of the log file its logpath points at.

**Bans** — all currently banned addresses in one place. An address banned by several
jails appears once with the jails that matched it. Each row shows when it was banned,
when it expires and how long is left (permanent bans show only the ban time). Search by
address, sort, copy with one click, unban a single address or a selection.

**Filters** — system and custom fail2ban filters, with the jails that use them. Custom
filters can be created, edited and deleted; system filters are never overwritten.

**Notifications** — 11 channels: Bark, DingTalk, WeCom, WeCom App, Feishu, ServerChan,
PushPlus, WxPusher, Telegram, Slack and a custom webhook. Subscribe per event (ban,
unban, service down, service recovered); messages are bilingual (Chinese and English).
Every push leaves a delivery record, success or failure, with the reason attached.

**fn-knock integration** — shares the ban list with a companion block-list service:
addresses banned by fail2ban are pushed into it, and ban actions can be delegated to it.
The required filter and action file are installed automatically. The page is split into
blocks — ban method, sync, connection, status, blocklist and log — one at a time.

**Storage** — how much the data directory and backups take, with one-click cleanup of
logs, old backups or both. Logs are truncated in place while the service is running, so
nothing that is still being written is lost.

**Update** — current and latest version, release notes, check and one-click upgrade.
The downloaded package is verified before installing, and a failure rolls back to the
previous version.

**Settings** — ten sections: panel behaviour, appearance (theme, language, sidebar,
table density), protection policy, notifications, backup, storage, update, guide and about.

**Guide** — every location the panel writes to, on one page: its own web, data, config,
backup and log directories, the fail2ban system files with their read order, and the
action and filter written by the fn-knock integration. Each path can be copied in one
click, and the rows marked as system-owned are explicitly not the panel's to change.

## Highlights

- **Your fail2ban configuration is left alone.** Everything is written to
  `jail.d/50-fn-fail2ban.local`; the shipped `jail.conf` and `jail.local` are never
  touched, and that file survives an upgrade byte for byte.
- **No login page.** Access control is a CIDR allow list (loopback and private ranges by
  default), which fits a NAS reachable only from the local network.
- **No dependencies.** The backend is Python standard library only; the front end is
  plain JavaScript and CSS with no build step, no third-party packages and no CDN.
- **Bilingual.** Every label, field hint and push message exists in Chinese and English;
  switching takes effect immediately.
- **Dark mode.** Two complete themes, either following the system or fixed by hand.
- **Works on a phone.** The sidebar collapses into a drawer, tables scroll horizontally
  and touch targets are sized for fingers.

## Install

1. Download `fn-fail2ban-<version>-all.fpk`;
2. fnOS → App Center → Manual install → pick the file;
3. Confirm the initial policy in the wizard (whether to install fail2ban automatically,
   whether to enable SSH protection, default ban time, max retry, and the allow list);
4. Open the panel from the desktop icon or the App Center.

Upgrading an existing install is done from the App Center: per-app data, notification
channels, allow lists and backup settings are all preserved.

The package is a single file that runs on both x86_64 and ARM devices.

## Requirements

The host needs fail2ban. The install wizard offers to install it through `apt`
(network and root access required); an existing installation is left untouched.

## Known limits

- Ban and expiry times come from parsing the fail2ban log. If the log has been cleared
  or the service has never started, those cells show a dash — that is expected.
- On phones the tables scroll horizontally instead of turning into cards: the jail table
  has 8 columns and cards would hurt scanability.
- It does not rewrite host firewall policy; that is fail2ban's job. The panel manages and
  displays.

## Version

Current version 26.11.41. See [CHANGELOG](CHANGELOG.md) for the change history and
[docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the build and verification workflow.

Installers are on the [Releases](https://github.com/Smi13zZ/fail2ban/releases/latest)
page: pick `fn-fail2ban-<version>-all.fpk` and install it from the fnOS App Center.
Devices already running an older version can just use the upgrade button there;
configuration and data are preserved.
