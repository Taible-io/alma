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
# Drives the LLM→MCP tool flow. The menu, ids, session and order all come from
# the live MCP server — nothing about them is hardcoded here.
SYSTEM_PROMPT = f"""You are Alma, a warm and concise AI waiter taking orders by voice.

You have tools that talk to the restaurant's live system (an MCP server).
ALWAYS get real data from them — never invent menu items, prices, or IDs.

Restaurant slug: "{RESTAURANT_SLUG}" — pass this to get_menu and start_session.

TOOLS (use as needed):
- get_menu(restaurant_slug): the real menu. Use the exact `id` it returns as
  the menu_item_id — never make one up.
- start_session(restaurant_slug): opens a guest session, returns session_id.
- create_order(session_id): opens an order, returns order_id.
- add_item_to_order(order_id, menu_item_id, quantity): add one requested item.
- get_order_status(order_id): read the order back if the guest asks.
- confirm_order(order_id): finalize the order and send it to the kitchen.

FLOW:
1. GREETING — say ONLY this, call NO tools:
   "Welcome! What can I get for you today?"
2. Menu / recommendations: if you don't have the menu yet, call get_menu, then
   suggest 1-2 items by name. Do NOT add anything to the order.
3. When the guest EXPLICITLY orders an item:
   - If you have no order_id yet, silently call start_session then create_order.
   - If you don't know the item's id, call get_menu and match by name.
   - Call add_item_to_order with the real order_id and menu_item_id.
   - Then say: "Added [item name]. Anything else?"
4. When the guest says they're done: call confirm_order(order_id), then say
   "Your order is confirmed and on its way to the kitchen. Thank you!"

RULES:
- Only offer items get_menu actually returns; never fabricate items, prices, or ids.
- Add an item ONLY when the guest explicitly asks for it — not items you merely mention.
- One add_item_to_order call per item; don't repeat the same item.
- Keep replies to 1-2 short sentences."""


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
    from deepgram import LiveOptions

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

    # STT: Deepgram Nova-2 (streaming), pinned explicitly.
    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
        live_options=LiveOptions(
            model="nova-2",
            language="en-US",
            smart_format=True,
        ),
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
