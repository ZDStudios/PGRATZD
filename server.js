const path = require("path");
const http = require("http");
const express = require("express");
const { WebSocketServer } = require("ws");

const PORT            = process.env.PORT             || 3000;
const TOKEN           = process.env.STREAM_TOKEN      || "changeme";
const VIEWER_PASSWORD = process.env.VIEWER_PASSWORD   || "changeme";

const app = express();
app.use(express.static(path.join(__dirname, "public")));

// MODIFICATION: Removed the '70mb' limit constraint entirely.
// Express will now fall back to its internal or system default limits.
app.use(express.json()); 

app.get("/healthz", (_req, res) => res.send("ok"));
app.get("/api-docs.md", (_req, res) => res.sendFile(path.join(__dirname, "API.md")));

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

const agents  = new Map();
const viewers = new Map();

let agentSeq = 0;

const pendingApiRequests = new Map();
let apiSeq = 0;

function agentList() {
  return [...agents.entries()].map(([id, a]) => ({
    id, name: a.name, connectedAt: a.connectedAt,
  }));
}

function broadcast(msg) {
  const raw = JSON.stringify(msg);
  for (const [vws] of viewers)
    if (vws.readyState === vws.OPEN) vws.send(raw);
}

function apiAuth(req, res) {
  const pw = req.headers["x-password"] || req.query.password || (req.body && req.body.password);
  if (pw !== VIEWER_PASSWORD) { res.status(401).json({ error: "Invalid password" }); return false; }
  return true;
}

function findAgent(nameOrId) {
  if (agents.has(nameOrId)) return [nameOrId, agents.get(nameOrId)];
  for (const [id, agent] of agents)
    if (agent.name === nameOrId) return [id, agent];
  return [null, null];
}

function wsBridge(agent, msg, timeout) {
  // MODIFICATION: Setting timeout to 0 (or ignoring it) disables the timer logic 
  // so the pending API request will never auto-reject due to a timeout.
  timeout = 0; 
  return new Promise((resolve, reject) => {
    const apiId = "api_" + (++apiSeq);
    msg.id = apiId;
    
    // MODIFICATION: Wrapped the timer in a conditional check. If timeout is 0, 
    // no timeout logic is ever instantiated.
    let timer = null;
    if (timeout > 0) {
      timer = setTimeout(() => {
        pendingApiRequests.delete(apiId);
        reject(new Error("Request timed out after " + timeout + "ms"));
      }, timeout);
    }
    
    pendingApiRequests.set(apiId, { resolve, reject, timer, lines: [] });
    agent.ws.send(JSON.stringify(msg));
  });
}

app.get("/api/status", (req, res) => {
  if (!apiAuth(req, res)) return;
  res.json({ ok: true, devices: agents.size, viewers: viewers.size });
});

app.get("/api/devices", (req, res) => {
  if (!apiAuth(req, res)) return;
  res.json({ devices: agentList() });
});

