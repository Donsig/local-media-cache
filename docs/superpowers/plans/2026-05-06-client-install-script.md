# Client Install Script — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve a parameterized bash installer from `GET /api/install.sh?client_id=<id>` and surface a copy-paste curl command per client in the UI, so provisioning a new Raspberry Pi satellite takes one command.

**Architecture:** The server renders a self-contained bash script with `SERVER_URL`, `CLIENT_ID`, and `CLIENT_TOKEN` baked in; everything else (library path) is prompted interactively. The script installs Python, aria2, creates a `syncarr` user, creates a venv, installs the agent from GitHub, writes config files, and enables systemd services. The UI reads the token from `localStorage` to construct the full authenticated curl command and shows it inline under each client row.

**Tech Stack:** Python (stdlib `string.Template`), FastAPI `PlainTextResponse`, React (inline state, no new components)

---

## File Map

| File | Change | Responsibility |
|------|--------|---------------|
| `server/src/syncarr_server/install_script.py` | Create | Bash script template + `render()` function |
| `server/src/syncarr_server/routes/installer.py` | Modify | `GET /install.sh` endpoint |
| `server/tests/test_installer.py` | Create | Endpoint tests |
| `ui/src/screens/ClientsScreen.tsx` | Modify | Install button + curl command display per client |

---

## Task 1: Bash script template module

**Files:**
- Create: `server/src/syncarr_server/install_script.py`

The script installs into a venv at `/opt/syncarr-agent/venv` to avoid PEP 668 "externally managed environment" errors on modern Debian/Raspberry Pi OS. The agent binary path in the systemd unit must match.

Note: the systemd unit files embedded in the script are slightly adjusted from `agent/systemd/` — `ExecStart` points at the venv binary.

- [ ] **Step 1: Create the template module**

Create `server/src/syncarr_server/install_script.py`:

```python
"""Bash installer script template for satellite agents."""

from __future__ import annotations

from string import Template

# $SERVER_URL, $CLIENT_ID, $CLIENT_TOKEN are substituted at request time.
_TEMPLATE = Template(r"""#!/usr/bin/env bash
# Syncarr satellite agent installer
# Client: $CLIENT_ID  |  Server: $SERVER_URL
#
# Run as root on the target device:
#   sudo bash <(curl -fsSL -H "Authorization: Bearer TOKEN" SERVER/api/install.sh?client_id=ID)

set -euo pipefail

SYNCARR_SERVER_URL="$SERVER_URL"
SYNCARR_CLIENT_ID="$CLIENT_ID"
SYNCARR_CLIENT_TOKEN="$CLIENT_TOKEN"

VENV=/opt/syncarr-agent/venv
CONFIG_DIR=/etc/syncarr-agent
STATE_DIR=/var/lib/syncarr
AGENT_REPO="https://github.com/Donsig/local-media-cache.git"
AGENT_SUBDIR="agent"

# ── helpers ────────────────────────────────────────────────────────────────────
log()  { printf '\033[1;34m[syncarr]\033[0m %s\n' "$$*"; }
ok()   { printf '\033[1;32m[ok]\033[0m %s\n' "$$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$$*" >&2; exit 1; }

# ── checks ─────────────────────────────────────────────────────────────────────
[ "$$(id -u)" -eq 0 ] || die "Must run as root. Prefix with: sudo"

# ── prompt ─────────────────────────────────────────────────────────────────────
read -rp $'\nMedia library path on this device [/mnt/media]: ' LIBRARY_ROOT
LIBRARY_ROOT="$${LIBRARY_ROOT:-/mnt/media}"
echo

# ── apt packages ───────────────────────────────────────────────────────────────
log "Installing system packages (python3, pip, git, aria2)..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    python3 python3-pip python3-venv git aria2

# ── syncarr system user ────────────────────────────────────────────────────────
log "Creating syncarr user..."
id -u syncarr &>/dev/null \
    || useradd --system --create-home --home-dir "$${STATE_DIR}" \
               --shell /usr/sbin/nologin syncarr

# ── directories ────────────────────────────────────────────────────────────────
mkdir -p "$${CONFIG_DIR}" "$${STATE_DIR}"
chown syncarr:syncarr "$${STATE_DIR}"

# ── python venv + agent ────────────────────────────────────────────────────────
log "Installing syncarr-agent into $${VENV}..."
mkdir -p "$(dirname "$${VENV}")"
python3 -m venv "$${VENV}"
"$${VENV}/bin/pip" install --quiet --upgrade pip
"$${VENV}/bin/pip" install --quiet \
    "git+$${AGENT_REPO}#subdirectory=$${AGENT_SUBDIR}"

# ── aria2 secret ───────────────────────────────────────────────────────────────
# Preserve existing secret if re-running installer; generate new one otherwise.
if [ -f "$${CONFIG_DIR}/aria2.env" ]; then
    ARIA2_SECRET="$$(grep -oP '(?<=ARIA2_SECRET=)\S+' "$${CONFIG_DIR}/aria2.env" || true)"
fi
ARIA2_SECRET="$${ARIA2_SECRET:-$$("$${VENV}/bin/python" -c 'import secrets; print(secrets.token_hex(24))')}"

# ── config.toml ────────────────────────────────────────────────────────────────
log "Writing $${CONFIG_DIR}/config.toml..."
cat > "$${CONFIG_DIR}/config.toml" << TOML
server_url = "$${SYNCARR_SERVER_URL}"
token = "$${SYNCARR_CLIENT_TOKEN}"
library_root = "$${LIBRARY_ROOT}"
poll_interval_seconds = 300
aria2_host = "127.0.0.1"
aria2_port = 6800
aria2_secret = "$${ARIA2_SECRET}"
TOML
chmod 640 "$${CONFIG_DIR}/config.toml"
chown root:syncarr "$${CONFIG_DIR}/config.toml"

# ── aria2.env ──────────────────────────────────────────────────────────────────
cat > "$${CONFIG_DIR}/aria2.env" << ENV
ARIA2_SECRET=$${ARIA2_SECRET}
ENV
chmod 640 "$${CONFIG_DIR}/aria2.env"
chown root:syncarr "$${CONFIG_DIR}/aria2.env"

# ── aria2 session file ─────────────────────────────────────────────────────────
touch "$${STATE_DIR}/aria2.session"
chown syncarr:syncarr "$${STATE_DIR}/aria2.session"

# ── systemd: aria2 ────────────────────────────────────────────────────────────
cat > /etc/systemd/system/aria2.service << 'UNIT'
[Unit]
Description=aria2 download daemon

[Service]
Type=simple
EnvironmentFile=/etc/syncarr-agent/aria2.env
ExecStart=/usr/bin/aria2c \
  --enable-rpc \
  --rpc-listen-port=6800 \
  --rpc-secret=${ARIA2_SECRET} \
  --save-session=/var/lib/syncarr/aria2.session \
  --input-file=/var/lib/syncarr/aria2.session \
  --save-session-interval=60 \
  --max-concurrent-downloads=1 \
  --continue=true
User=syncarr
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

# ── systemd: syncarr-agent ────────────────────────────────────────────────────
cat > /etc/systemd/system/syncarr-agent.service << UNIT
[Unit]
Description=Syncarr media sync agent
After=network-online.target aria2.service
Wants=aria2.service

[Service]
Type=simple
ExecStart=$${VENV}/bin/syncarr-agent --config $${CONFIG_DIR}/config.toml
Restart=on-failure
RestartSec=60
User=syncarr

[Install]
WantedBy=multi-user.target
UNIT

# ── enable & start ────────────────────────────────────────────────────────────
log "Reloading systemd and enabling services..."
systemctl daemon-reload
systemctl enable --now aria2.service syncarr-agent.service

echo
ok "Installation complete!"
ok "Client ID : $${SYNCARR_CLIENT_ID}"
ok "Server    : $${SYNCARR_SERVER_URL}"
ok "Library   : $${LIBRARY_ROOT}"
ok "Agent     : $$(systemctl is-active syncarr-agent.service)"
""")


def render(server_url: str, client_id: str, client_token: str) -> str:
    """Return the installer script with baked-in server URL and credentials."""
    return _TEMPLATE.safe_substitute(
        SERVER_URL=server_url,
        CLIENT_ID=client_id,
        CLIENT_TOKEN=client_token,
    )
```

