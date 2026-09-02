"""Unit-Tests für das Anhängen von Zulip-Uploads an Redmine-Issues."""

import unittest
from unittest import mock

from zulip_bots.bots.redmine.redmine import (
    DEFAULT_MAX_ATTACHMENT_MB,
    AttachmentTooLargeError,
    RedmineHandler,
)


def make_handler(max_attachment_mb=95):
    """Handler ohne initialize(), damit keine Netzverbindung nötig ist."""
    handler = RedmineHandler()
    handler.max_attachment_mb = max_attachment_mb
    handler.debug = False
    handler.redmine = mock.Mock()
    handler.zulipclient = mock.Mock(
        base_url="https://zulip.example.com/api/",
        email="bot@example.com",
        api_key="secret",
    )
    return handler


class TestCollectAttachmentLinks(unittest.TestCase):
    def test_collects_links_from_all_messages_in_order(self):
        handler = make_handler()
        messages = [
            {"content": "Hier der Fehler [shot.png](/user_uploads/2/ab/x/shot.png)"},
            {"content": "und ein Video [clip.mp4](/user_uploads/2/cd/y/clip.mp4)"},
            {"content": "@**Issuebot** create"},
        ]

        links = handler.collect_attachment_links(messages)

        self.assertEqual(
            links,
            [
                {"filename": "shot.png", "url": "/user_uploads/2/ab/x/shot.png"},
                {"filename": "clip.mp4", "url": "/user_uploads/2/cd/y/clip.mp4"},
            ],
        )

    def test_includes_attachment_of_the_create_message(self):
        handler = make_handler()
        messages = [
            {"content": "@**Issuebot** create\n[log.txt](/user_uploads/2/ef/z/log.txt)"}
        ]

        links = handler.collect_attachment_links(messages)

        self.assertEqual([link["filename"] for link in links], ["log.txt"])

    def test_deduplicates_repeated_links(self):
        handler = make_handler()
        messages = [
            {"content": "[a.png](/user_uploads/2/ab/x/a.png)"},
            {"content": "nochmal [a.png](/user_uploads/2/ab/x/a.png)"},
        ]

        self.assertEqual(len(handler.collect_attachment_links(messages)), 1)

    def test_matches_absolute_upload_urls(self):
        handler = make_handler()
        messages = [
            {"content": "[a.png](https://zulip.example.com/user_uploads/2/ab/x/a.png)"}
        ]

        links = handler.collect_attachment_links(messages)

        self.assertEqual(
            links[0]["url"], "https://zulip.example.com/user_uploads/2/ab/x/a.png"
        )

    def test_ignores_ordinary_links(self):
        handler = make_handler()
        messages = [{"content": "siehe [docs](https://example.com/handbuch)"}]

        self.assertEqual(handler.collect_attachment_links(messages), [])

    def test_falls_back_to_path_segment_when_link_text_is_empty(self):
        handler = make_handler()
        messages = [{"content": "[](/user_uploads/2/ab/x/mein%20bild.png)"}]

        self.assertEqual(
            handler.collect_attachment_links(messages)[0]["filename"], "mein bild.png"
        )

    def test_handles_missing_and_empty_messages(self):
        handler = make_handler()

        self.assertEqual(handler.collect_attachment_links(None), [])
        self.assertEqual(handler.collect_attachment_links([{"content": None}]), [])


class TestBuildUploads(unittest.TestCase):
    def test_returns_redmine_token_per_file(self):
        handler = make_handler()
        handler.download_zulip_file = mock.Mock(return_value=(b"data", "image/png"))
        handler.redmine.upload.return_value = {"token": "tok1"}

        uploads, skipped = handler.build_uploads(
            [{"filename": "a.png", "url": "/user_uploads/2/ab/x/a.png"}]
        )

        self.assertEqual(
            uploads,
            [{"token": "tok1", "filename": "a.png", "content_type": "image/png"}],
        )
        self.assertEqual(skipped, [])

    def test_guesses_content_type_when_server_sends_none(self):
        handler = make_handler()
        handler.download_zulip_file = mock.Mock(return_value=(b"data", None))
        handler.redmine.upload.return_value = {"token": "tok1"}

        uploads, _ = handler.build_uploads([{"filename": "a.png", "url": "/u"}])

        self.assertEqual(uploads[0]["content_type"], "image/png")

    def test_oversized_file_is_skipped_but_others_survive(self):
        handler = make_handler(max_attachment_mb=95)
        handler.download_zulip_file = mock.Mock(
            side_effect=[AttachmentTooLargeError("999"), (b"data", "image/png")]
        )
        handler.redmine.upload.return_value = {"token": "tok2"}

        uploads, skipped = handler.build_uploads(
            [
                {"filename": "big.mp4", "url": "/user_uploads/2/ab/x/big.mp4"},
                {"filename": "a.png", "url": "/user_uploads/2/cd/y/a.png"},
            ]
        )

        self.assertEqual([u["filename"] for u in uploads], ["a.png"])
        self.assertEqual(skipped[0]["filename"], "big.mp4")
        self.assertEqual(skipped[0]["reason"], "größer als 95 MB")

    def test_download_error_is_skipped_not_fatal(self):
        handler = make_handler()
        handler.download_zulip_file = mock.Mock(side_effect=OSError("boom"))

        uploads, skipped = handler.build_uploads([{"filename": "a.png", "url": "/u"}])

        self.assertEqual(uploads, [])
        self.assertEqual(skipped[0]["reason"], "Download aus Zulip fehlgeschlagen")

    def test_redmine_upload_error_is_skipped_not_fatal(self):
        handler = make_handler()
        handler.download_zulip_file = mock.Mock(return_value=(b"data", "image/png"))
        handler.redmine.upload.side_effect = Exception("redmine down")

        uploads, skipped = handler.build_uploads([{"filename": "a.png", "url": "/u"}])

        self.assertEqual(uploads, [])
        self.assertEqual(skipped[0]["reason"], "Upload nach Redmine fehlgeschlagen")

    def test_empty_file_is_skipped(self):
        handler = make_handler()
        handler.download_zulip_file = mock.Mock(return_value=(b"", None))

        uploads, skipped = handler.build_uploads([{"filename": "a.png", "url": "/u"}])

        self.assertEqual(uploads, [])
        self.assertEqual(skipped[0]["reason"], "Datei ist leer")
        handler.redmine.upload.assert_not_called()


