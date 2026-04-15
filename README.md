# SecondBrain — Deploy to Render (Free)

## Files
- `app.py` — the full Flask app
- `requirements.txt` — Python deps
- `render.yaml` — Render config

## Steps

### 1. Push to GitHub
```bash
git init
git add .
git commit -m "SecondBrain"
gh repo create secondbrain --private --push --source=.
```

### 2. Deploy on Render
1. Go to https://render.com and sign in (free account)
2. Click **New → Web Service**
3. Connect your GitHub repo
4. Render will auto-detect `render.yaml` — just click **Deploy**

### 3. Set your Groq API key
In the Render dashboard → your service → **Environment**:
- Add `GROQ_API_KEY` = your key from console.groq.com

That's it. Render gives you:
- Free HTTPS URL (e.g. `secondbrain.onrender.com`)
- 1 GB persistent disk for SQLite (graphs saved per user)
- Auto-restarts, zero config

## Local dev
```bash
pip install -r requirements.txt
GROQ_API_KEY=your_key python app.py
# Open http://localhost:4000
```

## Notes
- Each user's graph is stored separately in SQLite
- "Keep me signed in" = 30-day session cookie
- On the free Render plan, the service sleeps after 15 min idle (cold start ~30s)
  → upgrade to Starter ($7/mo) to avoid this
