# Skill authoring

- A skill is `skills/<name>/SKILL.md` with `name` equal to the directory name. Its `description` decides when it fires, so state concrete trigger phrases, in English.
- Skills orchestrate. Anything deterministic (parsing, merging, validating) belongs in a script.
- Keep a `SKILL.md` to what the run needs. Move long reference material to `references/` and load it when needed.
- `Skill()` is called only as a skill's final act. It has no return semantics, so nothing can follow it. Today the only such call is `/cortex:end` into `/cortex:ingest`.
- State the behavior only. Do not narrate history or mention what used to exist.
