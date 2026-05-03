# ai-gateway

## What this is

A LiteLLM proxy running locally with a custom Python guardrail in front
of AWS Bedrock (Claude Sonnet 4.5 in `ap-southeast-2`, accessed via an
inference profile). Every incoming chat-completions request runs through
a `pre_call` PII guardrail that blocks SSNs and credit-card numbers and
redacts emails, phone numbers, and `sk-`-prefixed API keys before the
prompt reaches the model. **Dev/local only — do not deploy as-is.**

## Prerequisites

- Docker + Docker Compose
- AWS credentials with `bedrock:InvokeModel` and
  `bedrock:InvokeModelWithResponseStream` permission on the inference
  profile ARN configured in `config.yaml`.
- Model access enabled for `anthropic.claude-sonnet-4-5` in the AWS
  Bedrock console for `ap-southeast-2`.

## Setup

```bash
cp .env.example .env
```

Then edit `.env`:

- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` — your Bedrock-enabled
  IAM credentials.
- `LITELLM_MASTER_KEY` — pick a strong random value, must start with
  `sk-`.
- `LITELLM_SALT_KEY` — generate with:

  ```bash
  openssl rand -hex 32
  ```

## Run

```bash
docker compose up -d
docker compose logs -f litellm
```

Wait for `Application startup complete`. Health check:

```bash
curl http://localhost:4000/health/liveliness
```

## Test the guardrail

All three tests POST to `http://localhost:4000/v1/chat/completions` with
the master key, model `claude-4-5`.

### 1. Clean prompt (200 OK)

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-5",
    "messages": [{"role": "user", "content": "Say hi in one word."}]
  }'
```

Expected: HTTP 200 with a normal chat-completion JSON body.

### 2. Prompt with SSN (400, blocked)

```bash
curl -i http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-5",
    "messages": [{"role": "user", "content": "my ssn is 123-45-6789"}]
  }'
```

Expected: HTTP 400 with body containing
`"Prompt contains restricted content (type: SSN)"`. The prompt is not
forwarded to Bedrock.

### 3. Prompt with email (200, redacted)

```bash
curl http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-5",
    "messages": [{"role": "user", "content": "contact me at alice@example.com"}]
  }'
```

Expected: HTTP 200. The model never sees `alice@example.com`; the prompt
it receives contains `[REDACTED_EMAIL]`, and any echo of that string in
the response will refer to the placeholder.

### 4. Streaming (verify SSE works)

```bash
curl -N http://localhost:4000/v1/chat/completions \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-4-5",
    "messages": [{"role": "user", "content": "count to five slowly"}],
    "stream": true
  }'
```

Expected: a stream of `data: {...}` SSE lines terminated by
`data: [DONE]`.

## Run the unit tests

The guardrails ship with a pure-regex test suite (no proxy, no Docker, no
network) under `tests/`. The root `conftest.py` stubs out `litellm` and
`fastapi` if they aren't installed, so the only hard requirement is
`pytest`.

```bash
python3 -m pip install --user pytest
python3 -m pytest tests/ -v
```

You should see ~100 tests pass in well under a second. Run this before
every change to `guardrails/guardrails.py` — it's the fastest way to
catch a regex regression without restarting the container.

To run a single file or class:

```bash
python3 -m pytest tests/test_company_pii_guardrail.py -v
python3 -m pytest tests/test_prompt_injection_guardrail.py::TestRoleScoping -v
```

The tests intentionally do **not** depend on the real `litellm` package,
so they run identically inside the Docker image (`docker compose run
--rm litellm python -m pytest tests/`) and on a bare host.

## Open the UI

Visit `http://localhost:4000/ui` and log in with the master key.
LiteLLM's built-in guardrails can be configured here later alongside
the custom Python guardrail in `guardrails/guardrails.py`.

## Adding a new pattern to the guardrail

Edit `guardrails/guardrails.py`:

- For a new BLOCK rule (reject the prompt), add a check inside
  `CompanyPIIGuardrail._check_block` that raises `HTTPException(400,
  ...)` with a generic detail string.
- For a new REDACT rule (rewrite in place), add a compiled regex at
  module scope and a new `.sub(...)` call inside
  `CompanyPIIGuardrail._redact`.

Reload:

```bash
docker compose restart litellm
```

## Troubleshooting

- **`ModuleNotFoundError: guardrails`** — check the volume mount in
  `docker-compose.yml`. The host directory `./guardrails` must map to
  `/app/guardrails`, and `guardrails/__init__.py` must exist (even
  empty).
- **DB connection errors** — `DATABASE_URL` must use `db` as the host
  (the docker-compose service name), not `localhost`.
- **401 on every request** — the `Authorization: Bearer ...` value in
  the request must match `LITELLM_MASTER_KEY` from `.env` exactly.
- **Bedrock `AccessDeniedException`** — verify IAM permissions on the
  inference profile ARN, and that model access is enabled for
  `anthropic.claude-sonnet-4-5` in the Bedrock console for
  `ap-southeast-2`.
