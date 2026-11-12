# Prototype scope and safety notes

This is an engineering prototype, not a medical device or a substitute for a caregiver. It does not diagnose, monitor medication adherence, detect emergencies, or verify that a meal or drink was consumed.

- The local gate is deterministic and contains no network or cloud-client code. Route any future model invocation through `invoke_if_eligible`; denied calls do not execute the supplied callback.
- The presence and cooldown helpers are not by themselves a complete system security boundary. An agent runtime can make requests outside this package unless its own pre-inference path is integrated and tested.
- MCP tools expose local presence/gate checks and explicit local logging. They do not force the host agent to obey a denied gate.
- `LIFE_STORY.md`, `TODAY_LOG.md`, and SQLite data may be sensitive. They are excluded from Git by default; restrict local OS account access and define a caregiver-approved retention/deletion policy before using real personal information.
- Templates model validation of feelings without affirming mistaken beliefs as facts. Family/caregiver review and testing with the intended person are essential.
- Medication prompts are intentionally disabled pending a verified caregiver workflow. Never infer or advise a dose from this prototype.
- Any real deployment needs failure handling for sensor disconnection, audio output, network outages, consent, privacy, and urgent situations, plus appropriate clinical, legal, and accessibility review.
