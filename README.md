# Taible — AI Voice Ordering System
> Cloud restaurant voice ordering with Pipecat, LiveKit, Deepgram, Fireworks AI (gpt-oss-120b), and FastMCP.
## Demo
https://github.com/user-attachments/assets/7ddceacc-8981-42ba-9df1-8ac47e1637cf

## Architecture

```
Guest Browser (PWA)
  │ WebRTC (LiveKit)
  ▼
voice-orchestration/ (Pipecat CPU pipeline)
  ├── Deepgram STT (nova-2) ──────────────────┐
  ├── Fireworks AI LLM (gpt-oss-120b) ─────────┤  managed cloud APIs
  └── Deepgram TTS (Aura) ─────────────────────┘
  │
  │ MCP tool calls (streamable HTTP)
  ▼
mcp-server/ (FastMCP — Python, Cloud Run)
  │ Supabase service-role key
  ▼
Supabase (PostgreSQL + Realtime)
  │ Realtime channel
  ▼
taible/ (Next.js Staff Dashboard)
```

> **Note on the GPU stack:** Taible was originally designed to run STT/LLM/TTS on a
> self-hosted **AMD ROCm** GPU (Whisper + vLLM/Qwen + Kokoro). That path is now
> **legacy** — the running system uses managed cloud APIs (Deepgram + Fireworks). The
> old GPU code still lives in `gpu-rocm/` but is not used by the agent.

## Repositories

| Folder | Purpose |
|--------|---------|
| `taible/` | Guest PWA + Staff Dashboard (Next.js 15) |
| `voice-orchestration/` | Pipecat CPU pipeline — WebRTC ↔ cloud STT/LLM/TTS glue |
| `mcp-server/` | FastMCP tool server (get_menu, create_order, …) over Supabase |
| `gpu-rocm/` | **Legacy** AMD ROCm Docker stack (vLLM, Whisper, Kokoro) — not used by the current pipeline |
| `architecture/` | C4 D2-as-code diagrams |

---

## Setup Order

### 1. Supabase Database

1. Create a project at [supabase.com](https://supabase.com) (free tier works)
2. In the SQL Editor, run in order:
   - `mcp-server/db/schema.sql`
   - `mcp-server/db/seed.sql`
3. Copy your **Project URL** and **service_role secret key** from Settings → API

### 2. MCP Server (FastMCP)

```bash
cd mcp-server
cp .env.example .env
# Edit .env: fill SUPABASE_URL and SUPABASE_SECRET_KEY

pip install -r requirements.txt
python server.py
# → FastMCP (streamable HTTP) listening on http://localhost:8080/mcp
```

The server speaks the **MCP protocol** (streamable HTTP / JSON-RPC) at `/mcp` — it does
**not** expose per-tool REST routes. Inspect its tools with any MCP client, e.g.:

```bash
npx @modelcontextprotocol/inspector   # then point it at http://localhost:8080/mcp
```

### 3. Voice Orchestration (Pipecat)

You need a **LiveKit** server. Use [LiveKit Cloud](https://livekit.io) (free tier) or self-host.
You also need **Deepgram** and **Fireworks AI** API keys.

```bash
cd voice-orchestration
cp .env.example .env
# Edit .env: fill LIVEKIT_*, DEEPGRAM_API_KEY, FIREWORKS_API_KEY, MCP_SERVER_URL

pip install -r requirements.txt
python main.py
# → Pipecat agent connected to LiveKit room "taible-demo"
```

The LLM adopts its tools directly from the MCP server: a single Pipecat `MCPClient`
connects to `MCP_SERVER_URL`, discovers the tools via `tools/list`, and registers them
on the Fireworks LLM.

### 4. Frontend (Next.js)

```bash
cd taible
cp .env.local.example .env.local
# Edit .env.local: fill NEXT_PUBLIC_PIPECAT_URL, NEXT_PUBLIC_SUPABASE_URL,
#                        NEXT_PUBLIC_SUPABASE_ANON_KEY

npm install
npm run dev
# → http://localhost:3000
```

---

## Demo Flow

1. Open `http://localhost:3000` on your phone (or scan the QR code)
2. Tap **"Start talking"** — the orb connects to Pipecat via LiveKit
3. Say: *"Hi, what's on the menu?"*
4. The AI reads the menu from Supabase via MCP and speaks back
5. Order something: *"I'd like a flat white with oat milk"*
6. Confirm: *"Yes, that's everything"*
7. Switch to **Staff View →** to see the order appear in real-time

---

## Environment Variables Reference

### `mcp-server/.env`
| Variable | Description |
|----------|-------------|
| `SUPABASE_URL` | `https://your-ref.supabase.co` |
| `SUPABASE_SECRET_KEY` | Service-role key (never expose to browser) |
| `PORT` | HTTP port (default `8080`) |

### `voice-orchestration/.env`
| Variable | Description |
|----------|-------------|
| `LIVEKIT_URL` | `wss://your.livekit.cloud` |
| `LIVEKIT_API_KEY` | LiveKit API key |
| `LIVEKIT_API_SECRET` | LiveKit API secret |
| `DEEPGRAM_API_KEY` | Deepgram key (STT + TTS) |
| `FIREWORKS_API_KEY` | Fireworks AI key (LLM) |
| `FIREWORKS_BASE_URL` | default `https://api.fireworks.ai/inference/v1` |
| `FIREWORKS_MODEL` | default `accounts/fireworks/models/gpt-oss-120b` |
| `MCP_SERVER_URL` | MCP streamable-HTTP endpoint (`…/mcp`) |
| `RESTAURANT_SLUG` | `taible-bistro` |
| `LIVEKIT_ROOM` | `taible-demo` |

### `taible/.env.local`
| Variable | Description |
|----------|-------------|
| `NEXT_PUBLIC_PIPECAT_URL` | LiveKit server URL |
| `NEXT_PUBLIC_SUPABASE_URL` | Supabase project URL |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | Supabase anon/publishable key (safe for browser) |
