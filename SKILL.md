---
name: ani-rss-auto-subscribe
description: Search, customize, preview, and add anime subscriptions to a self-hosted ANI-RSS instance through Mikan. Use when RSS sources, subtitle choices, match/exclude rules, fallback RSS feeds, dates, seasons, episode offsets, or TMDB/Emby numbering need to be checked before subscribing.
---

# ANI-RSS Auto Subscribe

Use the helper script as a thin ANI-RSS API adapter. The script collects data, applies explicit field patches, previews an Ani object, and protects the final add operation. The agent is responsible for organizing evidence and making recommendations; the user owns the anime and subtitle-group/RSS selection.

## Safety Rules

- Never print, commit, or embed the ANI-RSS API key.
- Do not call `add` until the user has confirmed the final anime, RSS source, field changes, and the saved final preview.
- Treat anime identity and subtitle/RSS selection as separate decisions.
- The user must choose the subtitle group and primary RSS. The agent may rank candidates and recommend one, but must stop and wait for the user's explicit choice before building an Ani draft, applying source-specific rules, or adding a subscription. A recommendation, a single returned candidate, `exists = false`, or a request to “use the best one” is not user authorization.
- Treat the RSS season label and the TMDB/Emby target season as separate concepts.
- Do not propose, create, or edit `standbyRssList` unless the user explicitly asks for a fallback RSS source.
- Do not alter ANI-RSS global exclusion settings. They are instance-wide configuration, not part of an Ani subscription patch.
- Use Mikan as the default source path for RSS discovery. For TV, first use the direct TMDB series endpoint and read its `seasons` list; only then query one or more real seasons for episode evidence. Do not replace this with a title-only Mikan lookup or by blindly querying the RSS season label.
- Prefer `scripts/ani_rss.py` over hand-written HTTP calls.

## Chat workflow gates

This Skill is intended to be driven from a chat session. Keep the interaction to three phases and stop at the two user-decision points:

```text
1. SEARCH_AND_WAIT_FOR_SOURCE
   plan/search → show candidates → stop and wait for the user's exact RSS/group choice
2. PREPARE_AND_WAIT_FOR_CONFIRMATION
   build → mandatory TMDB identity + season evidence → patch → preview → show final object → stop
3. COMMIT_AND_VERIFY
   only after the user explicitly confirms the shown object: preflight list → add/set → list/get verification
```

The LLM may recommend and calculate semantic mappings, but it must not silently cross either chat stop. A command returning `ok: true` is evidence, not user authorization. Never call `add` or `set` in the same response that presents candidates or the final preview.

For a new TV subscription, direct `tmdb-lookup --tmdb-id` is mandatory before any `tmdb-season` call. Read `result.seasons` from that response and treat it as the authoritative list of candidate TMDB seasons. Query `tmdb-season` only for a season that exists in that list; there is no “when needed” exception for the final episode evidence. Use `tmdb-group-details` only when the normal season list and per-season episodes cannot explain the RSS numbering.

## Normal Workflow

### 1. Search and collect evidence

Run the read-only plan command:

```bash
python scripts/ani_rss.py plan "<anime title>"
```

Inspect and present:

- anime title, Mikan URL, Bangumi URL, score, and existing-subscription status;
- subtitle group and RSS candidates;
- raw tags, sample titles, and sample subgroup values;
- `language_evidence`, `batch_evidence`, and `download_risk`;
- recent sample item titles and their detected episode numbers when available.

Do not silently choose between multiple plausible anime candidates or multiple plausible RSS groups.

If `count = 0`, do not repeatedly invent random spellings. Preserve the original input and use the title-normalization procedure below to build a small, evidence-backed alias set, then retry Mikan with those aliases. If all retries fail, ask the user for another title or a direct Mikan/TMDB reference.

### 1a. Normalize a title only after a failed Mikan search

“Title normalization” means converting an informal, abbreviated, translated, or differently numbered user title into a likely canonical title; it is not permission to choose a different work. Only enter this step after the original Mikan search returns no candidates (unless the user asks for alias research).

Use web search to confirm aliases and preserve the evidence:

1. Remove request filler such as “订阅”“追番”“最新”，but keep title-intrinsic numbers.
2. Separate an explicit season token from the base title. Recognize `第二季`/`第2季`/`Season 2`/`S2`/`2nd Season` and Chinese, Arabic, full-width, or Roman numerals. Do not treat `Part 2`、`第二部`、`二期` or `cour 2` as the same as TMDB season 2 without evidence.
3. Search for the official Japanese title, simplified/traditional Chinese title, English title, romaji, and common abbreviation. Prefer results that identify the year, media type, and season.
4. Retry only a bounded set of unique forms: canonical title, each confirmed alias, and the confirmed season form. Do not change a number that belongs to the title itself, such as `100个女朋友`.
5. Deduplicate by Mikan URL/Bangumi ID, show the original-to-normalized mapping and sources, and ask the user when more than one work remains plausible.

The agent must not convert a search-engine hit directly into an Ani object. It must return to the candidate and user-selection gates.

### 2. Let the user choose the source

Present the anime candidates and every plausible subtitle-group/RSS candidate with enough evidence to distinguish them: exact group label, RSS URL, tags, sample titles, language/spec clues, and batch-release risk. The agent may give a clearly labeled recommendation and explain its tradeoffs, but must not select on the user's behalf.

Wait for the user to name or explicitly confirm one exact anime candidate and one exact primary RSS/subtitle option. Do not call `build-from-rss`, create a source-specific regex, add a fallback, or call `add` before this choice. If the user says only “use the best one” or gives no group/RSS identity, ask a follow-up that makes the source choice explicit.

A source that publishes collections, full-season packs, or multi-episode ranges must be called out before the user chooses it. A source-specific recommendation is not a confirmation.

Only when the user explicitly asks for a fallback RSS, use a source for the same episode range. Different cours or parts are separate content, not fallback RSS entries. A fallback may use a different numbering style, so calculate its own offset. ANI-RSS's global standby-RSS setting must also be enabled; adding the list alone does not turn that global switch on.

### 2a. Season identity gate

Season identity is separate from TMDB/Emby episode alignment. Before asking for a subtitle group, compare the user's season wording with the Mikan candidate:

- If the user gave no season but the only candidate is clearly `Season 2` or later, ask whether that exact season is intended. Do not treat uniqueness as confirmation.
- If the user gave season `N` and the candidate is season `M`, stop when `N != M`; do not “correct” the user silently.
- If the candidate says `Part`, `cour`, `第二部`, or `二期`, keep that evidence separate until the user confirms what it means. It is not automatically TMDB season 2.
- If a title has no explicit season but the year, sequel status, or Mikan page makes the season uncertain, present the ambiguity and ask.

Only after the anime identity and season gate pass may the workflow ask the user to select the exact subtitle group/RSS.

### 3. Build an Ani draft

After source confirmation, create a draft:

```bash
python scripts/ani_rss.py build-from-rss \
  --rss "<rss-url>" \
  --type mikan \
  --bgm-url "<bgm-url>" \
  --subgroup "<group-label>" > ani-rss-selected.local.json
```

`build-from-rss` only converts RSS to an Ani object. It does not decide TMDB season mapping.

### 4. Mandatory TMDB evidence

Inspect the generated `tmdb`, `themoviedbName`, `season`, `offset`, and `totalEpisodeNumber` fields. For every new TV subscription, first run the direct TMDB series lookup and save its exact JSON result with `--result-file`:

```bash
python scripts/ani_rss.py tmdb-lookup --tmdb-id "<id>" \
  --result-file .ani-rss-runs/020-tmdb-lookup.json
```

`tmdb-lookup --title` may be used as an initial title search, but it never replaces the direct TMDB id lookup below.

Use `tmdb-groups` only when the normal TMDB season order is unclear:

```bash
python scripts/ani_rss.py tmdb-groups --ani-json ani-rss-selected.local.json
```

`tmdb-lookup --tmdb-id` reads the direct TMDB series record, including its `seasons` list, and needs `TMDB_API_TOKEN` (or `TMDB_API_KEY`). The ANI-RSS title lookup is still available with `--title`; do not send a bare `tmdbId` to ANI-RSS's title endpoint.

Read `result.seasons` from the saved lookup before selecting a TMDB season. The RSS/Mikan season label is only a source label and must not be copied into `--season-number`. After selecting a real season from that list, use the direct TMDB season evidence command:

```bash
python scripts/ani_rss.py tmdb-season \
  --tmdb-id "<id>" \
  --season-number <resolved-tmdb-season> \
  --language en-US \
  --result-file .ani-rss-runs/021-tmdb-season.json
```

