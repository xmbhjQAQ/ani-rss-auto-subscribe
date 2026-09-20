# TMDB Alignment Workflow

Use this reference when an ANI-RSS draft contains a season, episode, release date, or total episode count that may not match the numbering used by Emby/TMDB.

## Principle

The helper exposes TMDB responses and ANI-RSS fields. It does not decide whether two titles are the same work, calculate a canonical season automatically, or silently rewrite a subscription. The LLM compares the evidence, proposes a patch, previews it, and asks for confirmation before adding the subscription.

## Inputs to compare

- The user's title, requested season, and any known air date.
- ANI-RSS `tmdb`, `themoviedbName`, `releaseDate`, `season`, `offset`, and `totalEpisodeNumber`.
- One or more RSS sample titles and the episode number parsed from them.
- The raw result of `tmdb-lookup` and, when available, `tmdb-groups`.
- TMDB title, original title, media type, release/air dates, season number, episode number, and episode-group information.
- Existing `list` results, matched by TMDB id/title rather than only by the Mikan URL.

## Decision procedure

1. Confirm that the TMDB result is the exact work: compare title or original title, year/air period, media type, and known aliases. Do not align numbers for a merely similar title.
2. Check existing subscriptions by TMDB id/title. Mikan's `exists` flag can be false when the same work has a different page, subgroup, or RSS URL.
3. Classify the RSS number as season-relative (`S3E01`, `[01]`) or continuous (`E25`, `-36`, `S01E36`). Do not infer this from the Mikan page's season label. Use at least two samples and the episode values returned by `preview`, because ANI-RSS may parse a different number than the one that is visually most obvious in a title.
4. Determine the selected TMDB episode-group numbering and the target season-local episode for the same release, then calculate the episode offset. Some TMDB groups put all episodes in one season; the `Seasons` group usually uses season-local numbers.

   ```text
   target episode = RSS parsed episode + offset
   offset = TMDB episode number - RSS parsed episode number
   ```

   Examples:
   - if S1 and S2 each contain 12 episodes and TMDB combines them in `Season 1`, RSS `S3E01` is TMDB `S1E25`, so use `season: 1, offset: 24`;
   - if TMDB has separate seasons, RSS `S4E11` is TMDB `S4E11`, so use `season: 4, offset: 0`;
   - if the source has only global `E77` and TMDB S1/S2/S3 contain 25/25/16 episodes, map it to `S4E11` with `season: 4, offset: -66`;
   - if the filename contains both a local and global number such as `[11 - 总第77]`, enable `customEpisode` and use a Java regex that captures local `11` (for example `([0-9]{1,3}) - 总第[0-9]{1,3}` with group index `1`); after preview confirms local episodes, use no global offset.
5. Set `season` to the TMDB/Emby metadata season. A custom storage path such as `Season 3` is separate and must not be used to justify `season: 3`.
6. Only when the user explicitly requests a standby RSS, calculate its offset independently from the primary source; the two feeds may use different numbering modes.
7. Keep `releaseDate` tied to the RSS season/cour. Do not replace it with TMDB's top-level series premiere date. Only use a season-specific TMDB air date as replacement evidence.
8. Treat `totalEpisodeNumber` as the source subscription's season total unless its meaning is explicitly confirmed. Do not copy a global TMDB episode-group count into it.
9. Treat episode groups, split cours, specials, and recap episodes as exceptions. Split cours under one TMDB season keep the same `season`, but each RSS can have its own offset and source total. If `tmdb-groups` returns only aggregate counts, obtain the actual episode list before patching; never select the largest group automatically.
10. When title identity, season mapping, numbering mode, or the matching episode is ambiguous, show the evidence and ask the user rather than guessing.

## Tool sequence

```text
tmdb-lookup --title "..."       # raw title search/confirmation data
tmdb-groups --ani-json ani.json # raw episode-group data for the ANI item
patch --ani-json ani.json --patch-json ani-rss-patch.json
preview --ani-json ani-rss-patched.json
```

The `patch` command only validates field names and basic JSON types. It does not validate regex meaning or perform TMDB alignment. Keep the proposed patch reviewable, for example:

```json
{
  "season": 1,
  "offset": 12,
  "totalEpisodeNumber": 24
}
```

Run `preview` after applying the patch and summarize the old values, new values, TMDB evidence, and any unresolved assumptions. For a new subscription, only after the user confirms that summary may the agent call `add --confirm-add`. For an existing subscription, retrieve its complete object with `get`, then use `set --confirm-set`; never use `add` as an edit operation.
