# voice-orchestration

Cloud voice pipeline for the Taible AI waiter (**Alma**) — built on **Pipecat** + **LiveKit**.

Bridges the diner's browser (WebRTC mic/speaker) to a streaming
**STT → LLM → TTS** loop with tool-calling, interruptions, and turn-taking.

## Pipeline

```
Browser WebRTC mic
  → Deepgram STT
  → Fireworks AI LLM (gpt-oss-120b)   ── tool calls ──▶ FastMCP server
  → Deepgram TTS (Aura)
  → Browser WebRTC speaker
```

| Stage       | Service                                                     |
| ----------- | ---------------------------------------------------------- |
| Transport   | LiveKit (WebRTC)                                            |
| STT         | Deepgram                                                    |
| **LLM**     | **Fireworks AI — `accounts/fireworks/models/gpt-oss-120b`** (OpenAI-compatible API) |
| TTS         | Deepgram Aura (`aura-asteria-en`)                          |
| Tools       | FastMCP server (with in-memory mock DB fallback)           |
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
| `MCP_SERVER_URL`        |          | FastMCP URL (default `http://localhost:8080`)            |
| `RESTAURANT_SLUG`       |          | Restaurant slug (default `taible-bistro`)                |
| `LIVEKIT_ROOM`          |          | Room name (default `taible-demo`)                        |

## Notes

- The LLM runs on **Fireworks AI** via Pipecat's OpenAI-compatible `OpenAILLMService`,
  so no extra dependency is needed beyond the `openai` Pipecat extra.
- The legacy AMD/vLLM (Qwen) env vars remain in `.env.example` but are no longer used
  for the LLM path.
- Never commit secrets — keep real keys in `.env`.
