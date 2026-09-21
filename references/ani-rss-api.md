# ANI-RSS API Reference

This reference captures the ANI-RSS endpoints used by this skill. The validated local instance reported ANI-RSS `v3.1.49`.

## Authentication

ANI-RSS uses a global API key header:

```text
api-key: <key>
```

Do not expose the key in output. The helper script reads configuration from command-line flags, environment variables, `ani-rss-config.local.json`, or the fallback `ani-rss-key.txt`.

## Primary Mikan Flow

### Search Mikan

```text
POST /api/mikan?text=<title>
Content-Type: application/json
```

Use `{}` as the default body. If a user supplies year/season, send a `Season` object:

```json
{
  "year": 2026,
  "season": "夏",
  "seasonLabel": "2026年夏",
  "select": true
}
```

Relevant candidate fields:

- `bangumiId`
- `title`
- `url`
- `bgmUrl`
- `exists`
- `score`
- `groups`

### Get Subtitle/RSS Groups

```text
POST /api/mikanGroup?url=<mikan_url>
```

Relevant group fields:

- `label`
- `subgroupId`
- `rss`
- `bgmUrl`
- `updateDay`
- `items`
- `groupRegex.tags`

Recent `items[].title` values often contain language/spec evidence that ANI-RSS does not expose as normalized fields. The helper script should preserve this evidence and leave interpretation to the agent/user instead of classifying it with regex rules.

Recent `items[].title` values can also reveal collection-style releases that ANI-RSS may download but fail to parse into per-episode matches. The helper script may derive warning-only heuristics such as:

- `batch_evidence`
- `download_risk`
- per-sample `release_detection`

### Convert RSS To Subscription

```text
POST /api/rssToAni
Content-Type: application/json
```

Body:

```json
{
  "url": "https://mikanime.tv/RSS/Bangumi?bangumiId=3945&subgroupid=1231",
  "type": "mikan",
  "bgmUrl": "https://bgm.tv/subject/533027",
  "subgroup": "沸班亚马制作组",
  "enable": true
}
```

The response contains an `Ani` object.

### Add Subscription

```text
POST /api/addAni
Content-Type: application/json
```

Body: the selected `Ani` object.

The helper script requires `--confirm-add`, `--preview-evidence`, and `--tmdb-lookup-evidence` before calling this endpoint. TV subscriptions additionally require `--tmdb-season-evidence`. Evidence must match the final Ani object; `add` also runs a duplicate preflight and verifies the persisted object.

### Edit an existing subscription

```text
POST /api/setAni
Content-Type: application/json
```

Body: the complete existing `Ani` object, including its `id`. Do not use `/api/addAni` to edit an existing subscription: ANI-RSS treats it as a new subscription and can reject it as a duplicate. The helper exposes this as `set`, guarded by `--confirm-set` plus matching preview/TMDB evidence. It does not move files unless `--move-files` is explicitly supplied, and it verifies the persisted object with `listAni` after writing.

### Preview an Ani object

```text
POST /api/previewAni
Content-Type: application/json
```

Body: the proposed `Ani` object. This is a read-only validation/preview step. Keep its response as evidence for the user; it does not replace the final `add --confirm-add` confirmation. The helper adds a local `input_sha256` and `coverage` summary with returned count, parsed episode range, unparsed items, gaps, duplicates, and first/middle/last parsed anchors. It explicitly marks full-feed verification as false unless the workflow checks the source range separately.

### TMDB title lookup

```text
POST /api/getThemoviedbName
Content-Type: application/json
```

With `--title`, the helper sends `{ "title": "...", "ova": false }` to ANI-RSS. With `--tmdb-id`, it reads the direct TMDB series endpoint and requires a TMDB token/key; it does not send a bare `tmdbId` field to ANI-RSS's title endpoint. The response is returned without applying a title, season, or offset decision.

### TMDB season and episode-group evidence

```text
GET https://api.themoviedb.org/3/tv/{series_id}/season/{season_number}
GET https://api.themoviedb.org/3/tv/episode_group/{tv_episode_group_id}
```

The helper exposes these as read-only commands:

```bash
python scripts/ani_rss.py tmdb-season --tmdb-id "<id>" --season-number 2
python scripts/ani_rss.py tmdb-group-details --group-id "<group-id>"
```

They return raw per-episode evidence. For every new TV subscription, save the direct series lookup first, read its `seasons` list, and query `tmdb-season` only for an advertised season number. The season result must contain a non-empty `episodes` list. A missing RSS-labelled season or a 404 is not proof that the work is missing; select a real season from the series list and align the RSS range against its episodes. The helper rejects a final TV season that is absent from the saved series-level season list. These commands do not decide which season or episode group is correct and do not calculate offsets. Configure `TMDB_API_TOKEN` (recommended) or `TMDB_API_KEY` locally; never commit either credential.

### TMDB episode groups

```text
POST /api/getThemoviedbGroup
Content-Type: application/json
```

Body: the current `Ani` object. The response is raw episode-group evidence. Compare it with the RSS samples and let the workflow decide whether an alignment patch is justified.

### List Subscriptions

```text
POST /api/listAni
```

Use this for duplicate or status checks.

## Editing an Ani draft

The local helper exposes a safe, reviewable patch operation instead of a collection of one-off setters:

```bash
python scripts/ani_rss.py patch \
  --ani-json ani-rss-selected.local.json \
  --patch-json ani-rss-patch.json > ani-rss-patched.local.json
```

