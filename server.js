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

// agents: id -> { ws, name, lastFrame, connectedAt }
const agents = new Map();
// viewers: ws -> { watchingId }
const viewers = new Map();

let agentSeq = 0;

function agentList() {
  return [...agents.entries()].map(([id, a]) => ({
    id,
    name: a.name,
    connectedAt: a.connectedAt,
  }));
}

function broadcast(msg) {
  const raw = JSON.stringify(msg);
  for (const [vws] of viewers) {
    if (vws.readyState === vws.OPEN) vws.send(raw);
  }
}

wss.on("connection", (ws, req) => {
  const url = new URL(req.url, "http://localhost");
  const role = url.searchParams.get("role");
  const token = url.searchParams.get("token");

  if (role === "agent") {
    if (token !== TOKEN) {
      ws.close(1008, "invalid token");
      return;
    }

    const id = `a${++agentSeq}`;
    const name = url.searchParams.get("name") || "Unknown Device";
    const agent = { ws, name, lastFrame: null, connectedAt: Date.now() };
    agents.set(id, agent);
    console.log(`agent connected: ${name} (${id}), total: ${agents.size}`);
    broadcast({ type: "agents", list: agentList() });

    ws.on("message", (data, isBinary) => {
      if (isBinary) {
        agent.lastFrame = data;
        for (const [vws, vstate] of viewers) {
          if (vstate.watchingId === id && vws.readyState === vws.OPEN) {
            vws.send(data, { binary: true });
          }
        }
      } else {
        // JSON response from agent (fs_res etc.) — forward to viewers watching it
        const raw = data.toString();
        for (const [vws, vstate] of viewers) {
          if (vstate.watchingId === id && vws.readyState === vws.OPEN) {
            vws.send(raw);
          }
        }
      }
    });

    ws.on("close", () => {
      agents.delete(id);
      console.log(`agent disconnected: ${name} (${id}), total: ${agents.size}`);
      broadcast({ type: "agent_offline", id });
      broadcast({ type: "agents", list: agentList() });
    });

  } else {
    const vstate = { watchingId: null };
    viewers.set(ws, vstate);
    console.log(`viewer connected, total: ${viewers.size}`);

    ws.send(JSON.stringify({ type: "agents", list: agentList() }));

    ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.type === "watch") {
          vstate.watchingId = msg.id;
          const agent = agents.get(msg.id);
          if (agent?.lastFrame) ws.send(agent.lastFrame, { binary: true });
        } else if ((msg.type === "control" || msg.type === "fs_req" || msg.type === "exec_req") && vstate.watchingId) {
          const agent = agents.get(vstate.watchingId);
          if (agent?.ws.readyState === agent.ws.OPEN) {
            agent.ws.send(JSON.stringify(msg));
          }
        }
      } catch (_) {}
    });

    ws.on("close", () => {
      viewers.delete(ws);
      console.log(`viewer disconnected, total: ${viewers.size}`);
    });
  }
});

server.listen(PORT, () => {
  console.log(`screen-stream server listening on :${PORT}`);
  if (TOKEN === "changeme") {
    console.warn("WARNING: STREAM_TOKEN is the default. Set a real one in Render.");
  }
});