- [ ] **Step 2: Verify the module imports cleanly**

```bash
cd server
python3 -c "from syncarr_server.install_script import render; print(render('http://home:8000', 'caravan', 'tok-abc')[:120])"
```

Expected: prints first 120 chars of the script starting with `#!/usr/bin/env bash`.

---

## Task 2: Server endpoint

**Files:**
- Modify: `server/src/syncarr_server/routes/installer.py`

The endpoint requires UI auth so random callers can't fetch tokens. It fetches the `Client` row to confirm it exists, derives `server_url` from the incoming `Request` object (so it works behind Tailscale or a reverse proxy), and returns `text/x-shellscript`.

- [ ] **Step 1: Implement the endpoint**

Replace the empty `installer.py` with:

```python
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from syncarr_server.auth import require_ui_auth
from syncarr_server.db import get_session
from syncarr_server.install_script import render
from syncarr_server.models import Client

router = APIRouter(tags=["installer"])


@router.get(
    "/install.sh",
    response_class=PlainTextResponse,
    dependencies=[Depends(require_ui_auth)],
)
async def get_install_script(
    client_id: str,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PlainTextResponse:
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Client {client_id!r} not found",
        )

    server_url = str(request.base_url).rstrip("/")
    script = render(
        server_url=server_url,
        client_id=client.id,
        client_token=client.auth_token,
    )
    return PlainTextResponse(content=script, media_type="text/x-shellscript")
```

- [ ] **Step 2: Verify the server still starts**

```bash
cd server
python3 -c "from syncarr_server.main import app; print('OK')"
```

Expected: `OK` (with possible pydantic_settings warning about `/run/secrets`).

---

## Task 3: Endpoint tests

**Files:**
- Create: `server/tests/test_installer.py`

Tests use the same `http_client` and `auth_headers_ui` fixtures already in `conftest.py`. The HTTP client base URL is `http://testserver`, so `request.base_url` returns `http://testserver/`.

- [ ] **Step 1: Write the failing tests**

Create `server/tests/test_installer.py`:

```python
from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_install_script_returns_script(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    # First create a client so it exists in DB
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan", "name": "Caravan"},
    )

    response = await http_client.get(
        "/install.sh",
        headers=auth_headers_ui,
        params={"client_id": "caravan"},
    )

    assert response.status_code == 200
    assert "text/x-shellscript" in response.headers["content-type"]
    body = response.text
    assert "#!/usr/bin/env bash" in body
    assert "http://testserver" in body   # server_url baked in
    assert "caravan" in body             # client_id baked in
    # token baked in (starts with "agent-caravan-")
    assert "agent-caravan-" in body


async def test_install_script_requires_auth(
    http_client: AsyncClient,
) -> None:
    response = await http_client.get(
        "/install.sh",
        params={"client_id": "caravan"},
    )
    assert response.status_code == 401


async def test_install_script_404_unknown_client(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    response = await http_client.get(
        "/install.sh",
        headers=auth_headers_ui,
        params={"client_id": "ghost"},
    )
    assert response.status_code == 404


async def test_install_script_is_idempotent(
    http_client: AsyncClient,
    auth_headers_ui: dict[str, str],
) -> None:
    """Script contains the idempotency guard for the aria2 secret."""
    await http_client.post(
        "/clients",
        headers=auth_headers_ui,
        json={"id": "caravan2", "name": "Caravan 2"},
    )
    response = await http_client.get(
        "/install.sh",
        headers=auth_headers_ui,
        params={"client_id": "caravan2"},
    )
    assert response.status_code == 200
    # Idempotency guard: script preserves existing aria2 secret on re-run
    assert "ARIA2_SECRET" in response.text
    assert "grep" in response.text
```