If the RSS-labelled season is absent from `result.seasons`, do not probe it and do not interpret a 404 as “the work does not exist”. Inspect the advertised seasons and episode counts, query the plausible existing season(s), and align the RSS range against their concrete episode lists. If more than one mapping remains plausible, ask the user. A failed/404 season response is not valid season evidence and cannot be used for a write.

If the standard season order does not explain the RSS, also save the episode-group result:

```bash
python scripts/ani_rss.py tmdb-group-details \
  --group-id "<episode-group-id>" \
  --result-file .ani-rss-runs/022-tmdb-group.json
```

These commands return raw TMDB series, season, or episode-group data. They do not select a season, calculate an offset, or patch an Ani. Configure the TMDB token through an environment variable or ignored local config; never put it in committed examples. The LLM must inspect the title, year/type, season number, and concrete episode list before proposing a patch.

Treat `exists` from Mikan as a hint only: an existing subscription may use another Mikan page, subgroup, or RSS URL. Compare the normalized title/original title, TMDB id, release period, and source identity with existing Ani items when explaining duplicate risk. `add` performs the final duplicate preflight automatically.

## TMDB Season and Episode Alignment

Compare the RSS sample titles with the confirmed TMDB entry:

1. Confirm that the TMDB title, original title, type, and airing period identify the same work.
2. Read `result.seasons` from the direct series lookup before selecting a season. Exclude Season 0 unless the RSS is explicitly a specials feed. The RSS season label is only a source label; it is not proof that the same-numbered TMDB season exists. Query `tmdb-season` only for the selected existing TMDB season number.
3. Read at least two RSS samples and classify the source numbering mode. The number that matters is the one ANI-RSS actually parses in `preview.items[].episode`, not merely a number visible in the title:
   - **season-relative**: `S3E01`, `第三季 [01]`, or `S3 - 01`; the number restarts at the start of the season;
   - **continuous**: `E25`, `-36`, or `S01E36` on a page labelled “第三季”; the number already includes earlier seasons;
   - **ambiguous**: ask the user or inspect more samples. Never infer the mode from the Mikan page title alone.
4. Determine the target TMDB season and target episode for that same release. First identify the selected TMDB episode-group numbering: some groups store every episode in one season, while the `Seasons` group stores a season-local episode number. Do not call a global RSS number a TMDB season-local number without converting it.
5. Calculate the ANI-RSS offset from the actual numbers:

   ```text
   target episode = RSS parsed episode + offset
   offset = TMDB target episode - RSS parsed episode
   ```

   Examples:
   - if S1 and S2 each contain 12 episodes and TMDB keeps all episodes in `Season 1`, RSS `S3E01` maps to TMDB `S1E25`, so use `season: 1, offset: 24`;
   - if TMDB has separate seasons and RSS is `S4E11`, use `season: 4, offset: 0`;
   - if the same release is labelled only with global `E77`, and TMDB S1/S2/S3 contain 25/25/16 episodes, map it to `S4E11` with `season: 4, offset: -66`;
   - if a title contains both `[11 - 总第77]`, use a custom Java regex that captures local `11` (for example `([0-9]{1,3}) - 总第[0-9]{1,3}` with group index `1`), then use offset `0` after preview confirms the parsed episodes.
6. Set `season` to the TMDB/Emby target season, not the season label printed by the RSS. Keep a separate custom download path if the user wants files stored under a `Season 3` folder; do not confuse storage paths with the metadata season.
7. Calculate the offset independently for every primary or standby RSS. Two feeds for the same cour may use different numbering modes and therefore different offsets.
8. Keep `releaseDate` tied to the RSS season/cour's first-air date. TMDB's top-level `date` is usually the series premiere, not the current season's date; only use a season-specific TMDB date as replacement evidence.
9. Treat `totalEpisodeNumber` as the source subscription's season total unless the workflow explicitly confirms another meaning. Do not copy a global TMDB episode-group total into it automatically.

Do not infer the TMDB season directly from `S1`, `S2`, or `S3` in a release title. If the title, dates, episode order, or special/OVA status remain ambiguous, ask the user. Explain every proposed change in terms of source episode → target TMDB episode.

