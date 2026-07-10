# nmos-mcp

An **MCP server for AMWA NMOS**. It connects to an NMOS **Registry**, lets an agent
**query** everything on the network (IS-04) and — the headline feature — **connect
senders to receivers** to route media between devices (IS-05).

- **IS-04 (Discovery & Registration)** — read Nodes, Devices, Senders, Receivers,
  Flows, Sources and Subscriptions from the registry's Query API.
- **IS-05 (Device Connection Management)** — connect/disconnect, enable/disable
  senders, inspect staged/active state, and bulk-route.
- Works against a plain-HTTP lab registry **or** an HTTPS deployment with **IS-10**
  OAuth2 bearer tokens.
- Finds the registry from `NMOS_REGISTRY_URL`, or auto-discovers it over **mDNS**
  (`_nmos-query._tcp`).

---

## 1. Install

```bash
cd nmos-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"          # or: uv pip install -e ".[dev]"
```

This creates the `nmos-mcp` console command inside `.venv/bin/`.

> **Note (this machine):** the shell auto-activates another project's virtualenv, so
> plain `python3` may be the wrong interpreter. If `python3 -m venv` fails, build the
> venv with the real interpreter:
> `env -i HOME="$HOME" PATH="/usr/bin:/bin" /opt/homebrew/bin/python3 -m venv .venv`
> and use `.venv/bin/python` / `.venv/bin/nmos-mcp` directly.

## 2. Configure

Copy `.env.example` to `.env` and point it at your registry:

```bash
cp .env.example .env
```

```ini
NMOS_REGISTRY_URL=http://registry.example.local   # leave UNSET to auto-discover via mDNS
NMOS_QUERY_VERSION=v1.3
NMOS_CONNECTION_VERSION=v1.1
NMOS_USE_HTTPS=false
NMOS_VERIFY_TLS=true
# IS-10 auth (optional, for secured deployments):
# NMOS_AUTH_ENABLED=true
# NMOS_AUTH_TOKEN_URL=https://auth.local/oauth2/token
# NMOS_AUTH_CLIENT_ID=...
# NMOS_AUTH_CLIENT_SECRET=...
```

> `.env` is **git-ignored** — internal hostnames (e.g. `registry.example.local`) and
> credentials never get committed. `.env.example` is the only env file in git.
>
> `.env` is read relative to the process working directory. When Claude Code launches
> the server the working directory may differ, so **pass the registry URL via `-e`**
> in the Claude Code registration below (that value is stored in your private Claude
> config, not in the repo).

## 3. Start the server

The server speaks the MCP protocol over a transport — you normally don't run it by
hand; an MCP client (Claude Code) launches it. To run it manually:

```bash
nmos-mcp            # stdio transport (what Claude Code / Claude Desktop use)
nmos-mcp --http     # streamable-HTTP transport
```

To poke at the tools interactively with the MCP Inspector:

```bash
mcp dev src/nmos_mcp/server.py
```

## 4. Add it to Claude Code

Register the server with the CLI (from anywhere). Use `-e` to inject the registry URL
and `-s local` so it stays in your private config rather than the shared repo:

```bash
claude mcp add nmos \
  -s local \
  -e NMOS_REGISTRY_URL=http://registry.example.local \
  -- /ABSOLUTE/PATH/TO/nmos-mcp/.venv/bin/nmos-mcp
```

Verify it connected:

```bash
claude mcp get nmos       # Status: ✔ Connected
claude mcp list
```

Then in a Claude Code session just ask, e.g.:

> *"List the NMOS senders, then connect 'AES67 sender 4' to 'AES67 receiver 4'."*

To update or remove it:

```bash
claude mcp remove nmos -s local          # then re-add with new flags
```

**Scopes:** `-s local` (default) keeps the server private to you for this project
(stored in `~/.claude.json`). `-s user` makes it available in all your projects.
Avoid `-s project` (writes a committed `.mcp.json`) unless you deliberately want the
registry URL shared with the team via git.

### Claude Desktop (alternative client)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "nmos": {
      "command": "/ABSOLUTE/PATH/TO/nmos-mcp/.venv/bin/nmos-mcp",
      "env": { "NMOS_REGISTRY_URL": "http://registry.example.local" }
    }
  }
}
```

---

## Tools

**IS-04 (query):** `registry_info`, `list_nodes`, `list_devices`, `list_senders`,
`list_receivers`, `list_flows`, `list_sources`, `get_resource`, `query_resources`.

**IS-05 (connection):** `get_sender`, `get_receiver`, `get_sender_transport_file`,
`connect_sender_to_receiver`, `disconnect_receiver`, `enable_sender`,
`disable_sender`, `bulk_connect`, `stage_receiver`, `stage_sender`.

### How a connection is made

The Query API lives on the registry; the **Connection API (IS-05) lives on each
Node**. To wire a sender to a receiver the server:

1. Looks the receiver up in the registry and reads its device's `controls` array to
   find the IS-05 endpoint (`urn:x-nmos:control:sr-ctrl`).
2. Fetches the **sender's SDP transport file**.
3. `PATCH`es the receiver's `/staged` with the sender id, `master_enable: true`,
   the transport file, and `activation: { mode: activate_immediate }`.
4. Reads back the receiver's `/active` state to confirm the route.

The connection endpoint version is taken from the device's advertised control href,
so nodes exposing IS-05 **v1.0 or v1.1** both work.

## Test

```bash
pytest
```

Unit tests mock both the Registry Query API and a Node Connection API (via `respx`),
covering the connect/disconnect PATCH bodies, endpoint resolution, config coercion
and URL handling.

### End-to-end against a real registry

Point `NMOS_REGISTRY_URL` at a live registry (or a local **EasyNMOS** stack:
`docker run -d --net=host rhastie/easy-nmos`), then use `mcp dev` or Claude Code to
`list_senders` / `list_receivers`, run `connect_sender_to_receiver`, and confirm the
receiver's `/active` shows the sender's multicast group.

## Scope & roadmap

Current: IS-04 read/query + IS-05 connection management. The module layout leaves
room to add IS-04 registration writes, IS-08 audio channel mapping, IS-07
events/tally and IS-09 system parameters as additional tool groups.
