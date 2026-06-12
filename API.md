# PGRATZD REST API

Base URL: `https://pgratzd.onrender.com`

All endpoints require authentication via the **`X-Password`** header (or a `password` field in the request body / `?password=` query param).

---

## Authentication

| Method | Value |
|--------|-------|
| Header | `X-Password: <your-viewer-password>` |
| Body field | `"password": "<your-viewer-password>"` |
| Query param | `?password=<your-viewer-password>` |

Unauthorized requests return `401 { "error": "Invalid password" }`.

---

## Endpoints

### Server Status

**`GET /api/status`**

Returns server health and connection counts.

```http
GET /api/status
X-Password: yourpassword
```

```json
{
  "ok": true,
  "devices": 2,
  "viewers": 1
}
```

---

### List Devices

**`GET /api/devices`**

Returns all currently connected agents.

```http
GET /api/devices
X-Password: yourpassword
```

```json
{
  "devices": [
    {
      "id": "a3",
      "name": "Zayn-PC",
      "connectedAt": 1749687600000
    }
  ]
}
```

> **Tip:** Use the device **name** (e.g. `Zayn-PC`) as the `{device}` parameter in all other endpoints — it is stable across reconnects. The `id` changes every reconnect.

---

### File System

**`POST /api/fs/{device}`**

Perform file system operations on the remote machine.

#### List directory

```http
POST /api/fs/Zayn-PC
X-Password: yourpassword
Content-Type: application/json

{ "op": "list", "path": "C:\\" }
```

Response:
```json
{
  "type": "fs_res",
  "path": "C:\\",
  "entries": [
    { "name": "Users", "isDir": true, "size": 0, "mtime": "2024-01-01T00:00:00Z" },
    { "name": "pagefile.sys", "isDir": false, "size": 8589934592, "mtime": "2024-01-01T00:00:00Z" }
  ]
}
```

#### Download a file

```http
POST /api/fs/Zayn-PC
Content-Type: application/json

{ "op": "download", "path": "C:\\Users\\Zayn\\notes.txt" }
```

Response:
```json
{ "type": "fs_res", "content": "<base64-encoded file contents>" }
```

#### Upload a file

```http
POST /api/fs/Zayn-PC
Content-Type: application/json

{
  "op": "upload",
  "path": "C:\\Users\\Zayn\\hello.txt",
  "content": "<base64-encoded content>"
}
```

Response:
```json
{ "type": "fs_res", "ok": true }
```

#### Create folder

```http
POST /api/fs/Zayn-PC
Content-Type: application/json

{ "op": "mkdir", "path": "C:\\Users\\Zayn\\NewFolder" }
```

#### Delete file or folder

```http
POST /api/fs/Zayn-PC
Content-Type: application/json

{ "op": "delete", "path": "C:\\Users\\Zayn\\old.txt" }
```

#### Rename / Move

```http
POST /api/fs/Zayn-PC
Content-Type: application/json

{ "op": "rename", "path": "C:\\Users\\Zayn\\old.txt", "newName": "new.txt" }
```

---

### Execute Command

**`POST /api/exec/{device}`**

Run a PowerShell command on the remote machine. Waits for the command to finish (up to 65 seconds) and returns all output.

```http
POST /api/exec/Zayn-PC
X-Password: yourpassword
Content-Type: application/json

{ "command": "Get-Process | Select-Object -First 5 | Format-Table -AutoSize" }
```

Response:
```json
{
  "exitCode": 0,
  "output": [
    { "stream": "stdout", "text": " NPM(K)    PM(M)      WS(M)     CPU(s)     Id  SI ProcessName\n" },
    { "stream": "stdout", "text": " ------    -----      -----     ------     --  -- -----------\n" }
  ]
}
```

> Commands run in PowerShell with no window. Long-running commands will time out at 65 seconds.

---

### Apps

**`POST /api/apps/{device}`**

#### List installed apps

