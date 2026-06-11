# Screen Stream

View your computer's screen live in a web dashboard hosted on Render.

- **Server** (`server.js` + `public/`) — Node app that runs on Render. Hosts the dashboard and relays frames.
- **Agent** (`agent/agent.py`) — Python script that runs on your computer. Captures your screen and streams it up.

```
Your PC  ──(agent.py, WebSocket)──>  Render server  ──>  Browser dashboard
```

## 1. Deploy the server to Render

1. Push this folder to a GitHub repo.
2. On [render.com](https://render.com): **New → Web Service**, point it at the repo.
   Render reads `render.yaml` automatically:
   - Build: `npm install`
   - Start: `node server.js`
   - It generates a random `STREAM_TOKEN` for you.
3. After deploy, open the service → **Environment** tab and copy the value of
   `STREAM_TOKEN`. You'll need it for both the agent and the dashboard.
4. Your dashboard is now live at `https://<your-app>.onrender.com`.

> No `render.yaml`? Just set the build command to `npm install`, the start
> command to `node server.js`, and add an env var `STREAM_TOKEN` with a secret
> of your choosing.

## 2. Run the agent on your computer

You need [Python 3.9+](https://python.org).

```powershell
cd agent
pip install -r requirements.txt

# Use wss:// (not https://) and your real values:
$env:SERVER_URL  = "wss://your-app.onrender.com"
$env:STREAM_TOKEN = "the-token-from-render"
python agent.py
```

You should see `streaming at ~4 fps`.

## 3. Watch your screen

1. Open `https://<your-app>.onrender.com` in any browser.
2. Type the same `STREAM_TOKEN` into the access-token box.
3. Click **Connect**. Your screen appears.

## Tuning (optional env vars for the agent)

| Variable    | Default | Meaning                          |
| ----------- | ------- | -------------------------------- |
| `FPS`       | `4`     | Frames per second                |
| `MAX_WIDTH` | `1280`  | Downscale width (smaller = less bandwidth) |
| `QUALITY`   | `60`    | JPEG quality, 1–100              |

Example for a smoother, higher-quality stream (more bandwidth):

```powershell
$env:FPS=8; $env:MAX_WIDTH=1600; $env:QUALITY=70; python agent.py
```

## Notes

- This is one-way (screen only — no audio, no remote control).
- Render's **free** web service sleeps after inactivity and may cold-start;
  the agent auto-reconnects. For an always-on stream use a paid instance.
- Anyone with the URL **and** the token can watch, so keep the token private.
  Rotate it by changing `STREAM_TOKEN` in Render and restarting the agent.
- On Windows, `screenshot-desktop` captures the primary monitor by default.
