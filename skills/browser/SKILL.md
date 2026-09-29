---
name: browser
description: Browser automation with playwright-cli, with a Browser Use cloud fallback for CAPTCHA and bot walls. Use for any web page task, and when a site shows a CAPTCHA, bot check, or Cloudflare challenge.
---

# Browser

Use `playwright-cli` for all browser work. For its commands, use the `playwright-cli` skill. This skill tells you which browser to use: local or remote.

- **Local** is the default. It is free and fast.
- **Remote** is a Browser Use cloud browser. It has a residential IP, stealth fingerprints, and automatic CAPTCHA solving. It costs money for each minute it runs. Use it only when a bot wall blocks the local browser.

## Local

Open a named session in headed Chrome with a persistent profile. A real, headed browser gets fewer bot walls:

```bash
playwright-cli -s=<session> open <url> --browser=chrome --headed --persistent
```

If Chrome is not installed, remove `--browser=chrome`.

Use a session name that tells the site and the task, for example `-s=shop-login`. Complete the task in the local session unless you find a bot wall.

## Bot walls

A **bot wall** is a page that blocks automated browsers. It is a bot wall when the snapshot shows one of these:

- A CAPTCHA: reCAPTCHA, hCaptcha, Cloudflare Turnstile, or an "I'm not a robot" checkbox.
- A challenge page: "Verify you are human", "Just a moment...", "Checking your browser", "Press & Hold".
- A block page from a bot-protection vendor: Cloudflare, DataDome, Akamai "Access Denied", PerimeterX, Imperva.

These are not bot walls. Solve them in the local session, or report them to the user:

- A login form, a paywall, or a consent banner.
- A geo-block, or an HTTP 429 rate limit.

## Remote

Use this procedure only for a bot wall. The helper is `$HOME/.agents/skills/browser/scripts/browser-remote`. It needs `BROWSER_USE_API_KEY` in the environment.

1. Start a remote session. Use a new session name. `--from` copies the cookies and storage of the blocked local session:

   ```bash
   "$HOME/.agents/skills/browser/scripts/browser-remote" start <session>-remote --from <session>
   ```

   The output is JSON with `session` and `liveUrl`. The step is complete when the command exits with code 0.

2. Go to the blocked URL with `goto`:

   ```bash
   playwright-cli -s=<session>-remote goto <url>
   ```

   Use `goto` or `tab-new` in a remote session. `open` disconnects the remote browser and starts a local one.

3. Take a snapshot. If the bot wall is still there, wait 10 seconds and take a snapshot again. The cloud browser solves the CAPTCHA by itself. While it solves, do not click, type, or reload. Continue for a maximum of 30 seconds.

4. If the bot wall stays after 30 seconds, give the `liveUrl` to the user. Tell them to solve the CAPTCHA in that page, and wait for their reply. The `liveUrl` shows the same browser, and the user can control it.

5. Complete the task for this site in the remote session.

6. Stop the remote session. Do this every time: when the task is complete, when it fails, and when you stop the work. A remote browser that you do not stop continues to cost money until its timeout:

   ```bash
   "$HOME/.agents/skills/browser/scripts/browser-remote" stop <session>-remote
   ```

   The step is complete when the output shows `"status": "stopped"`. `playwright-cli close` and `detach` do not stop the remote browser.

For the next site, start again with a local session.

## Options

`start` accepts these options:

- `--country <code>`: the country of the residential proxy. The default is `$BROWSER_USE_PROXY_COUNTRY`, or `us`. Use the country that the site expects. Use `none` to turn off the proxy.
- `--timeout <minutes>`: the maximum run time, from 1 to 240. The default is 60. The browser stops at the timeout.
- `--profile <id>`: a Browser Use profile. The browser loads its cookies, and `stop` saves the cookies back to it. Use it only when the user gives a profile ID.

To see all remote sessions that are recorded, and their cost:

```bash
"$HOME/.agents/skills/browser/scripts/browser-remote" status
```

Before you finish your work, run `status`. Stop each session that you started.
