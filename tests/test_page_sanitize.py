from __future__ import annotations

import unittest

from collectors.page_sanitize import (
    detect_page_blockers,
    extract_readable_text,
    is_usable_page,
    sanitize_html,
)


class PageSanitizeTest(unittest.TestCase):
    def test_strips_nav_footer_and_css_noise(self) -> None:
        markup = """
        <html>
          <head><style>body{color:red;font-family:Arial;display:none}</style></head>
          <body>
            <nav class="site-menu">Home Products Login</nav>
            <main>
              <h1>Zeiss Makro-Planar 50mm f/2</h1>
              <p>Focal Length: 50mm. Maximum Aperture: f/2. Weight: 530g.</p>
            </main>
            <footer class="site-footer">Copyright 2026</footer>
            <script>window.__NUXT__ = {};</script>
          </body>
        </html>
        """
        page = sanitize_html("https://zeiss.example/specs", markup)
        self.assertIn("Focal Length: 50mm", page.rich_text)
        self.assertNotIn("site-menu", page.rich_text)
        self.assertNotIn("Copyright 2026", page.rich_text)
        self.assertNotIn("font-family", page.rich_text)

    def test_detects_captcha_blockers(self) -> None:
        markup = """
        <html><body>
          <div class="captcha-wrap">Please complete the security check</div>
          <div class="g-recaptcha"></div>
        </body></html>
        """
        blockers = detect_page_blockers("https://item.jd.com/123.html", markup, "security check")
        kinds = {blocker.kind for blocker in blockers}
        self.assertIn("auth_or_captcha", kinds)

    def test_extracts_json_ld_and_meta(self) -> None:
        markup = """
        <html>
          <head>
            <title>Product Page</title>
            <meta name="description" content="Compact 50mm macro lens" />
            <script type="application/ld+json">
              {"@type":"Product","name":"Zeiss 50mm","offers":{"price":"5999"}}
            </script>
          </head>
          <body><p>Minimum focus distance 0.24m.</p></body>
        </html>
        """
        page = sanitize_html("https://example.com/product", markup)
        self.assertEqual(page.meta_description, "Compact 50mm macro lens")
        self.assertEqual(page.json_ld[0]["name"], "Zeiss 50mm")
        self.assertIn("Minimum focus distance", page.rich_text)

    def test_is_usable_page_rejects_captcha_and_css_dump(self) -> None:
        captcha_page = sanitize_html(
            "https://item.jd.com/123.html",
            "<html><body><div>滑块验证</div></body></html>",
        )
        self.assertFalse(is_usable_page(captcha_page))

        css_dump = extract_readable_text(
            "@media screen { .foo { color: red; display:none; } } "
            "@keyframes spin { from { opacity: 0; } to { opacity: 1; } }"
        )
        page = sanitize_html("https://example.com/css", f"<html><body>{css_dump}</body></html>")
        self.assertFalse(is_usable_page(page))


if __name__ == "__main__":
    unittest.main()
