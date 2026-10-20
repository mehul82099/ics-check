# ics-check

A small linter for iCalendar (`.ics`) files. It checks the "MUST" and "SHOULD" rules of
[RFC 5545](https://www.rfc-editor.org/rfc/rfc5545) that calendar imports tend to trip over:
wrong line endings, over-long lines, missing required properties, date-times that are not real
dates, `DTEND` before `DTSTART`, `TZID`s with no `VTIMEZONE`, and broken alarms.

Python 3.9+, standard library only. No network. MIT license.

```
python ics_check.py invite.ics
python ics_check.py a.ics b.ics --json
cat invite.ics | python ics_check.py -
python ics_check.py invite.ics --strict   # warnings also give exit code 1
```

Exit codes: `0` no errors, `1` errors found (or warnings with `--strict`), `2` unreadable file or not UTF-8.
Output is `file:line: severity [rule] message`.

## What it checks

| Rule id | Severity | RFC 5545 basis |
|---|---|---|
| `line-endings` | error | 3.1: content lines end with CRLF |
| `line-length` | warning | 3.1: lines SHOULD NOT exceed 75 octets (counted as UTF-8 bytes, after unfolding rules for CRLF+space/tab) |
| `syntax`, `blank-line`, `nesting` | error | 3.1 content-line grammar, BEGIN/END pairing |
| `missing-property` | error | 3.6 `PRODID` and `VERSION`; 3.6.1-3.6.3 `UID`, `DTSTAMP`; 3.6.1 `DTSTART` unless the calendar has `METHOD`; 3.6.5 `VTIMEZONE` contents; 3.6.6 `VALARM` per `ACTION` |
| `bad-version` | error | 3.7.4: `VERSION` is `2.0` |
| `duplicate-property` | error | 3.6.1-3.6.3: properties that MUST NOT occur more than once |
| `end-and-duration` | error | 3.6.1, 3.6.2: `DTEND`/`DUE` and `DURATION` MUST NOT both occur |
| `end-before-start` | error | 3.8.2.2: `DTEND` MUST be later than `DTSTART` (including equal date-times and same-day all-day events); `DUE` must also be later than `DTSTART` (3.8.2.3) |
| `bad-datetime`, `bad-utc-datetime` | error | 3.3.4, 3.3.5: real calendar dates and times (leap days, seconds 00-60); `DTSTAMP`, `CREATED`, `LAST-MODIFIED`, `COMPLETED` must be UTC |
| `value-mismatch` | error | 3.2.20 / 3.8.2.4: a DATE needs `VALUE=DATE`; `DTEND` must have the same value type as `DTSTART` |
| `tzid-with-utc` | error | 3.3.5: `TZID` MUST NOT be applied to a UTC date-time |
| `undefined-tzid` | error | 3.2.19: a `TZID` needs a `VTIMEZONE` in the file (globally unique IDs starting with `/` are exempt) |
| `bad-status` | error | 3.8.1.11: values allowed per component |
| `alarm-repeat` | error | 3.6.6: `DURATION` and `REPEAT` both or neither |
| `duplicate-uid` | warning | 3.8.4.7: a UID identifies one event; reuse without `RECURRENCE-ID` is almost always a bug |
| `bom` | warning | a UTF-8 byte order mark before `BEGIN:VCALENDAR` upsets some importers |

## What it does not do

This is not a full RFC 5545 validator. It does not check `RRULE` syntax (see my
[rrule-check](https://github.com/mehul82099/rrule-check) for that), text escaping, every property's
value grammar, or `VFREEBUSY`. It does not compare time zones against the IANA database or
resolve `VTIMEZONE` rules. A file that passes can still be rejected by a particular client.
The section numbers above are my reading of the RFC; check the RFC text for the exact wording.

## Tests

```
python -m unittest -v
```

75 tests cover each rule in both directions (a passing and a failing case), the CLI exit codes,
JSON output, and UTF-8 octet counting. One test uses the Bastille Day `VEVENT` example from RFC 5545
section 3.4 and expects zero findings. CI runs on Ubuntu and Windows with Python 3.9 and 3.13
(see `.github/workflows/test.yml`); I have not run it on other versions.

## Sources

- RFC 5545, Internet Calendaring and Scheduling Core Object Specification: https://www.rfc-editor.org/rfc/rfc5545

## License

MIT, see `LICENSE`.
