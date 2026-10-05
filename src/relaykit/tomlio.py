"""TOML read/write with no third-party dependency.

Reading uses the standard library's ``tomllib`` (Python 3.11+). On 3.9/3.10 it falls back to a
small parser for the subset relaykit's own files use: comments, ``[table]`` / ``[a.b]`` headers,
``key = value`` (dotted keys allowed), basic and literal strings, integers, floats, booleans,
arrays (multi-line allowed) and inline tables. Writing always uses the small writer below.
"""
from __future__ import annotations

import re
from typing import Any

try:  # Python 3.11+
    import tomllib as _tomllib  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - exercised on 3.9/3.10 only
    _tomllib = None


class TomlError(ValueError):
    pass


def loads(text: str) -> dict:
    if _tomllib is not None:
        try:
            return _tomllib.loads(text)
        except Exception as exc:  # normalise the error type
            raise TomlError(str(exc)) from exc
    return _MiniParser(text).parse()


def load_file(path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return loads(fh.read())


# --------------------------------------------------------------------------- writer
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _key(k: str) -> str:
    return k if _BARE_KEY.match(k) else _string(k)


def _string(s: str) -> str:
    out = ['"']
    for ch in s:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ord(ch) < 0x20:
            out.append("\\u%04x" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    if v is None:
        return '""'
    raise TomlError(f"cannot write {type(v).__name__} to TOML")


def dumps(data: dict, header_comment: str = "") -> str:
    lines: list[str] = []
    if header_comment:
        for ln in header_comment.strip("\n").splitlines():
            lines.append(("# " + ln).rstrip())
        lines.append("")

    def emit_table(prefix: list[str], table: dict) -> None:
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
        subs = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        if prefix and (scalars or not subs):
            lines.append("[" + ".".join(_key(p) for p in prefix) + "]")
        for k, v in scalars:
            lines.append(f"{_key(k)} = {_value(v)}")
        if prefix and (scalars or not subs):
            lines.append("")
        for k, v in subs:
            emit_table(prefix + [k], v)

    emit_table([], data)
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- fallback parser
class _MiniParser:
    def __init__(self, text: str):
        self.s = text.replace("\r\n", "\n")
        self.i = 0
        self.root: dict = {}

    # -- helpers
    def _err(self, msg: str):
        line = self.s.count("\n", 0, self.i) + 1
        raise TomlError(f"line {line}: {msg}")

    def _peek(self) -> str:
        return self.s[self.i] if self.i < len(self.s) else ""

    def _skip_ws(self, newlines: bool = False) -> None:
        while self.i < len(self.s):
            c = self.s[self.i]
            if c in " \t" or (newlines and c == "\n"):
                self.i += 1
            elif c == "#":
                while self.i < len(self.s) and self.s[self.i] != "\n":
                    self.i += 1
            else:
                break

    def _table_at(self, keys: list[str]) -> dict:
        t = self.root
        for k in keys:
            nxt = t.setdefault(k, {})
            if not isinstance(nxt, dict):
                self._err(f"key {k!r} is not a table")
            t = nxt
        return t

    # -- grammar
    def parse(self) -> dict:
        current = self.root
        while True:
            self._skip_ws(newlines=True)
            if self.i >= len(self.s):
                return self.root
            if self._peek() == "[":
                if self.s.startswith("[[", self.i):
                    self._err("arrays of tables are not supported by the fallback parser")
                self.i += 1
                keys = self._keys("]")
                self.i += 1
                current = self._table_at(keys)
            else:
                keys = self._keys("=")
                self.i += 1
                self._skip_ws()
                val = self._val()
                tgt = current
                for k in keys[:-1]:
                    tgt = tgt.setdefault(k, {})
                tgt[keys[-1]] = val
            self._skip_ws()
            if self.i < len(self.s) and self._peek() != "\n":
                self._err("expected end of line")

    def _keys(self, end: str) -> list[str]:
        keys = []
        while True:
            self._skip_ws()
            c = self._peek()
            if c == '"':
                keys.append(self._basic_str())
            elif c == "'":
                keys.append(self._literal_str())
            else:
                m = re.compile(r"[A-Za-z0-9_-]+").match(self.s, self.i)
                if not m:
                    self._err("bad key")
                keys.append(m.group(0))
                self.i = m.end()
            self._skip_ws()
            c = self._peek()
            if c == ".":
                self.i += 1
                continue
            if c == end:
                return keys
            self._err(f"expected {end!r}")

    def _val(self):
        c = self._peek()
        if c == '"':
            return self._basic_str()
        if c == "'":
            return self._literal_str()
        if c == "[":
            return self._array()
        if c == "{":
            return self._inline_table()
        m = re.compile(r"true|false|[+-]?(\d[\d_]*)(\.\d[\d_]*)?([eE][+-]?\d+)?").match(self.s, self.i)
        if not m:
            self._err("bad value")
        self.i = m.end()
        tok = m.group(0)
        if tok == "true":
            return True
        if tok == "false":
            return False
        tok = tok.replace("_", "")
        return float(tok) if (m.group(2) or m.group(3)) else int(tok)

    def _basic_str(self) -> str:
        if self.s.startswith('"""', self.i):
            self.i += 3
            end = self.s.find('"""', self.i)
            if end < 0:
                self._err("unterminated string")
            raw = self.s[self.i:end]
            self.i = end + 3
            return raw[1:] if raw.startswith("\n") else raw
        self.i += 1
        out = []
        while True:
            if self.i >= len(self.s):
                self._err("unterminated string")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(out)
            if c == "\n":
                self._err("newline in string")
            if c == "\\":
                n = self.s[self.i + 1:self.i + 2]
                esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "b": "\b", "f": "\f"}
                if n in esc:
                    out.append(esc[n])
                    self.i += 2
                    continue
                if n in ("u", "U"):
                    width = 4 if n == "u" else 8
                    out.append(chr(int(self.s[self.i + 2:self.i + 2 + width], 16)))
                    self.i += 2 + width
                    continue
                self._err("bad escape")
            out.append(c)
            self.i += 1

    def _literal_str(self) -> str:
        self.i += 1
        end = self.s.find("'", self.i)
        if end < 0:
            self._err("unterminated string")
        val = self.s[self.i:end]
        self.i = end + 1
        return val

    def _array(self) -> list:
        self.i += 1
        out = []
        while True:
            self._skip_ws(newlines=True)
            if self._peek() == "]":
                self.i += 1
                return out
            out.append(self._val())
            self._skip_ws(newlines=True)
            if self._peek() == ",":
                self.i += 1
                continue
            if self._peek() == "]":
                self.i += 1
                return out
            self._err("expected , or ] in array")

    def _inline_table(self) -> dict:
        self.i += 1
        out: dict = {}
        self._skip_ws()
        if self._peek() == "}":
            self.i += 1
            return out
        while True:
            keys = self._keys("=")
            self.i += 1
            self._skip_ws()
            tgt = out
            for k in keys[:-1]:
                tgt = tgt.setdefault(k, {})
            tgt[keys[-1]] = self._val()
            self._skip_ws()
            if self._peek() == ",":
                self.i += 1
                self._skip_ws()
                continue
            if self._peek() == "}":
                self.i += 1
                return out
            self._err("expected , or } in inline table")
