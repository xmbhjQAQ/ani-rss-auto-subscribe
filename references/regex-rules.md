# Subtitle-Group Regex Rules

Use this reference when modifying an Ani subscription's `match` or `exclude` rules, especially when a user asks to add a standby RSS source from another subtitle group.

## Rule scope

- A raw Java regex applies to every subtitle group. Example: `简繁日|简日`.
- A subtitle-group-specific rule has the form `{{字幕组名}}:正则`. Example: `{{绿茶字幕组}}:简繁日|简日`.
- The UI's empty “字幕组” field produces the raw form. Do not encode it as `{{}}:正则`.

Use the exact group label from the selected Mikan/RSS evidence. Do not infer a group name from a release title when the source metadata already provides one.

`match` selects eligible releases; `exclude` removes releases that would otherwise match. They are not episode-extraction expressions: use `customEpisodeStr` only for parsing an episode number.

## Adding without discarding defaults

An Ani object already has its own ordered `match` and `exclude` arrays. ANI-RSS commonly initializes exclusions such as:

```text
720[Pp]
\d-\d
合集
特别篇
```

When the user asks to add a rule, first obtain the current Ani object (the output of `build-from-rss` for a new draft, or `get` for an existing subscription). Use a patch such as:

```json
{
  "appendMatch": ["{{绿茶字幕组}}:简繁日|简日"],
  "appendExclude": ["{{绿茶字幕组}}:预告"]
}
```

`appendMatch` and `appendExclude` are local helper operators. They append in order after the current lists, then are removed; ANI-RSS receives ordinary `match` / `exclude` arrays. Use `match` or `exclude` directly only when the user explicitly wants to replace the whole corresponding list.

## Standby RSS boundary

Do not introduce a standby RSS merely because group-specific regex support exists. Only when the user explicitly asks for a fallback source:

1. Confirm the fallback covers the same episode range as the primary RSS.
2. Keep the source-specific regex rules so releases from one group do not accidentally pass rules intended for another group.
3. Calculate the fallback's own episode offset from preview evidence; it may use continuous numbering while the primary source uses season-local numbering.
4. Add `standbyRssList` only after those checks and user confirmation.

Global exclusions are instance-wide ANI-RSS settings. They are separate from an Ani's `exclude` list and are deliberately outside this helper's patch API. Do not change either layer unless the user has identified which one they want changed.
