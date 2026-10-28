# GH Dash

Show your open pull requests, review requests, assigned issues and the latest CI result for a few repos.

## Usage

Needs Wi-Fi and a GitHub token. Set these in `secrets.py` on the badge: `WIFI_SSID`, `WIFI_PASSWORD`, `GITHUB_USERNAME` and `GITHUB_TOKEN`. Optionally set `DASH_REPOS = "owner/repo,owner/other"` (up to 3) to show the latest completed workflow run per repo. Data refreshes every 5 minutes and each request blocks the screen for about a second. Select refreshes now. Back exits. Without a username and token it shows demo numbers.

Use a fine-grained token with read-only access and a short expiry, since the token sits in plain text on the badge. Review requests, PRs and issues in private repos only show up if the token can see them. Never commit real values to `secrets.py`.
