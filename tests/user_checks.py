import unittest
from unittest.mock import patch

from mangadex_downloader.chapter import Chapter
from mangadex_downloader.user import User


class UserChecks(unittest.TestCase):
    def test_unexpanded_chapter_uploader(self):
        data = {
            "id": "chapter-id",
            "attributes": {
                "translatedLanguage": "pt-br",
                "volume": None,
                "chapter": "1",
                "title": None,
            },
            "relationships": [
                {"id": "manga-id", "type": "manga",
                 "attributes": {"title": {"en": "Example"}}},
                {"id": "user-id", "type": "user"},
            ],
        }
        with patch("mangadex_downloader.user.get_user") as fetch:
            chapter = Chapter.from_data(data)
        fetch.assert_not_called()
        self.assertEqual(chapter.user.id, "user-id")
        self.assertEqual(chapter.groups_name, "User - user-id")
        self.assertEqual(chapter.user.roles, [])

    def test_missing_or_partial_attributes(self):
        for attributes in (None, {}, {"username": None}, {"roles": None}):
            with self.subTest(attributes=attributes):
                user = User(data={"id": "user-id", "attributes": attributes})
                self.assertEqual(user.name, "user-id")
                self.assertEqual(user.roles, [])

    def test_expanded_user_keeps_metadata(self):
        user = User(data={"id": "user-id", "attributes": {
            "username": "Uploader", "roles": ["member"],
        }})
        self.assertEqual(user.name, "Uploader")
        self.assertEqual(user.roles, ["member"])

    def test_explicit_user_lookup_still_fetches(self):
        with patch("mangadex_downloader.user.get_user", return_value={
            "data": {"id": "user-id", "attributes": {
                "username": "Uploader", "roles": ["member"],
            }},
        }) as fetch:
            user = User("user-id")
        fetch.assert_called_once_with("user-id")
        self.assertEqual(user.name, "Uploader")


if __name__ == "__main__":
    unittest.main()
