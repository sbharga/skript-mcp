## Skript MCP

This project exposes a small MCP server backed by a permanently running Paper
server. Paper, Skript, and the installed addons are the parser: the Python code
does not attempt to understand Skript syntax itself.

The default image contains this compatibility profile:

| Component | Version |
| --- | --- |
| Java | 21 |
| Paper | 1.21.4 build 232 |
| Skript | 2.14.3 |
| SkBee | 3.16.1 |
| skript-reflect | 2.6.3 |
| skript-gui | 1.4.0 |

Every downloaded jar is checked during the image build. Override all related
URLs and checksums together in `.env` to use another authoritative profile.
Never update only one addon without verifying the complete set.

Skript 2.14.3 is intentional for the 1.21.4 profile. SkBee 3.16.1 is the last
SkBee release for Minecraft 1.21.4, and newer Skript 2.15/2.16 releases collide
with its Adventure `audience` type registration. Newer Skript requires moving
the whole profile to a newer Minecraft and SkBee release.

### Start Paper

Create a local environment file and replace the password with a long random
value containing only letters, numbers, dots, underscores, or hyphens:

```bash
cp .env.example .env
docker compose build
docker compose up -d --wait
```

Set `PUID` and `PGID` in `.env` to the output of `id -u` and `id -g`. They
default to 1000 and let Paper read the host-created mode-0600 temporary files.

Paper logs are mounted at `runtime/logs`. Host scripts are mounted directly at
`/server/plugins/Skript/scripts` from `scripts`. World and plugin-generated data
remain disposable container state. Only RCON is published, and it is bound to
`127.0.0.1:25575`; the gameplay port is not published.

Check startup and plugin versions with:

```bash
docker compose logs paper
docker compose exec paper sh -c 'nc -z 127.0.0.1 25575'
```

### Run the MCP server

Install and run it with `uv`:

```bash
uv sync
uv run --env-file .env skript-mcp
```

The MCP process reads these environment variables:

| Variable | Default |
| --- | --- |
| `RCON_PASSWORD` | Required |
| `RCON_HOST` | `127.0.0.1` |
| `RCON_PORT` | `25575` |
| `SKRIPT_SCRIPTS_DIR` | `scripts` |
| `MINECRAFT_LOG_PATH` | `runtime/logs/latest.log` |
| `RCON_TIMEOUT` | `5` seconds |
| `RELOAD_TIMEOUT` | `30` seconds |
| `MAX_SCRIPT_BYTES` | `1048576` |

For OpenCode, add a local MCP entry and pass the same RCON password through the
environment:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "skript": {
      "type": "local",
      "command": ["uv", "run", "--directory", "/absolute/path/to/skript-mcp", "--env-file", "/absolute/path/to/skript-mcp/.env", "skript-mcp"],
      "enabled": true,
      "environment": {}
    }
  }
}
```

Restart OpenCode after changing its configuration.

### Tools

- `skript_check_file(contents)` writes a unique temporary script, reloads it,
  parses diagnostics, unloads it, and deletes it.
- `skript_reload_file(path)` reloads an existing path relative to `scripts`.
- `minecraft_command(command)` runs a privileged command over local RCON.
- `minecraft_logs(lines=200)` returns a bounded tail of `latest.log`.
- `skript_reset()` removes only stale `__mcp_check_<uuid>.sk` files.

Reload operations are serialized. Diagnostics are parsed primarily from the
RCON response because Skript deliberately redirects command diagnostics away
from normal server logging; bytes appended to `latest.log` after the operation
offset are captured and merged as a secondary source.

### Safety

`skript_check_file` performs a real Skript load, not a sandboxed parse. Source
containing `on load` handlers or addon behavior can modify the world, files, or
external systems. Only submit trusted source, and treat `minecraft_command` and
the RCON password as full administrator access.

Run unit tests with:

```bash
uv run pytest
```