app.post("/api/fs/:device", async (req, res) => {
  if (!apiAuth(req, res)) return;
  const [, agent] = findAgent(req.params.device);
  if (!agent) return res.status(404).json({ error: "Device not found or offline" });
  try {
    const body = Object.fromEntries(Object.entries(req.body).filter(([k]) => k !== "password"));
    // MODIFICATION: Pass 0 to indicate no timeout constraint
    const result = await wsBridge(agent, { type: "fs_req", ...body }, 0);
    res.json(result);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/api/exec/:device", async (req, res) => {
  if (!apiAuth(req, res)) return;
  const [, agent] = findAgent(req.params.device);
  if (!agent) return res.status(404).json({ error: "Device not found or offline" });
  try {
    const body = Object.fromEntries(Object.entries(req.body).filter(([k]) => k !== "password"));
    // MODIFICATION: Pass 0 to indicate no timeout constraint
    const result = await wsBridge(agent, { type: "exec_req", ...body }, 0);
    res.json(result);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/api/apps/:device", async (req, res) => {
  if (!apiAuth(req, res)) return;
  const [, agent] = findAgent(req.params.device);
  if (!agent) return res.status(404).json({ error: "Device not found or offline" });
  try {
    const body = Object.fromEntries(Object.entries(req.body).filter(([k]) => k !== "password"));
    // MODIFICATION: Pass 0 to indicate no timeout constraint
    const result = await wsBridge(agent, { type: "apps_req", ...body }, 0);
    res.json(result);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post("/api/control/:device", (req, res) => {
  if (!apiAuth(req, res)) return;
  const [, agent] = findAgent(req.params.device);
  if (!agent) return res.status(404).json({ error: "Device not found or offline" });
  const body = Object.fromEntries(Object.entries(req.body).filter(([k]) => k !== "password"));
  agent.ws.send(JSON.stringify({ type: "control", ...body }));
  res.json({ ok: true });
});

wss.on("connection", (ws, req) => {
  const url   = new URL(req.url, "http://localhost");
  const role  = url.searchParams.get("role");
  const token = url.searchParams.get("token");

  if (role === "agent") {
    if (token !== TOKEN) { ws.close(1008, "invalid token"); return; }

    const name = url.searchParams.get("name") || "Unknown Device";

    let evictedId = null;
    for (const [existingId, existingAgent] of agents) {
      if (existingAgent.name === name) {
        console.log("agent reconnect: replacing " + name + " (" + existingId + ")");
        existingAgent.replaced = true;
        existingAgent.ws.close(1001, "replaced by new connection");
        agents.delete(existingId);
        evictedId = existingId;
        break;
      }
    }

    const id    = "a" + (++agentSeq);
    const agent = { ws, name, lastFrame: null, connectedAt: Date.now() };
    agents.set(id, agent);

    if (evictedId !== null) {
      for (const [vws, vstate] of viewers)
        if (vstate.watchingId === evictedId) vstate.watchingId = id;
    }

    console.log("agent connected: " + name + " (" + id + "), total: " + agents.size);
    broadcast({ type: "agents", list: agentList() });

    ws.on("message", (data, isBinary) => {
      if (isBinary) {
        agent.lastFrame = data;
        for (const [vws, vstate] of viewers)
          if (vstate.watchingId === id && vws.readyState === vws.OPEN)
            vws.send(data, { binary: true });
      } else {
        const raw = data.toString();
        let toViewers = true;
        try {
          const m = JSON.parse(raw);
          if (m.id && pendingApiRequests.has(m.id)) {
            toViewers = false;
            const p = pendingApiRequests.get(m.id);
            if (m.type === "exec_res") {
              if (m.output !== undefined) p.lines.push({ stream: m.stream || "stdout", text: m.output });
              if (m.done) {
                // MODIFICATION: Check if timer exists before clearing it
                if (p.timer) clearTimeout(p.timer);
                pendingApiRequests.delete(m.id);
                p.resolve({ exitCode: m.exitCode != null ? m.exitCode : 0, output: p.lines });
              }
            } else {
              // MODIFICATION: Check if timer exists before clearing it
              if (p.timer) clearTimeout(p.timer);
              pendingApiRequests.delete(m.id);
              p.resolve(m);
            }
          }
        } catch (_) {}
        if (toViewers) {
          for (const [vws, vstate] of viewers)
            if (vstate.watchingId === id && vws.readyState === vws.OPEN)
              vws.send(raw);
        }
      }
    });

    ws.on("close", () => {
      agents.delete(id);
      console.log("agent disconnected: " + name + " (" + id + "), total: " + agents.size);
      if (!agent.replaced) {
        broadcast({ type: "agent_offline", id });
        broadcast({ type: "agents", list: agentList() });
      }
    });

  } else {
    const password = url.searchParams.get("password");
    if (password !== VIEWER_PASSWORD) { ws.close(1008, "invalid password"); return; }
    const vstate = { watchingId: null };
    viewers.set(ws, vstate);
    console.log("viewer connected, total: " + viewers.size);
    ws.send(JSON.stringify({ type: "agents", list: agentList() }));

    ws.on("message", (data) => {
      try {
        const msg = JSON.parse(data.toString());
        if (msg.type === "watch") {
          vstate.watchingId = msg.id;
          const agent = agents.get(msg.id);
          if (agent && agent.lastFrame) ws.send(agent.lastFrame, { binary: true });
        } else if ((msg.type === "control" || msg.type === "fs_req" || msg.type === "exec_req" || msg.type === "apps_req") && vstate.watchingId) {
          const agent = agents.get(vstate.watchingId);
          if (agent && agent.ws.readyState === agent.ws.OPEN) agent.ws.send(JSON.stringify(msg));
        }
      } catch (_) {}
    });

    ws.on("close", () => {
      viewers.delete(ws);
      console.log("viewer disconnected, total: " + viewers.size);
    });
  }
});

// MODIFICATION: Explicitly forcing the node HTTP server configuration 
// to keep connections alive infinitely (no timeout).
server.timeout = 0;
server.keepAliveTimeout = 0;

server.listen(PORT, () => {
  console.log("screen-stream server listening on :" + PORT);
  if (TOKEN === "changeme")
    console.warn("WARNING: STREAM_TOKEN is the default. Set a real one in Render.");
  if (VIEWER_PASSWORD === "changeme")
    console.warn("WARNING: VIEWER_PASSWORD is the default. Set a real one in Render.");
});
