# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A self-hosted gateway that sits in front of Claude Code in air-gapped environments. It accepts the Anthropic Messages API on the inbound side (so Claude Code can talk to it via `ANTHROPIC_BASE_URL`), authorizes the call, runs prompt-security rules, and forwards the request to any OpenAI-compatible backend via LiteLLM.

## Run / develop

Python 3.10+ is required (uses PEP 604 `X | Y` type hints; the system `python3` may be older — create a 3.10+ venv).

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                     # set BACKEND_API_BASE / BACKEND_API_KEY / BACKEND_MODEL
cp auth.example.yaml auth.yaml           # gateway api keys + allowed IPs
cp security.example.yaml security.yaml   # optional; missing file = no rules + startup warning
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
```

Health: `curl http://localhost:8080/healthz`

There is no test suite yet. Quick syntax check during refactors:

```bash
python3 -c "import ast, pathlib; [ast.parse(p.read_text()) for p in pathlib.Path('app').rglob('*.py')]"
```

## Request pipeline

Every `POST /v1/messages` flows through these stages in order. They are **not** Starlette middleware — the auth stage is a FastAPI dependency and the security stage is an explicit call inside the route. New stages must be wired in by hand at the right point in `app/routers/messages.py`.

1. **`app/middleware/auth.py :: authorize`** — validates `x-api-key` against `auth.yaml`, then checks the source IP against the matched client's CIDR allowlist. Source IP is `request.client.host` unless `TRUST_PROXY_HEADERS=true`, in which case the first `X-Forwarded-For` entry is used. Returns a `Client` to the route handler so downstream stages can attribute the request.
2. **`app/middleware/prompt_security.py :: SecurityEngine.check`** — extracts text from the inbound payload via `adapters.anthropic.content_to_text`, runs each loaded `Rule.evaluate(texts)`, and raises `SecurityViolation` (HTTP 400) on the first match. The prompt is not forwarded on a block.
3. **`app/services/llm.py`** — calls `litellm.acompletion` with `model="openai/<BACKEND_MODEL>"` plus `api_base` / `api_key` from `.env`. Translates request and response via `app/adapters/anthropic.py` for both streaming and non-streaming.

The route file marks intended insertion points for future stages (e.g. `# TODO(audit)`). Engines are loaded once in `app/main.py:create_app()` and stored on `app.state.<name>` so the route can reach them via `request.app.state`.

## Adding a new middleware (audit, rate-limit, etc.)

Match the pattern used by `auth` and `prompt_security`:

1. New module under `app/middleware/` exposing an engine/store class with a `from_yaml` classmethod (or whatever loader fits).
2. In `app/main.py:create_app()`, instantiate it and assign to `app.state.<name>`. Log its size at startup.
3. In `app/routers/messages.py`, call it at the right point in the pipeline (`await request.app.state.<name>.<method>(payload, client)`).
4. If it has YAML config, add a `<NAME>_CONFIG_PATH` field to `app/config.py`, ship a `<name>.example.yaml`, and add the real `<name>.yaml` to `.gitignore`.

## Adding a new prompt-security rule type

In `app/middleware/prompt_security.py`:

1. Add a `@dataclass(frozen=True)` that satisfies the `Rule` protocol — `name: str` and `evaluate(self, texts: list[str]) -> str | None` (return a non-None reason to block).
2. Add a branch keyed on the new `type` value inside `_build_rule(raw)`.
3. Document the new type in `security.example.yaml`.

## Anthropic ↔ OpenAI adapter

`app/adapters/anthropic.py` is intentionally minimal and is the most likely place to need extension:

- **Inbound** (`anthropic_to_openai_messages`) extracts text from `system` plus each message's `content` (string or list of text blocks). **Tool-use and image content blocks are silently dropped today.**
- **Outbound non-stream** (`openai_to_anthropic_response`) rebuilds an Anthropic `message` with a single text content block.
- **Outbound stream** (`openai_stream_to_anthropic_events`) emits the Anthropic SSE event sequence: `message_start` → `content_block_start` → `content_block_delta`\* → `content_block_stop` → `message_delta` → `message_stop`.
- `_map_stop_reason` translates OpenAI `finish_reason` → Anthropic `stop_reason`; `tool_calls` maps to `tool_use` but no tool blocks are emitted yet.

Adding tool-use means changes in both translation directions and the SSE sequence (additional `content_block_start` of type `tool_use` plus `input_json_delta` events).

## Config files

- `.env` — pydantic-settings, defaults live in `app/config.py`. `BACKEND_API_BASE` and `BACKEND_API_KEY` have no defaults and will fail startup if unset.
- `auth.yaml` — `clients[]` with `api_key` and `allowed_ips` (single IP or CIDR, IPv4/IPv6).
- `security.yaml` — optional; `rules[]`, each with `name`, `type`, and type-specific fields.

All three real files are gitignored. The `.example` versions are checked in.

## Docker

`Dockerfile` (root) builds a slim Python 3.11 image running uvicorn as a non-root `gateway` user. `docker-compose/docker-compose.yml` is the orchestration entry point — it mounts `../.env` via `env_file` and bind-mounts `../auth.yaml` and `../security.yaml` read-only. Both YAML files must exist on the host before `up` (security.yaml may have an empty `rules` list). Engines load once at startup, so config changes require `docker compose restart`.

```bash
docker compose -f docker-compose/docker-compose.yml up --build
```

## Pointing Claude Code at the gateway

```bash
export ANTHROPIC_BASE_URL=http://<gateway-host>:8080
export ANTHROPIC_API_KEY=<api_key from auth.yaml>
claude
```

Claude Code issues `POST ${ANTHROPIC_BASE_URL}/v1/messages` with `x-api-key`; the gateway mounts the route at exactly that path.
