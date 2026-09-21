# -*- coding: utf-8 -*-

import contextlib
import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "ani_rss.py"
SPEC = importlib.util.spec_from_file_location("ani_rss", SCRIPT_PATH)
assert SPEC and SPEC.loader
ani_rss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ani_rss)


class AniAdapterTests(unittest.TestCase):
    def write_json(self, value):
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json", delete=False
        )
        with handle:
            json.dump(value, handle, ensure_ascii=False)
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return str(path)

    def write_write_evidence(self, ani):
        preview = self.write_json(
            {
                "ok": True,
                "command": "preview",
                "input_sha256": ani_rss.json_sha256(ani),
                "preview": {"items": []},
            }
        )
        lookup = self.write_json(
            {
                "ok": True,
                "command": "tmdb-lookup",
                "query": {"tmdb_id": str(ani["tmdb"]["id"])},
                "result": {
                    "id": int(ani["tmdb"]["id"]),
                    "name": "Example",
                    "seasons": [
                        {
                            "season_number": 1,
                            "episode_count": 1,
                        },
                        {
                            "season_number": ani.get("season", 0),
                            "episode_count": 1,
                        },
                    ],
                },
            }
        )
        season = self.write_json(
            {
                "ok": True,
                "command": "tmdb-season",
                "query": {
                    "tmdb_id": str(ani["tmdb"]["id"]),
                    "season_number": ani["season"],
                },
                "result": {
                    "season_number": ani["season"],
                    "episodes": [{"episode_number": 1}],
                },
            }
        )
        return preview, lookup, season

    def test_patch_replaces_requested_fields_without_mutating_input(self):
        ani = {
            "title": "Example",
            "match": ["old"],
            "exclude": ["old-exclude"],
            "standbyRssList": [],
            "season": 3,
            "offset": 0,
        }
        patch_value = {
            "match": ["简繁日"],
            "exclude": ["720[Pp]", "合集"],
            "standbyRssList": [
                {"label": "ANi", "url": "https://example.test/rss", "offset": 12}
            ],
            "releaseDate": "2026-07-05",
            "season": 1,
            "offset": 12,
            "totalEpisodeNumber": 36,
        }

        patched = ani_rss.apply_patch(ani, patch_value)

        self.assertEqual(["简繁日"], patched["match"])
        self.assertEqual(12, patched["standbyRssList"][0]["offset"])
        self.assertEqual("2026-07-05", patched["releaseDate"])
        self.assertEqual(1, patched["season"])
        self.assertEqual(12, patched["offset"])
        self.assertEqual(36, patched["totalEpisodeNumber"])
        self.assertEqual("Example", patched["title"])
        self.assertEqual(["old"], ani["match"])
        self.assertNotIn("releaseDate", ani)

    def test_patch_appends_regex_rules_without_sending_local_operator_to_ani(self):
        ani = {
            "match": ["简繁日"],
            "exclude": ["720[Pp]", "\\d-\\d", "合集", "特别篇"],
        }

        patched = ani_rss.apply_patch(
            ani,
            {
                "appendMatch": ["{{绿茶字幕组}}:简繁日|简日"],
                "appendExclude": ["{{某字幕组}}:预告"],
            },
        )

        self.assertEqual(
            ["简繁日", "{{绿茶字幕组}}:简繁日|简日"], patched["match"]
        )
        self.assertEqual(
            ["720[Pp]", "\\d-\\d", "合集", "特别篇", "{{某字幕组}}:预告"],
            patched["exclude"],
        )
        self.assertNotIn("appendMatch", patched)
        self.assertNotIn("appendExclude", patched)
        self.assertEqual(["简繁日"], ani["match"])

    def test_patch_rejects_replacing_and_appending_same_regex_field(self):
        with self.assertRaisesRegex(ani_rss.AniRssError, "either match or appendMatch"):
            ani_rss.validate_patch({"match": ["简繁日"], "appendMatch": ["简日"]})
        with self.assertRaisesRegex(ani_rss.AniRssError, "either exclude or appendExclude"):
            ani_rss.validate_patch({"exclude": ["合集"], "appendExclude": ["特别篇"]})

    def test_patch_rejects_unknown_fields(self):
        with self.assertRaisesRegex(ani_rss.AniRssError, "Unsupported Ani patch"):
            ani_rss.validate_patch({"typoSeason": 1})

    def test_patch_rejects_invalid_standby_rss(self):
        with self.assertRaisesRegex(ani_rss.AniRssError, "non-empty string"):
            ani_rss.validate_patch(
                {"standbyRssList": [{"label": "ANi", "url": "", "offset": 0}]}
            )
        with self.assertRaisesRegex(ani_rss.AniRssError, "url must be"):
            ani_rss.validate_patch({"standbyRssList": [{"label": "ANi", "offset": 0}]})
        with self.assertRaisesRegex(ani_rss.AniRssError, "offset must be"):
            ani_rss.validate_patch(
                {"standbyRssList": [{"label": "ANi", "url": "https://example.test/rss"}]}
            )
        with self.assertRaisesRegex(ani_rss.AniRssError, "Unsupported standby"):
            ani_rss.validate_patch(
                {"standbyRssList": [{"label": "ANi", "rss": "https://example.test/rss", "offset": 0}]}
            )

    def test_patch_rejects_invalid_cross_field_values(self):
        with self.assertRaisesRegex(ani_rss.AniRssError, "YYYY-MM-DD"):
            ani_rss.validate_patch({"releaseDate": "20260705"})
        for field in ("season", "totalEpisodeNumber", "customEpisodeGroupIndex"):
            with self.subTest(field=field):
                with self.assertRaisesRegex(ani_rss.AniRssError, "must not be negative"):
                    ani_rss.validate_patch({field: -1})
        with self.assertRaisesRegex(ani_rss.AniRssError, "requires a non-empty"):
            ani_rss.apply_patch({}, {"customEpisode": True})

    def test_patch_requires_a_complete_tmdb_identity(self):
        with self.assertRaisesRegex(ani_rss.AniRssError, "patched together"):
            ani_rss.validate_patch({"tmdb": {"id": "1"}})
        with self.assertRaisesRegex(ani_rss.AniRssError, "tmdb tmdbType"):
            ani_rss.validate_patch(
                {"tmdb": {"id": "1"}, "themoviedbName": "Example"}
            )
        ani_rss.validate_patch(
            {
                "tmdb": {
                    "id": "1",
                    "name": "Example",
                    "tmdbType": "TV",
                },
                "themoviedbName": "Example",
            }
        )

    def test_tmdb_lookup_is_a_thin_endpoint_wrapper(self):
        args = SimpleNamespace(
            title="Example",
            tmdb_id=None,
            movie=False,
            timeout=60,
        )
        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss,
                "request_json",
                return_value={"data": {"themoviedbName": "Example", "tmdb": {"id": "1"}}},
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_tmdb_lookup(args)

        request.assert_called_once_with(
            "http://ani",
            "key",
            "POST",
            "/api/getThemoviedbName",
            body={"ova": False, "title": "Example"},
            timeout=60,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual("tmdb-lookup", payload["command"])
        self.assertEqual("1", payload["result"]["tmdb"]["id"])

    def test_tmdb_groups_and_preview_forward_raw_objects(self):
        ani_path = self.write_json({"tmdb": {"id": "1"}, "season": 1})
        common = SimpleNamespace(ani_json=ani_path, timeout=30)

        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss, "request_json", return_value={"data": [{"id": "group"}]}
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_tmdb_groups(common)
        request.assert_called_once_with(
            "http://ani",
            "key",
            "POST",
            "/api/getThemoviedbGroup",
            body={"tmdb": {"id": "1"}, "season": 1},
            timeout=30,
        )
        self.assertEqual([{"id": "group"}], json.loads(output.getvalue())["groups"])

        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss, "request_json", return_value={"data": {"items": []}}
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_preview(common)
        request.assert_called_once_with(
            "http://ani",
            "key",
            "POST",
            "/api/previewAni",
            body={"tmdb": {"id": "1"}, "season": 1},
            timeout=30,
        )
        preview_payload = json.loads(output.getvalue())
        self.assertTrue(preview_payload["requires_user_confirmation"])
        self.assertEqual({"items": []}, preview_payload["preview"])

    def test_get_flattens_list_response_and_set_persists_only_with_confirmation(self):
        existing = {
            "id": "ani-1",
            "title": "Example",
            "season": 1,
            "tmdb": {"id": "1", "name": "Example", "tmdbType": "TV"},
        }
        get_args = SimpleNamespace(id="ani-1", timeout=30)
        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss,
                "request_json",
                return_value={"data": {"weekList": [{"items": [existing]}]}},
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_get(get_args)
        request.assert_called_once_with(
            "http://ani", "key", "POST", "/api/listAni", timeout=30
        )
        self.assertEqual(existing, json.loads(output.getvalue())["ani"])

        ani_path = self.write_json(existing)
        preview, lookup, season = self.write_write_evidence(existing)
        set_args = SimpleNamespace(
            confirm_set=True,
            move_files=False,
            ani_json=ani_path,
            timeout=30,
            preview_evidence=preview,
            tmdb_lookup_evidence=lookup,
            tmdb_season_evidence=season,
        )
        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss,
                "request_json",
                side_effect=[
                    {"code": 200},
                    {"data": {"weekList": [{"items": [existing]}]}},
                ],
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_set(set_args)
        self.assertEqual(2, request.call_count)
        self.assertEqual(
            ("http://ani", "key", "POST", "/api/setAni"),
            request.call_args_list[0].args[:4],
        )
        self.assertEqual(
            ("http://ani", "key", "POST", "/api/listAni"),
            request.call_args_list[1].args[:4],
        )
        self.assertEqual("set", json.loads(output.getvalue())["command"])

        set_args.confirm_set = False
        with self.assertRaises(SystemExit) as raised:
            ani_rss.command_set(set_args)
        self.assertEqual(2, raised.exception.code)

    def test_preview_coverage_reports_anchors_duplicates_and_gaps(self):
        coverage = ani_rss.preview_coverage(
            {
                "items": [
                    {"episode": 1, "title": "E01", "pubDate": "a"},
                    {"episode": 3, "title": "E03", "pubDate": "b"},
                    {"episode": 3, "title": "E03 alt", "pubDate": "c"},
                    {"episode": 5, "title": "E05", "pubDate": "d"},
                ]
            }
        )
        self.assertEqual("observed-range", coverage["coverage_status"])
        self.assertEqual([2, 4], coverage["missing_episodes"])
        self.assertEqual([3.0], coverage["duplicate_episodes"])
        self.assertEqual([1, 3, 5], [item["episode"] for item in coverage["anchors"]])
        self.assertFalse(coverage["full_feed_verified"])

    def test_preview_coverage_requires_three_parsed_episodes(self):
        coverage = ani_rss.preview_coverage(
            {
                "items": [
                    {"title": "unparsed-1"},
                    {"title": "unparsed-2"},
                    {"episode": 5},
                ]
            }
        )
        self.assertEqual("insufficient-samples", coverage["coverage_status"])
        self.assertEqual(1, coverage["parsed_episode_count"])
        self.assertEqual(2, coverage["unparsed_item_count"])

    def test_add_requires_persistence_verification(self):
        ani = {
            "id": "ani-1",
            "title": "Example",
            "season": 1,
            "url": "https://example.test/rss",
            "subgroup": "Group",
            "tmdb": {"id": "1", "name": "Example", "tmdbType": "TV"},
        }
        ani_path = self.write_json(ani)
        preview, lookup, season = self.write_write_evidence(ani)
        args = SimpleNamespace(
            confirm_add=True,
            ani_json=ani_path,
            timeout=30,
            preview_evidence=preview,
            tmdb_lookup_evidence=lookup,
            tmdb_season_evidence=season,
        )
        list_response = {"data": {"weekList": [{"items": [ani]}]}}
        empty_list_response = {"data": {"weekList": [{"items": []}]}}
        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(
                ani_rss,
                "request_json",
                side_effect=[empty_list_response, {"code": 200}, list_response],
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_add(args)
        self.assertEqual(3, request.call_count)
        self.assertEqual(
            {"verified": True},
            {
                key: json.loads(output.getvalue())["verification"][key]
                for key in ("verified",)
            },
        )

    def test_write_requires_matching_tmdb_and_preview_evidence(self):
        ani = {
            "title": "Example",
            "season": 2,
            "tmdb": {"id": "123", "name": "Example", "tmdbType": "TV"},
        }
        preview, lookup, season = self.write_write_evidence(ani)
        with self.assertRaisesRegex(ani_rss.AniRssError, "tmdb-season-evidence"):
            ani_rss.validate_write_evidence(
                ani,
                preview_path=preview,
                tmdb_lookup_path=lookup,
                tmdb_season_path=None,
            )
        other = dict(ani, season=1)
        with self.assertRaisesRegex(ani_rss.AniRssError, "does not match"):
            ani_rss.validate_write_evidence(
                other,
                preview_path=preview,
                tmdb_lookup_path=lookup,
                tmdb_season_path=season,
            )

    def test_write_rejects_a_final_season_missing_from_tmdb_season_list(self):
        ani = {
            "title": "Example",
            "season": 3,
            "tmdb": {"id": "123", "name": "Example", "tmdbType": "TV"},
        }
        preview, lookup, season = self.write_write_evidence(dict(ani, season=1))
        with self.assertRaisesRegex(ani_rss.AniRssError, "not present.*seasons list"):
            ani_rss.validate_write_evidence(
                ani,
                preview_path=self.write_json(
                    {
                        "ok": True,
                        "command": "preview",
                        "input_sha256": ani_rss.json_sha256(ani),
                    }
                ),
                tmdb_lookup_path=lookup,
                tmdb_season_path=season,
            )

    def test_tmdb_season_numbers_reads_direct_lookup_seasons(self):
        self.assertEqual(
            {0, 1, 3},
            ani_rss.tmdb_season_numbers(
                {
                    "seasons": [
                        {"season_number": 0},
                        {"season_number": 1},
                        {"season_number": "3"},
                        {"season_number": "not-a-number"},
                    ]
                }
            ),
        )

    def test_tmdb_season_adapter_returns_raw_episode_evidence(self):
        args = SimpleNamespace(
            tmdb_id="123",
            season_number=2,
            language="zh-CN",
            timeout=30,
        )
        with patch.object(
            ani_rss,
            "resolve_tmdb_credentials",
            return_value=("https://tmdb.test/3", "token", None),
        ):
            with patch.object(
                ani_rss,
                "request_tmdb_json",
                return_value={"season_number": 2, "episodes": [{"episode_number": 1}]},
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_tmdb_season(args)
        request.assert_called_once_with(
            "https://tmdb.test/3",
            "token",
            None,
            "/tv/123/season/2",
            query={"language": "zh-CN"},
            timeout=30,
        )
        payload = json.loads(output.getvalue())
        self.assertEqual("tmdb-season", payload["command"])
        self.assertEqual(1, payload["result"]["episodes"][0]["episode_number"])

    def test_tmdb_id_lookup_uses_direct_tmdb_api(self):
        args = SimpleNamespace(
            tmdb_id="123",
            title=None,
            movie=False,
            timeout=30,
        )
        with patch.object(
            ani_rss,
            "resolve_tmdb_credentials",
            return_value=("https://tmdb.test/3", "token", None),
        ):
            with patch.object(
                ani_rss,
                "request_tmdb_json",
                return_value={"id": 123, "name": "Example"},
            ) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_tmdb_lookup(args)
        request.assert_called_once_with(
            "https://tmdb.test/3",
            "token",
            None,
            "/tv/123",
            timeout=30,
        )
        self.assertEqual("tmdb-api", json.loads(output.getvalue())["source"])

    def test_result_file_persists_json_atomically(self):
        handle = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        handle.close()
        result_path = Path(handle.name)
        result_path.unlink()
        self.addCleanup(result_path.unlink, missing_ok=True)
        previous = ani_rss.CURRENT_RESULT_FILE
        ani_rss.CURRENT_RESULT_FILE = result_path
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                ani_rss.print_json({"ok": True, "command": "test"})
            self.assertEqual({"ok": True, "command": "test"}, json.loads(result_path.read_text()))
        finally:
            ani_rss.CURRENT_RESULT_FILE = previous

    def test_add_still_requires_explicit_confirmation(self):
        args = SimpleNamespace(confirm_add=False)
        with self.assertRaises(SystemExit) as raised:
            ani_rss.command_add(args)
        self.assertEqual(2, raised.exception.code)

    def test_parser_exposes_interface_commands(self):
        parser = ani_rss.build_parser()
        patch_args = parser.parse_args(
            ["patch", "--ani-json", "ani.json", "--patch-json", "patch.json"]
        )
        self.assertIs(patch_args.func, ani_rss.command_patch)
        preview_args = parser.parse_args(
            ["preview", "--ani-json", "ani.json", "--base-url", "http://ani"]
        )
        self.assertIs(preview_args.func, ani_rss.command_preview)
        lookup_args = parser.parse_args(["tmdb-lookup", "--tmdb-id", "123"])
        self.assertIs(lookup_args.func, ani_rss.command_tmdb_lookup)
        season_args = parser.parse_args(
            ["tmdb-season", "--tmdb-id", "123", "--season-number", "2"]
        )
        self.assertIs(season_args.func, ani_rss.command_tmdb_season)
        group_details_args = parser.parse_args(
            ["tmdb-group-details", "--group-id", "grp-1"]
        )
        self.assertIs(group_details_args.func, ani_rss.command_tmdb_group_details)
        groups_args = parser.parse_args(
            ["tmdb-groups", "--ani-json", "ani.json"]
        )
        self.assertIs(groups_args.func, ani_rss.command_tmdb_groups)
        get_args = parser.parse_args(["get", "--id", "ani-1"])
        self.assertIs(get_args.func, ani_rss.command_get)
        set_args = parser.parse_args(
            [
                "set",
                "--ani-json",
                "ani.json",
                "--confirm-set",
                "--move-files",
                "--preview-evidence",
                "preview.json",
                "--tmdb-lookup-evidence",
                "lookup.json",
            ]
        )
        self.assertIs(set_args.func, ani_rss.command_set)
        self.assertTrue(set_args.move_files)


if __name__ == "__main__":
    unittest.main()
