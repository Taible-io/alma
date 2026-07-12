# Taible-io — Architecture

Diagrams are [D2](https://d2lang.com) source, versioned in git. CI renders them to SVG on every push — the `.d2` is the source of truth, never hand-export.

> **Just want to look?** Paste any `.d2` into the **[D2 Playground](https://play.d2lang.com/)** to render it in your browser — no install.

## The system

A restaurant **voice agent**. A diner scans a QR at the table, talks to the agent in the browser over WebRTC, and a **self-hosted AMD GPU (ROCm)** pipeline does speech→LLM→speech. The LLM calls our MCP tools to read the menu and place orders.

**Canonical diagram:** [`diagrams/container/voice-agent.d2`](./diagrams/container/voice-agent.d2)
**Engineering-level detail:** [`TECHNICAL.md`](./TECHNICAL.md) — components, data flow, tool/MCP contracts, env config, and the current cloud-vs-GPU design drift.

```
QR → PWA (WebRTC) → Pipecat/LiveKit (CPU)
      → AMD GPU/ROCm: Whisper STT → vLLM → Kokoro TTS
      → MCP tools → Postgres + POS
```

## Organized by C4

We manage this repo with the **[C4 model](https://c4model.com)**: one folder per zoom level, big-picture → detail. Put each diagram at the level it describes ([ADR-0002](./adr/0002-d2-c4-and-ci-rendering.md)).

| Folder | C4 level | Answers | Status |
|---|---|---|---|
| `diagrams/context/` | 1 · Context | Who uses Taible and what external systems it touches | ✅ `system-context.d2` |
| `diagrams/container/` | 2 · Container | The apps/services/datastores and how they talk | ✅ `voice-agent.d2` |
| `diagrams/component/` | 3 · Component | Inside one container (e.g. the ROCm pipeline) | ⬜ to add |
| `diagrams/data/` | — | Database / data-model sketches | ⬜ to add |
| `diagrams/ui/` | — | UI flows and screen maps | ⬜ to add |

Rule of thumb: **start at Container** (where we are), add a **Context** view once external integrations firm up, and a **Component** view for any container complex enough to need one.

## Stack

| Stage | Tech | Host |
|---|---|---|
| STT | whisper.cpp (HIP) | **AMD GPU · ROCm** |
| LLM | vLLM — Llama 3.1 8B / Qwen 2.5 7B | **AMD GPU · ROCm** |
| TTS | Kokoro (PyTorch/ROCm) | **AMD GPU · ROCm** |
| Orchestration | Pipecat / LiveKit Agents | CPU |
| Tools | MCP server (`get_menu`, `place_order`, …) | small VPS / Fly.io |
| Data | Postgres / Supabase + POS/kitchen | managed |

Fits in **24 GB VRAM** (e.g. Radeon 7900 XTX). **AMD/ROCm on AMD cloud is a hard requirement** — no CUDA.

## Edit a diagram

1. Add or change a `.d2` in the C4 folder that matches its level (see table above).
2. Open a PR — CI checks it renders; merge to `main` writes the SVG to `rendered/`.

Preview options:
- **In-browser (no install):** paste the `.d2` into the **[D2 Playground](https://play.d2lang.com/)**.
- **Local:**
  ```bash
  curl -fsSL https://d2lang.com/install.sh | sh -s --
  d2 diagrams/container/voice-agent.d2 out.svg
  ```

Decisions behind this setup: [`adr/`](./adr). New to Taible? Start at [playbook_startup](https://github.com/Taible-io/playbook_startup).
