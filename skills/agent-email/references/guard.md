# Guard setup

The guard is an optional model that reads each mail before the agent sees it. `read` and `wait` use it. `list` does not.

Set `AGENT_EMAIL_GUARD_CMD` to a command that reads a prompt on stdin and writes the answer on stdout. `mail.py` runs it in an empty temporary folder. Only the exact answer `SAFE` shows the mail. Any other answer withholds it with the reason `guard`. An error or no answer in 60 seconds withholds it with the reason `guard unavailable`.

Use a fast model with no tools. Example with Claude Haiku:

```bash
export AGENT_EMAIL_GUARD_CMD='claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'
```

Each `read` and `wait` then takes some seconds more.
