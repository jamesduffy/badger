# GH Dash

Show your open pull requests, review requests, assigned issues and the latest CI result for a few repos.

## Usage

Needs Wi-Fi and a GitHub token. Set these in `secrets.py` on the badge: `WIFI_SSID`, `WIFI_PASSWORD`, `GITHUB_USERNAME` and `GITHUB_TOKEN`. Optionally set `DASH_REPOS = "owner/repo,owner/other@branch"` (up to 3) to show the latest completed workflow run per repo. Without `@branch` it is the latest completed run on any branch, including PR branches, so add the branch (for example `@main`) if you want the main build. Bad entries are ignored and the footer says so.

Data refreshes every 5 minutes. Each request blocks the screen for about a second, so a small spinner and "updating..." show first. Select refreshes now. Back exits. If a refresh fails the old value stays up, dimmed, with its age. Without a username and token it shows demo numbers.

## Token

The token sits in plain text on the badge, so use a short expiry and read-only access. A fine-grained token can only reach resources owned by one user or one organization, plus public repos. It cannot search across several orgs. Pick the owner that has the repos you care about, and give it read access to Actions, Issues and Pull requests (Metadata is added for you). If you need results from several orgs, use a classic token with the `repo` scope (and authorize it for SSO orgs), which is broader, so keep the expiry short. Private repos only show up if the token can see them. Never commit real values to `secrets.py`.

The app only makes read requests: three searches and one runs lookup per repo, at most 6 requests per 5 minutes. The token is never drawn or logged, and is stripped from any error text.
