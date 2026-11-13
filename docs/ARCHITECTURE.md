# Architecture

An untrusted agent process can reach the world only through a separate harness. The harness issues a short-lived token per tool, gates every call, and queues every outbound message. The stop controller in that harness latches a persisted safe state and then runs the six HKMA steps in order, timing each one. Every component writes to a hash-chained log. The evidence card is built from that log.

```mermaid
flowchart TB
  OP["Operator<br/>ksdrill stop / drill"]:::human
  subgraph AGENT["Agent process (untrusted)"]
    LOOP["Agent loop<br/>planner + executor"]
    LLM["LLM adapter<br/>MockLLM (default) | Ollama (opt.)"]
    LOOP --> LLM
  end
  subgraph HARNESS["Harness / control plane (separate process, 127.0.0.1)"]
    SC["Stop controller<br/>safe-state latch + step timer"]
    TB["Token broker<br/>per-tool scoped, short TTL, epoch"]
    GW["Tool gateway<br/>default-deny, verifies token + state"]
    OQ["Outbound queue<br/>held / dispatching / paused"]
    FB["Fallback mode<br/>manual work queue, no tokens"]
    LOG["Audit log<br/>append-only, SHA-256 hash chain"]
  end
  subgraph TOOLS["Fake tools (local)"]
    EM["email.send -> outbox sink"]
    PAY["payments.initiate -> fake ledger"]
    FS["files.read/write -> sandbox dir"]
  end
  DR["Drill runner<br/>start mid-task, fire stop, bypass attempts"]
  EC["Evidence-card generator<br/>HTML + PDF, clause-mapped"]
  OP -->|admin key| SC
  DR -->|admin key| SC
  LOOP -->|token request| TB
  LOOP -->|tool call + token| GW
  GW --> FS
  GW --> PAY
  GW -->|enqueue| OQ
  OQ --> EM
  SC -->|1 halt| LOOP
  SC -->|2 revoke| TB
  SC -->|3 disconnect| GW
  SC -->|4 pause| OQ
  SC -->|5 activate| FB
  SC -->|6 seal + export| LOG
  TB -.-> LOG
  GW -.-> LOG
  OQ -.-> LOG
  SC -.-> LOG
  LOG --> EC
  DR --> EC
  classDef human fill:#fde68a,stroke:#92400e
```

## Processes

| Process | Started by | Holds | Can reach |
| --- | --- | --- | --- |
| Harness (`ksdrill harness up`) | Operator or the drill runner | Signing key, admin key, state, the only log writer | Tool adapters, in-process |
| Agent (`ksdrill agent start`) | Launcher, own process group | Agent id, bootstrap key, short-lived tokens in memory | `http://127.0.0.1:8701` only |
| Operator CLI / drill runner | The person running the demo | Admin key in `runs/<id>/secrets/admin.key` (mode 0600) | `http://127.0.0.1:8702` |

The launcher builds the agent environment from an allow-list. Names starting with `KSD_ADMIN` are refused. The agent package does not import the harness.

## Gateway order

Every tool call is decided in this order, and every decision is logged:

1. Latch set → `409 HALTED`
2. Unknown tool or action → `404 DENY_UNKNOWN`
3. Token → `401 TOKEN_INVALID`, `TOKEN_REVOKED`, `TOKEN_EXPIRED`, or `403 SCOPE_MISMATCH`
4. Policy (allow-list, payment cap) → `403 POLICY_DENY`
5. Adapter disconnected → `503 TOOLS_DISCONNECTED`
6. Dispatch. Outbound tools return `202 QUEUED`.

Arguments are stored as a SHA-256 digest. The token string is not stored in the log; the `jti` is.

## What is deliberately small

One log writer, JSONL, no search, no SIEM export, no policy language, no admin UI. A production gateway or ledger would be a different product. This repository is the drill.
