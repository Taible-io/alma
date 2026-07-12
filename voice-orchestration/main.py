"""
Taible Voice Orchestration — Cloud pipeline (Deepgram + Fireworks).

Flow:
  Browser WebRTC mic → Deepgram STT → Fireworks LLM (gpt-oss-120b)
    → [tool calls → MCP server, streamable HTTP]
    → Deepgram TTS → Browser WebRTC speaker

The LLM adopts its tools directly from the remote MCP server: a single
Pipecat MCPClient connects to MCP_SERVER_URL, discovers the tools via
`tools/list`, and registers them on the LLM.

Environment variables:
  LIVEKIT_URL           — LiveKit server WebSocket URL
  LIVEKIT_API_KEY       — LiveKit API key
  LIVEKIT_API_SECRET    — LiveKit API secret
  DEEPGRAM_API_KEY      — Deepgram API key (STT + TTS)
  FIREWORKS_API_KEY     — Fireworks AI API key (LLM)
  FIREWORKS_BASE_URL    — Fireworks endpoint (default api.fireworks.ai/inference/v1)
  FIREWORKS_MODEL       — Fireworks model id (default gpt-oss-120b)
  MCP_SERVER_URL        — MCP server streamable-HTTP endpoint (…/mcp)
  RESTAURANT_SLUG       — restaurant slug
"""

import asyncio
import os
import json
import time
from pipecat.frames.frames import TextFrame
from dotenv import load_dotenv

load_dotenv()

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMContextFrame,
)
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

