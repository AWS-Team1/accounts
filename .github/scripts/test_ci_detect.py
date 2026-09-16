#!/usr/bin/env python3
"""Unit tests for the accounts CI change mapping."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("ci_detect.py")
SPEC = importlib.util.spec_from_file_location("ci_detect", MODULE_PATH)
assert SPEC and SPEC.loader
ci_detect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ci_detect)


class DetectMappingTest(unittest.TestCase):
    def test_docs_only(self) -> None:
        self.assertEqual(ci_detect.classify(["README.md"]), ([], []))

    def test_service_source(self) -> None:
        self.assertEqual(
            ci_detect.classify(["src/accounts/contacts/contacts.py"]),
            (["contacts"], []),
        )

    def test_docker_input(self) -> None:
        self.assertEqual(
            ci_detect.classify(["src/accounts/userservice/uv.lock"]),
            (["userservice"], ["userservice"]),
        )

    def test_common_workflow(self) -> None:
        expected = (["contacts", "userservice"], ["contacts", "userservice"])
        self.assertEqual(
            ci_detect.classify([".github/workflows/build-and-push.yml"]), expected
        )

    def test_delete_uses_deleted_path(self) -> None:
        paths = ci_detect.paths_from_name_status(
            "D\tsrc/accounts/contacts/contacts.py\n"
        )
        self.assertEqual(ci_detect.classify(paths), (["contacts"], []))

    def test_rename_checks_old_and_new_paths(self) -> None:
        paths = ci_detect.paths_from_name_status(
            "R100\tsrc/accounts/contacts/a.py\tsrc/accounts/userservice/a.py\n"
        )
        self.assertEqual(
            ci_detect.classify(paths), (["contacts", "userservice"], [])
        )

    def test_duplicate_paths_are_canonical(self) -> None:
        self.assertEqual(
            ci_detect.classify(
                [
                    "src/accounts/userservice/userservice.py",
                    "src/accounts/contacts/contacts.py",
                    "src/accounts/userservice/userservice.py",
                ]
            ),
            (["contacts", "userservice"], []),
        )

    def test_unknown_path_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "unclassified paths"):
            ci_detect.classify(["new-unmapped-config.yaml"])


if __name__ == "__main__":
    unittest.main()
