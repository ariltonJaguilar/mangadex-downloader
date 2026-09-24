import importlib.util
import sys
import unittest
from pathlib import Path


def load_models_module():
    module_path = Path(__file__).resolve().parents[1] / "src" / "core" / "models.py"
    spec = importlib.util.spec_from_file_location("models_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


models = load_models_module()


class SafePathComponentTests(unittest.TestCase):
    def test_manga_title_does_not_keep_trailing_space_after_truncation(self):
        title = (
            "The Poison King, Now that I've Gained Ultimate Power, "
            "the Bewitching Beauties in My Harem Can't Get Enough of Me"
        )

        safe_title = models.MangaInfo(title=title).get_safe_title()

        self.assertLessEqual(len(safe_title), 100)
        self.assertEqual(
            safe_title,
            "The Poison King_ Now that I_ve Gained Ultimate Power_ "
            "the Bewitching Beauties in My Harem Can_t Get",
        )
        self.assertFalse(safe_title.endswith((" ", ".")))

    def test_manga_title_uses_fallback_when_component_is_empty(self):
        self.assertEqual(models.MangaInfo(title="     ").get_safe_title(), "Unknown")

    def test_chapter_title_does_not_keep_trailing_space_after_truncation(self):
        chapter = models.Chapter(
            chapter_id=1,
            number="1.1",
            title="a" * 49 + " trailing text",
        )

        safe_folder_name = chapter.get_safe_folder_name()

        self.assertEqual(safe_folder_name, f"Chapter_1.1_{'a' * 49}")
        self.assertFalse(safe_folder_name.endswith((" ", ".")))

    def test_chapter_number_does_not_leave_trailing_period(self):
        chapter = models.Chapter(chapter_id=1, number="1.")

        self.assertEqual(chapter.get_safe_folder_name(), "Chapter_1")


if __name__ == "__main__":
    unittest.main()
