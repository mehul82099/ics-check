#!/usr/bin/env python3
"""ics-check: lint iCalendar (.ics) files against the MUST/SHOULD rules of RFC 5545.

Standard library only. Exit codes: 0 no errors, 1 errors found, 2 usage/IO problem.
Warnings (RFC "SHOULD" rules) do not change the exit code unless --strict.
"""
import argparse
import datetime
import json
import re
import sys

ERROR, WARNING = "error", "warning"

DT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")


class Prop:
    __slots__ = ("name", "params", "value", "line")

    def __init__(self, name, params, value, line):
        self.name, self.params, self.value, self.line = name, params, value, line

    def param(self, key):
        v = self.params.get(key)
        return v[0] if v else None


class Comp:
    def __init__(self, name, line):
        self.name, self.line = name, line
        self.props, self.children = [], []

    def all(self, name):
        return [p for p in self.props if p.name == name]


def unfold(data):
    """Return (logical_lines, findings). logical_lines = [(lineno, text)]."""
    findings = []
    if "\r\n" not in data and "\n" in data:
        findings.append((ERROR, 1, "line-endings",
                         "lines end with bare LF; RFC 5545 section 3.1 requires CRLF"))
    elif re.search(r"(?<!\r)\n", data):
        findings.append((ERROR, 1, "line-endings",
                         "mixed line endings; RFC 5545 section 3.1 requires CRLF"))
    physical = re.split(r"\r\n|\n", data)
    if physical and physical[-1] == "":
        physical.pop()
    out = []
    for i, text in enumerate(physical, 1):
        if len(text.encode("utf-8")) > 75:
            findings.append((WARNING, i, "line-length",
                             "line is %d octets; RFC 5545 section 3.1 says lines SHOULD NOT "
                             "exceed 75 octets (fold with CRLF + space)"
                             % len(text.encode("utf-8"))))
        if text[:1] in (" ", "\t") and out:
            out[-1] = (out[-1][0], out[-1][1] + text[1:])
        else:
            out.append((i, text))
    return out, findings


def parse_line(text):
    """Split 'NAME;P=v:value' honouring DQUOTE in parameter values. None if malformed."""
    i, n, in_q = 0, len(text), False
    while i < n:
        c = text[i]
        if c == '"':
            in_q = not in_q
        elif c == ":" and not in_q:
            break
        i += 1
    if i >= n or in_q:
        return None
    head, value = text[:i], text[i + 1:]
    parts, cur, in_q = [], "", False
    for c in head:
        if c == '"':
            in_q = not in_q
            cur += c
        elif c == ";" and not in_q:
            parts.append(cur)
            cur = ""
        else:
            cur += c
    parts.append(cur)
    name = parts[0].upper()
    if not re.match(r"^[A-Za-z0-9-]+$", name):
        return None
    params = {}
    for p in parts[1:]:
        if "=" not in p:
            return None
        k, v = p.split("=", 1)
        vals, cur, in_q = [], "", False
        for c in v:
            if c == '"':
                in_q = not in_q
            elif c == "," and not in_q:
                vals.append(cur)
                cur = ""
            else:
                cur += c
        vals.append(cur)
        params[k.upper()] = vals
    return name, params, value


def parse_dt(value):
    """Return ('date'|'datetime', date/datetime, is_utc) or None."""
    m = DATE_RE.match(value)
    try:
        if m:
            return "date", datetime.date(*map(int, m.groups())), False
        m = DT_RE.match(value)
        if m:
            g = list(map(int, m.groups()[:6]))
            if g[5] == 60:  # leap second is legal in RFC 5545 (seconds 00-60)
                g[5] = 59
            return "datetime", datetime.datetime(*g), m.group(7) == "Z"
    except ValueError:
        return None
    return None


