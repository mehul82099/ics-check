import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

import ics_check as ic

HERE = os.path.dirname(os.path.abspath(__file__))


def crlf(s):
    return s.strip("\n").replace("\n", "\r\n") + "\r\n"


# RFC 5545 section 3.6.1 example (Bastille Day Party), as printed in the RFC.
RFC_EXAMPLE = crlf("""
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//hacksw/handcal//NONSGML v1.0//EN
BEGIN:VEVENT
UID:19970610T172345Z-AF23B2@example.com
DTSTAMP:19970610T172345Z
DTSTART:19970714T170000Z
DTEND:19970715T040000Z
SUMMARY:Bastille Day Party
END:VEVENT
END:VCALENDAR
""")


def ev(*lines, head=("VERSION:2.0", "PRODID:-//x//y//EN"), extra=()):
    base = ["BEGIN:VCALENDAR"] + list(head) + list(extra) + ["BEGIN:VEVENT"]
    base += list(lines) + ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(base) + "\r\n"


GOOD = ("UID:a@x", "DTSTAMP:20260101T000000Z", "DTSTART:20260102T100000Z")


def rules(text, sev=None):
    return [r for s, _, r, _ in ic.lint_text(text) if sev is None or s == sev]


class Parse(unittest.TestCase):
    def test_rfc_example_clean(self):
        self.assertEqual(ic.lint_text(RFC_EXAMPLE), [])

    def test_good_minimal(self):
        self.assertEqual(rules(ev(*GOOD)), [])

    def test_quoted_param_with_colon(self):
        n, p, v = ic.parse_line('ATTENDEE;CN="Doe: John";ROLE=CHAIR:mailto:j@x.org')
        self.assertEqual((n, p["CN"], v), ("ATTENDEE", ['Doe: John'], "mailto:j@x.org"))

    def test_param_multi_value(self):
        _, p, _ = ic.parse_line('X;MEMBER="a,b","c":v')
        self.assertEqual(p["MEMBER"], ["a,b", "c"])

    def test_name_case_insensitive(self):
        self.assertEqual(rules(ev("uid:a@x", "dtstamp:20260101T000000Z", "dtstart:20260102T100000Z")), [])

    def test_malformed_lines(self):
        for bad in ("noColonHere", "BAD NAME:x", 'A;P="unterminated:x', "A;noequals:x"):
            self.assertIsNone(ic.parse_line(bad), bad)

    def test_unfold(self):
        lines, _ = ic.unfold("DESCRIPTION:abc\r\n def\r\n\tghi\r\nEND:X\r\n")
        self.assertEqual(lines[0][1], "DESCRIPTION:abcdefghi")
        self.assertEqual(lines[1][1], "END:X")

    def test_folded_long_line_ok(self):
        long = "DESCRIPTION:" + "a" * 60 + "\r\n " + "b" * 60
        t = ev(*GOOD).replace("DTSTART", long + "\r\nDTSTART", 1)
        self.assertNotIn("line-length", rules(t))

    def test_bom_warning(self):
        self.assertIn("bom", rules("\ufeff" + ev(*GOOD), ic.WARNING))


class LineEndings(unittest.TestCase):
    def test_bare_lf(self):
        self.assertIn("line-endings", rules(ev(*GOOD).replace("\r\n", "\n"), ic.ERROR))

    def test_mixed(self):
        t = ev(*GOOD).replace("\r\n", "\n", 1)
        self.assertIn("line-endings", rules(t, ic.ERROR))

    def test_crlf_ok(self):
        self.assertNotIn("line-endings", rules(ev(*GOOD)))

    def test_long_line_ascii(self):
        t = ev(*GOOD, "SUMMARY:" + "x" * 80)
        self.assertIn("line-length", rules(t, ic.WARNING))

    def test_75_ok_76_warn(self):
        self.assertNotIn("line-length", rules(ev(*GOOD, "SUMMARY:" + "x" * 67)))
        self.assertIn("line-length", rules(ev(*GOOD, "SUMMARY:" + "x" * 68)))

    def test_octets_not_chars(self):
        # 34 two-byte chars + "SUMMARY:" = 76 octets but only 42 characters
        self.assertIn("line-length", rules(ev(*GOOD, "SUMMARY:" + "\u00e9" * 34)))


