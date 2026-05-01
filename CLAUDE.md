# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A locally-run [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/docker_quick_start) sitting in front of AWS Bedrock (Claude Sonnet 4.5 in `ap-southeast-2`, accessed via inference profile). A custom Python `pre_call` guardrail enforces a company PII policy on every prompt before it reaches the model.

This repo is dev/local-only. There is no application code beyond the guardrail — the proxy itself is the upstream LiteLLM container.

## Run

```bash
cp .env.example .env                    # fill in AWS creds, master key, salt key
docker compose up -d
docker compose logs -f litellm          # wait for "Application startup complete"
```

Health: `curl http://localhost:4000/health/liveliness`

UI: `http://localhost:4000/ui` (login with `LITELLM_MASTER_KEY`).

Engines and config load once at container startup, so any change to `config.yaml`, `guardrails/`, or `.env` requires `docker compose restart litellm`.

## Repo layout

```
.
├── docker-compose.yml      litellm + postgres, port 4000 bound to 127.0.0.1 only
├── config.yaml             model_list, guardrails, general/litellm settings
├── .env.example            secrets template (real .env is gitignored)
├── guardrails/
│   ├── __init__.py
│   └── guardrails.py       CompanyPIIGuardrail (pre_call hook)
└── README.md               setup + curl-based test recipes
```

## Request pipeline

Inbound `POST /v1/chat/completions` flows through:

1. **LiteLLM proxy auth** — `Authorization: Bearer <LITELLM_MASTER_KEY>` is validated by LiteLLM itself (or by a virtual key the master key has issued in the UI).
2. **`guardrails.guardrails.CompanyPIIGuardrail.async_pre_call_hook`** (mode `pre_call`, `default_on: true`) — walks every message's text content and:
   - **BLOCK** (raises `HTTPException(400)`) on US SSN or credit-card numbers (Luhn-validated).
   - **REDACT** (rewrites in place) emails, US-ish phone numbers, `sk-…` API-key-shaped strings.
   - Fails closed (`HTTPException(500)`) on any unexpected internal error — this is a security control, not best-effort filtering.
3. **Bedrock** — LiteLLM forwards the (possibly redacted) payload to the inference-profile ARN in `config.yaml`. AWS credentials come from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in the env, picked up by boto3 — they must NOT live in `config.yaml`.

## Adding a new guardrail pattern

Edit `guardrails/guardrails.py` only — no other file needs to change.

- **BLOCK** rule (reject the prompt): add a compiled regex at module scope and a check inside `CompanyPIIGuardrail._check_block`. Raise `HTTPException(status_code=400, detail="Prompt contains restricted content (type: <LABEL>)")`. **Do not echo the matched content back** in the detail string.
- **REDACT** rule (rewrite in place): add a compiled regex at module scope and a `.sub("[REDACTED_<LABEL>]", text)` call inside `CompanyPIIGuardrail._redact`. The function must preserve the original content shape (string stays string; Anthropic content-block list stays a list with `text` fields modified).

Then `docker compose restart litellm`.

The guardrail does no I/O — pure regex. Don't introduce DB or network calls; that would change the threat model and likely break the fail-closed contract.

## Adding a new model

In `config.yaml` under `model_list:`, add another entry. Keep credentials out — boto3 reads AWS creds from env, and other providers should similarly use `os.environ/<VAR>` indirection in `litellm_params` rather than embedding secrets.

## Config files

- **`.env`** (gitignored) — secrets only. Required keys: `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `DATABASE_URL`, `POSTGRES_USER`/`PASSWORD`/`DB`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`. `.env.example` is the source of truth for the schema.
- **`config.yaml`** (committed) — non-secret proxy config. Uses `os.environ/<VAR>` to reference secrets. `set_verbose` must stay `false` (it leaks API keys in logs).
- **`docker-compose.yml`** — port 4000 is bound to `127.0.0.1` only by design; do not change to `0.0.0.0` unless you understand you've just exposed a key-protected proxy on the LAN.

## What to avoid

- Don't put AWS or any other provider secrets in `config.yaml`.
- Don't add S3, Langfuse, or external logging integrations — this is local-only.
- Don't add OpenAI or non-Bedrock models to `config.yaml` unless explicitly asked.
- Don't expose Postgres to the host — it has no port mapping for a reason.
- Don't bypass the guardrail's fail-closed behavior; if the regex layer breaks, prompts must NOT fall through.
- Don't echo matched PII back in error detail strings.
