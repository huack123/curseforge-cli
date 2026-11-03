import hashlib
import io
import json
import random
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest import mock

import cf


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def ref_fingerprint(data):
    """Straight port of CurseForge's C++ loop, used to cross-check cf_fingerprint."""
    m = 1540483477
    clean = [b for b in data if b not in (9, 10, 13, 32)]
    h = (1 ^ len(clean)) & 0xFFFFFFFF
    acc = bits = 0
    for b in clean:
        acc |= b << bits
        bits += 8
        if bits == 32:
            k = (acc * m) & 0xFFFFFFFF
            k = ((k ^ (k >> 24)) * m) & 0xFFFFFFFF
            h = ((h * m) & 0xFFFFFFFF) ^ k
            acc = bits = 0
    if bits:
        h = ((h ^ acc) * m) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * m) & 0xFFFFFFFF
    return (h ^ (h >> 15)) & 0xFFFFFFFF


class TestCf(unittest.TestCase):
    def run_cli(self, argv, *payloads):
        reqs = []

        def fake_urlopen(req, timeout=0):
            reqs.append(req)
            return FakeResponse(json.dumps(payloads[min(len(reqs) - 1, len(payloads) - 1)]).encode())

        with mock.patch("urllib.request.urlopen", fake_urlopen), \
                mock.patch.dict("os.environ", {"CURSEFORGE_API_KEY": "k"}), \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            cf.main(argv)
        return reqs

    def test_search_builds_query(self):
        reqs = self.run_cli(
            ["search", "jei", "--loader", "fabric", "--version", "1.21.1", "--limit", "10"],
            {"data": [], "pagination": {"totalCount": 0}},
        )
        url = reqs[0].full_url
        self.assertIn("gameId=432", url)
        self.assertIn("searchFilter=jei", url)
        self.assertIn("modLoaderType=4", url)
        self.assertIn("gameVersion=1.21.1", url)
        self.assertIn("pageSize=10", url)

    def test_mod_by_slug_resolves_id(self):
        reqs = self.run_cli(
            ["mod", "jei"],
            {"data": [{"id": 238222}]},
            {"data": {"id": 238222, "name": "JEI", "authors": [], "categories": [], "links": {}}},
        )
        self.assertIn("/mods/search?", reqs[0].full_url)
        self.assertIn("slug=jei", reqs[0].full_url)
        self.assertIn("/mods/238222", reqs[1].full_url)

    def test_bulk_mods_posts_ids(self):
        reqs = self.run_cli(["mods", "1", "2"], {"data": []})
        self.assertEqual(reqs[0].get_method(), "POST")
        self.assertEqual(json.loads(reqs[0].data), {"modIds": [1, 2]})

    def test_featured_posts_body(self):
        reqs = self.run_cli(
            ["featured", "--version-type", "5", "--exclude", "1", "2"],
            {"data": {}},
        )
        self.assertEqual(reqs[0].get_method(), "POST")
        self.assertEqual(json.loads(reqs[0].data),
                         {"gameId": 432, "excludedModIds": [1, 2], "gameVersionTypeId": 5})

    def test_install_resolves_required_deps(self):
        payloads = [
            {"data": [{"id": 1}]},
            {"data": [{"id": 10, "modId": 1, "fileName": "a.jar", "displayName": "A",
                       "dependencies": [{"modId": 2, "relationType": 3}]}]},
            {"data": "https://example.test/a.jar"},
            {"data": [{"id": 20, "modId": 2, "fileName": "b.jar", "displayName": "B", "dependencies": []}]},
            {"data": "https://example.test/b.jar"},
        ]
        def fake_download(req, dest):
            Path(dest).write_bytes(b"jar")

        with tempfile.TemporaryDirectory() as tmp, mock.patch("urllib.request.urlretrieve", side_effect=fake_download) as download:
            self.run_cli(["install", "foo", "--dir", tmp], *payloads)
            self.assertEqual(download.call_count, 2)
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["a.jar", "b.jar"])

    def test_auth_preserves_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({"profiles": {"x": {}}, "active_profile": "x"}))
            with mock.patch.object(cf, "CONFIG", config):
                self.run_cli(["auth", "newkey"])
            data = json.loads(config.read_text())
            self.assertEqual(data["api_key"], "newkey")
            self.assertIn("x", data["profiles"])

    def test_profile_create_add_list_remove(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            with mock.patch.object(cf, "CONFIG", config):
                self.run_cli(["profile", "create", "test", "--version", "1.21.1",
                              "--loader", "fabric", "--dir", tmp])
                data = json.loads(config.read_text())
                self.assertEqual(data["active_profile"], "test")
                self.assertEqual(data["profiles"]["test"]["loader"], "fabric")

                self.run_cli(
                    ["add", "238222"],
                    {"data": {"id": 238222, "name": "JEI", "slug": "jei"}},
                    {"data": [{"id": 1, "modId": 238222, "fileName": "jei.jar", "displayName": "JEI"}]},
                )
                data = json.loads(config.read_text())
                self.assertEqual([m["id"] for m in data["profiles"]["test"]["mods"]], [238222])

                self.run_cli(["list"])

                self.run_cli(["remove", "238222"])
                data = json.loads(config.read_text())
                self.assertEqual(data["profiles"]["test"]["mods"], [])

    def test_upgrade_downloads_missing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(json.dumps({
                "active_profile": "test",
                "profiles": {"test": {"game": 432, "version": "1.21.1", "loader": "fabric",
                                      "dir": tmp, "mods": [{"id": 1, "name": "A", "slug": "a"}]}},
            }))
            payloads = [
                {"data": [{"id": 10, "modId": 1, "fileName": "a.jar", "displayName": "A",
                           "dependencies": []}]},
                {"data": "https://example.test/a.jar"},
            ]

            def fake_download(req, dest):
                Path(dest).write_bytes(b"jar")

            with mock.patch.object(cf, "CONFIG", config), \
                    mock.patch("urllib.request.urlretrieve", side_effect=fake_download):
                self.run_cli(["upgrade"], *payloads)
            self.assertTrue((Path(tmp) / "a.jar").exists())

    def test_retry_on_429(self):
        calls = []

        def flaky(req, timeout=0):
            calls.append(req)
            if len(calls) < 3:
                raise urllib.error.HTTPError(req.full_url, 429, "slow down", None, io.BytesIO(b""))
            return FakeResponse(b'{"data": []}')

        with mock.patch("urllib.request.urlopen", flaky), \
                mock.patch.dict("os.environ", {"CURSEFORGE_API_KEY": "k"}), \
                mock.patch("time.sleep") as sleep, \
                mock.patch("sys.stdout", new_callable=io.StringIO):
            cf.main(["games"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(sleep.call_count, 2)

    def test_download_checksum_mismatch(self):
        payloads = [
            {"data": {"id": 10, "modId": 1, "fileName": "a.jar", "displayName": "A",
                      "hashes": [{"algo": 1, "value": hashlib.sha1(b"good").hexdigest()}]}},
            {"data": "https://example.test/a.jar"},
        ]

        def fake_download(req, dest):
            Path(dest).write_bytes(b"bad")

        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch("urllib.request.urlretrieve", side_effect=fake_download):
            with self.assertRaises(SystemExit):
                self.run_cli(["download", "1", "10", "--out", tmp], *payloads)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_install_dry_run_writes_nothing(self):
        payloads = [{"data": [{"id": 10, "modId": 1, "fileName": "a.jar",
                               "displayName": "A", "dependencies": []}]}]
        with tempfile.TemporaryDirectory() as tmp, mock.patch("urllib.request.urlretrieve") as download:
            self.run_cli(["install", "1", "--dir", tmp, "--dry-run"], *payloads)
            download.assert_not_called()
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_import_extracts_mods_and_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            pack = Path(tmp) / "pack.zip"
            manifest = {"name": "TestPack", "version": "1.0",
                        "minecraft": {"version": "1.21.1", "modLoaders": [{"id": "fabric-0.16.0"}]},
                        "files": [{"projectID": 1, "fileID": 10}]}
            with zipfile.ZipFile(pack, "w") as z:
                z.writestr("manifest.json", json.dumps(manifest))
                z.writestr("overrides/config/x.txt", "hi")
            payloads = [
                {"data": {"id": 10, "modId": 1, "fileName": "a.jar", "displayName": "A"}},
                {"data": "https://example.test/a.jar"},
            ]

            def fake_download(req, dest):
                Path(dest).write_bytes(b"jar")

            out = Path(tmp) / "out"
            with mock.patch("urllib.request.urlretrieve", side_effect=fake_download):
                self.run_cli(["import", str(pack), "--dir", str(out)], *payloads)
            self.assertEqual((out / "mods" / "a.jar").read_bytes(), b"jar")
            self.assertEqual((out / "config" / "x.txt").read_text(), "hi")

    def test_fingerprint_matches_reference(self):
        rng = random.Random(42)
        samples = [b"", b"abc", bytes(range(256)), bytes(rng.randrange(256) for _ in range(4096))]
        for data in samples:
            self.assertEqual(cf.cf_fingerprint(data), ref_fingerprint(data))

    def test_fingerprint_ignores_whitespace(self):
        self.assertEqual(cf.cf_fingerprint(b"a b\tc\r\nd"), cf.cf_fingerprint(b"abcd"))

    def test_human(self):
        self.assertEqual(cf.human(0), "0")
        self.assertEqual(cf.human(1234), "1.2K")
        self.assertEqual(cf.human(2_500_000), "2.5M")

    def test_table_renders_rows(self):
        buf = io.StringIO()
        with mock.patch("sys.stdout", buf):
            cf.table(["A", "B"], [[1, "x"]])
        self.assertIn("A  B", buf.getvalue())
        self.assertIn("1  x", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
