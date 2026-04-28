# ai-gateway

Self-hosted LLM gateway for air-gapped environments. Accepts requests from the
Claude Code CLI on the Anthropic Messages API and forwards them, via
[LiteLLM](https://github.com/BerriAI/litellm), to any OpenAI-compatible backend.

```
Claude Code CLI ──(POST /v1/messages, x-api-key)──▶ ai-gateway ──(LiteLLM)──▶ OpenAI-compatible backend
                                                       │
                                                       └─ authorize: api key + source-IP allowlist
```

The gateway is intentionally split into small pieces so the planned next
components — prompt-security checks and prompt/response auditing — can drop
in as router hooks without touching the LLM layer.

## Project layout

```
app/
  main.py             FastAPI app factory
  config.py           pydantic-settings (.env)
  middleware/
    auth.py             api key + IP allowlist (yaml-backed)
    prompt_security.py  pluggable rule engine; blocks on match
                        ← future: audit.py, rate_limit.py
  routers/
    health.py         GET /healthz
    messages.py       POST /v1/messages   ← Claude CLI talks here
  services/
    llm.py            LiteLLM wrapper (acompletion, streaming)
  adapters/
    anthropic.py      Anthropic ↔ OpenAI request/response translation
```

## Requirements

- Python 3.10+ (uses PEP 604 `X | Y` type hints)

## Quick start

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env                     # edit BACKEND_API_BASE / BACKEND_API_KEY / BACKEND_MODEL
cp auth.example.yaml auth.yaml           # gateway api key(s) + allowed IPs
cp security.example.yaml security.yaml   # optional: prompt-security rules

uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Health check:

```bash
curl http://localhost:8080/healthz
```

## Run with Docker

```bash
cp .env.example .env
cp auth.example.yaml auth.yaml
cp security.example.yaml security.yaml   # required by the volume mount; can be empty rules

docker compose -f docker-compose/docker-compose.yml up --build
```

- Settings come from `.env` via the compose `env_file`.
- `auth.yaml` and `security.yaml` are mounted read-only into `/app/` — config changes require a container restart (engines load once at startup).
- The image runs as a non-root `gateway` user and exposes port 8080.

## Pointing Claude Code at the gateway

```bash
export ANTHROPIC_BASE_URL=http://<gateway-host>:8080
export ANTHROPIC_API_KEY=<api_key from auth.yaml>
claude
```

Claude Code will issue `POST {ANTHROPIC_BASE_URL}/v1/messages` with the
`x-api-key` header set to `ANTHROPIC_API_KEY`. The gateway authorizes on
that header **plus** the source IP, then forwards to the backend you
configured in `.env`.

## Behind a reverse proxy

If the gateway runs behind a proxy that adds `X-Forwarded-For`, set
`TRUST_PROXY_HEADERS=true` in `.env`. Otherwise the source IP is taken
from the TCP peer address, which is the safe default.

## Roadmap

- [x] Auth: api key + source-IP allowlist
- [x] Anthropic ↔ OpenAI translation (text + streaming)
- [x] Prompt-security rules (regex-based, pluggable engine)
- [ ] Tool-use translation
- [ ] Prompt/response audit log (durable storage)
- [ ] Multi-backend / model routing
- [ ] Rate limiting & per-client quotas