class Structure(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(rules(""), ["empty"])

    def test_no_vcalendar(self):
        self.assertIn("structure", rules("BEGIN:VEVENT\r\nEND:VEVENT\r\n"))

    def test_unclosed(self):
        self.assertIn("nesting", rules("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))

    def test_mismatch_end(self):
        t = ev(*GOOD).replace("END:VEVENT", "END:VTODO")
        self.assertIn("nesting", rules(t))

    def test_stray_end(self):
        self.assertIn("nesting", rules(ev(*GOOD) + "END:VEVENT\r\n"))

    def test_blank_line(self):
        t = ev(*GOOD).replace("BEGIN:VEVENT", "\r\nBEGIN:VEVENT")
        self.assertIn("blank-line", rules(t))

    def test_prop_outside(self):
        self.assertIn("syntax", rules("SUMMARY:x\r\n" + ev(*GOOD)))

    def test_empty_calendar(self):
        t = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:p\r\nEND:VCALENDAR\r\n"
        self.assertIn("empty-calendar", rules(t))

    def test_missing_prodid_version(self):
        r = rules(ev(*GOOD, head=()))
        self.assertEqual(r.count("missing-property"), 2)

    def test_bad_version(self):
        self.assertIn("bad-version", rules(ev(*GOOD, head=("VERSION:1.0", "PRODID:p"))))

    def test_duplicate_prodid(self):
        self.assertIn("duplicate-property", rules(ev(*GOOD, head=("VERSION:2.0", "PRODID:a", "PRODID:b"))))

    def test_duplicate_method(self):
        self.assertIn("duplicate-property", rules(ev(*GOOD, extra=("METHOD:PUBLISH", "METHOD:REQUEST"))))


class Events(unittest.TestCase):
    def test_missing_uid_dtstamp(self):
        r = rules(ev("DTSTART:20260102T100000Z"))
        self.assertEqual(r.count("missing-property"), 2)

    def test_dtstart_required_without_method(self):
        self.assertIn("missing-property", rules(ev("UID:a", "DTSTAMP:20260101T000000Z")))

    def test_dtstart_optional_with_method(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", extra=("METHOD:REQUEST",))
        self.assertEqual(rules(t), [])

    def test_dtend_and_duration(self):
        t = ev(*GOOD, "DTEND:20260102T110000Z", "DURATION:PT1H")
        self.assertIn("end-and-duration", rules(t))

    def test_duplicate_summary(self):
        self.assertIn("duplicate-property", rules(ev(*GOOD, "SUMMARY:a", "SUMMARY:b")))

    def test_multiple_attendees_ok(self):
        self.assertEqual(rules(ev(*GOOD, "ATTENDEE:mailto:a@x", "ATTENDEE:mailto:b@x")), [])

    def test_end_before_start(self):
        self.assertIn("end-before-start", rules(ev(*GOOD, "DTEND:20260102T090000Z")))

    def test_end_equals_start(self):
        self.assertIn("end-before-start", rules(ev(*GOOD, "DTEND:20260102T100000Z")))

    def test_end_after_start_ok(self):
        self.assertEqual(rules(ev(*GOOD, "DTEND:20260102T100001Z")), [])

    def test_all_day_ok(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20260102", "DTEND;VALUE=DATE:20260103")
        self.assertEqual(rules(t), [])

    def test_all_day_same_day_end(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20260102", "DTEND;VALUE=DATE:20260102")
        self.assertIn("end-before-start", rules(t))

    def test_date_without_value_param(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:20260102")
        self.assertIn("value-mismatch", rules(t))

    def test_value_date_with_datetime(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20260102T100000Z")
        self.assertIn("value-mismatch", rules(t))

    def test_mixed_types_start_end(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;VALUE=DATE:20260102", "DTEND:20260103T000000Z")
        self.assertIn("value-mismatch", rules(t))

    def test_impossible_dates(self):
        for v in ("20260230T100000Z", "20261301T100000Z", "20260101T250000Z", "2026-01-01T10:00:00Z", "20260101T1000Z"):
            t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:" + v)
            self.assertIn("bad-datetime", rules(t), v)

    def test_leap_day(self):
        t = ev("UID:a", "DTSTAMP:20240101T000000Z", "DTSTART:20240229T100000Z")
        self.assertEqual(rules(t), [])
        t = ev("UID:a", "DTSTAMP:20240101T000000Z", "DTSTART:20250229T100000Z")
        self.assertIn("bad-datetime", rules(t))

    def test_leap_second_accepted(self):
        t = ev("UID:a", "DTSTAMP:20161231T235960Z", "DTSTART:20161231T235960Z")
        self.assertEqual(rules(t), [])

    def test_dtstamp_must_be_utc(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000", "DTSTART:20260102T100000Z")
        self.assertIn("bad-utc-datetime", rules(t))

    def test_tzid_with_utc(self):
        t = ev("UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=Europe/Paris:20260102T100000Z")
        self.assertIn("tzid-with-utc", rules(t))

    def test_status(self):
        self.assertIn("bad-status", rules(ev(*GOOD, "STATUS:COMPLETED")))
        self.assertEqual(rules(ev(*GOOD, "STATUS:confirmed")), [])

    def test_duplicate_uid_warns(self):
        one = "BEGIN:VEVENT\r\n" + "\r\n".join(GOOD) + "\r\nEND:VEVENT\r\n"
        t = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:p\r\n" + one + one + "END:VCALENDAR\r\n"
        self.assertIn("duplicate-uid", rules(t, ic.WARNING))

    def test_same_uid_with_recurrence_id_ok(self):
        a = "BEGIN:VEVENT\r\n" + "\r\n".join(GOOD) + "\r\nEND:VEVENT\r\n"
        b = "BEGIN:VEVENT\r\n" + "\r\n".join(GOOD + ("RECURRENCE-ID:20260102T100000Z",)) + "\r\nEND:VEVENT\r\n"
        t = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:p\r\n" + a + b + "END:VCALENDAR\r\n"
        self.assertNotIn("duplicate-uid", rules(t))


class TodoJournal(unittest.TestCase):
    def wrap(self, comp, *lines):
        return crlf("BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:p\nBEGIN:%s\n%s\nEND:%s\nEND:VCALENDAR"
                    % (comp, "\n".join(lines), comp))

    def test_todo_ok(self):
        self.assertEqual(rules(self.wrap("VTODO", "UID:a", "DTSTAMP:20260101T000000Z", "STATUS:NEEDS-ACTION")), [])

    def test_todo_bad_status(self):
        self.assertIn("bad-status", rules(self.wrap("VTODO", "UID:a", "DTSTAMP:20260101T000000Z", "STATUS:CONFIRMED")))

    def test_todo_due_and_duration(self):
        t = self.wrap("VTODO", "UID:a", "DTSTAMP:20260101T000000Z", "DUE:20260102T000000Z", "DURATION:PT1H")
        self.assertIn("end-and-duration", rules(t))

    def test_todo_due_before_start(self):
        t = self.wrap("VTODO", "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:20260103T000000Z", "DUE:20260102T000000Z")
        self.assertIn("end-before-start", rules(t))

    def test_journal_missing_uid(self):
        self.assertIn("missing-property", rules(self.wrap("VJOURNAL", "DTSTAMP:20260101T000000Z")))

    def test_journal_status(self):
        t = self.wrap("VJOURNAL", "UID:a", "DTSTAMP:20260101T000000Z", "STATUS:FINAL")
        self.assertEqual(rules(t), [])


class Alarms(unittest.TestCase):
    def alarm(self, *lines):
        t = ev(*GOOD, "BEGIN:VALARM", *lines, "END:VALARM")
        return rules(t)

    def test_display_ok(self):
        self.assertEqual(self.alarm("ACTION:DISPLAY", "TRIGGER:-PT15M", "DESCRIPTION:x"), [])

    def test_missing_action_trigger(self):
        self.assertEqual(self.alarm("DESCRIPTION:x").count("missing-property"), 2)

    def test_display_needs_description(self):
        self.assertIn("missing-property", self.alarm("ACTION:DISPLAY", "TRIGGER:-PT15M"))

    def test_audio_ok(self):
        self.assertEqual(self.alarm("ACTION:AUDIO", "TRIGGER:-PT15M"), [])

    def test_email_needs_three(self):
        self.assertEqual(self.alarm("ACTION:EMAIL", "TRIGGER:-PT5M").count("missing-property"), 3)

    def test_duration_without_repeat(self):
        self.assertIn("alarm-repeat", self.alarm("ACTION:AUDIO", "TRIGGER:-PT15M", "DURATION:PT5M"))

    def test_repeat_with_duration_ok(self):
        self.assertEqual(self.alarm("ACTION:AUDIO", "TRIGGER:-PT15M", "DURATION:PT5M", "REPEAT:2"), [])


class Timezones(unittest.TestCase):
    TZ = ("BEGIN:VTIMEZONE", "TZID:Europe/Paris", "BEGIN:STANDARD", "DTSTART:19701025T030000",
          "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "END:STANDARD", "END:VTIMEZONE")

    def cal(self, tz, *ev_lines):
        return "\r\n".join(["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:p", *tz,
                            "BEGIN:VEVENT", *ev_lines, "END:VEVENT", "END:VCALENDAR"]) + "\r\n"

    def test_defined_tzid(self):
        t = self.cal(self.TZ, "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=Europe/Paris:20260102T100000")
        self.assertEqual(rules(t), [])

    def test_undefined_tzid(self):
        t = self.cal((), "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=Europe/Paris:20260102T100000")
        self.assertIn("undefined-tzid", rules(t))

    def test_global_tzid_exempt(self):
        t = self.cal((), "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART;TZID=/example.org/Paris:20260102T100000")
        self.assertNotIn("undefined-tzid", rules(t))

    def test_tzid_in_alarm_checked(self):
        t = self.cal((), "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:20260102T100000Z",
                     "BEGIN:VALARM", "ACTION:AUDIO", "TRIGGER;VALUE=DATE-TIME;TZID=Nowhere:20260102T090000",
                     "END:VALARM")
        self.assertIn("undefined-tzid", rules(t))

    def test_vtimezone_incomplete(self):
        tz = ("BEGIN:VTIMEZONE", "END:VTIMEZONE")
        r = rules(self.cal(tz, "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:20260102T100000Z"))
        self.assertIn("missing-component", r)
        self.assertIn("missing-property", r)

    def test_standard_missing_offsets(self):
        tz = ("BEGIN:VTIMEZONE", "TZID:X", "BEGIN:STANDARD", "DTSTART:19701025T030000", "END:STANDARD", "END:VTIMEZONE")
        r = rules(self.cal(tz, "UID:a", "DTSTAMP:20260101T000000Z", "DTSTART:20260102T100000Z"))
        self.assertEqual(r.count("missing-property"), 2)


class Cli(unittest.TestCase):
    def run_cli(self, text, *args):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.ics")
            with open(p, "wb") as fh:
                fh.write(text.encode("utf-8"))
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = ic.main([p, *args])
            return code, buf.getvalue()

    def test_clean_exit_0(self):
        code, out = self.run_cli(RFC_EXAMPLE)
        self.assertEqual(code, 0)
        self.assertIn("ok", out)

    def test_error_exit_1(self):
        code, out = self.run_cli(ev("UID:a"))
        self.assertEqual(code, 1)
        self.assertIn("error", out)

    def test_warning_only_exit_0_strict_1(self):
        t = ev(*GOOD, "SUMMARY:" + "x" * 90)
        self.assertEqual(self.run_cli(t)[0], 0)
        self.assertEqual(self.run_cli(t, "--strict")[0], 1)

    def test_json(self):
        code, out = self.run_cli(ev("UID:a"), "--json")
        data = json.loads(out)
        (k, v), = data.items()
        self.assertTrue(any(x["rule"] == "missing-property" for x in v))

    def test_missing_file_exit_2(self):
        self.assertEqual(ic.main(["/nonexistent/x.ics"]), 2)

    def test_non_utf8_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.ics")
            with open(p, "wb") as fh:
                fh.write(b"BEGIN:VCALENDAR\r\nSUMMARY:\xff\xfe\r\n")
            self.assertEqual(ic.main([p]), 2)

    def test_subprocess(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "t.ics")
            with open(p, "wb") as fh:
                fh.write(RFC_EXAMPLE.encode())
            r = subprocess.run([sys.executable, os.path.join(HERE, "ics_check.py"), p],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
