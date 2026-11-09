# AgentMail

Use the `agentmail` CLI. If it is not on PATH, install it: `npm i -g agentmail-cli`. It reads `AGENTMAIL_API_KEY`. The inbox ID is `$AGENT_EMAIL_ADDRESS`. For other commands and flags, run `agentmail <command> --help`.

List mail received after `$start`, newest first:

```bash
agentmail inboxes messages list --inbox-id "$AGENT_EMAIL_ADDRESS" --after "$start" \
  --query 'messages[].{id:message_id,from:from,subject:subject,at:timestamp}'
```

If the mail is not there, add `--include-spam true`.

Read one message:

```bash
agentmail inboxes messages get --inbox-id "$AGENT_EMAIL_ADDRESS" --message-id "<id>" \
  --query '{from:from,subject:subject,text:text,html:html}'
```

Send, and reply:

```bash
agentmail inboxes messages send --inbox-id "$AGENT_EMAIL_ADDRESS" --to "<to>" --subject "<subject>" --text "<body>"
agentmail inboxes messages reply --inbox-id "$AGENT_EMAIL_ADDRESS" --message-id "<id>" --text "<body>"
```

Use the one inbox. Create a new inbox only when the user asks: `agentmail inboxes create --username <name>`.
