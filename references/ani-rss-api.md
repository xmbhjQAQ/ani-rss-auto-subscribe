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

The helper script requires `--confirm-add` before calling this endpoint.

### List Subscriptions

```text
POST /api/listAni
```

Use this for duplicate or status checks.

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
