# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A locally-run [LiteLLM proxy](https://docs.litellm.ai/docs/proxy/docker_quick_start) sitting in front of AWS Bedrock (Claude Sonnet 4.5 in `ap-southeast-2`, accessed via inference profile).

Policy enforcement (PII redaction, prompt-injection blocking) is delegated to a separate Go service — `guardrails` — over LiteLLM's `generic_guardrail_api`. **No guardrail logic lives in this repo.** All rules live in the [`go-guardrails`](../go-guardrails) repo at `/Users/ashokdan/Developer/Github/go-guardrails`.

## Run

```bash
docker network create litellm-net           # one-time, shared with go-guardrails
cp .env.example .env                        # fill in AWS creds, master key, salt key, GUARDRAIL_API_KEY

# Bring guardrails up first:
( cd ../go-guardrails && make app-up )

# Then this stack:
docker compose up -d
docker compose logs -f litellm              # wait for "Application startup complete"
```

Health: `curl http://localhost:4000/health/liveliness`

UI: `http://localhost:4000/ui` (login with `LITELLM_MASTER_KEY`).

## Repo layout

```
.
├── docker-compose.yml      litellm + postgres + pgadmin, port 4000 on 127.0.0.1 only
├── config.yaml             model_list, generic_guardrail_api guardrails, settings
├── .env.example            secrets template (real .env is gitignored)
└── README.md               setup + curl-based test recipes
```

The previous `guardrails/` Python directory and `tests/` regex tests have been removed — all policy code now lives in the `go-guardrails` repo.

## Request pipeline

Inbound `POST /v1/chat/completions` flows through:

1. **LiteLLM proxy auth** — `Authorization: Bearer <LITELLM_MASTER_KEY>` is validated by LiteLLM.
2. **PII guardrail** — LiteLLM POSTs `texts[]` to `http://guardrails:8080/beta/litellm_basic_guardrail_api` with `additional_provider_specific_params.policy=pii` and header `X-Guardrail-Auth-Key`. Response action ∈ `{BLOCKED, GUARDRAIL_INTERVENED, NONE}`. `BLOCKED` → 400 to caller; `GUARDRAIL_INTERVENED` → LiteLLM substitutes returned `texts[]`.
3. **Prompt-injection guardrail** — same shape, `policy=prompt_injection`. Block-only.
4. **Bedrock** — LiteLLM forwards the (possibly redacted) payload. AWS creds come from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — they must NOT live in `config.yaml`.

`unreachable_fallback: fail_closed` is set on both guardrail entries: if the guardrails service is down, requests are rejected (security control, not best-effort).

## Adding a new guardrail pattern

**Edit the `go-guardrails` repo, not this one.** See its `README.md`. After updating patterns there:

```bash
( cd ../go-guardrails && make test && make app-down && make app-up )
```

LiteLLM does not need to be restarted — the next request picks up the new rules.

## Adding a new model

In `config.yaml` under `model_list:`, add another entry. Keep credentials out — boto3 reads AWS creds from env, and other providers should use `os.environ/<VAR>` indirection in `litellm_params`.

## Config files

- **`.env`** (gitignored) — secrets only. Required keys: `LITELLM_MASTER_KEY`, `LITELLM_SALT_KEY`, `DATABASE_URL`, `POSTGRES_USER`/`PASSWORD`/`DB`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION_NAME`, `GUARDRAIL_API_KEY`. `.env.example` is the source of truth for the schema.
- **`config.yaml`** (committed) — non-secret proxy config. Uses `os.environ/<VAR>` to reference secrets. `set_verbose` must stay `false` (it leaks API keys in logs).
- **`docker-compose.yml`** — port 4000 is bound to `127.0.0.1` only by design. Network `litellm-net` is `external: true` and shared with the `go-guardrails` stack.

## What to avoid

- Don't put AWS or any other provider secrets in `config.yaml`.
- Don't add S3, Langfuse, or external logging integrations — this is local-only.
- Don't add OpenAI or non-Bedrock models to `config.yaml` unless explicitly asked.
- Don't expose Postgres to the host — it has no port mapping for a reason.
- Don't change `unreachable_fallback` from `fail_closed`. The fail-closed behavior is the security contract.
- Don't reintroduce a Python guardrail wrapper. The contract is `generic_guardrail_api` direct → Go service.