```http
POST /api/apps/Zayn-PC
X-Password: yourpassword
Content-Type: application/json

{ "op": "list" }
```

Response:
```json
{
  "type": "apps_res",
  "apps": [
    {
      "name": "Google Chrome",
      "path": "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Google Chrome.lnk",
      "icon": "<base64-png>"
    }
  ]
}
```

#### Launch an app

```http
POST /api/apps/Zayn-PC
X-Password: yourpassword
Content-Type: application/json

{ "op": "launch", "path": "C:\\ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\\Google Chrome.lnk" }
```

Response:
```json
{ "type": "apps_res", "ok": true }
```

---

### Mouse & Keyboard Control

**`POST /api/control/{device}`**

Fire-and-forget mouse/keyboard events. Returns immediately — does not wait for the agent to process the event.

#### Mouse move

```http
POST /api/control/Zayn-PC
X-Password: yourpassword
Content-Type: application/json

{ "event": "mousemove", "x": 0.5, "y": 0.3 }
```

> `x` and `y` are **normalized** (0–1), relative to the screen size.

#### Mouse click

```http
{ "event": "mousedown", "button": 0, "x": 0.5, "y": 0.3 }
{ "event": "mouseup",   "button": 0 }
```

> `button`: 0 = left, 1 = middle, 2 = right

#### Scroll

```http
{ "event": "scroll", "dy": 120, "mode": 0 }
```

#### Key press

```http
{ "event": "keydown", "code": "KeyA" }
{ "event": "keyup",   "code": "KeyA" }
```

> `code` uses [KeyboardEvent.code](https://developer.mozilla.org/en-US/docs/Web/API/KeyboardEvent/code) values: `"KeyA"`, `"Enter"`, `"Space"`, `"ArrowUp"`, etc.

All control endpoints return `{ "ok": true }` on success.

---

## curl Examples

```bash
BASE="https://pgratzd.onrender.com"
PW="yourpassword"
DEVICE="Zayn-PC"

# Status
curl -H "X-Password: $PW" "$BASE/api/status"

# List devices
curl -H "X-Password: $PW" "$BASE/api/devices"

# List C:\ directory
curl -H "X-Password: $PW" -H "Content-Type: application/json" \
  -d '{"op":"list","path":"C:\\"}' "$BASE/api/fs/$DEVICE"

# Run a command
curl -H "X-Password: $PW" -H "Content-Type: application/json" \
  -d '{"command":"whoami"}' "$BASE/api/exec/$DEVICE"

# Click at center of screen
curl -H "X-Password: $PW" -H "Content-Type: application/json" \
  -d '{"event":"mousedown","button":0,"x":0.5,"y":0.5}' "$BASE/api/control/$DEVICE"
```

---

## Python Example

```python
import requests, base64

BASE = "https://pgratzd.onrender.com"
HEADERS = {"X-Password": "yourpassword", "Content-Type": "application/json"}
DEVICE = "Zayn-PC"

# List devices
devices = requests.get(f"{BASE}/api/devices", headers=HEADERS).json()
print(devices)

# Run a command
result = requests.post(f"{BASE}/api/exec/{DEVICE}", headers=HEADERS,
    json={"command": "Get-Date"}).json()
print(result)

# Download a file
res = requests.post(f"{BASE}/api/fs/{DEVICE}", headers=HEADERS,
    json={"op": "download", "path": "C:\\Users\\Zayn\\notes.txt"}).json()
data = base64.b64decode(res["content"])
print(data.decode())

# Upload a file
content_b64 = base64.b64encode(b"Hello from API!").decode()
requests.post(f"{BASE}/api/fs/{DEVICE}", headers=HEADERS,
    json={"op": "upload", "path": "C:\\Users\\Zayn\\api_test.txt", "content": content_b64})
```

---

## Error Responses

| Status | Meaning |
|--------|---------|
| `401` | Wrong or missing password |
| `404` | Device not found / offline |
| `500` | Agent returned an error or request timed out |

All errors return `{ "error": "message" }`.