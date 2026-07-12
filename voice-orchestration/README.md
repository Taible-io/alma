# voice-orchestration

Cloud voice pipeline for the Taible AI waiter (**Alma**) — built on **Pipecat** + **LiveKit**.

Bridges the diner's browser (WebRTC mic/speaker) to a streaming
**STT → LLM → TTS** loop with tool-calling, interruptions, and turn-taking.

## Pipeline

```
Browser WebRTC mic
  → Deepgram STT
  → Fireworks AI LLM (gpt-oss-120b)   ── tool calls ──▶ MCP server (streamable HTTP)
  → Deepgram TTS (Aura)
  → Browser WebRTC speaker
```

| Stage       | Service                                                     |
| ----------- | ---------------------------------------------------------- |
| Transport   | LiveKit (WebRTC)                                            |
| STT         | Deepgram — **model not pinned** (uses the Deepgram/Pipecat default; no `model` is set in code) |
| **LLM**     | **Fireworks AI — `accounts/fireworks/models/gpt-oss-120b`** (OpenAI-compatible API) |
| TTS         | Deepgram Aura (`aura-asteria-en`)                          |
| Tools       | Remote MCP server, adopted by the LLM via a single Pipecat `MCPClient` |
| Framework   | Pipecat                                                    |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the values
python main.py
```

## Environment variables

| Variable                | Required | Notes                                                    |
| ----------------------- | -------- | -------------------------------------------------------- |
| `LIVEKIT_URL`           | ✅       | LiveKit server WebSocket URL                             |
| `LIVEKIT_API_KEY`       | ✅       | LiveKit API key                                          |
| `LIVEKIT_API_SECRET`    | ✅       | LiveKit API secret                                       |
| `DEEPGRAM_API_KEY`      | ✅       | Deepgram key (STT + TTS)                                 |
| `FIREWORKS_API_KEY`     | ✅       | Fireworks AI key (LLM)                                   |
| `FIREWORKS_BASE_URL`    |          | Default `https://api.fireworks.ai/inference/v1`          |
| `FIREWORKS_MODEL`       |          | Default `accounts/fireworks/models/gpt-oss-120b`         |
| `MCP_SERVER_URL`        |          | MCP streamable-HTTP endpoint (`…/mcp`); the LLM adopts its tools from here |
| `RESTAURANT_SLUG`       |          | Restaurant slug (default `taible-bistro`)                |
| `LIVEKIT_ROOM`          |          | Room name (default `taible-demo`)                        |

## Notes

- The LLM runs on **Fireworks AI** via Pipecat's OpenAI-compatible `OpenAILLMService`.
- **Tools:** a single Pipecat `MCPClient` connects to `MCP_SERVER_URL` (streamable HTTP,
  no auth), runs `tools/list`, and registers the discovered tools on the LLM — this is
  the only tool source (no local mock, no fallback). If the MCP server is unreachable at
  startup the pipeline errors and restarts.
- Never commit secrets — keep real keys in `.env`.
