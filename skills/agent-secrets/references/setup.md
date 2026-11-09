# Setup

Do this one time. The agent gets access to one project only. The personal vault of the user stays separate.

The user does the steps in the Bitwarden web app and types the access token. Keep the token out of the chat.

1. Install `jq` from the system package manager. Install `bws` with the official script, which checks the checksum: `curl https://bws.bitwarden.com/install | sh`.
2. Make an organization for the agents. In the Bitwarden web app, select **New organization** and the **Free** plan. Secrets Manager is free on a Free organization, for a maximum of 2 users, 3 projects, and 3 machine accounts. A Families organization cannot add Secrets Manager. A Teams or Enterprise organization can add it, at a cost: select **Limit machine accounts** to stop extra billing.
3. In the Admin Console of the organization, go to **Billing** → **Subscription**. Select **Subscribe to Secrets Manager**, then **Submit**. If Secrets Manager is not in the product switcher, go to **Members**, open the menu on the user row, and select **Activate Secrets Manager**.
4. Open Secrets Manager from the product switcher. Select **New** → **Project** and name it `agent-signups`.
5. Select **New** → **Machine account** and name it `agents`. In its **Projects** tab, add `agent-signups` with **Can read, write**.
6. In the **Access tokens** tab of the machine account, select **Create access token**. Copy the token. The web app shows it one time only.
7. Write `~/.config/agent-secrets/.env` with mode `0600`:

   ```sh
   BWS_ACCESS_TOKEN='<token>'
   AGENT_SECRETS_PROJECT_ID='<project id>'
   ```

   To get the project ID: `BWS_ACCESS_TOKEN='<token>' bws project list | jq -r '.[] | select(.name == "agent-signups") | .id'`.

8. Load the file in every shell. Add this line to a file that all shells read, for example `~/.zshenv`:

   ```sh
   [ -f "$HOME/.config/agent-secrets/.env" ] && { set -a; . "$HOME/.config/agent-secrets/.env"; set +a; }
   ```

Setup is complete when a new shell shows `agent-signups` for `bws project list | jq -r '.[].name'`.

For the EU cloud or a self-hosted server, set the server before step 7: `bws config server-base <server URL>`.
