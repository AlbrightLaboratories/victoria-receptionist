<!-- toc-backlink -->
> 📚 **Master TOC:** [Org-wide repo index](https://github.com/AlbrightLaboratories/daxxon-ai-gpu-01/issues/17) — auto-updated every 15 min from this repo's commit stream. No manual entry needed; just write commit subjects that read well as one-line bullets.

# Victoria Albright — virtual receptionist

Victoria is the front-desk presence on [albrightlab.com](https://albrightlab.com). She greets visitors via a floating chat bubble, answers questions from the site's content using RAG, falls back to web search (Tavily or in-cluster SearXNG) when she doesn't already know, rewrites web-search-derived answers in AlbrightLab's voice, and — when she still can't answer confidently — files a GitHub issue against the main site repo so the team can fill the gap.

> **Phase 2 note.** The LLM training/fine-tune pipeline (NeMo Curator → SFT on AlbrightLab tone) is **explicitly out of scope** for this scaffold. Victoria currently uses whatever instruct model is loaded on the cluster's Triton inference server (default: `llama3.1:8b-instruct`). The scaffold is wired so a fine-tuned model can drop in via the `LLM_MODEL` env var with no code change.

## Architecture

```
                            ┌────────────────────────────────────┐
                            │   albrightlab.com (any page)       │
                            │   <script src="…/widget.js">        │
                            └────────────────┬───────────────────┘
                                             │  POST /api/chat
                                             ▼
                     ┌──────────────────────────────────────────┐
                     │   Victoria FastAPI (this repo)            │
                     │                                           │
                     │   conversation.py — state machine         │
                     │     1. RAG (pgvector top-k)               │
                     │     2. Web search (Tavily / SearXNG)      │
                     │     3. Rebrand pass (LLM)                 │
                     │     4. Knowledge-gap → GitHub issue       │
                     └──┬───────────┬────────────┬───────────┬───┘
                        │           │            │           │
                        ▼           ▼            ▼           ▼
                  ┌──────────┐ ┌─────────┐ ┌──────────┐ ┌────────┐
                  │ Postgres │ │ Triton  │ │  Tavily/ │ │ GH API │
                  │ pgvector │ │ Llama-3 │ │  SearXNG │ │ Issues │
                  └──────────┘ └─────────┘ └──────────┘ └────────┘
```

## Local dev quickstart

```bash
# 1. Bring up Postgres+pgvector and the app in mock-LLM mode.
docker compose up --build

# 2. Verify the app is alive.
curl http://localhost:8080/health
# {"status":"ok"}

# 3. Talk to Victoria.
curl -s -X POST http://localhost:8080/api/chat \
  -H 'content-type: application/json' \
  -d '{"message":"hi, what does AlbrightLab do?"}' | jq
```

In local mode (`MODE=local`, the default in `docker-compose.yml`), Victoria uses a deterministic pattern-matched response set from `src/victoria/mock_llm.py`. No GPU, Triton, GitHub, or Tavily credentials required.

To try the full stack against real upstreams, copy `.env.example` to `.env`, set `MODE=staging` (or `production`), and supply `TRITON_URL`, `EMBEDDING_URL`, `DATABASE_URL`, `GH_TOKEN`, and optionally `TAVILY_API_KEY`.

## Embedding the widget

Drop this one line into any page that should host the chat bubble:

```html
<script src="https://victoria.albrightlab.com/widget.js" defer></script>
```

The script self-injects a floating bubble at bottom-right. It also exposes a small API for programmatic control:

```js
window.victoria.open();   // open the chat panel
window.victoria.close();  // close it
window.victoria.toggle(); // flip
```

The matching stylesheet is loaded automatically from `/widget.css` on the same origin.

## Production deployment

```bash
# 1. Create the namespace + supporting resources.
kubectl apply -f k8s/namespace.yaml

# 2. Apply the canonical schema as a ConfigMap (replaces the placeholder
#    in k8s/postgres.yaml).
kubectl create configmap victoria-schema \
  --namespace=victoria \
  --from-file=schema.sql=schema.sql \
  --dry-run=client -o yaml | kubectl apply -f -

# 3. Create the secret. NEVER commit a populated copy.
kubectl create secret generic victoria-secrets \
  --namespace=victoria \
  --from-literal=database_url='postgresql://victoria:STRONG_PW@postgres:5432/victoria' \
  --from-literal=postgres_user='victoria' \
  --from-literal=postgres_password='STRONG_PW' \
  --from-literal=gh_token='ghp_xxx_with_issues_write_scope' \
  --from-literal=tavily_api_key=''   # optional; omit to use SearXNG

# 4. Apply the rest.
kubectl apply -f k8s/postgres.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/ingress.yaml

# 5. Build the RAG index (one-off; re-run after site content changes).
kubectl exec -n victoria deploy/victoria -- \
  python scripts/build_rag_index.py --start https://albrightlab.com
```

After the ingress is live, `https://victoria.albrightlab.com/health` should return `{"status":"ok"}`.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `MODE` | `local` | `local` (mock LLM), `staging` (real upstream, no GH writes), `production` |
| `HOST` | `0.0.0.0` | uvicorn bind |
| `PORT` | `8080` | uvicorn port |
| `LOG_LEVEL` | `info` | Python logging level |
| `TRITON_URL` | `http://triton-inference.triton-inference.svc.cluster.local:8000` | Upstream LLM base URL |
| `TRITON_OPENAI_COMPAT` | `true` | If `true`, call `/v1/chat/completions`; else `/v2/models/<model>/generate` |
| `LLM_MODEL` | `llama3.1:8b-instruct` | Model identifier on the upstream |
| `LLM_TIMEOUT_S` | `60` | Per-call timeout |
| `LLM_MAX_TOKENS` | `512` | Generation cap |
| `LLM_TEMPERATURE` | `0.4` | Sampling temperature |
| `EMBEDDING_URL` | NeMo Embedding svc | Embedding endpoint (OpenAI-compatible) |
| `EMBEDDING_MODEL` | `intfloat/e5-large-v2` | Embedding model name |
| `EMBEDDING_DIM` | `1024` | Must match the `vector(N)` column in `schema.sql` |
| `DATABASE_URL` | `postgresql://victoria:victoria@postgres:5432/victoria` | asyncpg DSN |
| `RAG_TOP_K` | `4` | Number of chunks to retrieve |
| `RAG_CONFIDENCE_THRESHOLD` | `0.72` | Minimum `1 - cosine_distance` to use RAG over web search |
| `TAVILY_API_KEY` | _(unset)_ | When set, Tavily is used for web fallback |
| `SEARXNG_URL` | `http://searxng.search.svc.cluster.local:8080` | SearXNG fallback when no Tavily key |
| `GH_REPO` | `AlbrightLaboratories/albrightlaboratories-dot-com` | Where knowledge-gap issues are filed |
| `GH_TOKEN` | _(unset)_ | PAT with `repo` scope; falls back to `gh` CLI when present |
| `GH_GAP_LABEL` | `victoria-knowledge-gap` | Label applied to gap issues |
| `ALLOWED_ORIGINS` | `https://albrightlab.com,https://www.albrightlab.com,http://localhost:8080` | CORS allowlist (CSV) |

## API surface

| Method | Path | Notes |
|---|---|---|
| `GET` | `/health` | Liveness probe |
| `GET` | `/widget.js` | Drop-in chat-bubble injector |
| `GET` | `/widget.css` | Companion stylesheet |
| `POST` | `/api/chat` | `{message, conversation_id?, page_url?}` → `{reply, source, citations, confidence, knowledge_gap_issue_url?}` |
| `GET` | `/api/conversations/{id}` | Full message history (admin/debug) |

## Knowledge-gap loop

When Victoria can't answer from RAG and web search returns nothing useful, she:

1. Asks the LLM (`GAP_RESEARCH_PROMPT` in `personas.py`) for a 2-3 sentence research direction.
2. Calls `GitHubIssueClient.create_gap_issue(...)`, which either shells out to `gh issue create` (preferred — the image installs `gh`) or falls back to the REST API if `GH_TOKEN` is set.
3. Files an issue titled `[victoria-gap] <question>` with the `victoria-knowledge-gap` label.
4. Replies to the visitor with the human contacts and the issue URL.

After someone updates the site to cover the gap, rebuild the RAG index (`python scripts/build_rag_index.py --start https://albrightlab.com`) and close the issue.

## Phase-2 roadmap (explicitly not in this scaffold)

- NeMo Curator pipeline to extract Q/A pairs from the conversation log + closed gap issues.
- LoRA / full-SFT on a 7-8B base model to bake AlbrightLab tone into the weights.
- Replace the rebrand-pass system prompt with the fine-tuned model directly.
- Streaming responses over SSE.
- Admin dashboard at `/admin/victoria/` (consumed by `albrightlaboratories-dot-com/admin/`).

## Tests

```bash
pip install -r requirements.txt
MODE=local pytest -q
```

The smoke suite exercises `/health`, the mock LLM, `/api/chat`, and asserts the widget assets are served. It does **not** require Postgres or Triton — that's intentional, so the test suite stays green on a laptop.

## License

Proprietary — Albright Laboratories.
