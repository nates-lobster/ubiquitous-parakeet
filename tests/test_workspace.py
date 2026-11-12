import tempfile
import unittest
from pathlib import Path

from ubiquitous_parakeet.workspace import initialize_workspace


class WorkspaceTests(unittest.TestCase):
    def test_layout_and_existing_files_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "alzheimers_buddy"
            first = initialize_workspace(root)
            self.assertTrue((root / "SOUL.md").is_file())
            self.assertTrue((root / "LIFE_STORY.md").is_file())
            self.assertTrue((root / "TODAY_LOG.md").is_file())
            self.assertTrue((root / "AGENTS.md").is_file())
            self.assertTrue((root / "plugins" / "hardware_mcp.py").is_file())
            self.assertTrue((root / "plugins" / "tts_piper.py").is_file())
            self.assertTrue((root / "data" / "companion.db").is_file())
            self.assertIn("LIFE_STORY.md", first["created"])
            self.assertIn("AGENTS.md", first["created"])

            story = root / "LIFE_STORY.md"
            story.write_text("caregiver content\n", encoding="utf-8")
            second = initialize_workspace(root)
            self.assertEqual(story.read_text(encoding="utf-8"), "caregiver content\n")
            self.assertNotIn("LIFE_STORY.md", second["created"])


if __name__ == "__main__":
    unittest.main()
