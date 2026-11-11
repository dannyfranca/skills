# Guard setup

The guard is an optional model. It reads the description of each event or task of another person before the agent sees it. Only `--body` shows these descriptions, so the guard runs only with `--body`. The text of the user is not sent to the guard.

Set `GOOGLE_CALENDAR_GUARD_CMD` to a command that reads a prompt on stdin and writes the answer on stdout. `gcal` runs it in an empty temporary folder. Only the exact answer `SAFE` shows the text. Any other answer withholds it with the reason `guard`. An error or no answer in 60 seconds withholds it with the reason `guard unavailable`.

Use a fast model with no tools. Example with Claude Haiku:

```bash
export GOOGLE_CALENDAR_GUARD_CMD='claude -p --model haiku --tools "" --strict-mcp-config --no-session-persistence'
```

Each event with a description of another person then takes some seconds more.
