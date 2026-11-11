# Setup

`gcal setup` does these stages in a terminal. Use this file only when the user cannot run the wizard. Give the steps to the user one stage at a time. Replace `<project>` with the Project ID of stage 1.

The user does stages 1 to 5 in the Google Cloud console. Each stage is done once for each Google account.

## 1. Create a project

Open <https://console.cloud.google.com/projectcreate>.

1. Type a project name, for example `agent-calendar`.
2. Copy the Project ID under the name field. Google can add numbers to it.
3. Click Create.

The user can also use a project that already exists.

## 2. Enable the APIs

Open `https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com,tasks.googleapis.com&project=<project>`.

1. Make sure that the project is selected at the top of the page.
2. Click Next, then click Enable.

## 3. Configure the consent screen

Open `https://console.cloud.google.com/auth/overview?project=<project>`.

1. Click Get started.
2. App name: `Agent Calendar`. User support email: the email of the user. Click Next.
3. Audience: External. For a Google Workspace account, Internal is also good. Click Next.
4. Contact information: the email of the user. Click Next.
5. Agree to the policy. Click Continue, then click Create.

If the page shows an overview with no Get started button, this stage is already done.

## 4. Publish the app

Open `https://console.cloud.google.com/auth/audience?project=<project>`.

1. Under Publishing status, click Publish app, then click Confirm.
2. Make sure that the status is In production.

Do this stage. In the Testing status, Google stops the login after 7 days. Google does not need to verify the app, because only the user uses it. For an Internal audience, there is no Publish button: skip this stage.

## 5. Create the OAuth client

Open `https://console.cloud.google.com/auth/clients/create?project=<project>`.

1. Application type: Desktop app.
2. Name: `agent-calendar`. Click Create.
3. In the dialog, click Download JSON.

Google shows the client secret only in this dialog. If the user closes it first, the user deletes the client and makes a new one.

## 6. Import the client

The file has a secret. Do not read it, and do not ask the user to paste it into the chat. Run:

```bash
$HOME/.agents/skills/google-calendar/scripts/gcal auth --client ~/Downloads/client_secret_<...>.json
```

The command copies the client to `~/.config/google-calendar/credentials.json` (mode 600) and deletes the downloaded file.

## 7. Log in

The command of stage 6 also starts the login. Follow [Login](../SKILL.md#login). On the Google page, the user:

1. Chooses the Google account of the calendar.
2. Sees that Google has not verified the app. This is the app of the user. The user clicks Advanced, then Go to Agent Calendar (unsafe).
3. Ticks every box, then clicks Continue.

## 8. Verify

Run `"$gcal" whoami`. It shows the account and its time zone.
