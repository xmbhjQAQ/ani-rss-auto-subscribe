# TMDB Alignment Workflow

Use this reference when an ANI-RSS draft contains a season, episode, release date, or total episode count that may not match the numbering used by Emby/TMDB.

## Principle

The helper exposes TMDB responses and ANI-RSS fields. It does not decide whether two titles are the same work, calculate a canonical season automatically, or silently rewrite a subscription. The user must choose the anime, subtitle group, and RSS. The LLM compares the evidence, proposes a patch, audits the preview, and asks for confirmation before adding the subscription.

## Inputs to compare

- The user's title, requested season, and any known air date.
- ANI-RSS `tmdb`, `themoviedbName`, `releaseDate`, `season`, `offset`, and `totalEpisodeNumber`.
- One or more RSS sample titles and the episode number parsed from them.
- The raw result of `tmdb-lookup`, including its direct `seasons` list, and, when available, `tmdb-groups`.
- The complete per-season result of `tmdb-season` for every TV subscription, plus the per-group result of `tmdb-group-details` when an alternate ordering is needed.
- TMDB title, original title, media type, release/air dates, season number, episode number, and episode-group information.
- Existing `list` results, matched by TMDB id/title rather than only by the Mikan URL.

## Decision procedure

1. Confirm that the TMDB result is the exact work: compare title or original title, year/air period, media type, and known aliases. Do not align numbers for a merely similar title.
2. Enforce the user season gate. If the user omitted a season but the only candidate is Season 2 or later, ask explicitly whether that season is intended. Do not treat a unique result as confirmation.
3. Check existing subscriptions by TMDB id/title. Mikan's `exists` flag can be false when the same work has a different page, subgroup, or RSS URL.
4. Read `result.seasons` from the direct series lookup before selecting a season. Exclude Season 0 unless the RSS is explicitly a specials feed. The RSS/Mikan season label is not proof that the same-numbered TMDB season exists; a missing season must not be probed just to see whether it returns 404. Select one or more advertised candidate seasons, then query their complete `tmdb-season` responses. If no unique mapping remains, ask the user.
5. Classify the RSS number as season-relative (`S3E01`, `[01]`) or continuous (`E25`, `-36`, `S01E36`). Do not infer this from the Mikan page's season label. Use at least two samples and the episode values returned by `preview`, because ANI-RSS may parse a different number than the one that is visually most obvious in a title.
6. Determine the selected TMDB episode-group numbering and the target season-local episode for the same release, then calculate the episode offset. Some TMDB groups put all episodes in one season; the `Seasons` group usually uses season-local numbers. Use `tmdb-group-details` as an additional source when an alternate group is needed. A series-level lookup alone is not enough for episode mapping, but it is mandatory for selecting valid season numbers.

   ```text
   target episode = RSS parsed episode + offset
   offset = TMDB episode number - RSS parsed episode number
   ```

   Examples:
   - if S1 and S2 each contain 12 episodes and TMDB combines them in `Season 1`, RSS `S3E01` is TMDB `S1E25`, so use `season: 1, offset: 24`;
   - if TMDB has separate seasons, RSS `S4E11` is TMDB `S4E11`, so use `season: 4, offset: 0`;
   - if the source has only global `E77` and TMDB S1/S2/S3 contain 25/25/16 episodes, map it to `S4E11` with `season: 4, offset: -66`;
   - if the filename contains both a local and global number such as `[11 - 总第77]`, enable `customEpisode` and use a Java regex that captures local `11` (for example `([0-9]{1,3}) - 总第[0-9]{1,3}` with group index `1`); after preview confirms local episodes, use no global offset.
7. Set `season` to the TMDB/Emby metadata season. A custom storage path such as `Season 3` is separate and must not be used to justify `season: 3`.
8. Only when the user explicitly requests a standby RSS, calculate its offset independently from the primary source; the two feeds may use different numbering modes.
9. Keep `releaseDate` tied to the RSS season/cour. Do not replace it with TMDB's top-level series premiere date. Only use a season-specific TMDB air date as replacement evidence.
10. Treat `totalEpisodeNumber` as the source subscription's season total unless its meaning is explicitly confirmed. Do not copy a global TMDB episode-group count into it.
11. Treat episode groups, split cours, specials, and recap episodes as exceptions. Split cours under one TMDB season keep the same `season`, but each RSS can have its own offset and source total. If `tmdb-groups` returns only aggregate counts, obtain the actual episode list before patching; never select the largest group automatically.
12. Audit preview coverage: inspect the first, middle, and last returned source episodes, check every returned item's offset, and report gaps, duplicates, and whether the RSS itself was proven complete. Fewer than three returned items is insufficient to claim whole-season alignment.
13. When title identity, season mapping, numbering mode, or the matching episode is ambiguous, show the evidence and ask the user rather than guessing.

## Tool sequence

```text
tmdb-lookup --tmdb-id 65942      # direct identity + advertised seasons
tmdb-season --tmdb-id 65942 --season-number <season-from-lookup>
tmdb-group-details --group-id <id>
patch --ani-json ani.json --patch-json ani-rss-patch.json
preview --ani-json ani-rss-patched.json --result-file .ani-rss-runs/040-preview.json
add --ani-json ani-rss-patched.json \
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json \
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json \
  --preview-evidence .ani-rss-runs/040-preview.json --confirm-add
```

The `patch` command only validates field names and basic JSON types. It does not validate regex meaning or perform TMDB alignment. Keep the proposed patch reviewable, for example:

```json
{
  "season": 1,
  "offset": 12,
  "totalEpisodeNumber": 24
}
```

Run `preview` after applying the patch and summarize the old values, new values, TMDB evidence, and any unresolved assumptions. Save the preview result and do not change the Ani JSON afterward. For a new subscription, only after the user confirms that summary may the agent call `add --confirm-add` with the matching evidence files. For an existing subscription, retrieve its complete object with `get`, then use `set --confirm-set` with the same evidence requirements; never use `add` as an edit operation.
