---
name: browser
description: Browser guidance. Use for any task that needs a web browser.
---

# Browser

Use `playwright-cli` for all browser work. For its commands, use the `playwright-cli` skill.

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

When you find a bot wall, read [`references/remote.md`](references/remote.md) and move the task to a remote browser.

These are not bot walls. Solve them in the local session, or report them to the user:

- A login form, a paywall, or a consent banner.
- A geo-block, or an HTTP 429 rate limit.
