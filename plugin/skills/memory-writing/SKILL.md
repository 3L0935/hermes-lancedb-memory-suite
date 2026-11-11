---
name: memory-writing
description: How to write LanceDB memories that come back when needed - when to write, the shape (small, linked), updates, relations, and the contract's traps. Load before the first lancedb_add or lancedb_update of a session.
version: 10.1.0
triggers:
  - lancedb_add
  - lancedb_update
  - write memory
  - memory cleanup
  - outdated memory
---

# Memory writing

LanceDB holds the technical record across sessions: projects, setup and
configuration, decisions and their reasons, corrections, patterns, references,
and lasting preferences. The built-in `MEMORY.md` / `USER.md` hold identity and
working conventions only (see [Built-in memory or LanceDB](#built-in-memory-or-lancedb)).

Never store secrets, credentials, task progress, or unverified speculation.

**Right after loading this skill, load the user's companion skill `memory-writing`**
(the same short name without the `lancedb-suite:` prefix) if it appears in your
skill list. It holds this user's own rules and wins over this skill wherever
they differ. See [The companion skill](#the-companion-skill-the-users-own-rules).

## When to write (without being asked)

- The user corrects you, or you find out you were wrong: `correction`.
- A decision is made and its reason is known: `decision`.
- A path, port, command, config value or fix is found after effort: `tech` / `project`.
- The user states a lasting preference or says "remember": `user_pref`.

Write when the fact is established, not at the end of the session. The test for
every candidate: **what will I know in two weeks that I would not otherwise?** If
the honest answer is "that something happened", it is not a memory.

Not memories: task progress ("phase 3 done", PR numbers, commit SHAs), data that
expires within a week, and **session stubs** - a session title rephrased as a
fact ("the user simplified repo cloning") with no procedure, value or decision in
it. If a fact can be rewritten as "the user did <session name>", drop it.

## Shape: small memories, linked

A memory is read back into a context window: every stored character is paid
again at each recall. A long fact is also a bad search target - its embedding
averages several topics, so it ranks well for none of them, and changing one
detail means rewriting the whole block.

- **One subject per memory.** A subject names one component, failure or
  decision (`Gateway_Restart_Timeout`), not a whole project (`Hermes`).
  Subjects that equal the domain or a generic word come back with
  `overly_broad_subject`.
- **One claim per fact, about 200 characters at most.** Prefer `key=value`
  (`port=7777`, `cause=fd leak in watcher`). Facts over 240 characters come back
  with a `long_fact` warning.
- **1 to 6 facts on one subject.** More means the subject is too broad.
- **Several independent aspects make a hub, not a big memory.** Write a short
  hub first (1-2 facts naming the aspects, usually tier 1 or 2), then one child
  per aspect with `part_of` -> the hub's id. The hub is the table of contents;
  children carry the detail and match precise queries. Do not repeat the detail
  in the hub. Create the hub first so you have its id.
- **Do not compress a paragraph to get under a limit.** A `facts_too_long`
  rejection is not a size problem but a shape problem: five 450-character facts
  keep the same defect as one 2,000-character fact. Split into hub + children.
- **Link instead of repeating.** A memory with 3+ facts and no relation comes
  back with `no_relations`.

## Choose the tool

1. `lancedb_search` first when the subject may already exist, and to get the ids
   of memories to link to.
2. `lancedb_add` for a new subject or a distinct fact.
3. `lancedb_update` with the existing id when the same thing changed (moved URL,
   new version, applied fix) - it keeps the id, links and access history.
4. **A reversed decision is an update, never a new memory.** When a rule you
   stored is overturned (the user changes their mind, or the measurement behind
   it falls), rewrite that memory in place and put the reason in it. Two
   memories saying opposite things both match the same query, and the next
   session applies the stale one with no signal that it is stale.
5. Read the response: `status`, `canonical_content`, `warnings`, `conflicts`.

`write_mode="create"` (default) never destroys anything: an exact retry returns
`status=idempotent` with the existing id, a same-subject write returns
`update_suggested`. `upsert_subject` replaces the single matching subject and
is only for an intended replacement.

## Create example

```python
lancedb_add(
    domain="Hermes",
    subject="Gateway_Restart_Timeout",
    facts=[
        "symptom=gateway restart hangs 90s then SIGKILL",
        "cause=watcher thread holds the inotify fd",
        "fix=close watcher in on_shutdown before joining threads",
        "verified=2026-11-11 restart in 2s",
    ],
    tier=2,
    category="correction",
    relations=[
        {"type": "part_of", "target_id": "<id of Hermes:Gateway>"},
        {"type": "supersedes", "target_id": "<id of the old workaround memory>"},
    ],
)
```

Pass `domain`, `subject` and `tier` as fields. The plugin renders the stored
string itself (prefix, fact separators, tier marker); never write those
yourself, and never copy them back from a `canonical_content` you read.

## Updating: it replaces the whole memory

`lancedb_update` is not a partial patch. It requires the complete form -
`memory_id`, `domain`, `subject`, `facts`, `tier`, `category` - and has no
`content` field.

- **A fact you leave out is deleted, silently.** The response does not warn.
  Before updating, read the current memory (`lancedb_get`, or the
  `replaced_content` of your previous call) and pass every fact you keep.
- `relations`: omit to keep the current ones; passing a list **replaces all**
  outgoing relations, so merge the existing ones with the new one.
- `tags`, `quality`, `type` are optional.
- `tier` is an integer (`2`, not `"2"`), and no fact may end with a tier marker
  (rejected: `nested_tier_marker`).

```python
lancedb_update(
    memory_id="12345678-abc",
    domain="Hermes", subject="Gateway_Restart_Timeout",
    facts=[...every fact you keep, edited...],
    tier=2, category="correction",
)
```

## Tiers and categories

- Tier 1: forgetting it causes an immediate failure (critical correction, rule).
- Tier 2: recurring project or configuration knowledge.
- Tier 3: background context.

Categories: `correction`, `pattern`, `decision`, `user_pref`, `reference`,
`insight`, `project`, `tech`, `fact`, `question`. When several fit, take the
first in that order. Unknown values are rejected, not coerced.

## Relations

Direction is always **this memory -> target**. Each relation takes exactly one of
`target_id` (preferred, from a search) or `target` (a `Domain:Subject` label,
resolved only when it matches one memory). Traversal is bounded to one hop.

| Type | Use it when this memory... |
|---|---|
| `part_of` | is a piece of a bigger component or project (the most useful one) |
| `depends` | stops working if the target breaks |
| `requires` | needs the target installed or configured first |
| `runs_on` | is hosted by the target (machine, container, service) |
| `connects_to` | talks to the target over a network, API or socket. Not a catch-all |
| `uses` | calls or embeds the target as a library or tool |
| `extends` | adds detail to the target without replacing it |
| `supersedes` | replaces the target; the target is now history |
| `invalidates` | proves the target wrong |
| `contradicts` | disagrees with the target and it is not settled yet |

If none fits, use `part_of` toward the project hub, or `extends`.

- **Declare relations when you write.** The store never infers them. Inferring
  links from shared entities afterwards produces wrong edges, and a wrong
  relation is worse than a missing one: it claims a coupling that does not
  exist.
- **Ambiguous target (two or more candidates): declare nothing** and say so.
- **When re-passing stored relations, pass exactly one form per relation.** A
  stored relation often carries both `target_id` and `target`; sending both is
  rejected. Keep `target_id` when it still resolves; if the target memory was
  deleted, send the label instead, or the relation is dropped without warning.
- A healthy graph is not all `part_of`: `part_of` follows from the subject,
  `depends`/`requires`/`runs_on` take judgment and are the valuable ones.

## Conflicts

Two memories with the same `Domain:Subject` and the same `key` in a `key=value`
fact, with different values, conflict. A write that would create such a
conflict is **rejected whole** (`conflicting_claims`), naming both memories and
both values; nothing is written, not even the unrelated facts.

- If one of them is outdated, update that one.
- If both are true (a remote URL and a local path, say), do not overwrite the
  other memory to "win": state yours in prose (`local clone at ~/code/x`) - only
  `key=value` pairs are compared.
- Conflicts that reach the store through imports are listed by
  `lancedb_conflicts`; resolve one with an audit note once settled.

## Contract traps

- Hard limits: 12 facts, 1,000 characters per fact, 2,000 across all facts, 20
  relations, domain 32 and subject 80 characters. The per-fact limit usually
  bites first. Content over 2,000 characters cannot be one memory: it is a hub
  with children.
- A fact holds no newline, no fact separator, no tier marker, and does not
  repeat the `Domain:Subject` prefix. Domain and subject hold no `:`, brackets
  or spaces (`Radio`, not `Radio:Radio`; hyphens and underscores are fine).
- **Documenting the storage format inside a memory trips the parser.** Write
  "tier N" in words, and do not quote the fact separator in examples.
- Values are case-sensitive (`/tmp/Alpha` is not `/tmp/alpha`); keys are
  normalised to lower case. Re-sending a memory that differs only by key case
  returns `idempotent` and writes nothing.
- A memory whose *stored* content is not canonical cannot be updated: the store
  re-parses the existing content first and reports an error about it whatever
  you send. Report it; repairing stored rows is a maintenance operation with a
  backup, not something to work around from a tool call.
- Tags are auto-extracted. Paths, file names and symbols in facts can produce
  odd tags; that is cosmetic, never paraphrase an exact path to avoid it.

## Built-in memory or LanceDB

Test: "is this about who the user is or how they like to work?" -> built-in
`memory` tool. "How does X work / how do I fix X?" -> LanceDB.

- The built-in files are injected in every session and are small (a few
  thousand characters). Keep there: working style, durable conventions,
  meta-rules, and short pointers that prevent a mistake. Move there nothing that
  is a path, version, config or procedure - those go to LanceDB, with at most a
  one-line pointer left behind.
- When moving technical entries out of the built-in files, write them to
  LanceDB first and confirm they are stored. **Never delete built-in entries
  without the user's confirmation**: report them as candidates instead.

## The companion skill: the user's own rules

This skill is the same for everyone. What is specific to one user lives in
**their** skill named `memory-writing` (in their own skills, not the plugin's):
their naming conventions (domains, hub subjects), what they want stored or never
stored, how much you may do without asking, pitfalls met on their setup, dated
decisions about their memory. Facts themselves still go to LanceDB; the
companion holds *rules about handling memory* for this user.

- **Read it** after this skill, before the first write of a session.
- **Record a rule in it** when the user states one ("always link to the project
  hub", "never store anything about X", "ask before deleting"), when they correct
  how you handled memory, or when you hit a pitfall that only exists on their
  setup. One short line each, dated, with the reason when known.
- **Update or drop** a rule that the user reverses or that stopped being true,
  rather than adding a contradicting one.
- **Create it** the first time there is something to record and it does not exist,
  with `skill_manage` (`action: create`, `name: memory-writing`), starting from:

  ```markdown
  ---
  name: memory-writing
  description: "<user>'s own rules for LanceDB memory. Load after lancedb-suite:memory-writing; wins where they differ."
  version: 1.0.0
  ---

  # Memory writing: <user>'s rules

  Complement of `lancedb-suite:memory-writing`. Rules here win where they differ.

  ## Rules
  - (YYYY-MM-DD) <rule> - <why>
  ```

- Edit it with `skill_manage` `patch`. Hermes may stage skill writes for the
  user's approval; say what you added and why either way.
- Keep it short and personal: no secrets, no copy of this skill. A rule that
  would help every user is worth telling the user about, not storing there.

## Before every write

1. Is it durable, and not a secret? (two-week test above)
2. `lancedb_search` for the subject: update instead of duplicating; collect the
   ids to link.
3. Shape it: one subject, short `key=value` facts, hub + children if needed.
4. Write with relations, then read the warnings and fix what they point at.

If the `lancedb_*` tools are missing from your tool list, the provider is
active but its tools are gated by the toolset allowlist (a cron job or profile
without `memory` in `enabled_toolsets`). Say so and ask for `memory` to be added;
do not write through Python behind the contract.