def build(lines, findings):
    root = Comp("<root>", 0)
    stack = [root]
    for no, text in lines:
        if text.strip() == "":
            findings.append((ERROR, no, "blank-line", "blank line inside calendar data"))
            continue
        parsed = parse_line(text)
        if parsed is None:
            findings.append((ERROR, no, "syntax", "not a valid content line (NAME[;PARAM=v]:value)"))
            continue
        name, params, value = parsed
        if name == "BEGIN":
            c = Comp(value.upper(), no)
            stack[-1].children.append(c)
            stack.append(c)
        elif name == "END":
            if len(stack) == 1:
                findings.append((ERROR, no, "nesting", "END:%s without matching BEGIN" % value))
            elif stack[-1].name != value.upper():
                findings.append((ERROR, no, "nesting",
                                 "END:%s does not match open BEGIN:%s (line %d)"
                                 % (value, stack[-1].name, stack[-1].line)))
                stack.pop()
            else:
                stack.pop()
        else:
            if len(stack) == 1:
                findings.append((ERROR, no, "syntax", "property %s outside any component" % name))
            else:
                stack[-1].props.append(Prop(name, params, value, no))
    for c in stack[1:]:
        findings.append((ERROR, c.line, "nesting", "BEGIN:%s is never closed" % c.name))
    return root


ONCE_VEVENT = ["CLASS", "CREATED", "DESCRIPTION", "DTSTART", "GEO", "LAST-MODIFIED",
               "LOCATION", "ORGANIZER", "PRIORITY", "SEQUENCE", "STATUS", "SUMMARY",
               "TRANSP", "URL", "RECURRENCE-ID", "DTEND", "DURATION", "DTSTAMP", "UID"]
ONCE_VTODO = ["CLASS", "COMPLETED", "CREATED", "DESCRIPTION", "DTSTAMP", "DTSTART", "GEO",
              "LAST-MODIFIED", "LOCATION", "ORGANIZER", "PERCENT-COMPLETE", "PRIORITY",
              "RECURRENCE-ID", "SEQUENCE", "STATUS", "SUMMARY", "UID", "URL", "DUE", "DURATION"]
ONCE_VJOURNAL = ["CLASS", "CREATED", "DTSTART", "DTSTAMP", "LAST-MODIFIED", "ORGANIZER",
                 "RECURRENCE-ID", "SEQUENCE", "STATUS", "SUMMARY", "UID", "URL"]
STATUS_OK = {"VEVENT": {"TENTATIVE", "CONFIRMED", "CANCELLED"},
             "VTODO": {"NEEDS-ACTION", "COMPLETED", "IN-PROCESS", "CANCELLED"},
             "VJOURNAL": {"DRAFT", "FINAL", "CANCELLED"}}


def add(f, sev, line, rule, msg):
    f.append((sev, line, rule, msg))


def check_once(comp, names, f):
    for n in names:
        ps = comp.all(n)
        if len(ps) > 1:
            add(f, ERROR, ps[1].line, "duplicate-property",
                "%s appears %d times in %s; it MUST NOT occur more than once" % (n, len(ps), comp.name))


def check_dt_prop(p, f):
    """Validate a date/date-time property; return parsed tuple or None."""
    vt = (p.param("VALUE") or "").upper()
    parsed = parse_dt(p.value.strip())
    if parsed is None:
        add(f, ERROR, p.line, "bad-datetime", "%s value %r is not a valid DATE or DATE-TIME" % (p.name, p.value))
        return None
    kind, _, utc = parsed
    if vt == "DATE" and kind != "date":
        add(f, ERROR, p.line, "value-mismatch", "%s has VALUE=DATE but a date-time value" % p.name)
    if vt == "DATE-TIME" and kind != "datetime":
        add(f, ERROR, p.line, "value-mismatch", "%s has VALUE=DATE-TIME but a date value" % p.name)
    if kind == "date" and not vt:
        pass  # DATE without VALUE=DATE is a spec violation, reported below
    if kind == "date" and vt != "DATE":
        add(f, ERROR, p.line, "value-mismatch", "%s is a DATE but lacks VALUE=DATE" % p.name)
    if utc and p.param("TZID"):
        add(f, ERROR, p.line, "tzid-with-utc", "%s has both a TZID parameter and a UTC 'Z' value" % p.name)
    return parsed


