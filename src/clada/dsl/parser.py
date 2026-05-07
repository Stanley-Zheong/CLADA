"""
S-expression parser for CLADA DSL.
Reads LISP-style expressions into nested Python structures.

Forms:
  (tag positional* key: value* nested-form*)

A form starts with a tag (symbol), followed by:
  - Positional arguments (bare atoms)
  - Key:value pairs (atom ending in ':' followed by a value)
  - Nested forms (parenthesized lists)

The result is a Python list: [tag, *positionals, {key: value*, form-tag: [form-body*]*}]
"""

import re
from pathlib import Path
from typing import Union

SExpr = Union[str, int, float, bool, None, list, dict]

_TOKEN_RE = re.compile(r"""
    \s*(?:
        ;[^\n]*                     |  # line comment
        (?P<open>[([])              |  # open paren/bracket
        (?P<close>[)\]])            |  # close paren/bracket
        "(?P<string>(?:[^"\\]|\\.)*)"  |  # double-quoted string
        (?P<atom>[^()\[\]\s"';]+)      # atom
    )
""", re.VERBOSE | re.UNICODE)


def _atom_value(token: str) -> Union[str, int, float, bool, None]:
    """Convert a raw token to its Python value."""
    if token in ("null", "nil"):
        return None
    if token.lower() == "true":
        return True
    if token.lower() == "false":
        return False
    if re.match(r'^-?\d+$', token):
        return int(token)
    if re.match(r'^-?\d+\.\d+$', token):
        return float(token)
    return token


def _parse_tokens(source: str) -> list:
    """
    Tokenize and build a raw nested list structure.
    Each form is [token | nested_list, ...].
    Key:value pairs are not yet resolved.
    """
    stack: list = [[]]  # outermost list collects top-level forms

    for m in _TOKEN_RE.finditer(source):
        if m.lastgroup == "open":
            bracket = m.group("open")
            if bracket == "(":
                new_list = []
                stack[-1].append(new_list)
                stack.append(new_list)
            else:  # "["
                new_list = ["__list__"]  # marker for list type
                stack[-1].append(new_list)
                stack.append(new_list)

        elif m.lastgroup == "close":
            if len(stack) > 1:
                stack.pop()

        elif m.lastgroup == "string":
            val = m.group("string")
            val = val.replace('\\"', '"').replace('\\n', '\n').replace('\\t', '\t')
            stack[-1].append(("string", val))

        elif m.lastgroup == "atom":
            token = m.group("atom")
            stack[-1].append(("atom", token, _atom_value(token)))

    return stack[0]


def _resolve_form(form: list) -> list:
    """
    Resolve a raw form into [tag, *positionals, {key: value*, ...}].

    A form is: [("atom", tag, _), positional-atoms..., ("atom", "key:", _), value..., nested-forms...]

    Strategy:
    1. First element ("atom", tag, _) → tag
    2. Collect remaining items:
       - ("atom", "key:", _) → start a pending key
       - next item becomes the value for that key
       - nested lists → process recursively, group by their tag
       - bare atoms without preceding key → positional
    """
    if not form or not isinstance(form, list):
        return form

    result = []
    kv_dict = {}
    pending_key = None
    pending_val = None

    for i, item in enumerate(form):
        if isinstance(item, tuple) and item[0] == "atom":
            name = item[1]
            val = item[2]

            if name.endswith(":") and len(name) > 1:
                # This is a key
                if pending_key is not None:
                    # Previous pending key had no value → treat as flag (True)
                    kv_dict[pending_key] = True
                pending_key = name[:-1]
                pending_val = None
            else:
                # This is a value (or positional)
                if pending_key is not None:
                    if pending_val is None:
                        kv_dict[pending_key] = val
                        pending_key = None
                        pending_val = None
                    else:
                        # Multiple values for the same key → collect as list
                        if isinstance(kv_dict[pending_key], list):
                            kv_dict[pending_key].append(val)
                        else:
                            kv_dict[pending_key] = [kv_dict[pending_key], val]
                else:
                    result.append(val)

        elif isinstance(item, tuple) and item[0] == "string":
            val = item[1]
            if pending_key is not None:
                kv_dict[pending_key] = val
                pending_key = None
            else:
                result.append(val)

        elif isinstance(item, list):
            # Nested form — resolve recursively
            nested = _resolve_form(item)
            if nested and isinstance(nested, list):
                tag = nested[0] if isinstance(nested[0], str) else ""
                if isinstance(tag, str) and tag and not tag.startswith("__"):
                    # Group by tag: (constraint ...) → kv_dict["constraint"] = [body]
                    body = nested[1] if len(nested) == 2 else nested[1:]
                    if len(body) == 1 and isinstance(body, list):
                        body = body[0]
                    existing = kv_dict.get(tag)
                    if existing is None:
                        kv_dict[tag] = body
                    elif isinstance(existing, list):
                        existing.append(body)
                    else:
                        kv_dict[tag] = [existing, body]
                else:
                    result.append(nested)
            else:
                if pending_key is not None:
                    kv_dict[pending_key] = nested
                    pending_key = None
                else:
                    result.append(nested)

    # Flush pending key
    if pending_key is not None:
        kv_dict[pending_key] = True

    if kv_dict:
        result.append(kv_dict)

    return result


def parse_string(source: str) -> list[SExpr]:
    """Parse a CLADA DSL string into a list of resolved S-expressions."""
    raw_forms = _parse_tokens(source)
    return [_resolve_form(f) for f in raw_forms if isinstance(f, list) and f]


def parse_file(path: Path) -> list[SExpr]:
    """Parse a .dsl file into S-expressions."""
    return parse_string(path.read_text(encoding="utf-8"))
