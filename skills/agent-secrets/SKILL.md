---
name: agent-secrets
description: Password store for accounts the agent creates. Use to save or look up a login.
---

# Agent secrets

The logins of the accounts that the agent creates are in Bitwarden Secrets Manager. Use `$HOME/.agents/skills/agent-secrets/scripts/login.py`. It needs Python 3, `bws`, `BWS_ACCESS_TOKEN`, and `AGENT_SECRETS_PROJECT_ID`, and no packages. Output is JSON. If it reports a missing tool or variable, read [`references/setup.md`](references/setup.md) and help the user complete it.

A login is `<domain>/<username>`, for example `github.com/agent@example.com`. Its status is `pending` until the signup is verified, then `active`.

```bash
login=$HOME/.agents/skills/agent-secrets/scripts/login.py
"$login" get <domain> <username>                    # one login, with its password
"$login" list [<domain>]                            # logins, without passwords
"$login" new <domain> <username> <url> [<email>]    # random password, pending
printf '%s' '<password>' | "$login" set-password <domain> <username>
"$login" activate <domain> <username>
"$login" delete <domain> <username>
```

`set-password`, `activate`, and `delete` work only on `pending` logins. An `active` login is a working account. Only the user changes it, in the Bitwarden web app.

## Signup

1. Run `list <domain>`. If a login is `active`, log in with it. If it is `pending`, continue its signup from step 3.
2. Run `new` before you submit the form.
3. Run `get` and fill the form with the password. If the site rejects the password, make one that obeys the site rules and run `set-password`.
4. Submit the form and complete the verification. For email steps, use the `agent-email` skill.
5. Run `activate`. If the signup fails, run `delete`.

Use a password only to fill a form or to log in. Keep it out of messages, files, and logs.