def check_alarm(a, f):
    for need in ("ACTION", "TRIGGER"):
        if not a.all(need):
            add(f, ERROR, a.line, "missing-property", "VALARM is missing required %s" % need)
    act = a.all("ACTION")
    action = act[0].value.upper() if act else None
    if action == "DISPLAY" and not a.all("DESCRIPTION"):
        add(f, ERROR, a.line, "missing-property", "VALARM with ACTION:DISPLAY requires DESCRIPTION")
    if action == "EMAIL":
        for need in ("DESCRIPTION", "SUMMARY", "ATTENDEE"):
            if not a.all(need):
                add(f, ERROR, a.line, "missing-property", "VALARM with ACTION:EMAIL requires %s" % need)
    if bool(a.all("DURATION")) != bool(a.all("REPEAT")):
        add(f, ERROR, a.line, "alarm-repeat", "VALARM must have both DURATION and REPEAT, or neither")


def check_tz(c, f):
    if not c.all("TZID"):
        add(f, ERROR, c.line, "missing-property", "VTIMEZONE is missing TZID")
    subs = [x for x in c.children if x.name in ("STANDARD", "DAYLIGHT")]
    if not subs:
        add(f, ERROR, c.line, "missing-component", "VTIMEZONE needs at least one STANDARD or DAYLIGHT")
    for s in subs:
        for need in ("DTSTART", "TZOFFSETTO", "TZOFFSETFROM"):
            if not s.all(need):
                add(f, ERROR, s.line, "missing-property", "%s is missing required %s" % (s.name, need))


def check_scheduled(c, f, has_method, uids):
    once = {"VEVENT": ONCE_VEVENT, "VTODO": ONCE_VTODO, "VJOURNAL": ONCE_VJOURNAL}[c.name]
    check_once(c, once, f)
    for need in ("UID", "DTSTAMP"):
        if not c.all(need):
            add(f, ERROR, c.line, "missing-property", "%s is missing required %s" % (c.name, need))
    if c.name == "VEVENT" and not has_method and not c.all("DTSTART"):
        add(f, ERROR, c.line, "missing-property", "VEVENT is missing DTSTART (required when the calendar has no METHOD)")
    parsed = {}
    for n in ("DTSTART", "DTEND", "DUE", "DTSTAMP", "CREATED", "LAST-MODIFIED", "COMPLETED", "RECURRENCE-ID"):
        for p in c.all(n):
            r = check_dt_prop(p, f) if n not in ("DTSTAMP", "CREATED", "LAST-MODIFIED", "COMPLETED") else None
            if n in ("DTSTAMP", "CREATED", "LAST-MODIFIED", "COMPLETED"):
                r = parse_dt(p.value.strip())
                if r is None or r[0] != "datetime" or not r[2]:
                    add(f, ERROR, p.line, "bad-utc-datetime",
                        "%s must be a DATE-TIME in UTC form (YYYYMMDDTHHMMSSZ)" % n)
            parsed.setdefault(n, r)
    end_name = "DTEND" if c.name == "VEVENT" else "DUE" if c.name == "VTODO" else None
    if end_name:
        if c.all(end_name) and c.all("DURATION"):
            add(f, ERROR, c.all("DURATION")[0].line, "end-and-duration",
                "%s and DURATION MUST NOT both occur" % end_name)
        s, e = parsed.get("DTSTART"), parsed.get(end_name)
        if s and e:
            if s[0] != e[0]:
                add(f, ERROR, c.all(end_name)[0].line, "value-mismatch",
                    "%s must have the same value type as DTSTART (one is DATE, the other DATE-TIME)" % end_name)
            elif s[0] == "date" and e[1] <= s[1]:
                add(f, ERROR, c.all(end_name)[0].line, "end-before-start", "%s must be later than DTSTART" % end_name)
            elif s[0] == "datetime" and s[2] == e[2] and e[1] < s[1]:
                add(f, ERROR, c.all(end_name)[0].line, "end-before-start", "%s is earlier than DTSTART" % end_name)
            elif s[0] == "datetime" and s[2] == e[2] and e[1] == s[1]:
                add(f, ERROR, c.all(end_name)[0].line, "end-before-start",
                    "%s must be later than DTSTART (they are equal)" % end_name)
    for p in c.all("STATUS"):
        if p.value.upper() not in STATUS_OK[c.name]:
            add(f, ERROR, p.line, "bad-status", "STATUS:%s is not valid for %s (allowed: %s)"
                % (p.value, c.name, ", ".join(sorted(STATUS_OK[c.name]))))
    uid = c.all("UID")
    if uid and not c.all("RECURRENCE-ID"):
        key = uid[0].value
        if key in uids:
            add(f, WARNING, uid[0].line, "duplicate-uid",
                "UID %r already used by the VEVENT at line %d without RECURRENCE-ID" % (key, uids[key]))
        else:
            uids[key] = uid[0].line
    for a in c.children:
        if a.name == "VALARM":
            check_alarm(a, f)


