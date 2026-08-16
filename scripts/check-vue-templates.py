#!/usr/bin/env python3
"""Three coherence checks the build and the unit tests cannot perform.

1. Every name a template calls exists in its own script. Vue compiles a
   template without resolving its identifiers, so a rename touching only
   half a component breaks neither the Vite build nor the tests: the
   failure appears in the browser console, on the one tab nobody opened.
   That is how a whole configuration tab shipped calling functions that no
   longer existed.

2. No orphan left in the script. The mirror of the first check catches the
   opposite drift: a control removed from the template while its handler
   stays behind, calling a route that has since been deleted.

3. Every route the frontend calls is declared by the backend. This is what
   turns a moved feature into a dead button: the panel kept a saveViewer()
   pointed at PUT /api/admin/sharing months after that route was removed.

Exit code 1 on the first inconsistency.
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

        for orphan in orphan_members(script.group(1), text, script.span()):
            problems += 1
            print(f"{path}: declared but used nowhere: {orphan}")

    problems += check_routes()

    if problems:
        print(f"\n{problems} inconsistency(ies).")
        return 1
    print(f"{len(files)} component(s) checked: templates resolve, no orphan, "
          "every route called exists.")
    return 0


def orphan_members(script: str, text: str, script_span: tuple[int, int]) -> list[str]:
    """Functions and state declared, then referenced neither in the script
    nor in the template."""
    rest = text[:script_span[0]] + text[script_span[1]:]
    template = re.sub(r"<style[^>]*>(?:.|\n)*?</style>", "", rest)

    orphans = []
    declarations = (re.findall(r"\bfunction\s+([A-Za-z_$][\w$]*)", script)
                    + re.findall(r"\bconst\s+([A-Za-z_$][\w$]*)\s*=\s*"
                                 r"(?:ref|computed|reactive)\(", script))
    for name in declarations:
        word = r"\b" + re.escape(name) + r"\b"
        # One occurrence in the script is its own declaration.
        if len(re.findall(word, script)) <= 1 and not re.search(word, template):
            orphans.append(name)
    return orphans


def check_routes() -> int:
    """Every /api/... path the frontend calls must be declared by the backend."""
    def normalise(path: str) -> str:
        path = path.split("?")[0].rstrip("/")
        path = re.sub(r"^/console", "", path)
        # {name} on one side, ${...} on the other: same placeholder.
        path = re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "*", path)
        return path

    module = open("services/auth-service/sources/admin_module.py",
                  encoding="utf-8").read()
    declared = {normalise(m) for m in re.findall(
        r"@router\.(?:get|put|post|patch|delete)\(\"([^\"]+)\"", module)}

    called = set()
    for path in subprocess.run(["git", "ls-files", "services/auth-service/frontend/src"],
                               capture_output=True, text=True,
                               check=True).stdout.split():
        source = open(path, encoding="utf-8").read()
        for call in re.findall(r"api\(\s*[`'\"]([^`'\"]+)[`'\"]", source):
            if call.startswith("/console/api/") or call.startswith("/api/"):
                called.add((normalise(call), path))

    missing = sorted({(route, path) for route, path in called
                      if route not in declared
                      and normalise("/console" + route) not in declared
                      and route.replace("/api/setup/", "/setup/") not in declared})
    for route, path in missing:
        print(f"{path}: calls {route}, which no backend route declares")
    return len(missing)


if __name__ == "__main__":
    sys.exit(main())
