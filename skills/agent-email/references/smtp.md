# SMTP setup

`mail.py` sends over SMTP and reads the IMAP `INBOX`.

| Variable | Value |
| --- | --- |
| `AGENT_EMAIL_PROVIDER` | `smtp`. |
| `AGENT_EMAIL_ADDRESS` | The sender address. |
| `AGENT_EMAIL_USER` | The login. The default is `AGENT_EMAIL_ADDRESS`. |
| `AGENT_EMAIL_PASSWORD` | The password for SMTP and IMAP. |
| `AGENT_EMAIL_SMTP_HOST`, `AGENT_EMAIL_SMTP_PORT` | The SMTP server. Port 465 uses TLS; other ports use STARTTLS. The default is 465. |
| `AGENT_EMAIL_IMAP_HOST`, `AGENT_EMAIL_IMAP_PORT` | The IMAP server, with TLS. The default port is 993. |

SMTP has no server scan for prompt injection. `mail.py` cleans the mail. To add a model check, set a guard: see [`guard.md`](guard.md).