Supported fields are `match`, `exclude`, `appendMatch`, `appendExclude`, `standbyRssList`, `releaseDate`, `season`, `offset`, `totalEpisodeNumber`, `customEpisode`, `customEpisodeStr`, `customEpisodeGroupIndex`, `tmdb`, and `themoviedbName`. `match` and `exclude` replace the corresponding list. `appendMatch` and `appendExclude` are local patch operators: they append in order to the existing list and are removed before the Ani object is previewed or submitted. A patch cannot both replace and append the same list. The command checks field names, value ranges, and cross-field requirements, then returns the patched object with `requires_user_confirmation: true`. It deliberately does not evaluate Java regex meaning or infer TMDB numbering.

Rules use ANI-RSS Java-regex semantics. A raw rule such as `简繁日|简日` applies to all subtitle groups. A source-specific rule uses `{{字幕组名}}:正则`, for example `{{绿茶字幕组}}:简繁日|简日`; an empty group in the UI means raw regex, not `{{}}:...`. Preserve the existing rule arrays and use the append operators for normal additions so built-in exclusions such as `720[Pp]`, `\d-\d`, `合集`, and `特别篇` stay ahead of new rules. `match`/`exclude` here are per-Ani fields; this helper intentionally has no command for instance-wide global exclusions. See [regex-rules.md](regex-rules.md).

`standbyRssList` is an array of ANI-RSS fallback-source objects. Each entry must contain a non-empty `url` and an integer `offset`; `label` is optional. ANI-RSS does not accept an `rss` alias. Do not add or edit the list unless the user explicitly requests a fallback source. A fallback must cover the same content range as the primary source, although it may require a different offset due to different source numbering. ANI-RSS also has a global standby-RSS setting, which must be enabled for these entries to be used.

Source selection is a user decision gate. The agent may present evidence and a recommendation, but must wait for the user to choose the exact anime, subtitle group, and primary RSS before calling `rssToAni`/`build-from-rss`, adding group-specific regex rules, or creating a fallback entry. A single candidate, an agent recommendation, or a request to “use the best one” is not confirmation.

`tmdb` and `themoviedbName` must be patched together. Supply the full `tmdb` object returned by lookup, not only an id, because Ani updates replace nested objects rather than applying a deep JSON merge.

The usual read/patch/preview sequence is:

```bash
python scripts/ani_rss.py tmdb-lookup --tmdb-id "<id>" --result-file .ani-rss-runs/020-tmdb-lookup.json
python scripts/ani_rss.py tmdb-season --tmdb-id "<id>" --season-number <season> --result-file .ani-rss-runs/021-tmdb-season.json
python scripts/ani_rss.py patch --ani-json ani-rss-selected.local.json --patch-json ani-rss-patch.json
python scripts/ani_rss.py preview --ani-json ani-rss-patched.local.json --result-file .ani-rss-runs/040-preview.json
```

For terminal/output recovery, add `--result-file .ani-rss-runs/<run-id>/<step>.json` to any command. The helper mirrors JSON to stdout and atomically stores the same payload. An empty elevated terminal is not evidence of failure; inspect the result file, stderr, and exit code. Never retry a mutating command until `get/list` establishes whether it already succeeded.

For an existing subscription, use the safe edit sequence instead:

```bash
python scripts/ani_rss.py get --id "<existing-ani-id>" > ani-rss-existing.local.json
python scripts/ani_rss.py patch --ani-json ani-rss-existing.local.json --patch-json ani-rss-patch.local.json > ani-rss-patched.local.json
python scripts/ani_rss.py preview --ani-json ani-rss-patched.local.json
python scripts/ani_rss.py set \
  --ani-json ani-rss-patched.local.json \
  --tmdb-lookup-evidence .ani-rss-runs/020-tmdb-lookup.json \
  --tmdb-season-evidence .ani-rss-runs/021-tmdb-season.json \
  --preview-evidence .ani-rss-runs/040-preview.json \
  --confirm-set
```

These TMDB endpoints expose evidence only. `tmdb-groups` may return only aggregate group counts, which are insufficient to map one RSS episode to a TMDB episode; use `tmdb-season` or `tmdb-group-details` to obtain the concrete episode list. The LLM must identify the exact work, enforce the user season gate and subtitle-group/RSS choice, compare against `list` results by TMDB id/title, classify each RSS as season-relative or continuous, confirm the actual parsed number from `preview.items[].episode`, audit first/middle/last preview anchors and all returned offsets, explain any proposed `season`/`offset` change, and obtain user confirmation before calling `add --confirm-add` or `set --confirm-set`. The helper refuses a write without matching TMDB and preview evidence, performs a duplicate preflight for `add`, and verifies the complete changed object after `add` or `set`. See `references/tmdb-alignment.md` for the decision workflow.

## Validated Example

For `租借女友 第五季`:

- Mikan search returned `https://mikanime.tv/Home/Bangumi/3945`.
- `mikanGroup` returned 5 groups.
- Candidate tags and sample titles included `CHT`, `繁日内嵌`, `简日内嵌`, `简繁日内封`, `简／繁`, and `简繁内封字幕`.

## Agent Decision Boundaries

The API returns candidates and source evidence; it does not decide user intent.

Stop and ask the user when:

- No candidates are returned.
- Multiple candidates are plausible.
- A candidate may already be subscribed.
- The title may refer to different seasons, remakes, movies, OVAs, specials, or spin-offs.
- Subtitle language/spec/source cannot be confidently explained from group label, tags, and sample titles.
- Sample titles suggest a collection, full season, or explicit multi-episode pack that may not parse cleanly after download.
- Mikan returns no usable RSS group.

Use web search as a research step when title aliases, official names, season numbering, or source abbreviations are unclear. Web search can improve the candidate list and explanation, but it is not authorization to subscribe.

## Non-Default Fallbacks

Do not use BGM endpoints by default. If Mikan results are insufficient, ask the user before trying non-Mikan metadata or fallback endpoints.
