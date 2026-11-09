# SMTP

Use `$HOME/.agents/skills/agent-email/scripts/smtp_mail.py`. It sends over SMTP and reads the IMAP `INBOX`. It needs Python 3.11 or later and no packages. Output is JSON.

Environment:

| Variable | Value |
| --- | --- |
| `AGENT_EMAIL_ADDRESS` | The sender address. |
| `AGENT_EMAIL_USER` | The login. The default is `AGENT_EMAIL_ADDRESS`. |
| `AGENT_EMAIL_PASSWORD` | The password for SMTP and IMAP. |
| `AGENT_EMAIL_SMTP_HOST`, `AGENT_EMAIL_SMTP_PORT` | The SMTP server. Port 465 uses TLS; other ports use STARTTLS. The default is 465. |
| `AGENT_EMAIL_IMAP_HOST`, `AGENT_EMAIL_IMAP_PORT` | The IMAP server, with TLS. The default port is 993. |

List mail received after `$start`, newest first:

```bash
"$HOME/.agents/skills/agent-email/scripts/smtp_mail.py" list --after "$start" [--from <sender>] [--subject <text>]
```

Read one message. The output has `text` and `html`:

```bash
"$HOME/.agents/skills/agent-email/scripts/smtp_mail.py" read <id>
```

Send, and reply with the `message_id` from `read`:

```bash
"$HOME/.agents/skills/agent-email/scripts/smtp_mail.py" send --to "<to>" --subject "<subject>" --text "<body>" [--in-reply-to "<message_id>"]
```
