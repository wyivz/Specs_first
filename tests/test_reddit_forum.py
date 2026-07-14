from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from collectors.credentials import RedditCredentials, load_reddit_credentials, request_headers_for_url
from collectors.sources.forum import ForumSourceCollector
from schemas.category_profile import forum_search_queries


class ForumSearchQueriesTest(unittest.TestCase):
    def test_reddit_excluded_by_default(self) -> None:
        queries = forum_search_queries("Sony 50mm")
        platforms = [platform for platform, _ in queries]
        self.assertEqual(platforms, ["Chiphell"])

    def test_reddit_included_when_enabled(self) -> None:
        queries = forum_search_queries("Sony 50mm", include_reddit=True)
        platforms = [platform for platform, _ in queries]
        self.assertEqual(platforms, ["Chiphell", "Reddit"])


class RedditCredentialsTest(unittest.TestCase):
    def test_reddit_credentials_headers(self) -> None:
        creds = RedditCredentials(cookie="reddit_session=abc; token_v2=xyz")
        self.assertTrue(creds.configured)
        self.assertIn("Cookie", creds.request_headers())

    @patch("collectors.credentials.load_reddit_credentials")
    def test_request_headers_for_reddit(self, load_reddit) -> None:
        load_reddit.return_value = RedditCredentials(cookie="reddit_session=abc")
        headers = request_headers_for_url("https://www.reddit.com/r/SonyAlpha/comments/abc/")
        self.assertIn("Cookie", headers)


class ForumSourceCollectorRedditTest(unittest.TestCase):
    def test_skips_reddit_search_without_cookie(self) -> None:
        http = MagicMock()
        http.search.return_value = []
        collector = ForumSourceCollector(http)
        with patch("collectors.sources.forum.load_reddit_credentials") as load_reddit:
            load_reddit.return_value = RedditCredentials()
            collector.collect(
                type("Candidate", (), {"sku": "Sony 50mm"})(),  # type: ignore[arg-type]
            )
        search_queries = [call.args[0] for call in http.search.call_args_list]
        self.assertTrue(all("reddit.com" not in query for query in search_queries))

    def test_includes_reddit_search_with_cookie(self) -> None:
        http = MagicMock()
        http.search.return_value = []
        collector = ForumSourceCollector(http)
        with patch("collectors.sources.forum.load_reddit_credentials") as load_reddit:
            load_reddit.return_value = RedditCredentials(cookie="reddit_session=abc")
            collector.collect(
                type("Candidate", (), {"sku": "Sony 50mm"})(),  # type: ignore[arg-type]
            )
        search_queries = [call.args[0] for call in http.search.call_args_list]
        self.assertTrue(any("reddit.com" in query for query in search_queries))

    def test_load_reddit_credentials_from_settings(self) -> None:
        # load_reddit_credentials reloads .env then reads os.environ — not the
        # frozen Settings snapshot — so Cookie edits apply without restart.
        with patch("collectors.settings.reload_credential_env"):
            with patch.dict(os.environ, {"REDDIT_COOKIE": "reddit_session=from_env"}):
                creds = load_reddit_credentials()
        self.assertTrue(creds.configured)
        self.assertIn("reddit_session=from_env", creds.cookie)

    def test_reddit_http_usable_page_skips_browser(self) -> None:
        from collectors.browser import BrowserCapture
        from collectors.http import FetchResult
        from collectors.resilient_fetch import ResilientFetcher
        from collectors.site_strategy import strategy_for_url

        self.assertEqual(strategy_for_url("https://www.reddit.com/r/x/comments/1/").mode, "http_first")

        class _Http:
            def fetch(self, url, *, platform="", extra_headers=None):
                body = (
                    "<html><body><article>"
                    + ("quality control sticky ring sample variation. " * 8)
                    + "</article></body></html>"
                )
                return FetchResult(url=url, status=200, text=body, content_type="text/html")

        class _Browser:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def capture_page_slices(self, url, task_id="manual", storage_state_path=None):
                self.calls.append(url)
                return BrowserCapture(url=url, screenshot_paths=[], page_text="browser", page_html="<html></html>")

        browser = _Browser()
        fetcher = ResilientFetcher(_Http(), browser=browser)  # type: ignore[arg-type]
        snapshot = fetcher.fetch("https://www.reddit.com/r/SonyAlpha/comments/abc/")
        self.assertEqual(snapshot.method, "http")
        self.assertEqual(browser.calls, [])


if __name__ == "__main__":
    unittest.main()
