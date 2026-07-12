# Taible — Technical Architecture

Engineering-level reference for how the Taible voice-ordering system is actually
built and wired. Complements the C4 diagrams in [`diagrams/`](./diagrams) — where
this document and a diagram disagree, **this document reflects the current code**
(see [§9 Design drift](#9-design-drift-diagrams-vs-code)).

- **Scope:** the running system across `voice-orchestration/`, `mcp-server/`, `taible/`.
- **Audience:** engineers modifying the pipeline, the MCP tools, or the frontend.
- **Last verified against code:** 2026-07-12.

---

## 1. System overview

A diner scans a table QR, opens a browser PWA, and talks to **Alma**, an AI waiter.
Audio streams over WebRTC to a cloud voice pipeline that does
speech-to-text → LLM → text-to-speech, with the LLM calling MCP tools to read the
menu and build an order. Confirmed orders land in Postgres (Supabase) and surface
on a staff dashboard.

```
Guest browser (PWA, WebRTC)
   │  bidirectional audio
   ▼
voice-orchestration/  ── Pipecat pipeline (CPU) ──────────────────┐
   Deepgram STT → Fireworks LLM (gpt-oss-120b) → Deepgram TTS      │
                        │ MCP tool calls (streamable HTTP)         │
                        ▼                                          │
mcp-server/  ── FastMCP "taible-mcp" ──────────────────────────────┘
                        │ Supabase service-role key
                        ▼
                 Supabase (Postgres + Realtime)
                        ▲
                        │ reads menu/orders
                 taible/ (Next.js staff dashboard + guest PWA)
```

---

## 2. Component inventory

| # | Component | Path | Runtime | Responsibility |
|---|-----------|------|---------|----------------|
| 1 | Guest PWA + Staff dashboard | `taible/` | Next.js 15 / React 19 | Mic capture, WebRTC client, live cart, kitchen view |
| 2 | Voice orchestration | `voice-orchestration/` | Python / Pipecat, CPU | Turn-taking, STT→LLM→TTS glue, tool dispatch |
| 3 | STT + TTS | external | Deepgram cloud | Streaming transcription + Aura voice synthesis |
| 4 | LLM | external | Fireworks AI | `gpt-oss-120b`, OpenAI-compatible, tool-calling |
| 5 | Transport | external | LiveKit | WebRTC media routing browser ⇄ agent |
| 6 | Tool server | `mcp-server/` | Python / FastMCP, Cloud Run | Menu/session/order tools over Supabase |
| 7 | Database | external | Supabase (Postgres) | Restaurants, menu, sessions, orders |
| 8 | (legacy) GPU stack | `gpu-rocm/` | AMD ROCm / Docker | Original self-hosted Whisper/vLLM/Kokoro — see §9 |

---

## 3. Voice pipeline (voice-orchestration/main.py)

Pipecat `Pipeline`, one instance per guest session, restarted in a loop by `main()`:

```
transport.input()            # LiveKit WebRTC audio in
  → DeepgramSTTService        # streaming STT
  → context_aggregator.user() # accumulate the user turn
  → OpenAILLMService          # Fireworks gpt-oss-120b (base_url = Fireworks)
  → TextCapture               # mirror assistant text to messages.json
  → DeepgramTTSService        # Aura (aura-asteria-en)
  → transport.output()        # WebRTC audio out
  → context_aggregator.assistant()
```

Key implementation facts:

- **LLM** is `OpenAILLMService` pointed at `FIREWORKS_BASE_URL` with model
  `FIREWORKS_MODEL` (default `accounts/fireworks/models/gpt-oss-120b`). Fireworks is
  OpenAI-wire-compatible, so no dedicated service class is needed.
- **VAD:** Silero (`SileroVADAnalyzer`) with interruptions enabled
  (`allow_interruptions=True`).
- **Greeting:** on `on_first_participant_joined`, an empty `LLMContextFrame` is queued
  so Alma speaks first.
- **Session isolation:** `create_pipeline()` clears `messages.json` and `order.json` at
  the start of every session to avoid stale cart/transcript bleed-through.
- **Tools:** either the live MCP server (preferred) or a local mock — see §4.

### Environment (voice-orchestration)

| Var | Required | Default | Purpose |
|-----|----------|---------|---------|
| `LIVEKIT_URL` / `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | ✅ | — | WebRTC transport + agent token |
| `DEEPGRAM_API_KEY` | ✅ | — | STT + TTS |
| `FIREWORKS_API_KEY` | ✅ | — | LLM |
| `FIREWORKS_BASE_URL` | | `https://api.fireworks.ai/inference/v1` | LLM endpoint |
| `FIREWORKS_MODEL` | | `accounts/fireworks/models/gpt-oss-120b` | LLM model id |
| `MCP_SERVER_URL` | | `http://localhost:8080` | Legacy REST tool URL (`/tools/{name}`) |
| `MCP_SERVER_URL_2` | | Cloud Run `.../mcp` | MCP streamable-HTTP endpoint |
| `RESTAURANT_SLUG` / `LIVEKIT_ROOM` | | `taible-bistro` / `taible-demo` | Session config |
| `GROQ_API_KEY` | ✅ (unused) | — | Legacy required-but-dead var; slated for removal |

---

## 4. Tool layer (MCP)

Two ways the LLM can obtain tools, selected at pipeline build time:

1. **Remote MCP (preferred).** Pipecat `MCPClient` connects to `MCP_SERVER_URL_2`
   (streamable HTTP, no auth), runs `tools/list`, and registers the discovered tools
   on the LLM. This is the "Fireworks adopts the MCP server" path.
2. **Local mock (fallback).** If the MCP client can't connect, the code registers a
   hand-written `MCP_TOOLS` schema backed by an in-memory `MOCK_DB` handler. Used for
   offline demos.

### 4.1 MCP server (`mcp-server/server.py`)

FastMCP app named `taible-mcp` (v3.4.x), deployed on **Google Cloud Run**
(`southamerica-east1`). Every tool goes through the Supabase **service-role** key —
this process is the only writer to the `taibledb` tables (RLS assumes this).

| Tool | Args | Effect |
|------|------|--------|
| `get_restaurants` | — | List restaurants / valid slugs |
| `get_menu` | `restaurant_slug` | Available menu items + modifiers |
| `start_session` | `restaurant_slug`, `table_number?` | Open a guest session |
| `create_order` | `session_id` | New pending order |
| `add_item_to_order` | `order_id`, `menu_item_id`, `quantity?`, `modifier_ids?` | Add line item; recompute total |
| `confirm_order` | `order_id` | Mark confirmed → kitchen |
| `get_order_status` | `order_id` | Status, total, items |
| `toggle_stock` | `menu_item_id`, `is_available` | Staff: stock in/out |
| `close_session` | `session_id` | End session |

**Protocol:** MCP over streamable HTTP (JSON-RPC): `initialize` → `notifications/initialized`
→ `tools/list` / `tools/call`. Session id is returned in the `mcp-session-id` response
header and echoed on subsequent requests.

---

## 5. Data model (Supabase / Postgres)

Tables (`mcp-server/db/schema.sql`), ordered by dependency:

```
restaurants
  └─ menu_items ─── modifiers
  └─ sessions
       └─ orders
            └─ order_items ─── order_item_modifiers
```

- Order total is recomputed server-side on every `add_item_to_order`
  (`_recalculate_order_total`: Σ quantity × (unit_price + Σ modifier price_delta)).
- `get_menu` filters `is_available = true` at the item level.

---

## 6. Frontend integration (`taible/`)

Next.js 15 / React 19, using `@pipecat-ai/client-react`, `@livekit/components-react`,
and `@supabase/supabase-js`. Two surfaces: the **guest PWA** (voice + live cart) and
the **staff dashboard** (kitchen orders).

**File-polling bridge.** The orchestrator and the frontend share state through JSON
files in `taible/public/`:

| File | Written by | Read by | Contents |
|------|-----------|---------|----------|
| `messages.json` | orchestrator `TextCapture` | guest PWA | Rolling chat transcript (≤60 msgs) |
| `order.json` | orchestrator mock `add_item` handler | guest PWA | Current cart line items |
| `kitchen-orders.json` | (staff/kitchen flow) | staff dashboard | Orders for the kitchen |

> ⚠️ **Bridge caveat:** `order.json` is written by the **local mock** tool handler.
> When the LLM adopts the **remote MCP** server (§4, path 1), order writes go straight
> to Supabase and `order.json` is no longer updated — the guest cart needs an
> alternate source (poll `get_order_status`, Supabase Realtime, or an explicit
> write-back). Track this before shipping the MCP-adoption branch.

---

## 7. Runtime sequence (happy path)

```
Guest scans QR → PWA opens → WebRTC join (LiveKit)
  → agent greets ("Welcome to Cafe Alma!")
  → guest: "I'll have a burger"
      → Deepgram STT → Fireworks LLM
      → LLM tool call: add_item_to_order(order_id, menu_item_id)
          → MCP server → Supabase insert + total recompute
      → LLM reply → Deepgram TTS → guest hears confirmation
  → guest taps "Confirm Order" in the UI (confirm is UI-driven, not LLM-driven)
  → order → kitchen / staff dashboard
```

Note: the system prompt deliberately forbids the LLM from calling `confirm_order`;
confirmation is a UI action. The remote MCP server *exposes* `confirm_order`, so this
is enforced only by the prompt once remote tools are adopted.

---

## 8. Deployment topology

| Component | Host | Notes |
|-----------|------|-------|
| MCP server | Google Cloud Run (`southamerica-east1`) | Secrets via Secret Manager; public `/mcp` |
| Voice orchestration | Container (`Dockerfile` present) | Long-running; one pipeline per session |
| Frontend | Next.js host (Vercel-class) | PWA + dashboard |
| Database | Supabase | Postgres + Realtime |
| LLM / STT / TTS / transport | Fireworks / Deepgram / LiveKit | Managed SaaS |

---

## 9. Design drift: diagrams vs code

The C4 diagrams (`diagrams/context/`, `diagrams/container/`) and the repo root README
still describe the **original self-hosted design**: STT/LLM/TTS on an **AMD ROCm GPU**
(whisper.cpp, vLLM Llama/Qwen, Kokoro), with the GPU host as a hard runtime dependency.

The code has since **pivoted to managed cloud services**:

| Stage | Diagrams (original) | Code (current) |
|-------|---------------------|----------------|
| STT | Whisper on ROCm | **Deepgram** |
| LLM | vLLM Llama/Qwen on ROCm | **Fireworks `gpt-oss-120b`** |
| TTS | Kokoro on ROCm | **Deepgram Aura** |
| Tools | MCP on Fly.io/VPS | **MCP on Cloud Run** |

`gpu-rocm/` and the GPU env vars (`VLLM_BASE_URL`, `WHISPER_BASE_URL`,
`KOKORO_BASE_URL`) remain as the legacy path. **Action:** update the D2 diagrams to a
cloud container view (or add a Component-level diagram) so the canonical picture
matches the code.

---

## 10. Known gaps / tech debt

- `GROQ_API_KEY` is required at import but never used — remove.
- Module docstring in `main.py` still says "Groq + Deepgram" — update to Fireworks.
- `call_mcp_tool()` (REST `/tools/{name}`) is dead code superseded by MCP client.
- `order.json` sync breaks under remote-MCP adoption (§6 caveat).
- Pipecat `MCPClient` connection lifetime under Cloud Run needs load verification.
