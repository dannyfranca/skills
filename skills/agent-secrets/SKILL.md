---
name: agent-secrets
description: Password store for accounts the agent creates. Use to save or look up a login.
---

# Agent secrets

The logins of the accounts that the agent creates are in Bitwarden Secrets Manager, in the project `$AGENT_SECRETS_PROJECT_ID`. Use the `bws` CLI. It reads `BWS_ACCESS_TOKEN`. If `bws` is not on PATH or a variable is not set, read [`references/setup.md`](references/setup.md) and help the user complete it.

## Logins

One secret is one login:

- Key: `<domain>/<username>`, for example `github.com/agent@example.com`.
- Value: the password.
- Note: JSON `{"url","username","email","created","status"}`. The `status` is `pending` until the signup is verified, then `active`.

`bws secret list` shows the values of all secrets. Always pipe it through `jq` and select only the login that you need.

Look up one login:

```bash
bws secret list "$AGENT_SECRETS_PROJECT_ID" | jq '.[] | select(.key == "<domain>/<username>") | {id, value, note}'
```

List the logins for a site, without passwords:

```bash
bws secret list "$AGENT_SECRETS_PROJECT_ID" | jq '[.[] | select(.key | startswith("<domain>/")) | {id, key, note}]'
```

## Signup

1. Look up the login. If it is `active`, log in with it. If it is `pending`, continue the signup with it from step 3.
2. Store a new random password as `pending` before you submit the form:

   ```bash
   bws secret create "<domain>/<username>" "$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')" "$AGENT_SECRETS_PROJECT_ID" \
     --note '{"url":"<url>","username":"<username>","email":"<email>","created":"<UTC ISO time>","status":"pending"}' | jq '{id, key}'
   ```

3. Look up the login and fill the form with its value. If the site rejects the password, make one that obeys the site rules and store it: `bws secret edit <id> --value '<password>' | jq '{id, key}'`.
4. Submit the form and complete the verification. For email steps, use the `agent-email` skill.
5. Set the status to `active`: `bws secret edit <id> --note '<the same note, with "status":"active">' | jq '{id, key}'`. The signup is complete when the lookup shows `"status":"active"`.

## Autonomy

Read, create, and edit `pending` logins without approval. When a signup fails, delete its `pending` login: `bws secret delete <id>`. Get approval from the user before you change or delete an `active` login.

Use a password only to fill a form or to log in. Keep it out of messages, files, and logs.
