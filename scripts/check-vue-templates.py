#!/usr/bin/env python3
"""Check that every name a Vue template calls exists in its own script.

A rename that only touches half a single-file component breaks neither the
Vite build nor the unit tests: Vue compiles templates without resolving
their identifiers, so the failure only appears at runtime, in the browser
console, on the one tab nobody opened. That is exactly how a whole
configuration tab shipped calling functions that no longer existed.

Exit code 1 as soon as a template references something its script does not
declare.
"""
import re
import subprocess
import sys

# Names available without being declared in the script block.
GLOBALS_ = {
    "true", "false", "null", "undefined", "Object", "Array", "JSON", "Math",
    "String", "Number", "Boolean", "Date", "Set", "Map", "console", "window",
    "document", "t", "$event", "$emit", "$slots", "$attrs", "of", "in",
    "typeof", "instanceof", "new", "return", "if", "else", "length", "value",
    "push", "filter", "map", "join", "split", "trim", "includes", "toString",
}


def declared_names(script: str) -> set[str]:
    names = set(re.findall(r"\b(?:const|let|var|function)\s+([A-Za-z_$][\w$]*)",
                           script))
    for motif in (r"import\s+\{([^}]*)\}", r"\b(?:const|let)\s*\{([^}]*)\}\s*="):
        for bloc in re.findall(motif, script):
            names |= set(re.findall(r"[A-Za-z_$][\w$]*", bloc))
    names |= set(re.findall(r"import\s+([A-Za-z_$][\w$]*)\s+from", script))
    return names


def template_names(text: str, script_span: tuple[int, int]) -> tuple[set[str], set[str], list[str]]:
    """Return (names used, local aliases, raw expressions)."""
    excluded = [script_span]
    for m in re.finditer(r"<style[^>]*>(?:.|\n)*?</style>", text):
        excluded.append((m.start(), m.end()))

    def outside(pos: int) -> bool:
        return any(start <= pos < end for start, end in excluded)

    expressions, aliases = [], set()
    for m in re.finditer(r"\{\{(?:[^}]|\}(?!\}))*\}\}", text):
        if not outside(m.start()):
            expressions.append(m.group(0)[2:-2])

    for tag in re.finditer(r"<[a-zA-Z][^>]*>", text):
        if outside(tag.start()):
            continue
        for attr in re.finditer(r"((?:[:@#]|v-)[\w:.\-\[\]]*)\s*=\s*\"([^\"]*)\"",
                                tag.group(0)):
            name, value = attr.group(1), attr.group(2)
            if name.startswith("v-for"):
                left = value.split(" in ")[0].strip(" ()")
                aliases |= set(re.findall(r"[A-Za-z_$][\w$]*", left))
                value = " in ".join(value.split(" in ")[1:])
            elif name.startswith("v-slot") or name.startswith("#"):
                aliases |= set(re.findall(r"[A-Za-z_$][\w$]*", value))
                continue
            expressions.append(value)

    used = set()
    for expression in expressions:
        # Strings hold interface text, not references; a member access
        # (g.fields) only depends on its head.
        without_strings = re.sub(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"",
                                 "''", expression)
        for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)", without_strings):
            used.add(m.group(1))
    return used, aliases, expressions


def main() -> int:
    files = subprocess.run(["git", "ls-files", "*.vue"], capture_output=True,
                           text=True, check=True).stdout.split()
    problems = 0
    for path in files:
        text = open(path, encoding="utf-8").read()
        script = re.search(r"<script[^>]*>((?:.|\n)*?)</script>", text)
        if not script:
            continue

        used, aliases, expressions = template_names(
            text, (script.start(), script.end()))
        unknown = sorted(used - declared_names(script.group(1)) - GLOBALS_ - aliases)
        # An object key ({ active: x }) is not a reference.
        joined = " ".join(expressions)
        unknown = [name for name in unknown
                   if not re.search(r"\b" + re.escape(name) + r"\s*:", joined)]
        if unknown:
            problems += len(unknown)
            print(f"{path}: not declared in the script: {', '.join(unknown)}")

    if problems:
        print(f"\n{problems} template identifier(s) without a definition.")
        return 1
    print(f"{len(files)} component(s) checked, every template identifier resolves.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
