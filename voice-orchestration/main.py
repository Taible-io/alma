"""
Taible Voice Orchestration — Cloud pipeline (Groq + Deepgram).

Flow:
  Browser WebRTC mic → Groq Whisper STT → Groq Llama 3 LLM
    → [tool calls → FastMCP server]
    → Deepgram TTS → Browser WebRTC speaker

Environment variables:
  LIVEKIT_URL           — LiveKit server WebSocket URL
  LIVEKIT_API_KEY       — LiveKit API key
  LIVEKIT_API_SECRET    — LiveKit API secret
  GROQ_API_KEY          — Groq API key (STT + LLM)
  DEEPGRAM_API_KEY      — Deepgram API key (TTS)
  MCP_SERVER_URL        — FastMCP URL (http://localhost:8080)
  RESTAURANT_SLUG       — restaurant slug
"""

import asyncio
import os
import json
import time
import httpx
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
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.transports.livekit.transport import LiveKitParams, LiveKitTransport

# ── Environment ──────────────────────────────────────────────────────────
LIVEKIT_URL = os.environ["LIVEKIT_URL"]
LIVEKIT_API_KEY = os.environ["LIVEKIT_API_KEY"]
LIVEKIT_API_SECRET = os.environ["LIVEKIT_API_SECRET"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]
VLLM_BASE_URL = os.environ.get("VLLM_BASE_URL", "")
FIREWORKS_API_KEY = os.environ["FIREWORKS_API_KEY"]
FIREWORKS_BASE_URL = os.environ.get("FIREWORKS_BASE_URL", "https://api.fireworks.ai/inference/v1")
FIREWORKS_MODEL = os.environ.get("FIREWORKS_MODEL", "accounts/fireworks/models/gpt-oss-120b")
MCP_SERVER_URL = os.environ.get("MCP_SERVER_URL", "http://localhost:8080")
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

# ── MCP Tools Manifest ───────────────────────────────────────────────────
MCP_TOOLS = [
    FunctionSchema(
        name="get_menu",
        description="Returns all available menu items with prices.",
        properties={"restaurant_slug": {"type": "string"}},
        required=["restaurant_slug"],
    ),
    FunctionSchema(
        name="start_session",
        description="Opens a guest session. Call when conversation starts.",
        properties={
            "restaurant_slug": {"type": "string"},
            "table_number": {"type": "string"},
        },
        required=["restaurant_slug"],
    ),
    FunctionSchema(
        name="create_order",
        description="Creates a new pending order for the session.",
        properties={"session_id": {"type": "string"}},
        required=["session_id"],
    ),
    FunctionSchema(
        name="add_item_to_order",
        description="Adds a menu item to an existing order. Call this silently for EACH item the customer orders.",
        properties={
            "order_id": {"type": "string"},
            "menu_item_id": {"type": "string"},
            "quantity": {"type": "integer", "default": 1},
        },
        required=["order_id", "menu_item_id"],
    ),
    # NOTE: confirm_order is intentionally removed — the customer confirms via the UI button.
    FunctionSchema(
        name="call_waiter",
        description="Calls a human staff member to the table.",
        properties={"session_id": {"type": "string"}},
        required=["session_id"],
    ),
]