- [ ] **Step 2: Run tests — expect them to fail**

```bash
cd server
python3 -m pytest tests/test_installer.py -v
```

Expected: all 4 tests FAIL (endpoint not implemented yet — but Task 2 should already be done, so they should PASS here).

- [ ] **Step 3: Run the full test suite to check for regressions**

```bash
cd server
python3 -m pytest -v
```

Expected: all existing tests pass, 4 new tests pass.

- [ ] **Step 4: Commit**

```bash
cd /home/claude/workspace/local-media-cache
git add server/src/syncarr_server/install_script.py \
        server/src/syncarr_server/routes/installer.py \
        server/tests/test_installer.py
git commit -m "feat(server): GET /install.sh — parameterized satellite installer"
```

---

## Task 4: UI — Install button and curl command display

**Files:**
- Modify: `ui/src/screens/ClientsScreen.tsx`

Each client row gets an "Install" button. Clicking toggles an inline panel (no modal, no new component) beneath the row showing:
1. The curl command with the UI token read from `localStorage`
2. A reminder that it must be run as root
3. A copy button

The UI token is already stored in `localStorage` under key `'ui_token'` by the existing auth flow (see `api.ts` `request()` function).

The server URL is `window.location.origin` — works for both dev and production.

- [ ] **Step 1: Add per-client install state and the install panel**

Replace the client list section in `ClientsScreen.tsx`. The diff is:

1. Add state: `const [installClientId, setInstallClientId] = useState<string | null>(null)`

2. Replace the client row action cluster:

```tsx
// Add this state near the top of ClientsScreen():
const [installClientId, setInstallClientId] = useState<string | null>(null)

// Helper to build the curl command:
function buildInstallCommand(clientId: string): string {
  const token = localStorage.getItem('ui_token') ?? ''
  const origin = window.location.origin
  return `sudo bash <(curl -fsSL -H "Authorization: Bearer ${token}" ${origin}/api/install.sh?client_id=${clientId})`
}
```

3. In the client row, replace the `<div className="inline-cluster">` block with:

```tsx
<div className="inline-cluster">
  {client.decommissioning ? <span className="badge">decommissioning</span> : null}
  <Btn
    size="small"
    onClick={() => setInstallClientId((prev) => (prev === client.id ? null : client.id))}
  >
    {installClientId === client.id ? 'Hide' : 'Install'}
  </Btn>
  <Btn
    variant="danger"
    size="small"
    disabled={deleteMutation.isPending}
    onClick={() => deleteMutation.mutate(client.id)}
  >
    Delete
  </Btn>
</div>
```

4. Add an install panel below the row actions (still inside the `list-row` div, spanning full width):

```tsx
{installClientId === client.id ? (
  <InstallPanel clientId={client.id} />
) : null}
```

- [ ] **Step 2: Add the `InstallPanel` component (top of file, before `ClientsScreen`)**

```tsx
function copyToClipboard(text: string): void {
  void navigator.clipboard.writeText(text)
}

function InstallPanel({ clientId }: { clientId: string }) {
  const command = `sudo bash <(curl -fsSL -H "Authorization: Bearer ${localStorage.getItem('ui_token') ?? ''}" ${window.location.origin}/api/install.sh?client_id=${clientId})`
  const [copied, setCopied] = useState(false)

  function handleCopy(): void {
    copyToClipboard(command)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <div className="install-panel">
      <div className="install-panel__label section-label">One-time install command — run on the satellite as root</div>
      <div className="install-panel__row">
        <code className="install-panel__cmd">{command}</code>
        <Btn size="small" onClick={handleCopy}>{copied ? 'Copied!' : 'Copy'}</Btn>
      </div>
      <p className="install-panel__hint">
        The script installs Python, aria2, and the agent; prompts for the local media path; and enables systemd services.
        Re-running is safe — it preserves the existing aria2 secret.
      </p>
    </div>
  )
}
```

