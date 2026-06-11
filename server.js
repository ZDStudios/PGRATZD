// Screen-stream relay server.
// Deployed on Render. It:
//   1. Serves the web dashboard (public/index.html)
//   2. Accepts JPEG frames from your local agent over a WebSocket
//   3. Broadcasts those frames to every browser viewing the dashboard
//
// Auth is a shared secret in the STREAM_TOKEN env var. Both the agent and
// the dashboard must present the same token or the socket is rejected.

const path = require("path");
const http = require("http");
const express = require("express");
const { WebSocketServer } = require("ws");

const PORT = process.env.PORT || 3000;
const TOKEN = process.env.STREAM_TOKEN || "changeme";

const app = express();
app.use(express.static(path.join(__dirname, "public")));
app.get("/healthz", (_req, res) => res.send("ok"));

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

const agents = new Set(); // local capture scripts pushing frames
const viewers = new Set(); // browsers watching the dashboard
let lastFrame = null; // cache so a new viewer sees something immediately

wss.on("connection", (ws, req) => {
  const url = new URL(req.url, "http://localhost");
  const role = url.searchParams.get("role");
  const token = url.searchParams.get("token");

  if (token !== TOKEN) {
    ws.close(1008, "invalid token");
    return;
  }

  if (role === "agent") {
    agents.add(ws);
    console.log(`agent connected (${agents.size} total)`);

    ws.on("message", (data) => {
      lastFrame = data;
      for (const viewer of viewers) {
        if (viewer.readyState === viewer.OPEN) {
          viewer.send(data, { binary: true });
        }
      }
    });

    ws.on("close", () => {
      agents.delete(ws);
      console.log(`agent disconnected (${agents.size} left)`);
    });
  } else {
    viewers.add(ws);
    console.log(`viewer connected (${viewers.size} total)`);

    // Tell the viewer whether a screen is currently being streamed.
    ws.send(JSON.stringify({ type: "status", agents: agents.size }));
    if (lastFrame) ws.send(lastFrame, { binary: true });

    ws.on("close", () => {
      viewers.delete(ws);
      console.log(`viewer disconnected (${viewers.size} left)`);
    });
  }
});

server.listen(PORT, () => {
  console.log(`screen-stream server listening on :${PORT}`);
  if (TOKEN === "changeme") {
    console.warn("WARNING: STREAM_TOKEN is the default. Set a real one in Render.");
  }
});
