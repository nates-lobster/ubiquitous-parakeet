# Prototype scope and safety notes

This is an engineering prototype, not a medical device or a substitute for a caregiver. It does not diagnose, monitor medication adherence, detect emergencies, or verify that a meal or drink was consumed.

- The local gate is deterministic and contains no network or cloud-client code. Route any future model invocation through `invoke_if_eligible`; denied calls do not execute the supplied callback.
- The NousResearch Hermes adapter uses the documented `pre_llm_call` block directive and catches gate errors/timeouts. Hermes isolates plugin-hook failures; a callback that fails before returning its block may be skipped, and provider metadata probes may happen before the hook. This blocks prompt-bearing inference on a denied turn, but is not a strict zero-network firewall. Verify the plugin is loaded and healthy; a hard no-network guarantee needs a separate provider-access wrapper/firewall.
- The MCP tools expose local presence/gate checks and explicit local logging; they do not enforce policy on their own.
- Presence is sampled only for proactive Hermes turns. Direct user-initiated/reactive turns bypass the presence detector; proactive turns require a confirmed reading, and missing/malformed reports block them. This is not continuous monitoring or a substitute for a person.
- `LIFE_STORY.md`, `TODAY_LOG.md`, and SQLite data may be sensitive. They are excluded from Git by default; restrict local OS account access and define a caregiver-approved retention/deletion policy before using real personal information. If a cloud model is enabled, any story/log details placed in its prompt are sent to that model provider; obtain informed caregiver/user consent and review provider retention terms first.
- Templates model validation of feelings without affirming mistaken beliefs as facts. Family/caregiver review and testing with the intended person are essential.
- Medication prompts are intentionally disabled pending a verified caregiver workflow. Never infer or advise a dose from this prototype.
- Any real deployment needs failure handling for sensor disconnection, audio output, network outages, consent, privacy, and urgent situations, plus appropriate clinical, legal, and accessibility review.
