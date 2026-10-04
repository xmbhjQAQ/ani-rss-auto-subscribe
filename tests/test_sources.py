import contextlib
import copy
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from test_ani_rss import ani_rss as m


class SourceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.group = m.normalize_group({
            "label": "Group", "rss": "https://example.test/rss",
            "items": [{"title": "Example 01 简日内嵌 1080p MP4", "episode": 1}],
        })
        self.group["group_key"] = "group-1"
        self.plan = {"ok": True, "command": "plan", "candidates": [{
            "candidate_id": "candidate-1", "source": "mikan", "title": "Example",
            "bgm_url": "https://bgm.tv/subject/1", "groups": [self.group],
        }]}
        self.assessment = {"options": [{
            "candidate_id": "candidate-1", "group_key": "group-1",
            "language_status": "confirmed", "subtitle_languages": "简体中文＋日文双语",
            "subtitle_mode": "内嵌", "resource_spec": "1080p / MP4",
            "version_filter": "简日内嵌 1080p", "evidence": [
                {"sample_index": 0, "field": "title", "quote": "简日内嵌"}],
        }]}

    def args(self, source):
        return SimpleNamespace(source=source, title="Example", timeout=10,
                               sample_limit=5, year=None, season=None, bgm_url=None)

    def test_mikan_is_default_and_empty_result_does_not_switch_sources(self):
        args = m.build_parser().parse_args(["plan", "Example"])
        self.assertEqual("mikan", args.source)
        with patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
             patch.object(m, "request_json", return_value={"data": {"weeks": []}}) as request, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            m.command_plan(args)
        self.assertEqual("/api/mikan", request.call_args.args[3])
        self.assertEqual(1, request.call_count)
        self.assertEqual("normalize-title-before-other-sources", json.loads(output.getvalue())["next_action"])

    def test_anibt_plan_preserves_language_metadata_and_group_identity(self):
        responses = [{"data": {"byWeekday": [{"animes": [{
            "bgmId": "10", "title": {"primary": "Example", "chinese": "示例"},
        }]}]}}, {"data": [{"bgmId": "10", "groupId": "20", "slug": "group",
            "name": "真实组名", "rss": "https://anibt.net/rss/anime.xml?bgmId=10&groupSlug=group",
            "items": [{"title": "E01", "episodeKey": "01", "language": ["CHS", "JPN"],
                       "subtitle": "EMBEDDED", "resolution": "1080p", "publishedAt": 123}],
        }]}]
        with patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
             patch.object(m, "request_json", side_effect=responses) as request, \
             contextlib.redirect_stdout(io.StringIO()) as output:
            m.command_plan(self.args("ani-bt"))
        candidate = json.loads(output.getvalue())["candidates"][0]
        self.assertEqual("示例", candidate["title"])
        group = candidate["groups"][0]
        self.assertEqual("真实组名", group["label"])
        self.assertEqual("20", group["subgroup_id"])
        self.assertEqual(["CHS", "JPN"], group["samples"][0]["subtitle_languages"])
        self.assertIsNone(group["samples"][0]["episode"])
        self.assertEqual({"bgmId": "10"}, request.call_args.kwargs["query"])

    def test_animegarden_title_search_uses_bangumi_and_actual_group_fields(self):
        with patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
             patch.object(m, "request_json", side_effect=[
                 {"data": [{"id": "10", "name": "Original", "nameCn": "中文标题"}]},
                 {"data": [{"id": "20", "name": "真实组名", "bgmId": "10",
                    "rss": "https://api.animes.garden/feed.xml?subject=10&fansub=Group",
                    "items": [{"title": "简中", "fansub": {"name": "真实组名"}, "provider": "dmhy"}]}]},
             ]) as request, contextlib.redirect_stdout(io.StringIO()) as output:
            m.command_plan(self.args("anime-garden"))
        self.assertEqual(["/api/searchBgm", "/api/animeGardenGroup"], [call.args[3] for call in request.call_args_list])
        candidate = json.loads(output.getvalue())["candidates"][0]
        self.assertIsNone(candidate["exists"])
        self.assertEqual("中文标题", candidate["title"])
        self.assertEqual("dmhy", candidate["groups"][0]["samples"][0]["provider"])

    def test_bad_source_response_is_not_reported_as_no_results(self):
        for source in m.SOURCES:
            with self.subTest(source=source), patch.object(m, "request_json", return_value={"data": None}):
                with self.assertRaises(m.AniRssError):
                    m.search_candidates(self.args(source), "http://ani", "dummy")

    def test_fixed_cards_include_language_mode_spec_and_source_binding(self):
        result = m.render_options(self.plan, self.assessment)
        self.assertIn("字幕语言：简体中文＋日文双语", result["text"])
        self.assertIn("字幕形式：内嵌", result["text"])
        self.assertIn("资源规格：1080p / MP4", result["text"])
        self.assertEqual(self.group["rss"], result["options"][0]["rss"])
        self.assertTrue(result["requires_user_selection"])

    def test_missing_language_and_invented_evidence_are_rejected(self):
        for mutate in (
            lambda option: option.pop("subtitle_languages"),
            lambda option: option["evidence"][0].update(quote="不存在的字幕标签"),
            lambda option: option.update(group_key="invented-group"),
            lambda option: option.update(evidence=[]),
        ):
            assessment = copy.deepcopy(self.assessment)
            mutate(assessment["options"][0])
            with self.assertRaises(m.AniRssError):
                m.render_options(self.plan, assessment)

    def test_unknown_language_is_explicit_and_mixed_versions_can_be_separate(self):
        assessment = copy.deepcopy(self.assessment)
        assessment["options"][0].update(language_status="unknown", subtitle_languages="未知", evidence=[])
        self.assertIn("字幕语言：未知", m.render_options(self.plan, assessment)["text"])
        second = copy.deepcopy(self.assessment["options"][0])
        second["version_filter"] = "只要1080p"
        self.assessment["options"].append(second)
        result = m.render_options(self.plan, self.assessment)
        self.assertEqual(2, len({option["option_id"] for option in result["options"]}))

    def test_build_rejects_different_selected_rss_before_api_call(self):
        rendered = m.render_options(self.plan, self.assessment)
        args = SimpleNamespace(options_evidence="dummy.json", option_id=rendered["options"][0]["option_id"],
            rss="https://other.test/rss", type="mikan", subgroup="Group", bgm_url="https://bgm.tv/subject/1")
        with patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
             patch.object(m, "read_object_arg", return_value=rendered), patch.object(m, "request_json") as request:
            with self.assertRaisesRegex(m.AniRssError, "rendered option"):
                m.command_build_from_rss(args)
        request.assert_not_called()

    def test_build_forwards_canonical_types_and_preserves_selected_source(self):
        for source, rss in (
            ("mikan", "https://mikan.example/RSS/Bangumi?bangumiId=9&subgroupid=2"),
            ("ani-bt", "https://anibt.net/rss/anime.xml?bgmId=1&groupSlug=group"),
            ("anime-garden", "https://api.animes.garden/feed.xml?subject=1&fansub=Group"),
        ):
            plan = copy.deepcopy(self.plan)
            plan["candidates"][0]["source"] = source
            plan["candidates"][0]["groups"][0]["rss"] = rss
            rendered = m.render_options(plan, self.assessment)
            args = SimpleNamespace(options_evidence="dummy.json", option_id=rendered["options"][0]["option_id"],
                rss=rss, type=source, subgroup="Group", bgm_url="https://bgm.tv/subject/1", disabled=False, timeout=10)
            body = {"url": rss, "type": source, "subgroup": "Group", "bgmUrl": args.bgm_url, "enable": True}
            with self.subTest(source=source), patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
                 patch.object(m, "read_object_arg", return_value=rendered), \
                 patch.object(m, "request_json", return_value={"data": body}) as request, \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                m.command_build_from_rss(args)
            self.assertEqual(body, request.call_args.kwargs["body"])
            self.assertEqual(body, json.loads(output.getvalue())["ani"])

    def test_build_rejects_backend_source_reset(self):
        rendered = m.render_options(self.plan, self.assessment)
        args = SimpleNamespace(options_evidence="dummy.json", option_id=rendered["options"][0]["option_id"],
            rss=self.group["rss"], type="mikan", subgroup="Group", bgm_url="https://bgm.tv/subject/1", disabled=False, timeout=10)
        with patch.object(m, "resolve_config", return_value=("http://ani", "dummy")), \
             patch.object(m, "read_object_arg", return_value=rendered), \
             patch.object(m, "request_json", return_value={"data": {"url": self.group["rss"], "type": "other", "subgroup": "Group"}}):
            with self.assertRaisesRegex(m.AniRssError, "draft source"):
                m.command_build_from_rss(args)

    def test_final_confirmation_uses_actual_values(self):
        text = m.render_confirmation({"title": "Example", "season": 2, "offset": -12},
                                     {"items": [{"episode": number} for number in (1, 2, 3)]})
        self.assertIn("目标季度：2", text)
        self.assertIn("集数偏移：-12", text)
        self.assertIn("3个不同已解析集数", text)

    def evidence(self, ani, items):
        records = {
            "preview": {"ok": True, "command": "preview", "input_sha256": m.json_sha256(ani), "preview": {"items": items}},
            "lookup": {"ok": True, "command": "tmdb-lookup", "query": {"tmdb_id": "1"},
                       "result": {"id": 1, "seasons": [{"season_number": 1}]}},
            "season": {"ok": True, "command": "tmdb-season", "query": {"tmdb_id": "1", "season_number": 1},
                       "result": {"season_number": 1, "episodes": [{"episode_number": 1}]}},
        }
        return patch.object(m, "read_evidence_file", side_effect=lambda path, label: records[path])

    def test_empty_preview_and_three_versions_of_one_episode_cannot_authorize_write(self):
        ani = {"season": 1, "tmdb": {"id": "1", "tmdbType": "TV"}}
        for items in ([], [{"episode": 1}] * 3):
            with self.subTest(items=items), self.evidence(ani, items):
                with self.assertRaises(m.AniRssError):
                    m.validate_write_evidence(ani, preview_path="preview", tmdb_lookup_path="lookup", tmdb_season_path="season")
        coverage = m.preview_coverage({"items": [{"episode": 1}] * 3})
        self.assertEqual("insufficient-samples", coverage["coverage_status"])
        self.assertEqual(1, len(coverage["anchors"]))

    def test_cross_source_duplicate_requires_distinct_range_and_exact_duplicate_still_blocks(self):
        ani = {"title": "Example", "season": 1, "url": "https://one.test/rss", "subgroup": "Group", "tmdb": {"id": "1"}}
        other = dict(ani, url="https://two.test/rss", subgroup="Other")
        with patch.object(m, "request_json", return_value={"data": [ani]}):
            with self.assertRaisesRegex(m.AniRssError, "Same TMDB season"):
                m.preflight_duplicate_check("http://ani", "dummy", other, 10)
            result = m.preflight_duplicate_check("http://ani", "dummy", other, 10, distinct_feed_reason="User confirmed second cour")
            self.assertEqual(1, result["same_tmdb_season_count"])
            with self.assertRaises(m.AniRssError):
                m.preflight_duplicate_check("http://ani", "dummy", ani, 10, distinct_feed_reason="Another range")

    def test_config_and_key_file_resolve_relative_to_skill_from_other_cwd(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / m.LOCAL_CONFIG_FILE).write_text(json.dumps({"base_url": "http://ani", "api_key_file": "key.txt"}), encoding="utf-8")
            (root / "key.txt").write_text("dummy", encoding="utf-8")
            caller = root / "caller"
            caller.mkdir()
            previous = os.getcwd()
            try:
                os.chdir(caller)
                with patch.object(m, "SKILL_ROOT", root), patch.dict(os.environ, {}, clear=True):
                    self.assertEqual(("http://ani", "dummy"), m.resolve_config(SimpleNamespace()))
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