TMDB episode groups are an exceptional aid for alternate ordering. `tmdb-groups` may only return group summaries and episode counts; that is not enough to map an individual episode. The direct series lookup's `seasons` list selects candidate season numbers; `tmdb-season` then obtains the concrete standard-season episode list. Use `tmdb-group-details` as an additional query when an alternate group is needed, record which group was used, and ask the user when groups disagree. Do not automatically choose the group with the largest count.

## 5. Apply Explicit Field Changes

Create a local patch file based on [ani-rss-patch.example.json](ani-rss-patch.example.json), then apply it:

```bash
python scripts/ani_rss.py patch \
  --ani-json ani-rss-selected.local.json \
  --patch-json ani-rss-patch.local.json \
  > ani-rss-final.local.json
```

Supported patch fields:

- `match` / `exclude`: replacement arrays of download-filter and exclusion regex strings;
- `appendMatch` / `appendExclude`: local patch operators that append rules after the existing arrays, then disappear from the resulting Ani object;
- `standbyRssList`: fallback-source objects with `label`, non-empty `url`, and integer `offset`; only add or change this field on the user's explicit request;
- `releaseDate`: `YYYY-MM-DD`;
- `season`, `offset`, `totalEpisodeNumber`;
- `customEpisode`, `customEpisodeStr`, `customEpisodeGroupIndex`;
- a confirmed full `tmdb` object and matching `themoviedbName`, supplied together.

`match`/`exclude` are download filters. `customEpisodeStr` is a separate Java regex used to extract the episode number. Do not confuse these fields. Each explicitly requested standby RSS has its own offset. Confirm the global standby-RSS setting is enabled before relying on the list.

For a rule that applies to every subtitle group, use the raw Java regex, such as `简繁日|简日`. To restrict a rule to one subtitle group, use ANI-RSS's wrapper syntax `{{字幕组名}}:正则`, for example `{{绿茶字幕组}}:简繁日|简日`. The UI's empty “字幕组” input corresponds to the raw, unwrapped regex; never emit `{{}}:...`. The group name must be taken from the selected source's actual label/evidence, not guessed from the show title.

When adding a rule, inspect the draft's current `match` and `exclude` arrays and normally use `appendMatch` or `appendExclude`: this preserves the ANI-RSS defaults already present (commonly `720[Pp]`, `\d-\d`, `合集`, `特别篇`) and appends the new rule after them, matching the UI. Use replacement `match` or `exclude` only when the user explicitly requests replacing that whole rule list. The helper does not expose or modify global exclusions; change those only through ANI-RSS's global settings when the user explicitly requests it. Read [references/regex-rules.md](references/regex-rules.md) whenever match/exclude rules or a subtitle-group-specific fallback are involved.

The helper rejects unknown patch fields and invalid basic types. It does not attempt to judge whether a regex expresses the user's intent.

## 6. Preview Before Adding

Preview the exact final object and save the evidence:

```bash
python scripts/ani_rss.py preview \
  --ani-json ani-rss-final.local.json \
  --result-file .ani-rss-runs/040-preview.json
```

Check the returned `coverage` object as well as the raw preview. It reports the returned count, parsed episode min/max, unparsed items, duplicates, gaps, and first/middle/last parsed anchors. The preview also contains `input_sha256`; do not modify the final Ani JSON after this step. `full_feed_verified` remains false unless the source range is separately proven, but an ongoing RSS may be added when the observed parsed samples are sufficient and the limitation is stated.

For a complete or expected season range, inspect the first, middle, and last available parsed source episodes. Check every parsed returned item for a constant `target TMDB episode - parsed RSS episode` offset. If fewer than three items have a parsed episode number, the result is `insufficient-samples`; do not claim that the whole season is aligned. If the feed is rolling and does not contain the first or last episode, state that the preview proves only the observed range.

The offset must produce the intended TMDB episode for every checked sample. If the result is wrong or the coverage is insufficient, patch the JSON and preview again; do not add or set yet.

Before adding, summarize:

- selected anime and primary RSS;
- subtitle/spec interpretation and evidence;
- match/exclude rules;
- standby RSS entries and offsets;
- TMDB title/id;
- source episode → TMDB episode mapping;
- final season, offset, release date, and total episodes;
- preview result.

### 7. Persist an Existing Subscription Only After Confirmation

For an existing subscription, first retrieve its full object, patch it, and preview it. Do not use `add` for an edit:

