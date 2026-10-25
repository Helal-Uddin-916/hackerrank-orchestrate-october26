"""Tests for code/audit/readers/log_lines.py: every real log line parses, each style is understood.

Run from the repo root:
    python3 -m unittest discover -s code/tests -v
"""
import collections
import glob
import os
import sys
import unittest

CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, CODE_DIR)
from audit.readers.log_lines import parse_key_values, parse_line  # noqa: E402
from audit.sighting import parse_time  # noqa: E402

LOG_DIR = os.path.join(os.path.dirname(CODE_DIR), "dataset", "evidence", "logs")


class AllRealLogLines(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.lines = {}
        for path in sorted(glob.glob(os.path.join(LOG_DIR, "*.log"))):
            with open(path, encoding="utf-8") as f:
                cls.lines[os.path.basename(path)] = [line for line in f.read().splitlines() if line.strip()]

    def test_26_files(self):
        self.assertEqual(len(self.lines), 26)

    def test_every_line_parses_with_time_service_and_event(self):
        failures = collections.Counter()
        for name, lines in self.lines.items():
            for line in lines:
                parsed = parse_line(line)
                if parsed is None:
                    failures[(name, "unparsed")] += 1
                elif not parsed.event:
                    failures[(name, "no event")] += 1
                elif not parsed.service:
                    failures[(name, "no service")] += 1
        self.assertEqual(dict(failures), {})

    def test_each_file_uses_one_style(self):
        for name, lines in self.lines.items():
            styles = {parse_line(line).style for line in lines}
            self.assertEqual(len(styles), 1, f"{name}: {styles}")

    def test_all_six_styles_occur(self):
        styles = {parse_line(lines[0]).style for lines in self.lines.values()}
        self.assertEqual(styles, {"json", "bracket", "pipe", "syslog", "key_value", "level_key_value"})


class EachStyle(unittest.TestCase):

    def test_json(self):
        p = parse_line('2026-04-19T06:51:13Z {"severity":"DEBUG","service":"search_service","event":"index_query_started","attempt":"1"}')
        self.assertEqual((p.style, p.level, p.service, p.event, p.fields["attempt"]),
                         ("json", "DEBUG", "search_service", "index_query_started", "1"))

    def test_bracketed(self):
        p = parse_line('2026-07-28T05:51:20Z [DEBUG] [account_service] [tr-6074163] cache_lookup: instance="account-5" result="miss"')
        self.assertEqual((p.style, p.level, p.service, p.event, p.fields["result"], p.fields["trace_id"]),
                         ("bracket", "DEBUG", "account_service", "cache_lookup", "miss", "tr-6074163"))

    def test_pipe(self):
        p = parse_line('2026-07-03T07:54:16Z | INFO | export_service | tr-415 | export_download_check_completed | resource_id="export_5351" available="true"')
        self.assertEqual((p.style, p.service, p.event, p.fields["resource_id"], p.fields["available"]),
                         ("pipe", "export_service", "export_download_check_completed", "export_5351", "true"))

    def test_syslog(self):
        p = parse_line('2026-08-22T10:51:17Z <15>1 node-1 account_service tr-497 - - level=DEBUG resource_id="account_1785" event="profile_lookup_completed" http_status="200"')
        self.assertEqual((p.style, p.level, p.service, p.event, p.fields["http_status"]),
                         ("syslog", "DEBUG", "account_service", "profile_lookup_completed", "200"))

    def test_level_then_key_values(self):
        p = parse_line('2026-03-15T07:00:00Z INFO service=downstream_delivery_service event=record_lookup seq=1 result="NOT_FOUND"')
        self.assertEqual((p.style, p.level, p.service, p.event, p.fields["result"]),
                         ("level_key_value", "INFO", "downstream_delivery_service", "record_lookup", "NOT_FOUND"))

    def test_key_values_inside_a_quoted_message(self):
        line = ('2026-06-08T06:52:09Z requestId=tr-1 level=INFO function=search_service message="instance=\\"search-3\\" '
                'resource_id=\\"search_021\\" event=\\"index_query_completed\\" result=\\"hit\\""')
        p = parse_line(line)
        self.assertEqual((p.style, p.level, p.service, p.event, p.fields["resource_id"], p.fields["result"]),
                         ("key_value", "INFO", "search_service", "index_query_completed", "search_021", "hit"))

    def test_time_is_parsed(self):
        self.assertEqual(parse_line("2026-09-17T22:00:00Z level=DEBUG service=x event=y").time,
                         parse_time("2026-09-17T22:00:00Z"))

    def test_unknown_lines_return_none(self):
        self.assertIsNone(parse_line("no time at the start"))
        self.assertIsNone(parse_line("2026-09-17T22:00:00Z"))
        self.assertIsNone(parse_line("2026-09-17T22:00:00Z {not json"))

    def test_quoted_values_keep_spaces_and_escapes(self):
        self.assertEqual(parse_key_values('a="one two" b=3 c="say \\"hi\\""'), {"a": "one two", "b": "3", "c": 'say "hi"'})


if __name__ == "__main__":
    unittest.main()
