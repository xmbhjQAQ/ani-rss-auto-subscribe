# Source routing and fixed candidate cards

Default to Mikan. Only query AniBT/AnimeGarden directly when the user specifies them, or after title aliases have been researched and Mikan retries are genuinely empty. If the work is identified but no Mikan RSS is available, use its Bangumi subject identity to query alternatives. An API failure is not an empty search. These are alternative primary sources, not automatically created standby feeds. AnimeGarden means `api.animes.garden`, not DMHY.

## Read-only source commands

```bash
python scripts/ani_rss.py plan "<title>" --result-file .ani-rss-runs/<run-id>/010-mikan.json
python scripts/ani_rss.py plan "<confirmed-title>" --source ani-bt --result-file .ani-rss-runs/<run-id>/011-anibt.json
python scripts/ani_rss.py plan "<confirmed-title>" --source anime-garden --result-file .ani-rss-runs/<run-id>/012-garden.json
python scripts/ani_rss.py groups --source ani-bt --bgm-id <subject-id> --sample-limit 20
python scripts/ani_rss.py groups --source anime-garden --bgm-id <subject-id> --sample-limit 20
```

`plan/search --bgm-url https://bgm.tv/subject/<id>` performs exact identity lookup for the two new sources. AniBT uses `/api/aniBT` with `title`, `season`, `bgmUrl`, then `/api/aniBTGroup?bgmId=...`. A title search is global: upstream ignores the airing-season filter when a title is supplied. AnimeGarden uses `/api/searchBgm?name=...` to locate metadata, then `/api/animeGardenGroup?bgmId=...` to establish resource availability. Its exact-identity lookup uses `/api/animeGardenList?bgmUrl=...`; upstream `exists=true` on that path is not a subscription check, so helper reports `exists=null`. Check `list/get` instead.

Each saved plan contains `candidate_id`, `groups[].group_key`, raw subtitle/spec samples and returned-item counts. Mikan's `bangumi_id` is a site-local ID; `bgm_id` is a Bangumi subject ID. Samples and search results may be limited; never claim exhaustive coverage. AniBT's `source_episode_key` is not the episode actually parsed by ANI-RSS preview.

For a large/ambiguous title result, use `search` first and resolve the work before fetching all groups. To obtain more evidence for rendering, rerun `plan` with `--sample-limit 20` and, for the two alternatives, `--bgm-url` for the confirmed work; save the new plan and rebuild the assessment against its sample indices. A standalone `groups` result is for inspection, not a replacement for `--plan-json`.

## Assessment schema and rendering

Use a saved successful plan. Explain the subtitle versions from sample evidence, not group names or guesses. Write an ignored per-request assessment JSON with this exact schema:

```json
{
  "options": [{
    "candidate_id": "<candidate_id from plan>",
    "group_key": "<group_key from that candidate>",
    "language_status": "confirmed",
    "subtitle_languages": "简体中文＋日文双语",
    "subtitle_mode": "内嵌",
    "resource_spec": "1080p / MP4",
    "version_filter": "简日内嵌 1080p",
    "evidence": [{"sample_index": 0, "field": "title", "quote": "简日内嵌"}]
  }]
}
```

All fields are required. `language_status` is `confirmed`, `mixed`, or `unknown`. Unknown subtitle language must say `未知`; unknown mode/spec must also be explicitly described as unknown. Known/mixed language requires source-sample references. Each reference uses a zero-based sample index, an allowed field (`title`, `subtitle_languages`, `subtitle_mode`, `resolution`), and a literal quote that exists in that field. Do not cite audio metadata as subtitle evidence. The helper validates references/shape; the LLM remains responsible for interpreting their meaning and checking conflicting samples.

Separate a group's individually published simplified/traditional or embedded/internal releases into options with different `version_filter` descriptions. Do not describe two separate releases as one multilingual file. `mixed` may describe an intentionally unrestricted mixed feed, but state what it contains. A recommendation cannot hide unknown language or choose a version on the user's behalf.

```bash
python scripts/ani_rss.py render-options \
  --plan-json .ani-rss-runs/<run-id>/010-mikan.json \
  --options-json .ani-rss-runs/<run-id>/014-assessment.json \
  --result-file .ani-rss-runs/<run-id>/015-options.json
```

Show the returned `text` verbatim. Cards contain number, source/group, title, subtitle language, mode, specification, version condition, quoted evidence, risk and RSS. Put an optional recommendation after the cards. For alternatives from multiple plans, render each separately and identify choices by source plus card number/option id. Numbers are local to each render result. Do not renumber options and lose their identity.

After the user chooses a specific card, use its exact `option_id`, `rss`, `source`, `bgm_url`, `subgroup`, and its saved render result:

```bash
python scripts/ani_rss.py build-from-rss \
  --rss "<selected RSS>" --type "<selected source>" \
  --bgm-url "<selected Bangumi URL>" --subgroup "<selected group>" \
  --options-evidence .ani-rss-runs/<run-id>/015-options.json \
  --option-id "<selected option_id>" \
  --result-file .ani-rss-runs/<run-id>/016-draft.json
```

The selected version description is not itself a Java regex. Once the draft exists, implement the preference with evidence-backed appended match/exclude rules and preview every returned match. Show the final mapping/parameters and the version result before the second user confirmation. Keep the same final Ani and preview evidence for add/set. An old render result is not new user consent; use a fresh per-request run directory.