# ── Tool call dispatcher ─────────────────────────────────────────────────
async def call_mcp_tool(tool_name: str, tool_args: dict) -> str:
    """Forward a tool call to the FastMCP server and return the JSON result."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.post(
                f"{MCP_SERVER_URL}/tools/{tool_name}",
                json=tool_args,
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            print(f"[MCP] ERROR: {tool_name} failed: {exc}")
            return json.dumps({"error": str(exc)})


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

    # STT: Deepgram Nova-2 (streaming)
    stt = DeepgramSTTService(
        api_key=DEEPGRAM_API_KEY,
    )

    # LLM: Fireworks AI (gpt-oss-120b) via OpenAI-compatible API
    from pipecat.services.openai.llm import OpenAILLMService
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

    context = LLMContext(
        messages=[{"role": "user", "content": SYSTEM_PROMPT}],
        tools=MCP_TOOLS,
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

    # In-memory mock database for the hackathon demo
    MOCK_DB = {
        "menu": [
            {"id": "item_1", "name": "Taible Signature Burger", "price": 12.99, "description": "Beef patty, cheese, lettuce, house sauce"},
            {"id": "item_2", "name": "Truffle Fries", "price": 5.99, "description": "Crispy fries with truffle oil and parmesan"},
            {"id": "item_3", "name": "Vanilla Milkshake", "price": 4.50, "description": "Classic vanilla bean milkshake"},
            {"id": "item_4", "name": "Flared White Coffee", "price": 3.99, "description": "Our signature flared white coffee"},
            {"id": "item_5", "name": "Chocolate Brownie", "price": 6.50, "description": "Warm chocolate brownie with fudge"},
            {"id": "item_6", "name": "Pan-Seared Salmon", "price": 18.99, "description": "Fresh salmon with lemon dill sauce"},
            {"id": "item_7", "name": "Loaded Fries", "price": 7.99, "description": "Fries loaded with cheese and bacon"}
        ],
        "order": []
    }

    # Guard flag: add_item_to_order is BLOCKED until the customer has spoken at least once.
    # This prevents Qwen from calling the tool during its opening greeting.
    user_has_spoken = {"value": False}

    async def mcp_handler(params):
        tool_name = params.function_name
        try:
            tool_args = (
                json.loads(params.arguments)
                if isinstance(params.arguments, str)
                else params.arguments
            )
        except Exception:
            tool_args = {}

        print(f"[MCP] CALL: {tool_name}({tool_args})")

        # GUARD: Block add_item_to_order during the greeting (before user has spoken).
        # After the greeting, context grows as user turns are added.
        # The initial context has only 1 message (system prompt as user role).
        # Once the customer actually speaks, context will have ≥ 3 messages.
        if tool_name == "add_item_to_order":
            real_user_turns = [m for m in context.messages if m.get("role") == "user" and m.get("content") != SYSTEM_PROMPT]
            if not real_user_turns:
                print("[MCP] BLOCKED add_item_to_order — no real user turn yet (still in greeting phase)")
                await params.result_callback(json.dumps({"status": "ignored", "reason": "customer has not spoken yet"}))
                return


        # Mock implementations
        if tool_name == "start_session":
            MOCK_DB["order"] = []  # Reset order on new session
            result = json.dumps({"session_id": "sess_123", "status": "started", "message": "Welcome!"})
        elif tool_name == "get_menu":
            result = json.dumps(MOCK_DB["menu"])
        elif tool_name == "create_order":
            MOCK_DB["order"] = []
            result = json.dumps({"order_id": "ord_123", "status": "created"})
        elif tool_name == "add_item_to_order":
            item_id = tool_args.get("menu_item_id") or tool_args.get("item_id", "")
            found = next((item for item in MOCK_DB["menu"] if item_id.lower() in item["id"].lower() or item_id.lower() in item["name"].lower()), None)
            if found:
                MOCK_DB["order"].append(found)
                # Write item addition to the shared order file so frontend can update cart
                order_file = os.path.join(os.path.dirname(__file__), "..", "taible", "public", "order.json")
                try:
                    with open(order_file, "w") as f:
                        json.dump(MOCK_DB["order"], f)
                except: pass
                result = json.dumps({"status": "added", "item": found})
            else:
                result = json.dumps({"error": "Item not found in menu"})
        else:
            # confirm_order tool has been removed from AI. Any unknown tool returns ok silently.
            result = json.dumps({"status": "ok"})

        print(f"[MCP] DONE: {result[:120]}")
        await params.result_callback(result)

    for tool in MCP_TOOLS:
        llm.register_function(tool.name, mcp_handler)

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