# ── Environment ──────────────────────────────────────────────────────────
LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
FIREWORKS_API_KEY = os.environ["FIREWORKS_API_KEY"]
FIREWORKS_BASE_URL = os.environ.get("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
FIREWORKS_MODEL = os.environ.get("FIREWORKS_MODEL", "accounts/fireworks/models/gpt-oss-120b")
# Single MCP server — streamable-HTTP / JSON-RPC endpoint (".../mcp"); the LLM
# adopts its tools from here. No auth required.
MCP_SERVER_URL = os.environ.get(
    "MCP_SERVER_URL",
    "https://mcp-server-git-285659212975.southamerica-east1.run.app/mcp",
)
RESTAURANT_SLUG = os.environ.get("RESTAURANT_SLUG", "taible-bistro")

# ── System Prompt ────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Alma, a friendly AI waiter at Cafe Alma restaurant.

The menu has these items ONLY:
- Taible Signature Burger (item_1) - GBP 12.99
- Truffle Fries (item_2) - GBP 5.99
- Vanilla Milkshake (item_3) - GBP 4.50
- Flared White Coffee (item_4) - GBP 3.99
- Chocolate Brownie (item_5) - GBP 6.50
- Pan-Seared Salmon (item_6) - GBP 18.99
- Loaded Fries (item_7) - GBP 7.99

STARTING GREETING (say this ONLY, nothing else):
"Welcome to Cafe Alma! What can I get for you today?"

HOW TO RESPOND:
1. If the customer asks for the menu or recommendations:
   Say: "Our most popular items are the Taible Signature Burger and Truffle Fries. What would you like?" (DO NOT call any tools)
2. If the customer explicitly orders an item (e.g. "I want a burger"):
   - FIRST, call the add_item_to_order tool.
   - THEN say: "Sure, I have added [item name] to your order. Anything else?"
3. If the customer is done ordering:
   Say: "Perfect! Please tap the green Confirm Order button on your screen."

ABSOLUTE RULES:
- Your GREETING must NEVER call any tool. No exceptions.
- ONLY call add_item_to_order when the customer has EXPLICITLY requested a specific item.
- DO NOT call add_item_to_order based on items you mention, suggest, or recommend.
- DO NOT call add_item_to_order multiple times in a single response.
- NEVER call confirm_order.
- Keep responses under 2 sentences."""


# ── Message log (frontend polls this file) ────────────────────────────────
MESSAGES_LOG = os.path.join(os.path.dirname(__file__), "..", "taible", "public", "messages.json")
_messages: list = []

def _write_message(role: str, text: str):
    global _messages
    text = text.strip()
    if not text:
        return
    _messages.append({"id": f"{role}-{int(time.time()*1000)}", "role": role, "text": text, "ts": time.time()})
    if len(_messages) > 60:
        _messages = _messages[-60:]
    try:
        os.makedirs(os.path.dirname(MESSAGES_LOG), exist_ok=True)
        with open(MESSAGES_LOG, "w", encoding="utf-8") as f:
            json.dump(_messages, f)
    except Exception:
        pass

class TextCapture(FrameProcessor):
    """Captures LLM text output sentence by sentence and writes to messages.json."""
    def __init__(self):
        super().__init__()
        self._buf = ""

    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TextFrame) and frame.text:
            self._buf += frame.text
            # Use slightly safer sentence splitting to avoid breaking on "$12."
            if any(self._buf.endswith(p) for p in (". ", "! ", "? ", "\n")) or (len(self._buf) > 80 and any(self._buf.rstrip().endswith(p) for p in (".", "!", "?"))):
                _write_message("assistant", self._buf)
                self._buf = ""
        await self.push_frame(frame, direction)

# ── LiveKit token ────────────────────────────────────────────────────────
def generate_livekit_token(room_name: str) -> str:
    try:
        from livekit.api import AccessToken, VideoGrants
        token = (
            AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            .with_identity("taible-agent")
            .with_name("Taible AI")
            .with_grants(VideoGrants(room_join=True, room=room_name))
            .to_jwt()
        )
        return token
    except Exception as exc:
        print(f"[LiveKit] Token generation failed: {exc}")
        return ""


# ── Pipeline factory ─────────────────────────────────────────────────────
async def create_pipeline(room_name: str) -> PipelineTask:
    global _messages
    _messages = []  # Clear chat history on new session

    # ── CRITICAL: Clear ALL shared state files on every new session ──────────
    # Without this, stale order.json from a previous session gets read by the
    # frontend polling loop and auto-populates the cart before the user speaks.
    ORDER_LOG = os.path.join(os.path.dirname(__file__), "..", "taible", "public", "order.json")
    try:
        os.makedirs(os.path.dirname(MESSAGES_LOG), exist_ok=True)
        with open(MESSAGES_LOG, "w") as f:
            json.dump([], f)
        with open(ORDER_LOG, "w") as f:
            json.dump([], f)
        print("[Session] Cleared messages.json and order.json for fresh session.")
    except Exception as e:
        print(f"[Session] Warning: could not clear log files: {e}")

    from pipecat.services.openai.llm import OpenAILLMService
    from pipecat.services.deepgram.stt import DeepgramSTTService
    from pipecat.services.deepgram.tts import DeepgramTTSService

    token = generate_livekit_token(room_name)

    transport = LiveKitTransport(
        url=LIVEKIT_URL,
        token=token or None,
        room_name=room_name,
        params=LiveKitParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(),
            vad_audio_passthrough=True,
        ),
    )

    # STT: Deepgram (streaming). No model is pinned here, so this uses the
    # Deepgram/Pipecat default model. Pass live_options=LiveOptions(model=...)
    # to pin a specific one (e.g. "nova-3").
    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
    )

    # LLM: Fireworks AI (gpt-oss-120b) via OpenAI-compatible API
    llm = OpenAILLMService(
        api_key=FIREWORKS_API_KEY,
        model=FIREWORKS_MODEL,
        base_url=FIREWORKS_BASE_URL,
    )

    # TTS: Deepgram Aura
    tts = DeepgramTTSService(
        api_key=DEEPGRAM_API_KEY,
        voice="aura-asteria-en",
    )

    # ── LLM adopts the MCP server's tools ───────────────────────────────────
    # A single MCPClient connects to the remote MCP server over streamable HTTP
    # (MCP_SERVER_URL, ".../mcp", no auth), discovers its tools via `tools/list`,
    # and registers them on the Fireworks LLM. This is the only tool source.
    from pipecat.services.mcp_service import MCPClient
    mcp_client = MCPClient(server_params=MCP_SERVER_URL)
    tools_schema = await mcp_client.register_tools(llm)
    print(f"[MCP] Fireworks adopted tools from {MCP_SERVER_URL}")

    context = LLMContext(
        messages=[{"role": "user", "content": SYSTEM_PROMPT}],
        tools=tools_schema,
    )
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMUserAggregatorParams,
        LLMAssistantAggregatorParams,
    )
    context_aggregator = LLMContextAggregatorPair(
        context=context,
        user_params=LLMUserAggregatorParams(),
        assistant_params=LLMAssistantAggregatorParams(),
    )

    text_capture = TextCapture()

    pipeline = Pipeline(
        [
            transport.input(),               # WebRTC audio in
            stt,                              # Deepgram STT
            context_aggregator.user(),        # Accumulate user speech turn
            llm,                              # Fireworks LLM (gpt-oss-120b)
            text_capture,                     # Write agent text to messages.json
            tts,                              # Deepgram TTS
            transport.output(),               # WebRTC audio out
            context_aggregator.assistant(),   # Record assistant turn
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(allow_interruptions=True),
    )

    # Keep the MCP client referenced for the pipeline's lifetime so its
    # connection isn't garbage-collected mid-session.
    task._mcp_client = mcp_client

    @transport.event_handler("on_first_participant_joined")
    async def on_joined(transport, participant):
        # Trigger the agent's opening greeting immediately on join
        await task.queue_frames([LLMContextFrame(context=context)])

    return task


# ── Entry point ──────────────────────────────────────────────────────────
async def main():
    while True:
        try:
            runner = PipelineRunner()
            task = await create_pipeline(
                room_name=os.environ.get("LIVEKIT_ROOM", "taible-demo")
            )
            await runner.run(task)
        except Exception as e:
            print(f"Pipeline error: {e}")
        print("Pipeline ended, restarting for next session in 2 seconds...")
        await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
