# Deployment Guide (Railway)

## Prerequisites

1. GitHub account with this repository
2. Railway account ([railway.app](https://railway.app))
3. Required API keys:
   - Cielo Finance API key
   - OpenRouter API key
   - Google OAuth credentials
   - JWT secret (generate with `openssl rand -hex 32`)

---

## Step-by-Step Deployment

### 1. Create Railway Account

1. Go to [railway.app](https://railway.app)
2. Click "Login with GitHub"
3. Authorize Railway to access your repositories

### 2. Create New Project

1. Click "New Project"
2. Select "Deploy from GitHub repo"
3. Choose `wallet-analyzer` repository
4. Railway will detect Python and start building

### 3. Configure Environment Variables

Click on your service → Variables → Add all from `.env.example`:

**Required:**
```
CIELO_API_KEY=your_cielo_api_key
OPENROUTER_API_KEY=your_openrouter_api_key
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
JWT_SECRET=your_jwt_secret
```

**Optional:**
```
AUTO_CLASSIFY_ENABLED=false
FULL_CHRONOLOGY_COUNT=1
CONTEXT_COMPRESSION_ENABLED=true
```

### 4. Add Persistent Volume

1. Right-click on service `wallet-analyzer` → "Attach volume"
2. Click on the created volume → Settings
3. Configure:
   - Mount path: `/app/data`
   - Size: 1 GB (or more if needed)
4. Save

**Note:** Reports are stored inside `/app/data/reports/` (same volume)

### 5. Deploy

1. Railway will automatically deploy after adding variables
2. Wait for build to complete (~5-10 minutes first time)
3. Check logs for any errors
4. Your app will be available at: `https://your-app.railway.app`

---

## Post-Deployment

### Check Status

- **Logs**: Service → Logs (check for errors)
- **Metrics**: Service → Metrics (CPU, RAM usage)
- **Volume**: Settings → Volumes (check disk usage)

### Update Google OAuth

Add Railway URL to Google OAuth:
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. APIs & Services → Credentials
3. Edit your OAuth 2.0 Client
4. Add to "Authorized JavaScript origins":
   ```
   https://your-app.railway.app
   ```
5. Add to "Authorized redirect URIs":
   ```
   https://your-app.railway.app/api/auth/google/callback
   ```

### Monitor Costs

- Railway Hobby: $5/month free credits
- Check usage: Settings → Usage
- Expected: ~$0.02/hour = ~$15/month if running 24/7
- With auto-sleep (if available): can fit in $5/month

---

## Troubleshooting

### Build fails
- Check logs for missing dependencies
- Verify `requirements.txt` is up to date

### Frontend not loading
- Ensure frontend build succeeded
- Check logs for "Frontend dist folder not found"
- Verify `frontend/dist` exists after build

### Playwright errors
- Playwright requires `--with-deps` flag (included in `start.sh`)
- Check logs for Chromium installation errors

### Database errors
- Ensure volumes are mounted correctly
- Check `data/` and `reports/` paths in logs

### Out of credits
- Optimize resources: reduce CPU/RAM in Settings
- Enable auto-sleep (if available)
- Consider upgrading to Pro plan ($20/month)

---

## Local Development vs Production

| Feature | Local | Production (Railway) |
|---------|-------|---------------------|
| Frontend | Vite dev server (5173) | Static files served by FastAPI |
| Backend | uvicorn (8000) | uvicorn on $PORT |
| CORS | localhost only | Railway domain auto-added |
| Data | Local files | Persistent Volumes |
| Playwright | Requires manual install | Auto-installed in `start.sh` |

---

## Useful Commands

```bash
# View logs
railway logs

# SSH into container
railway shell

# Check environment variables
railway variables

# Redeploy
git push origin main  # Auto-deploys via GitHub webhook
```

---

## Need Help?

- Railway Docs: https://docs.railway.app
- Railway Discord: https://discord.gg/railway
- Project Issues: https://github.com/AlexanderKonopatsky/wallet-analyzer/issues
