# AutoClip remote publishing with GitHub Actions

This workflow runs AutoClip on a GitHub-hosted Ubuntu runner. Your Windows PC does not need to stay on while the workflow is running.

## Important limitations

- The workflow runs on-demand from **Actions → AutoClip Remote Publisher → Run workflow**.
- It also has a daily schedule at **18:00 India Standard Time**. GitHub only runs scheduled workflows from the repository's default branch.
- For the daily schedule, set repository variable `AUTOCLIP_DAILY_VIDEO_URL` to the source URL for that day. Update it daily or the same URL will be processed again.
- Scheduled runs can be delayed or skipped during high load.
- GitHub-hosted runners have time/resource limits. Long sources may fail or time out; CPU Whisper inference and model downloads can be slow.
- The workflow publishes generated Shorts as **PUBLIC** immediately. Only use content you own or are licensed to repurpose.

## One-time setup

### 1. Connect YouTube locally

1. Enable YouTube Data API v3 in Google Cloud.
2. Create an OAuth Desktop app client and save its JSON as `client_secrets.json` in the repository root locally.
3. Connect your YouTube channel in AutoClip.
4. Confirm `data/youtube_token.json` exists locally.

Do not commit either file. They contain credentials.

### 2. Add GitHub Actions secrets

Open **Settings → Secrets and variables → Actions → New repository secret** and create:

| Secret name | Value |
|---|---|
| `AUTOCLIP_YOUTUBE_CLIENT_SECRETS_JSON` | Complete contents of local `client_secrets.json` |
| `AUTOCLIP_YOUTUBE_TOKEN_JSON` | Complete contents of local `data/youtube_token.json` |

Never paste these values into chat, issues, pull requests, or source files. Limit repository access to trusted collaborators because workflow code can access repository secrets.

### 3. Configure daily source URL (optional)

Open **Settings → Secrets and variables → Actions → Variables → New repository variable**:
- Name: `AUTOCLIP_DAILY_VIDEO_URL`
- Value: a public VOD URL you own or are licensed to repurpose.

The schedule is 12:30 UTC (18:00 IST).

## Run immediately

1. Open the repository **Actions** tab.
2. Select **AutoClip Remote Publisher**.
3. Click **Run workflow**.
4. Enter a source URL you own or are licensed to repurpose.
5. Choose language, Whisper model and clip count (maximum five).
6. Start the run and inspect logs and the run artifact.

A successful workflow means YouTube accepted the uploads as public. Check the artifact and channel after the run.
