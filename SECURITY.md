# Security policy

This project places real orders on Bybit mainnet, so security reports are taken seriously.

## Reporting a vulnerability

Please do **not** open a public issue for a vulnerability. Use GitHub's private reporting instead:
**Security → Report a vulnerability** on this repository. Include the affected version or commit, steps to reproduce and the impact you see.

## Never share secrets

When reporting a problem or opening an issue, remove API keys, the Telegram bot token, your Telegram user id, `.env` contents, `storage/` files and any log lines that contain account balances. If you accidentally published a key, revoke it on Bybit (and the Telegram token via @BotFather) immediately.

## Hardening checklist

- Create the Bybit API key with **Contract Orders + Positions** only and **without Withdraw**.
- Keep the dashboard bound to `127.0.0.1` (the default) and do not expose it through a tunnel or reverse proxy.
- Keep `.env` and `storage/` out of version control and backups that leave your machine.
