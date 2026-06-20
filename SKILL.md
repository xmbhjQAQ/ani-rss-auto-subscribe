---
name: ani-rss-auto-subscribe
description: Search and add anime subscriptions to a self-hosted ANI-RSS instance through Mikan. Use when the user asks an agent to subscribe, auto-download, add, search, or fuzzy-match an anime in ANI-RSS, especially when subtitle group, RSS source, simplified/traditional Chinese, embedded subtitles, or other release-language choices must be confirmed before subscribing.
---

# ANI-RSS Auto Subscribe

Use this skill to add an anime subscription to a self-hosted ANI-RSS instance.

## Required Safety Rules

- Never print, commit, or embed the ANI-RSS API key.
- Do not call `add` until the user explicitly confirms both the anime and the subtitle/source option.
- Treat anime title matching and subtitle/source matching as separate decisions.
- Use Mikan as the default source path. Do not call BGM endpoints unless the user explicitly asks for a non-Mikan fallback.
- Prefer `scripts/ani_rss.py` over hand-written HTTP calls.

## Normal Workflow

1. Run a read-only plan:

   ```bash
   python scripts/ani_rss.py plan "<anime title>"
   ```

2. Present the candidates to the user:
   - anime title, Mikan URL, existing-subscription status, score
   - subtitle group/source label
   - raw language/spec evidence from group tags and sample titles
   - batch-release evidence from sample titles
   - sample recent item titles

3. If a group reports `download_risk: "batch-release-detected"`, warn the user that the source appears to publish collections or season packs and ANI-RSS may not parse the downloaded file into episodes reliably.

4. Ask the user to confirm one exact anime candidate and one exact subtitle/RSS option.

5. Convert the selected RSS to an ANI-RSS subscription object:

   ```bash
   python scripts/ani_rss.py build-from-rss \
     --rss "<rss-url>" \
     --type mikan \
     --bgm-url "<bgm-url>" \
     --subgroup "<group-label>" > ani-rss-selected.local.json
   ```

6. After the user confirms, add the subscription:

   ```bash
   python scripts/ani_rss.py add --ani-json ani-rss-selected.local.json --confirm-add
   ```

7. Report the result without exposing credentials.

## Candidate Handling

- If `plan` returns `count: 0`, tell the user no Mikan candidates were found. Ask for another title, alternate spelling, Japanese/English title, or season information. Do not subscribe anything.
- If `plan` returns more than one anime candidate, summarize the candidates and ask the user which exact anime to use. Do not choose silently.
- If there is one anime candidate but multiple subtitle/RSS groups, summarize the groups and ask the user which exact subtitle/source option to use.
- If any candidate has `exists: true`, tell the user it may already be subscribed and ask whether they want to inspect or continue.
- If the selected anime has no usable RSS group, explain that Mikan did not provide a source candidate and ask whether to try a non-Mikan fallback.

## Edge Case Workflow

Use this escalation order for uncertain cases:

1. Inspect the `plan` JSON evidence first.
2. If the evidence is insufficient, search the web for title aliases, official names, season numbering, airing year, and whether the item is TV/OVA/movie.
3. Re-run `plan` with better search terms when useful.
4. Ask the user to confirm before converting or adding anything.

Ask the user instead of deciding when:

- The title may refer to multiple seasons, remakes, movies, OVAs, specials, or spin-offs.
- Mikan returns multiple plausible candidates.
- The requested title does not exist in Mikan and web search suggests several aliases.
- The requested title is a typo but the correction is not obvious.
- The candidate is already subscribed.
- The subtitle group/language/spec cannot be confidently explained from `language_evidence`.
- `batch_evidence` suggests the group often publishes collections, full seasons, or other multi-episode packs.
- The RSS source is missing, stale, or has no recent sample titles.
- The user preference is needed, such as simplified/traditional Chinese, embedded subtitles, dual subtitles, file format, resolution, source platform, or subtitle group.

Use web search when:

- `plan` returns `count: 0`.
- Search terms are likely translated, abbreviated, typoed, or season-numbered differently.
- There are multiple plausible Mikan candidates and metadata can help explain the difference.
- The user asks for a seasonal title like "season 5" but Mikan/source titles may use episode continuation numbers or alternate season names.
- The source evidence references unfamiliar abbreviations and explaining them would help the user choose.

When searching the web, prefer official anime sites, Bangumi, AniList/MyAnimeList, Mikan pages, or other authoritative catalog pages. Do not treat web search as confirmation to subscribe; it only improves the options shown to the user.

## Configuration

The helper script reads configuration in this order:

1. Command-line flags such as `--base-url` and `--api-key-file`.
2. Environment variables: `ANI_RSS_BASE_URL`, `ANI_RSS_API_KEY`, `ANI_RSS_API_KEY_FILE`.
3. Local JSON config: `ani-rss-config.local.json`.
4. Local fallback key file: `ani-rss-key.txt`.

For normal use, copy `ani-rss-config.example.json` to `ani-rss-config.local.json` when installing/importing the skill, then fill in the local values. Keep local config and key files ignored.

## Language And Subtitle Handling

ANI-RSS/Mikan groups do not expose a normalized language field, and many subtitle groups do not follow consistent naming conventions. The helper script intentionally does not classify subtitle language with regex rules.

Use the `language_evidence` field and reason from:

- group label
- group tags
- sample item titles
- item subgroup fields

Explain your interpretation as uncertain unless the source clearly states it, then ask the user to confirm the exact subtitle group/RSS option.

## Batch Release Handling

The helper script also emits `batch_evidence` and `download_risk` based on sample item titles.

Use these fields to catch sources that look like:

- collections / `合集`
- full seasons / `全集` / `全季`
- explicit multi-episode packs such as `[01-12]`, `E01-12`, or `S01E01-E12`

Treat this as a warning signal, not a hard rule. If `download_risk` is `batch-release-detected`, tell the user the source may download successfully but still fail ANI-RSS episode parsing afterward.

## References

Read `references/ani-rss-api.md` when API details, payload fields, or endpoint flow are needed.
