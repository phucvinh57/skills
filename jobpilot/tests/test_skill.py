"""The skill folder meets the Agent Skills format (tests/validate_skill.py)."""
import io
import unittest
from contextlib import redirect_stdout

from tests.support import SKILL
from tests.validate_skill import main


class SkillFormatTest(unittest.TestCase):
    def test_skill_folder_is_valid(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = main(SKILL)
        self.assertEqual(code, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
