---
name: ani-rss-auto-subscribe
description: Search, customize, preview, and add anime subscriptions to a self-hosted ANI-RSS instance through Mikan. Use when RSS sources, subtitle choices, match/exclude rules, fallback RSS feeds, dates, seasons, episode offsets, or TMDB/Emby numbering need to be checked before subscribing.
---

# ANI-RSS Auto Subscribe

Use the helper script as a thin ANI-RSS API adapter. The script collects data, applies explicit field patches, previews an Ani object, and protects the final add operation. The agent is responsible for understanding anime titles, subtitle evidence, and TMDB season/episode numbering.

## Safety Rules

- Never print, commit, or embed the ANI-RSS API key.
- Do not call `add` until the user has confirmed the final anime, RSS source, field changes, and preview when changes were needed.
- Treat anime identity and subtitle/RSS selection as separate decisions.
- Treat the RSS season label and the TMDB/Emby target season as separate concepts.
- Do not propose, create, or edit `standbyRssList` unless the user explicitly asks for a fallback RSS source.
- Do not alter ANI-RSS global exclusion settings. They are instance-wide configuration, not part of an Ani subscription patch.
- Use Mikan as the default source path. Do not use non-Mikan metadata endpoints unless the user asks or Mikan is insufficient.
- Prefer `scripts/ani_rss.py` over hand-written HTTP calls.

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

### 2. Confirm the source

Ask the user to confirm one exact anime candidate and one exact primary RSS/subtitle option. A source that publishes collections, full-season packs, or multi-episode ranges must be called out before confirmation.

Only when the user explicitly asks for a fallback RSS, use a source for the same episode range. Different cours or parts are separate content, not fallback RSS entries. A fallback may use a different numbering style, so calculate its own offset. ANI-RSS's global standby-RSS setting must also be enabled; adding the list alone does not turn that global switch on.

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

### 4. Check TMDB when scraping or numbering matters

Inspect the generated `tmdb`, `themoviedbName`, `season`, `offset`, and `totalEpisodeNumber` fields. When confirmation is needed, expose the ANI-RSS lookup result:

```bash
python scripts/ani_rss.py tmdb-lookup --tmdb-id "<id>"
```

or:

```bash
python scripts/ani_rss.py tmdb-lookup --title "<title>"
```

Use `tmdb-groups` only when the normal TMDB season order is unclear:

```bash
python scripts/ani_rss.py tmdb-groups --ani-json ani-rss-selected.local.json
```

These commands only expose ANI-RSS data. The agent must inspect the TMDB result/page and decide whether the current Ani values are correct.

After the TMDB lookup, run `list` when duplicate risk exists. Treat `exists` from Mikan as a hint only: an existing subscription may use another Mikan page, subgroup, or RSS URL. Compare the normalized title/original title, TMDB id, release period, and source identity with existing Ani items before deciding that a candidate is new.

## TMDB Season and Episode Alignment

Compare the RSS sample titles with the confirmed TMDB entry:

1. Confirm that the TMDB title, original title, type, and airing period identify the same work.
2. Read at least two RSS samples and classify the source numbering mode. The number that matters is the one ANI-RSS actually parses in `preview.items[].episode`, not merely a number visible in the title:
   - **season-relative**: `S3E01`, `第三季 [01]`, or `S3 - 01`; the number restarts at the start of the season;
   - **continuous**: `E25`, `-36`, or `S01E36` on a page labelled “第三季”; the number already includes earlier seasons;
   - **ambiguous**: ask the user or inspect more samples. Never infer the mode from the Mikan page title alone.
3. Determine the target TMDB season and target episode for that same release. First identify the selected TMDB episode-group numbering: some groups store every episode in one season, while the `Seasons` group stores a season-local episode number. Do not call a global RSS number a TMDB season-local number without converting it.
4. Calculate the ANI-RSS offset from the actual numbers:

   ```text
   target episode = RSS parsed episode + offset
   offset = TMDB target episode - RSS parsed episode
   ```

   Examples:
   - if S1 and S2 each contain 12 episodes and TMDB keeps all episodes in `Season 1`, RSS `S3E01` maps to TMDB `S1E25`, so use `season: 1, offset: 24`;
   - if TMDB has separate seasons and RSS is `S4E11`, use `season: 4, offset: 0`;
   - if the same release is labelled only with global `E77`, and TMDB S1/S2/S3 contain 25/25/16 episodes, map it to `S4E11` with `season: 4, offset: -66`;
   - if a title contains both `[11 - 总第77]`, use a custom Java regex that captures local `11` (for example `([0-9]{1,3}) - 总第[0-9]{1,3}` with group index `1`), then use offset `0` after preview confirms the parsed episodes.