- [ ] **Step 3: Add CSS for the install panel in `index.css`**

Append to `ui/src/index.css`:

```css
.install-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  margin-top: 12px;
  padding: 12px;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--bg3);
}

.install-panel__label {
  margin-bottom: 2px;
}

.install-panel__row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.install-panel__cmd {
  flex: 1;
  min-width: 0;
  font-family: 'DM Mono', monospace;
  font-size: 12px;
  color: var(--text1);
  word-break: break-all;
  overflow-wrap: anywhere;
}

.install-panel__hint {
  margin: 0;
  font-size: 12px;
  color: var(--text2);
}
```

- [ ] **Step 4: Adjust the `list-row` to handle the full-width panel**

The existing `.list-row` uses `flex-wrap: wrap; align-items: center; justify-content: space-between`. The install panel needs to span the full row width. Wrap the existing row content in a container and render the panel after it:

The full updated client list item:

```tsx
{clientsQuery.data?.map((client) => (
  <div key={client.id} className="list-row list-row--column">
    <div className="list-row__content">
      <div className="stack" style={{ gap: 8, minWidth: 0 }}>
        <div>
          <h3 className="list-row__title">{client.name}</h3>
          <p className="list-row__meta mono">
            {client.id} · last seen {client.last_seen ?? 'never'}
          </p>
        </div>
        <ProgressBar
          value={client.storage_budget_bytes ? 0 : 0}
          label={`Storage budget: ${formatBytes(client.storage_budget_bytes)}`}
        />
      </div>

      <div className="inline-cluster">
        {client.decommissioning ? <span className="badge">decommissioning</span> : null}
        <Btn
          size="small"
          onClick={() => setInstallClientId((prev) => (prev === client.id ? null : client.id))}
        >
          {installClientId === client.id ? 'Hide' : 'Install'}
        </Btn>
        <Btn
          variant="danger"
          size="small"
          disabled={deleteMutation.isPending}
          onClick={() => deleteMutation.mutate(client.id)}
        >
          Delete
        </Btn>
      </div>
    </div>

    {installClientId === client.id ? (
      <InstallPanel clientId={client.id} />
    ) : null}
  </div>
))}
```

Add supporting CSS to `index.css`:

```css
.list-row--column {
  flex-direction: column;
  align-items: stretch;
}

.list-row__content {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  width: 100%;
}
```

- [ ] **Step 5: TypeScript check**

```bash
cd ui
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
cd /home/claude/workspace/local-media-cache
git add ui/src/screens/ClientsScreen.tsx ui/src/index.css
git commit -m "feat(ui): install command panel per client — copy-paste curl one-liner"
```

---

## Self-Review

### Spec coverage

| Requirement | Task |
|-------------|------|
| Served from server at `GET /install.sh` | Task 2 |
| Requires auth (curl command includes bearer token) | Task 2 (endpoint), Task 4 (UI builds command with stored token) |
| Client token and server URL baked into script | Tasks 1 + 2 |
| Library path prompted interactively | Task 1 (script `read -rp`) |
| Installs Python, aria2, creates user, systemd | Task 1 |
| Idempotent re-runs (preserves aria2 secret) | Task 1 |
| Copy-paste command in UI per client | Task 4 |
| Tests for endpoint | Task 3 |

### Placeholder scan
None — all code blocks are complete.

### Type consistency
- `render(server_url, client_id, client_token)` defined in Task 1, called with same param names in Task 2. ✓
- `installClientId: string | null` state used consistently. ✓
- `InstallPanel({ clientId })` defined and used with same prop name. ✓
- `.list-row--column` / `.list-row__content` defined in CSS (Task 4) and applied in JSX (Task 4). ✓

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-06-client-install-script.md`.

**Two execution options:**

**1. Subagent-Driven (recommended)** — Fresh subagent per task, review between tasks

**2. Inline Execution** — Execute tasks in this session using executing-plans

**Which approach?**
