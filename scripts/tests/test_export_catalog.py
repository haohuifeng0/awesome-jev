from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
EXPORTER = REPO_ROOT / "scripts" / "export-catalog.py"


class ExportCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        (self.root / "categories").mkdir()
        (self.root / "tags.json").write_text(
            (REPO_ROOT / "tags.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def write_category(self, name: str, body: str) -> Path:
        path = self.root / "categories" / name
        path.write_text(body, encoding="utf-8")
        return path

    def run_exporter(self, *categories: str, check: bool = False) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(EXPORTER),
            "--root",
            str(self.root),
            "--output",
            str(self.root / "catalog.json"),
        ]
        for category in categories:
            command.extend(("--category", category))
        if check:
            command.append("--check")
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_exports_deterministic_catalog_with_provenance(self) -> None:
        self.write_category(
            "routing.md",
            "# Routing\n\n## Entries\n\n"
            "- [Beta Tool](https://github.com/Owner/Beta.git) `{agent: pi, type: cli}` - Beta summary.\n"
            "- [Alpha Tool](https://example.com/Alpha/) - Alpha summary.\n",
        )

        first = self.run_exporter("routing.md")
        self.assertEqual(first.returncode, 0, first.stderr)
        first_bytes = (self.root / "catalog.json").read_bytes()
        second = self.run_exporter("routing.md")

        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((self.root / "catalog.json").read_bytes(), first_bytes)
        catalog = json.loads(first_bytes)
        self.assertEqual(catalog["schemaVersion"], 1)
        self.assertEqual(
            [project["slug"] for project in catalog["projects"]],
            ["alpha-tool", "owner-beta"],
        )
        beta = catalog["projects"][1]
        self.assertEqual(beta["id"], "https://github.com/owner/beta")
        self.assertEqual(beta["category"], {"key": "routing", "title": "Routing"})
        self.assertEqual(beta["summary"], "Beta summary.")
        self.assertEqual(beta["tags"], {"agent": "pi", "type": "cli"})
        self.assertEqual(beta["github"], {"owner": "Owner", "repo": "Beta"})
        self.assertEqual(beta["source"], {"file": "categories/routing.md", "line": 5})

    def test_check_rejects_drift_without_rewriting_catalog(self) -> None:
        self.write_category("routing.md", "# Routing\n\n## Entries\n\n- [One](https://one.example) - One.\n")
        output = self.root / "catalog.json"
        output.write_text("stale\n", encoding="utf-8")

        result = self.run_exporter("routing.md", check=True)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("catalog.json is out of date", result.stderr)
        self.assertEqual(output.read_text(encoding="utf-8"), "stale\n")

    def test_rejects_malformed_entry_with_file_and_line(self) -> None:
        self.write_category("routing.md", "# Routing\n\n## Entries\n\n- [Broken] (https://bad.example) - Bad.\n")

        result = self.run_exporter("routing.md")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("categories/routing.md:5: malformed entry", result.stderr)

    def test_rejects_non_numeric_port_with_concise_file_and_line_diagnostic(self) -> None:
        self.write_category(
            "routing.md",
            "# Routing\n\n## Entries\n\n"
            "- [Broken](https://example.com:notaport/path) - Bad.\n",
        )

        result = self.run_exporter("routing.md")

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "categories/routing.md:5: invalid URL: "
            "Port could not be cast to integer value as 'notaport'\n",
        )

    def test_rejects_sibling_malformed_hosts_and_ports_without_traceback(self) -> None:
        cases = (
            ("https://example.com:70000/path", "Port out of range 0-65535"),
            ("https://[::1/path", "Invalid IPv6 URL"),
        )
        for url, diagnostic in cases:
            with self.subTest(url=url):
                self.write_category(
                    "routing.md",
                    f"# Routing\n\n## Entries\n\n- [Broken]({url}) - Bad.\n",
                )

                result = self.run_exporter("routing.md")

                self.assertEqual(result.returncode, 1)
                self.assertEqual(
                    result.stderr,
                    f"categories/routing.md:5: invalid URL: {diagnostic}\n",
                )
                self.assertNotIn("Traceback", result.stderr)

    def test_rejects_duplicate_normalized_url_with_both_locations(self) -> None:
        self.write_category("one.md", "# One\n\n## Entries\n\n- [First](HTTPS://EXAMPLE.COM/tool/) - First.\n")
        self.write_category("two.md", "# Two\n\n## Entries\n\n- [Second](https://example.com/tool) - Second.\n")

        result = self.run_exporter("one.md", "two.md")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("categories/two.md:5: duplicate normalized URL", result.stderr)
        self.assertIn("categories/one.md:5", result.stderr)

    def test_rejects_unknown_tag_with_file_and_line(self) -> None:
        self.write_category(
            "routing.md",
            "# Routing\n\n## Entries\n\n- [One](https://one.example) `{agent: mystery}` - One.\n",
        )

        result = self.run_exporter("routing.md")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("categories/routing.md:5: unknown tag(s): agent: mystery", result.stderr)

    def test_rejects_slug_collision_with_both_locations(self) -> None:
        self.write_category(
            "routing.md",
            "# Routing\n\n## Entries\n\n"
            "- [Same Name](https://one.example) - One.\n"
            "- [Same-Name](https://two.example) - Two.\n",
        )

        result = self.run_exporter("routing.md")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("categories/routing.md:6: slug collision", result.stderr)
        self.assertIn("categories/routing.md:5", result.stderr)


if __name__ == "__main__":
    unittest.main()
