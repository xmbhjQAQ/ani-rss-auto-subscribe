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
        existing = {"id": "ani-1", "title": "Example", "season": 1}
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
        set_args = SimpleNamespace(
            confirm_set=True,
            move_files=False,
            ani_json=ani_path,
            timeout=30,
        )
        with patch.object(ani_rss, "resolve_config", return_value=("http://ani", "key")):
            with patch.object(ani_rss, "request_json", return_value={"code": 200}) as request:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    ani_rss.command_set(set_args)
        request.assert_called_once_with(
            "http://ani",
            "key",
            "POST",
            "/api/setAni",
            query=None,
            body=existing,
            timeout=30,
        )
        self.assertEqual("set", json.loads(output.getvalue())["command"])

        set_args.confirm_set = False
        with self.assertRaises(SystemExit) as raised:
            ani_rss.command_set(set_args)
        self.assertEqual(2, raised.exception.code)

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
        groups_args = parser.parse_args(
            ["tmdb-groups", "--ani-json", "ani.json"]
        )
        self.assertIs(groups_args.func, ani_rss.command_tmdb_groups)
        get_args = parser.parse_args(["get", "--id", "ani-1"])
        self.assertIs(get_args.func, ani_rss.command_get)
        set_args = parser.parse_args(
            ["set", "--ani-json", "ani.json", "--confirm-set", "--move-files"]
        )
        self.assertIs(set_args.func, ani_rss.command_set)
        self.assertTrue(set_args.move_files)


if __name__ == "__main__":
    unittest.main()