5. Set `season` to the TMDB/Emby target season, not the season label printed by the RSS. Keep a separate custom download path if the user wants files stored under a `Season 3` folder; do not confuse storage paths with the metadata season.
6. Calculate the offset independently for every primary or standby RSS. Two feeds for the same cour may use different numbering modes and therefore different offsets.
7. Keep `releaseDate` tied to the RSS season/cour's first-air date. TMDB's top-level `date` is usually the series premiere, not the current season's date; only use a season-specific TMDB date as replacement evidence.
8. Treat `totalEpisodeNumber` as the source subscription's season total unless the workflow explicitly confirms another meaning. Do not copy a global TMDB episode-group total into it automatically.

Do not infer the TMDB season directly from `S1`, `S2`, or `S3` in a release title. If the title, dates, episode order, or special/OVA status remain ambiguous, ask the user. Explain every proposed change in terms of source episode → target TMDB episode.

TMDB episode groups are an exceptional aid for alternate ordering. `tmdb-groups` may only return group summaries and episode counts; that is not enough to map an individual episode. Obtain the actual season/episode list from TMDB or another explicit evidence source, record which group was used, and ask the user when groups disagree. Do not automatically choose the group with the largest count.

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

Preview the exact final object:

```bash
python scripts/ani_rss.py preview --ani-json ani-rss-final.local.json
```

Check the resulting matched titles, parsed first/latest episodes, season, offset, and (only when requested) standby behavior. The offset must produce the intended TMDB episode for every checked sample. If the result is wrong, patch the JSON and preview again.

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

After the user explicitly confirms the exact existing subscription and preview:

```bash
python scripts/ani_rss.py set \
  --ani-json ani-rss-final.local.json \
  --confirm-set
```

`set` does not move files by default. Use `--move-files` only when the user explicitly asks ANI-RSS to move files after a path-changing edit.

### 8. Add a New Subscription Only After Confirmation

After explicit user confirmation:

```bash
python scripts/ani_rss.py add \
  --ani-json ani-rss-final.local.json \
  --confirm-add
```

If `--confirm-add` is absent, the helper must refuse to call `/api/addAni`.

## Candidate and Edge-Case Handling

- `count = 0`: try alternate title forms, then ask for another title if still unresolved. Do not add.
- `count > 1`: show the candidates and ask the user to choose.
- `exists = true`: warn that the title may already be subscribed. If the user asks to edit it, use `get` → `patch` → `preview` → `set`, never `add`.
- `exists = false`: do not treat that as proof of uniqueness; compare TMDB id/title against `list` results because Mikan can expose the same work under a new page or RSS URL.
- Missing, stale, or empty RSS groups: ask whether to try a non-Mikan fallback.
- Unclear subtitle tags: show raw evidence and ask the user; do not classify with hard-coded language regexes.
- Batch-release evidence: warn that download may succeed while per-episode parsing fails.
- Different cour/part feeds: do not silently model them as standby RSS. Do not add standby RSS at all unless the user requested one.
- Split cours or arcs under one TMDB season: keep the same TMDB `season`, calculate each feed's own offset, and keep each source's episode total separate unless the user intentionally combines the feeds.
- RSS numbering mode or TMDB episode-group mapping ambiguity: ask the user instead of guessing an offset.
- TMDB identity or season mapping ambiguity: ask the user instead of guessing.

## Configuration

The helper reads configuration in this order:

1. command-line flags such as `--base-url` and `--api-key-file`;
2. `ANI_RSS_BASE_URL`, `ANI_RSS_API_KEY`, and `ANI_RSS_API_KEY_FILE`;
3. `ani-rss-config.local.json`;
4. local `ani-rss-key.txt` fallback.

Keep API keys, local config, generated Ani JSON, and TMDB lookup output out of commits.

## References

Read [references/ani-rss-api.md](references/ani-rss-api.md) when endpoint payloads or Ani fields are needed. Read [references/tmdb-alignment.md](references/tmdb-alignment.md) when the source season label and TMDB season/episode numbering differ.
