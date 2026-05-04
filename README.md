# GEX Dashboard — Auto-Refreshing on GitHub Pages

Self-hosted Gamma Exposure dashboard for AAPL, SPY, QQQ, NVDA, TSLA, MSFT.
Free CBOE delayed quotes. Auto-refreshes via GitHub Actions. Bookmark on phone.

## Setup (one-time, ~10 minutes)

### 1. Create a GitHub account
- Go to [github.com](https://github.com) and sign up if you don't have one.

### 2. Create a new repository
- Click the **+** in top right → **New repository**.
- Name: `gex-dashboard` (or anything you like).
- Set **Public** (required for free GitHub Pages).
- **Do NOT** initialize with README/license/.gitignore — leave it empty.
- Click **Create repository**.

### 3. Upload these files to the repo
You have two paths — pick one:

#### Option A: Web upload (easiest, no Git needed)
- On the empty repo page, click **uploading an existing file**.
- Drag and drop **all the files in this folder**, including the hidden `.github` folder.
  - ⚠️ On Mac: press `Cmd + Shift + .` in Finder to show hidden folders before dragging.
  - ⚠️ The `.github/workflows/build.yml` file MUST be uploaded with its folder structure intact.
- Scroll down → write commit message "initial" → click **Commit changes**.

#### Option B: Git command line
```bash
cd gex-dashboard
git init
git add .
git commit -m "initial"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/gex-dashboard.git
git push -u origin main
```

### 4. Enable GitHub Pages
- In your repo, click **Settings** (top tab).
- Scroll left sidebar → **Pages**.
- Under "Build and deployment" → **Source** → select **GitHub Actions**.
- That's it — no other config.

### 5. Trigger the first build
- Click **Actions** tab.
- Click the **Build GEX Dashboard** workflow on the left.
- Click **Run workflow** → **Run workflow** (green button).
- Wait ~1 minute. Green checkmark = success.

### 6. Find your URL
- Go back to **Settings → Pages**.
- The URL appears at the top: `https://YOUR_USERNAME.github.io/gex-dashboard/`
- Open it on your phone. Add to home screen for app-like access.

## What it does

- Runs Python on GitHub's servers (free, no laptop required)
- Pulls fresh CBOE quotes every 30 min during peak market hours, hourly otherwise
- Pulls macro events (NFP, FOMC, CPI etc) from Forex Factory
- Pulls earnings dates per ticker from Nasdaq
- Computes Gamma Exposure (GEX) per strike
- Generates calendar spread trade recommendations with concrete strike, expiries, debit, stop, target
- Auto-downgrades verdict from TRADE → REDUCE if critical events fall in window
- Builds per-ticker dashboards + index page
- Publishes to your public GitHub Pages URL
- Bookmark the URL — always shows latest data

## Schedule (edit in `.github/workflows/build.yml`)

The schedule uses **redundant cron triggers** to combat GitHub Actions queue
delays during peak hours. Each target time fires twice (e.g. 9:25 AM and 9:35 AM
for the 9:30 AM target) so at least one fires close to the intended time.

**First 4 hours of US market — every 30 minutes** (most active price discovery)

| Target ET | MYT |
|---|---|
| 9:30 AM (open) | 9:30 PM |
| 10:00 AM | 10:00 PM |
| 10:30 AM | 10:30 PM |
| 11:00 AM | 11:00 PM |
| 11:30 AM | 11:30 PM |
| 12:00 PM | 12:00 AM next day |
| 12:30 PM | 12:30 AM |
| 1:00 PM | 1:00 AM |
| 1:30 PM | 1:30 AM |

**Rest of market + post-close — every hour**

| Target ET | MYT |
|---|---|
| 2:30 PM | 2:30 AM |
| 3:30 PM | 3:30 AM |
| 4:00 PM (close) | 4:00 AM |
| 5:00 PM (EOD update) | 5:00 AM |

**Next-morning review** (perfect for MYT timezone)

| UTC | MYT |
|---|---|
| 01:00 + 01:15 | 9:00 / 9:15 AM |

Total: ~26 builds per US trading day. You can also trigger manually anytime
via Actions tab → Run workflow.

## Add to phone home screen (iPhone)

1. Open the URL in Safari.
2. Tap the share button (square with arrow up).
3. Scroll down → **Add to Home Screen**.
4. Name it "GEX" → Add.

The icon appears like a real app. Tap to open instantly.

## Add to phone home screen (Android)

1. Open the URL in Chrome.
2. Tap the three-dot menu.
3. Tap **Add to Home screen** → Add.

## Customize tickers

Edit `build_all.py`, line 17:

```python
TICKERS = ["AAPL", "SPY", "QQQ", "NVDA", "TSLA", "MSFT"]
```

Add/remove any CBOE-listed ticker. Commit + push → next workflow run rebuilds.

## Troubleshooting

| Issue | Fix |
|---|---|
| Workflow fails on first run | Check Actions log. CBOE may have rate-limited — re-run. |
| 404 on the URL | Pages takes 1-2 min to deploy after first build. Refresh. |
| One ticker shows error | CBOE doesn't have data for that symbol. Remove from TICKERS. |
| Workflow doesn't run on schedule | GitHub disables scheduled workflows after 60 days of repo inactivity. Push any commit to reactivate. |

## Cost

**Free.**
- GitHub Pages: free for public repos.
- GitHub Actions: 2,000 free minutes/month for private; unlimited for public. Each run takes ~1 min, so even 30 runs/day = 900 min/month.
- CBOE delayed quotes: free, no API key needed.

## Limitations

- Data is **delayed 15 minutes** (CBOE free feed limitation).
- Naive GEX model — assumes dealers short customer flow.
- Public repo means anyone could view your dashboard URL. Data is public anyway.
- For private dashboards, you'd need GitHub Pro ($4/month) for private Pages.

## Not investment advice
