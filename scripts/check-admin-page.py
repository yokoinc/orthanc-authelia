#!/usr/bin/env python3
"""Coherence checks between admin.html, admin.js and admin_module.py.

None of these can be caught by the test suite. The page is rendered by the
server and driven by hand-written JS: a route renamed on one side, a tab
added without its panel, an onclick pointing at a function that no longer
exists -- all of it passes pytest and fails only in the browser, on the one
tab nobody opened.

Each check below exists because that class of failure actually happened:

  - a saveViewer() left behind, calling PUT /api/admin/sharing months after
    that route was deleted;
  - a whole configuration tab calling eleven functions that had been renamed
    on the script side only.

Exit code 1 on the first inconsistency.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

SOURCES = Path("services/auth-service/sources")
MODULE = SOURCES / "admin_module.py"
SCRIPT = SOURCES / "static/admin.js"
PAGE = SOURCES / "templates/admin.html"


def normalise(path: str) -> str:
    """A backend {name} and a frontend ${name} are the same placeholder."""
    path = path.split("?")[0].rstrip("/")
    return re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "*", path)


def routes_called_are_declared(module: str, script: str) -> list[str]:
    declared = {normalise(m) for m in re.findall(
        r'@router\.(?:get|put|post|patch|delete)\("([^"]+)"', module)}
    called = {normalise(m) for m in re.findall(
        r"api\(\s*[`'\"]([^`'\"]+)[`'\"]", script)}
    return sorted(c for c in called
                  if c.startswith("/api/") and c not in declared)


def elements_read_exist(script: str, page: str) -> list[str]:
    read = re.findall(r"getElementById\('([a-zA-Z0-9_-]+)'", script)
    read += re.findall(r"querySelector\('#([a-zA-Z0-9_-]+)", script)
    # An id built at runtime ('echo-' + name) cannot be checked statically.
    return sorted({i for i in read
                   if not i.endswith("-") and f'id="{i}"' not in page})


def tabs_and_panels_agree(script: str, page: str) -> str:
    tabs = set(re.findall(r'data-tab="([a-z]+)"', page))
    panels = set(re.findall(r'id="panel-([a-z]+)"', page))
    switch = re.search(r"\[([^\]]+)\]\.forEach\(t =>", script)
    listed = set(re.findall(r"'([a-z]+)'", switch.group(1))) if switch else set()
    if tabs == panels == listed:
        return ""
    return (f"tabs={sorted(tabs)} panels={sorted(panels)} "
            f"js={sorted(listed)}")


def onclick_targets_exist(script: str, page: str) -> list[str]:
    called = set(re.findall(r'onclick="([a-zA-Z_$][\w$]*)\(', page))
    defined = set(re.findall(r"function ([a-zA-Z_$][\w$]*)", script))
    return sorted(called - defined)


def dead_python(module: str) -> list[str]:
    tree = ast.parse(module)
    routes = {n.name for n in tree.body
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and any("router" in ast.dump(d) for d in n.decorator_list)}
    corpus = ""
    for path in subprocess.run(["git", "ls-files"], capture_output=True,
                               text=True, check=True).stdout.split():
        try:
            corpus += Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            pass
    return sorted(
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name not in routes
        and len(re.findall(r"\b" + re.escape(n.name) + r"\b", corpus)) <= 1)


def dead_javascript(script: str, page: str) -> list[str]:
    """Functions defined in the script, called from neither the script nor
    the page.

    This is the check that would have caught saveViewer(): its control had
    left the template months earlier, the handler stayed, and it went on
    pointing at a route that no longer existed.
    """
    dead = []
    for name in re.findall(r"\bfunction ([a-zA-Z_$][\w$]*)", script):
        word = r"\b" + re.escape(name) + r"\b"
        # One occurrence in the script is the definition itself.
        if len(re.findall(word, script)) <= 1 and not re.search(word, page):
            dead.append(name)
    return sorted(dead)


def main() -> int:
    module = MODULE.read_text(encoding="utf-8")
    script = SCRIPT.read_text(encoding="utf-8")
    page = PAGE.read_text(encoding="utf-8")

    failures = 0

    def report(problem, title: str) -> None:
        nonlocal failures
        if problem:
            failures += 1
            detail = problem if isinstance(problem, str) else ", ".join(problem)
            print(f"FAIL  {title}: {detail}")
        else:
            print(f"ok    {title}")

    report(routes_called_are_declared(module, script),
           "every /api/ route the page calls is declared by the backend")
    report(elements_read_exist(script, page),
           "every element the script reads exists in the page")
    report(tabs_and_panels_agree(script, page),
           "tabs, panels and the script's switch list agree")
    report(onclick_targets_exist(script, page),
           "every onclick target is a defined function")
    report(dead_python(module),
           "no Python function is defined and never called")
    report(dead_javascript(script, page),
           "no script function is defined and never called")

    if failures:
        print(f"\n{failures} inconsistency(ies).")
        return 1
    print("\nadmin.html, admin.js and admin_module.py agree.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
