# Slack Bot Onboarding Cookbook

This runbook guides administrators through creating a Slack app for Arthexis Constellation, wiring the workspace credentials into the suite, and validating that message broadcasts and slash commands work end-to-end.

## 1. Prerequisites

- Slack workspace owner or administrator rights so you can create and install custom apps.
- The public hostname for your Arthexis deployment (for example `https://watchtower.example.com`). Slash commands must reach `/teams/slack/command/` on that host.
- Access to the Arthexis Django admin as a superuser.

> ⚠️ Slack may take several minutes to propagate new settings. If a step below fails, wait a moment and retry before escalating.

## 2. Create the Slack app

1. Sign in to [https://api.slack.com/apps](https://api.slack.com/apps) with the workspace administrator account.
2. Click **Create New App → From scratch** and provide:
   - **App Name:** for example `Arthexis Bot`.
   - **Pick a workspace:** choose the workspace that should receive Arthexis broadcasts.
3. On the **Basic Information** page:
   - Copy the **Signing Secret** (you will store it in Arthexis later).
   - Scroll to **Display Information** and add a short description/icon if desired.

## 3. Configure permissions and install the bot

1. Open **Features → OAuth & Permissions**.
2. Under **Scopes → Bot Token Scopes**, add at minimum:
   - `chat:write` so the bot can post messages to channels configured in Arthexis.
   - `commands` so Slack will deliver slash commands to Arthexis.
   - Add `chat:write.public` if the bot needs to post in channels it is not a member of.
3. Click **Install to Workspace** (or **Reinstall to Workspace** after scope changes) and approve the permissions dialog.
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`). Arthexis uses it to call Slack APIs.
5. Visit **App Home → About** and note the **Bot User ID** (starts with `U` or `B`). Slack also returns it automatically when Arthexis validates the credentials.

### Token mapping quick reference

Slack’s dashboard may show multiple token types after installation. Use the table below to match them to Arthexis fields:

| Slack portal item | Value begins with | Store in Arthexis |
| --- | --- | --- |
| **Signing Secret** (Basic Information → App Credentials) | — | **Signing secret** |
| **Bot User OAuth Token** (OAuth & Permissions → OAuth Tokens for Your Workspace) | `xoxb-` | **Bot token** |
| **Workspace Access Token** / **Refresh Token** (App Credentials) | `xoxa-` / `xoxe-` | Not used; Arthexis does not consume these short-lived tokens |

> ℹ️ If Slack only shows Access/Refresh tokens, expand **OAuth & Permissions** and install the app to generate the bot token. Arthexis requires the `xoxb-` bot token and the signing secret; it does not support refreshing short-lived app tokens.

## 4. Register the slash command

1. Open **Features → Slash Commands** and click **Create New Command**.
2. Fill in the form:
   - **Command:** e.g. `/arthexis`.
   - **Request URL:** `https://YOUR-HOSTNAME/teams/slack/command/` (replace `YOUR-HOSTNAME`). Arthexis exposes the handler at this path.
   - **Short Description:** `Broadcast a Net Message via Arthexis`.
   - **Usage Hint:** `net <subject> | <body>` – matches the command grammar supported by Arthexis.
3. Save the command. Slack immediately starts signing requests with the secret captured earlier.

## 5. Collect channel identifiers

Arthexis stores the default broadcast list as JSON channel IDs. Gather them before opening the admin form:

1. In Slack, open each target channel and choose **View channel details → More → Copy channel ID**. Alternatively, open the channel menu (⋮) and select **Copy link**; the last path segment (for example `C01ABCDE`) is the channel ID.
2. Build a JSON array containing the IDs, such as `["C01ABCDE", "C02FGHIJ"]`.

## 6. Configure Arthexis Constellation

1. Sign in to the Django admin and navigate to **Teams → Slack Chatbots**.
2. Click **Add Slack chatbot** and complete the fields:
   - **Node / Owner:** choose the node or entity that should broadcast Net Messages through this workspace.
   - **Team ID:** paste the workspace **Team ID** from Slack’s Basic Information page (begins with `T`).
   - **Bot User ID:** optional; Arthexis fills it automatically after a successful credential test, but you may paste it from the App Home page now.
   - **Default channels:** paste the JSON array prepared above. Arthexis validates the structure and strips whitespace for you.
   - **Bot token:** click the sigil field and store the `xoxb-` OAuth token collected in step 3. Refer to the [Sigils Cookbook](sigils.md) if you need to reuse tokens across environments.
   - **Signing secret:** store the signing secret copied from Slack Basic Information.
   - Leave **Enabled** checked unless you want to stage the integration before broadcasting.
3. Save the profile. Arthexis normalizes the identifiers and checks for common validation issues (missing team ID, malformed channel list, absent credentials).

## 7. Verify the integration

1. With the profile selected, use the admin action **Save and continue editing**. In a separate shell, run:
   ```bash
   python manage.py shell -c "from apps.teams.models import SlackBotProfile; SlackBotProfile.objects.get(team_id='TXXXXX').connect()"
   ```
   Replace `TXXXXX` with your team ID. The command calls Slack’s `auth.test` endpoint and stores any returned team or bot identifiers.
2. Create a Net Message from **Nodes → Net Messages** in the Django admin or trigger an in-app event. Arthexis posts the formatted subject/body to every configured Slack channel using `chat.postMessage`.
3. From Slack, run your slash command (e.g. `/arthexis net Launch | Staging promotion finished`). Arthexis validates the request signature, enforces node ownership, and returns an ephemeral confirmation while broadcasting the Net Message to local operators.

## 8. Troubleshooting tips

- **401 Unauthorized slash command:** confirm the signing secret matches Slack’s Basic Information page and that your server clock is accurate (Slack rejects requests older than five minutes).
- **Bot skips broadcasts:** ensure the profile remains enabled and the channel list is not empty. Check the Django logs for messages such as “missing token” or “no default channels configured.”
- **`invalid_auth` errors from Slack:** reinstall the app to regenerate the bot token, then update the stored sigil in Arthexis.
- **Multiple workspaces:** create one Slack Chatbot profile per workspace; team IDs are enforced as unique so broadcasts are routed deterministically.

Once these steps succeed, your operators can push announcements directly from Arthexis while field teams issue urgent Net Messages from Slack.
