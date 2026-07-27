from __future__ import annotations

import unittest
from unittest.mock import patch


class WebScrapingTests(unittest.TestCase):
    def test_scrape_page_extracts_bounded_visible_main_text_and_links(self) -> None:
        from erga_mcp.web_scraping import scrape_page

        html = """
        <html><head><title>Example research</title><script>ignore_me()</script></head>
        <body><nav>Navigation</nav><main><h1>Useful findings</h1>
        <p>Evidence-backed public research belongs here.</p>
        <a href="/report">Read the report</a></main>
        <footer>Footer text</footer></body></html>
        """
        with patch("erga_mcp.web_scraping.fetch_public_page", return_value=html):
            result = scrape_page("https://example.com/research", max_characters=500, max_links=5)

        self.assertEqual(result.url, "https://example.com/research")
        self.assertEqual(result.title, "Example research")
        self.assertIn("Useful findings", result.text)
        self.assertIn("Evidence-backed public research belongs here.", result.text)
        self.assertNotIn("Navigation", result.text)
        self.assertNotIn("Footer text", result.text)
        self.assertEqual(result.links, ("https://example.com/report",))
        self.assertTrue(result.untrusted)

    def test_extract_page_returns_only_selected_text(self) -> None:
        from erga_mcp.web_scraping import extract_page

        html = """
        <html><body><main><section class="fact"><p>Primary fact</p></section>
        <section class="other"><p>Ignore this</p></section></main></body></html>
        """
        with patch("erga_mcp.web_scraping.fetch_public_page", return_value=html):
            result = extract_page(
                "https://example.com/research", css_selector=".fact", max_characters=200
            )

        self.assertEqual(result, "Primary fact")


if __name__ == "__main__":
    unittest.main()