class TestDownloadZulipFile(unittest.TestCase):
    def make_response(self, chunks, headers=None):
        response = mock.Mock()
        response.headers = headers or {}
        response.iter_content.return_value = iter(chunks)
        response.raise_for_status.return_value = None
        return response

    def test_downloads_with_basic_auth_against_site_root(self):
        handler = make_handler()
        response = self.make_response([b"abc"], {"Content-Type": "image/png"})

        with mock.patch("requests.get", return_value=response) as get:
            content, content_type = handler.download_zulip_file("/user_uploads/2/ab/x/a.png")

        self.assertEqual(content, b"abc")
        self.assertEqual(content_type, "image/png")
        # Site-Root ohne /api/, damit der Upload-Pfad stimmt.
        self.assertEqual(
            get.call_args[0][0], "https://zulip.example.com/user_uploads/2/ab/x/a.png"
        )
        auth = get.call_args[1]["auth"]
        self.assertEqual((auth.username, auth.password), ("bot@example.com", "secret"))

    def test_aborts_early_on_oversized_content_length(self):
        handler = make_handler(max_attachment_mb=1)
        response = self.make_response([], {"Content-Length": str(2 * 1024 * 1024)})

        with mock.patch("requests.get", return_value=response):
            with self.assertRaises(AttachmentTooLargeError):
                handler.download_zulip_file("/user_uploads/2/ab/x/big.mp4")

        response.iter_content.assert_not_called()

    def test_aborts_mid_stream_when_limit_is_exceeded(self):
        handler = make_handler(max_attachment_mb=1)
        chunk = b"x" * (512 * 1024)
        response = self.make_response([chunk, chunk, chunk])

        with mock.patch("requests.get", return_value=response):
            with self.assertRaises(AttachmentTooLargeError):
                handler.download_zulip_file("/user_uploads/2/ab/x/big.mp4")

    def test_keeps_absolute_urls_unchanged(self):
        handler = make_handler()
        response = self.make_response([b"abc"])

        with mock.patch("requests.get", return_value=response) as get:
            handler.download_zulip_file("https://other.example.com/user_uploads/2/a/b.png")

        self.assertEqual(
            get.call_args[0][0], "https://other.example.com/user_uploads/2/a/b.png"
        )


class TestAttachmentNoteAndSkippedSection(unittest.TestCase):
    def test_note_is_empty_without_attachments(self):
        self.assertEqual(RedmineHandler.format_attachment_note([], []), "")

    def test_note_omits_skipped_part_when_nothing_was_skipped(self):
        note = RedmineHandler.format_attachment_note([{"token": "t"}, {"token": "u"}], [])

        self.assertEqual(note, " (2 Anhänge übernommen)")

    def test_note_lists_both_counts(self):
        note = RedmineHandler.format_attachment_note([{"token": "t"}], [{"filename": "b"}])

        self.assertEqual(note, " (1 Anhang übernommen, 1 Anhang übersprungen)")

    def test_note_for_skipped_only(self):
        note = RedmineHandler.format_attachment_note([], [{"filename": "a"}, {"filename": "b"}])

        self.assertEqual(note, " (2 Anhänge übersprungen)")

    def test_skipped_section_links_absolute_urls(self):
        handler = make_handler()

        section = handler.format_skipped_attachments(
            [
                {
                    "filename": "big.mp4",
                    "url": "/user_uploads/2/ab/x/big.mp4",
                    "reason": "größer als 95 MB",
                }
            ]
        )

        self.assertIn("Nicht übernommene Anhänge:", section)
        self.assertIn(
            "* big.mp4 (größer als 95 MB): "
            "https://zulip.example.com/user_uploads/2/ab/x/big.mp4",
            section,
        )

    def test_skipped_section_is_empty_without_skips(self):
        self.assertEqual(make_handler().format_skipped_attachments([]), "")


class TestMaxAttachmentConfig(unittest.TestCase):
    def test_reads_configured_value(self):
        self.assertEqual(RedmineHandler.read_max_attachment_mb({"max_attachment_mb": "20"}), 20)

    def test_default_when_missing_or_blank(self):
        self.assertEqual(RedmineHandler.read_max_attachment_mb({}), DEFAULT_MAX_ATTACHMENT_MB)
        self.assertEqual(
            RedmineHandler.read_max_attachment_mb({"max_attachment_mb": "  "}),
            DEFAULT_MAX_ATTACHMENT_MB,
        )

    def test_default_on_invalid_values(self):
        self.assertEqual(
            RedmineHandler.read_max_attachment_mb({"max_attachment_mb": "viel"}),
            DEFAULT_MAX_ATTACHMENT_MB,
        )
        self.assertEqual(
            RedmineHandler.read_max_attachment_mb({"max_attachment_mb": "0"}),
            DEFAULT_MAX_ATTACHMENT_MB,
        )


if __name__ == "__main__":
    unittest.main()
