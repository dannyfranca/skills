# AgentMail setup

`mail.py` calls the AgentMail REST API. It needs no CLI.

| Variable | Value |
| --- | --- |
| `AGENT_EMAIL_PROVIDER` | `agentmail`, or unset. |
| `AGENTMAIL_API_KEY` | An API key from the AgentMail console. |
| `AGENT_EMAIL_ADDRESS` | The inbox address, for example `agent@agentmail.to`. It is also the inbox ID. |
| `AGENTMAIL_BASE_URL` | Optional. Set `https://api.agentmail.eu` for an EU account. The default is `https://api.agentmail.to`. |

Use the one inbox. Create a new inbox only when the user asks.

## Agent Armor

Agent Armor is an AgentMail scan that marks mail that tries to control an agent. `mail.py` withholds mail that it marks `review`, and mail that it cannot scan. It is a beta feature. Only an owner or admin of a US organization can turn it on:

1. Open the AgentMail console.
2. Go to **Settings** → **Labs**, and join Labs.
3. Turn on **Agent Armor**. Select **Monitor** mode.

Without Agent Armor, `mail.py` still cleans the mail, and it applies the guard if one is set.

## Guard

To add a model check that works with all providers, see [`guard.md`](guard.md).
