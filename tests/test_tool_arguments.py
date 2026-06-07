from __future__ import annotations

import unittest

from hermes_cgm_agent.services.tools.arguments import (
    optional_bool,
    optional_int,
    parse_limit,
    require_bool,
    require_enum,
    require_int,
)


class ToolArgumentTests(unittest.TestCase):
    def test_require_bool_rejects_truthy_strings(self) -> None:
        with self.assertRaisesRegex(ValueError, "confirmed must be a boolean"):
            require_bool("false", "confirmed")

    def test_optional_bool_uses_default_only_when_missing(self) -> None:
        self.assertFalse(optional_bool(None, "retrieve_context", default=False))
        self.assertTrue(optional_bool(True, "retrieve_context", default=False))

    def test_require_int_rejects_strings_and_bools(self) -> None:
        with self.assertRaisesRegex(ValueError, "top_k must be an integer"):
            require_int("2", "top_k", minimum=1, maximum=20)
        with self.assertRaisesRegex(ValueError, "top_k must be an integer"):
            require_int(True, "top_k", minimum=1, maximum=20)

    def test_optional_int_enforces_range(self) -> None:
        self.assertEqual(optional_int(None, "days", default=7, minimum=1, maximum=90), 7)
        with self.assertRaisesRegex(ValueError, "days must be between 1 and 90"):
            optional_int(91, "days", default=7, minimum=1, maximum=90)

    def test_parse_limit_uses_shared_limit_range(self) -> None:
        self.assertIsNone(parse_limit(None))
        self.assertEqual(parse_limit(10), 10)
        with self.assertRaisesRegex(ValueError, "limit must be between 1 and 10000"):
            parse_limit(10001)

    def test_require_enum_is_exact(self) -> None:
        self.assertEqual(require_enum("L1", "layer", ("L1", "L2")), "L1")
        with self.assertRaisesRegex(ValueError, "layer must be one of: L1, L2"):
            require_enum("l1", "layer", ("L1", "L2"))


if __name__ == "__main__":
    unittest.main()
