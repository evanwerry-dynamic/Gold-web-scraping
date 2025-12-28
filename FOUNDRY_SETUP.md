# Foundry Integration Setup

This guide walks you through setting up Palantir Foundry integration with GitHub secrets for secure data transfer.

## Step 1: Get Your Foundry Credentials

### 1. Find Your Foundry Instance URL
- Log in to your Palantir Foundry instance
- Copy the base URL from your browser (e.g., `https://your-instance.palantir.com`)

### 2. Generate a Foundry API Token
1. In Foundry, go to **Settings** → **Integrations & APIs**
2. Click **Create API Token** or **Create Authentication Token**
3. Give it a name like "Gold Scraper" and set appropriate permissions
4. Copy the token (you'll only see it once!)

### 3. Get Your Dataset ID
1. In Foundry, go to the dataset where you want gold prices stored
2. Open the dataset details
3. Copy the **Dataset ID** (found in the URL or dataset settings)
   - Format: Usually looks like `ri.foundry.main.dataset.xxxxx`

## Step 2: Add Secrets to GitHub

### Via GitHub Web UI (Recommended)

1. Go to your repository on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret**
4. Add three secrets:

| Name | Value |
|------|-------|
| `FOUNDRY_URL` | `https://your-instance.palantir.com` |
| `FOUNDRY_TOKEN` | Your API token from Step 1.2 |
| `FOUNDRY_DATASET` | Your dataset ID from Step 1.3 |

### Via GitHub CLI

```bash
gh secret set FOUNDRY_URL --body "https://your-instance.palantir.com"
gh secret set FOUNDRY_TOKEN --body "your-api-token"
gh secret set FOUNDRY_DATASET --body "ri.foundry.main.dataset.xxxxx"
```

## Step 3: Test the Connection

Run a manual workflow test:

1. Go to **Actions** tab in GitHub
2. Select **Scrape Gold & Upload to Foundry** workflow
3. Click **Run workflow**
4. Check the logs for success/failure

## Step 4: Verify Automation

The workflow will now run automatically:
- **Daily at 5 PM UTC** (configurable in `.github/workflows/scrape-and-upload.yml`)
- On manual trigger via the **Actions** tab

## How It Works

1. **Scrape**: Fetches latest gold OHLC from investing.com
2. **Upload**: Posts data to your Foundry dataset via API
3. **Store locally**: Saves to `data/gold_prices.json` for backup
4. **Commit**: Auto-commits to the repository

## Data Schema in Foundry

Each record uploaded to Foundry contains:

```json
{
  "timestamp": "2025-12-28T17:00:00.123456",
  "open": 4523.5,
  "high": 4584.0,
  "low": 4518.0,
  "close": 4552.7,
  "date": "Dec 26, 2025"
}
```

## Troubleshooting

### Check GitHub Actions Logs

1. Go to **Actions** tab
2. Click on the workflow run
3. Expand steps to see detailed logs

### Common Errors

**"Missing Foundry secrets"**
- Verify all three secrets are added: `FOUNDRY_URL`, `FOUNDRY_TOKEN`, `FOUNDRY_DATASET`

**"Connection failed: 401 Unauthorized"**
- Token may be expired or invalid
- Regenerate a new token in Foundry and update `FOUNDRY_TOKEN` secret

**"404 Dataset not found"**
- Verify the dataset ID is correct
- Check that your token has access to that dataset

**"Timeout"**
- Foundry instance may be slow
- Check your network connection
- Increase timeout in workflow if needed

## Manual Upload Script

You can also upload manually from the command line:

```python
import os
from scraper.scraper import scrape_latest_from_historical
from scraper.foundry import upload_latest_price

foundry_url = os.getenv("FOUNDRY_URL")
foundry_token = os.getenv("FOUNDRY_TOKEN")
dataset_id = os.getenv("FOUNDRY_DATASET")

price_data = scrape_latest_from_historical()
success = upload_latest_price(foundry_url, foundry_token, dataset_id, price_data)
print("✓ Uploaded!" if success else "✗ Failed")
```

## Security Notes

- Tokens are stored securely in GitHub's encrypted secrets
- Never commit tokens to the repository
- Rotate tokens regularly (in Foundry settings)
- Limit token permissions to only what's needed

## Customization

### Change Scraping Time

Edit `.github/workflows/scrape-and-upload.yml`:

```yaml
  schedule:
    - cron: '0 16 * * *'  # Change to 4 PM UTC
```

Cron format: `minute hour * * day-of-week`

### Add Additional Datasets

Duplicate the upload step and use different `FOUNDRY_DATASET` values.

## Support

For Foundry API issues, see:
- [Foundry API Documentation](https://www.palantir.com/docs/)
- Your Foundry admin or support team