def lint_text(data):
    """Lint calendar text; returns a list of (severity, line, rule, message), sorted by line."""
    findings = []
    if data.startswith("\ufeff"):
        add(findings, WARNING, 1, "bom", "file starts with a UTF-8 byte order mark")
        data = data[1:]
    lines, f1 = unfold(data)
    findings += f1
    if not lines:
        add(findings, ERROR, 1, "empty", "no calendar data")
        return findings
    root = build(lines, findings)
    cals = [c for c in root.children if c.name == "VCALENDAR"]
    for c in root.children:
        if c.name != "VCALENDAR":
            add(findings, ERROR, c.line, "structure", "top-level component %s is not inside a VCALENDAR" % c.name)
    if not cals:
        add(findings, ERROR, 1, "structure", "no VCALENDAR component found")
        return sorted(findings, key=lambda x: x[1])
    for cal in cals:
        for need in ("PRODID", "VERSION"):
            ps = cal.all(need)
            if not ps:
                add(findings, ERROR, cal.line, "missing-property", "VCALENDAR is missing required %s" % need)
            elif len(ps) > 1:
                add(findings, ERROR, ps[1].line, "duplicate-property", "%s MUST NOT occur more than once" % need)
        for p in cal.all("VERSION"):
            if p.value.strip() != "2.0":
                add(findings, ERROR, p.line, "bad-version", "VERSION is %r; RFC 5545 requires 2.0" % p.value)
        for n in ("CALSCALE", "METHOD"):
            if len(cal.all(n)) > 1:
                add(findings, ERROR, cal.all(n)[1].line, "duplicate-property", "%s MUST NOT occur more than once" % n)
        if not cal.children:
            add(findings, ERROR, cal.line, "empty-calendar", "VCALENDAR must contain at least one component")
        has_method = bool(cal.all("METHOD"))
        tzids = set()
        for c in cal.children:
            if c.name == "VTIMEZONE":
                check_tz(c, findings)
                for p in c.all("TZID"):
                    tzids.add(p.value)
        uids = {}
        for c in cal.children:
            if c.name in ("VEVENT", "VTODO", "VJOURNAL"):
                check_scheduled(c, findings, has_method, uids)
        def walk(c):
            for p in c.props:
                t = p.param("TZID")
                if t and not t.startswith("/") and t not in tzids:
                    add(findings, ERROR, p.line, "undefined-tzid",
                        "TZID=%s has no VTIMEZONE in this file (RFC 5545 section 3.2.19)" % t)
            for ch in c.children:
                walk(ch)
        walk(cal)
    return sorted(findings, key=lambda x: (x[1], x[0]))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="ics_check", description="Lint iCalendar files against RFC 5545.")
    ap.add_argument("files", nargs="+", help=".ics files, or - for stdin")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
    a = ap.parse_args(argv)
    bad, report = False, {}
    for name in a.files:
        try:
            if name == "-":
                data = sys.stdin.buffer.read().decode("utf-8")
            else:
                with open(name, "rb") as fh:
                    data = fh.read().decode("utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print("ics-check: cannot read %s: %s" % (name, e), file=sys.stderr)
            return 2
        res = lint_text(data)
        report[name] = res
        if any(s == ERROR for s, *_ in res) or (a.strict and res):
            bad = True
    if a.json:
        print(json.dumps({n: [dict(severity=s, line=l, rule=r, message=m) for s, l, r, m in v]
                          for n, v in report.items()}, indent=2))
    else:
        for n, v in report.items():
            for s, l, r, m in v:
                print("%s:%d: %s [%s] %s" % (n, l, s, r, m))
            if not v:
                print("%s: ok" % n)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
