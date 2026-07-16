from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from gem_filter.application.html_backfill import parse_telegram_export


class HtmlBackfillTests(unittest.TestCase):
    def test_telegram_export_message_and_reply(self) -> None:
        content = """
        <div class="message default clearfix" id="message100">
          <div class="body"><div class="pull_right date details"
            title="2026-07-08T02:23:48+07:00"></div>
          <div class="text">First<br>line</div></div>
        </div>
        <div class="message default clearfix" id="message101">
          <div class="body"><div class="pull_right date details"
            title="2026-07-08T02:24:48+07:00"></div>
          <div class="reply_to details"><a href="#go_to_message100">reply</a></div>
          <div class="text">Whale event</div></div>
        </div>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "messages.html"
            path.write_text(content, encoding="utf-8")
            result = parse_telegram_export(path)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].message_id, 100)
        self.assertEqual(result[0].text, "First\nline")
        self.assertEqual(result[1].reply_to_message_id, 100)


if __name__ == "__main__":
    unittest.main()