```bash
python scripts/ani_rss.py get --id "<existing-ani-id>" > ani-rss-existing.local.json
python scripts/ani_rss.py patch \
  --ani-json ani-rss-existing.local.json \
  --patch-json ani-rss-patch.local.json \
  > ani-rss-final.local.json
python scripts/ani_rss.py preview --ani-json ani-rss-final.local.json
```

After the user explicitly confirms the exact existing subscription and preview (the source choice must already have been made by the user), write using the saved evidence files:

```bash
python scripts/ani_rss.py set \
  --ani-json ani-rss-final.local.json \
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json \
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json \
  --preview-evidence .ani-rss-runs/040-preview.json \
  --confirm-set
```

`set` does not move files by default. Use `--move-files` only when the user explicitly asks ANI-RSS to move files after a path-changing edit. The helper verifies the persisted object with `listAni` before reporting success.

### 8. Add a New Subscription Only After Confirmation

After explicit user confirmation of the anime, subtitle group/RSS, proposed field changes, and preview:

```bash
python scripts/ani_rss.py add \
  --ani-json ani-rss-final.local.json \
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json \
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json \
  --preview-evidence .ani-rss-runs/040-preview.json \
  --confirm-add
```

If any evidence argument or `--confirm-add` is absent, the helper must refuse to call `/api/addAni`. `add` also performs a duplicate preflight before writing and then verifies the complete set of changed fields with `listAni`.

`add` automatically performs a read-only `listAni` verification after the add response. Treat the result as successful only when exactly one persisted subscription matches the submitted identity and key fields. If the terminal output is blank or verification fails, do not retry `add`; run `get/list` first because the write may already have succeeded.

## Candidate and Edge-Case Handling

- `count = 0`: try alternate title forms, then ask for another title if still unresolved. Do not add.
- `count > 1`: show the candidates and ask the user to choose; an agent recommendation remains only a recommendation.
- `count = 1`: still show the exact subtitle group/RSS and wait for the user's explicit choice; do not infer consent from uniqueness.
- `exists = true`: warn that the title may already be subscribed. If the user asks to edit it, use `get` → `patch` → `preview` → `set`, never `add`.
- `exists = false`: do not treat that as proof of uniqueness; compare TMDB id/title against `list` results because Mikan can expose the same work under a new page or RSS URL.
- Missing, stale, or empty RSS groups: explain the problem and ask whether to try a non-Mikan fallback; do not silently switch sources.
- Unclear subtitle tags: show raw evidence and ask the user; do not classify with hard-coded language regexes.
- Batch-release evidence: warn that download may succeed while per-episode parsing fails.
- Different cour/part feeds: do not silently model them as standby RSS. Do not add standby RSS at all unless the user requested one.
- Split cours or arcs under one TMDB season: keep the same TMDB `season`, calculate each feed's own offset, and keep each source's episode total separate unless the user intentionally combines the feeds.
- RSS numbering mode or TMDB episode-group mapping ambiguity: ask the user instead of guessing an offset.
- TMDB identity or season mapping ambiguity: ask the user instead of guessing.
- User selection gate not satisfied: keep the workflow paused even if the candidate list has one item or the agent has a strong recommendation.
- Add/set output is missing or cannot be parsed: treat the external write as unknown until `get/list` verifies it; never blindly retry a mutating command.

## Configuration

The helper reads configuration in this order:

1. command-line flags such as `--base-url` and `--api-key-file`;
2. `ANI_RSS_BASE_URL`, `ANI_RSS_API_KEY`, and `ANI_RSS_API_KEY_FILE`;
3. `ani-rss-config.local.json`;
4. local `ani-rss-key.txt` fallback.

Keep API keys, local config, generated Ani JSON, and TMDB lookup output out of commits.

For terminal/output recovery, pass `--result-file .ani-rss-runs/<run-id>/<step>.json`. The helper writes the exact JSON payload atomically and mirrors it to stdout. When elevated execution returns an empty terminal, inspect the result file, stderr, and process exit code before deciding what happened. Use ASCII command-step filenames rather than raw anime titles so Windows paths remain stable.

## References

Read [references/ani-rss-api.md](references/ani-rss-api.md) when endpoint payloads or Ani fields are needed. Read [references/tmdb-alignment.md](references/tmdb-alignment.md) when the source season label and TMDB season/episode numbering differ.
