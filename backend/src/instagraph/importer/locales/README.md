# System-message locale files

One JSON file per language (`<code>.json`, e.g. `en.json`). Every file in this folder is
picked up automatically by `instagraph.importer.system_messages` — adding a language
needs no code change. Locales are auto-detected per import (≥ 20 matches or ≥ 0.1 % of
messages, spec §4.3).

```jsonc
{
  "locale": "sk",                 // = file name
  "name": "Slovenčina",
  "verified": false,              // true once checked against a real export
  "self_actors": ["Vy", "Ty"],    // words used as the actor for the account owner ("You")
  "patterns": [
    {
      "id": "rename",             // unique within the file
      "type": "system",           // system | like | call | unsent | reaction
      "event": "rename",          // a store.dataset.SYSTEM_EVENTS value (required for system)
      "payload": "{target}\t{nickname}",   // optional template from named groups
      "call_kind": "audio",       // optional: audio | video | unknown
      "call_missed": false,       // optional
      "check_actor": true,        // optional (default true)
      "match": ["(?P<actor>.+?) pomenoval(?:a|\\(a\\))? skupinu (?P<payload>.+?)"]
    }
  ]
}
```

Matching rules:

* Python `re` syntax; each regex must match the **whole** trimmed message text (an
  optional trailing `.` is added automatically — don't add `^`/`$`).
* Case-insensitive and **accent-insensitive**: write the strings with diacritics; text
  without them (`Paci sa mu sprava`) matches too. Captured groups keep the original
  diacritics.
* Named groups: `actor` (who did it; must be a thread participant — full or first
  name — or one of `self_actors`, unless `check_actor` is false), `target`, `payload`,
  `emoji` (required for `type: reaction`), plus any others you use in `payload`.
  Without a `payload` template the payload is the `payload` group, else `target`.
* Each regex needs at least one literal word of 4+ letters (used as a fast pre-filter);
  the loader raises an error otherwise.
* Patterns are tried in file order; the first match wins. Put specific patterns before
  generic ones.
* Feminine/masculine forms: `poslal(?:a|\\(a\\)|i)?` covers *poslal*, *poslala*,
  *poslal(a)* and *poslali*.

Types: `like` → message type `like`; `call` → a call record (`call_kind`,
`call_missed`); `unsent`; `system` + `event`; `reaction` → an old-style
"Reacted 😂 to your message" text that becomes a reaction on the previous message from
another sender (not a message). Events `attachment`, `story_reply` and `story_mention`
are placeholders: if the message also has an attachment/share, that decides the type.
