"""Parse one log line, in any of the six styles found in evidence/logs, into one simple form.

Every line starts with an ISO-8601 UTC time, followed by one of:
    {"severity": ..., "service": ..., "event": ..., ...}                      JSON
    [LEVEL] [service] [trace] event: key="value" ...                          bracketed
    | LEVEL | service | trace | event | key="value" ...                       pipe
    <pri>1 host service trace - - key="value" ...                             syslog
    LEVEL key=value ...   or   key=value ...                                  key=value
    ... function=service message="key=\\"value\\" ..."                       key=value inside a message

This module only splits lines into fields; it gives them no meaning (that is the log reader's
job). Fields can hold raw emails: LogLine objects stay in memory and are never printed or
logged; readers turn identifiers into fingerprints.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Optional

from audit.sighting import parse_time

TIME = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s+(.*)$")
BRACKET = re.compile(r"^\[(\w+)\]\s+\[([\w.-]+)\]\s+\[([\w.-]+)\]\s+(\w+):\s*(.*)$")
PIPE = re.compile(r"^\|\s*(\w+)\s*\|\s*([\w.-]+)\s*\|\s*([\w.-]+)\s*\|\s*(\w+)\s*\|\s*(.*)$")
SYSLOG = re.compile(r"^<\d+>\d+\s+(\S+)\s+([\w.-]+)\s+(\S+)\s+\S+\s+\S+\s*(.*)$")
LEVEL_FIRST = re.compile(r"^(DEBUG|INFO|WARN|WARNING|ERROR|CRITICAL)\s+(.*)$")
KEY_VALUE = re.compile(r'(\w+)=("((?:[^"\\]|\\.)*)"|\S+)')


@dataclass(frozen=True)
class LogLine:
    time: datetime
    style: str
    level: str = ""
    service: str = ""
    event: str = ""
    fields: Dict[str, str] = field(default_factory=dict)


def parse_key_values(text):
    """Parse key=value / key="value" pairs; a quoted message holding pairs is opened up as well."""
    pairs = {}
    for match in KEY_VALUE.finditer(text):
        key, raw, quoted = match.group(1), match.group(2), match.group(3)
        pairs[key] = quoted.replace('\\"', '"').replace("\\\\", "\\") if quoted is not None else raw
    inner = pairs.get("message", "")
    if "=" in inner:
        for key, value in parse_key_values(inner).items():
            pairs.setdefault(key, value)
    return pairs


def _line(time, style, level, service, event, fields):
    return LogLine(time=time, style=style, level=level or fields.get("level", "") or fields.get("severity", ""),
                   service=service or fields.get("service", "") or fields.get("function", "") or fields.get("logger", ""),
                   event=event or fields.get("event", ""), fields=fields)


def parse_line(line) -> Optional[LogLine]:
    """Return the parsed line, or None if it matches no known style."""
    match = TIME.match(line.strip())
    if not match:
        return None
    time, rest = parse_time(match.group(1)), match.group(2)

    if rest.startswith("{"):
        try:
            data = json.loads(rest)
        except ValueError:
            return None
        if not isinstance(data, dict):
            return None
        fields = {k: (v if isinstance(v, str) else json.dumps(v)) for k, v in data.items()}
        return _line(time, "json", "", "", "", fields)

    m = BRACKET.match(rest)
    if m:
        fields = parse_key_values(m.group(5))
        fields.setdefault("trace_id", m.group(3))
        return _line(time, "bracket", m.group(1), m.group(2), m.group(4), fields)

    m = PIPE.match(rest)
    if m:
        fields = parse_key_values(m.group(5))
        fields.setdefault("trace_id", m.group(3))
        return _line(time, "pipe", m.group(1), m.group(2), m.group(4), fields)

    m = SYSLOG.match(rest)
    if m:
        fields = parse_key_values(m.group(4))
        fields.setdefault("trace_id", m.group(3))
        fields.setdefault("host", m.group(1))
        return _line(time, "syslog", "", m.group(2), "", fields)

    m = LEVEL_FIRST.match(rest)
    if m:
        return _line(time, "level_key_value", m.group(1), "", "", parse_key_values(m.group(2)))

    fields = parse_key_values(rest)
    if not fields:
        return None
    return _line(time, "key_value", "", "", "", fields)
