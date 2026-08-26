"""Import the built add-on the way Anki does, on the oldest Python Anki ships.

Run from the repository root, in its own process, with Anki installed:

    uv run --exact --group aqt_legacy --python 3.9 python scripts/check_addon_import.py

The test suite cannot do this even though it runs the add-on on 3.9. Pytest plugins and factory-boy
import django, asgiref and urllib3 from the development environment before `import ankihub` prepends
the bundle, so the bundled copies are shadowed there and never exercised.

scripts/build.py installs each layer with the oldest Python that can reach it, so a distribution
declaring a newer floor cannot be installed at all. What that cannot show is whether the result
imports: a distribution can declare 3.9 and still use newer syntax, and peewee and sentry_sdk
declare no floor to check.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
BUNDLE = PROJECT_ROOT / "ankihub" / "lib"
TARGET_PYTHON = (3, 9)

# Installed for Python 3.10 and above, which is also where the add-on gates its own use of them.
# Written out rather than derived from the bundle_modern group, because neither way of drifting from
# it is silent: a module missing from here is imported with the rest and fails, and one that does
# not belong here is caught by importable_modern_only_modules().
MODERN_ONLY_MODULES = ("protobuf", "protobuf_ext")


def target_python() -> str:
    return ".".join(str(part) for part in TARGET_PYTHON)


def bundle_top_level_modules():
    """Every name the bundle offers to `import`."""
    for entry in sorted(BUNDLE.iterdir()):
        if entry.name.endswith(".dist-info") or entry.name in ("bin", "__pycache__"):
            continue
        if entry.is_dir() and (entry / "__init__.py").exists():
            yield entry.name
        elif entry.suffix == ".py":
            yield entry.stem


def loaded_top_level_names() -> set:
    return {name.split(".")[0] for name in sys.modules}


def load_addon() -> None:
    """Import the add-on as Anki would, without starting it.

    This is also what puts the bundle within reach: ankihub/__init__.py prepends ankihub/lib to
    sys.path. Importing entry_point then pulls in the part of the bundle the add-on itself uses,
    which is how asgiref.sync comes to be loaded. Nothing checked after this means anything
    without it.
    """
    sys.path.insert(0, str(PROJECT_ROOT))
    os.environ["SKIP_INIT"] = "1"  # entry_point.run() needs a running Anki; importing is enough

    import ankihub  # noqa: F401
    import ankihub.entry_point  # noqa: F401


def wrong_bundle_module_origins(vendored, from_anki) -> list:
    """Modules resolving from somewhere other than the copy that should win."""
    bundle = str(BUNDLE.resolve())
    problems = []
    for name in sorted(vendored):
        origin = str(Path(__import__(name, fromlist=["__file__"]).__file__).resolve())
        if name in from_anki:
            if origin.startswith(bundle):
                problems.append(f"{name} came from the bundle even though Anki had already imported it")
        elif not origin.startswith(bundle):
            problems.append(f"{name} came from {origin}, not the bundle")
    return problems


def unparsable_bundle_modules() -> list:
    """Bundle modules the target Python cannot even parse.

    The checks around this one reach a module only if something imports it, which for most of the
    bundle means the add-on's own import graph - `asgiref.sync` is covered because Django's template
    stack happens to pull it in. Parsing reaches every file regardless. It is the weaker signal of
    the two: it catches syntax a release started using, not the `from typing import ParamSpec` kind
    of break, which parses on 3.9 and fails on import.
    """
    problems = []
    for path in sorted(BUNDLE.rglob("*.py")):
        if path.relative_to(BUNDLE).parts[0] in MODERN_ONLY_MODULES:
            continue
        try:
            compile(path.read_bytes(), str(path), "exec", dont_inherit=True)
        except SyntaxError as error:
            problems.append(f"{path.relative_to(BUNDLE)} does not parse on Python {target_python()}: {error}")
    return problems


def importable_modern_only_modules() -> list:
    """Modern-only modules that turn out to import here after all.

    Skipping something importable would quietly narrow every assertion above, so each skip has to
    earn itself. Forgetting one needs no check: it gets imported with the rest and fails.
    """
    problems = []
    for name in MODERN_ONLY_MODULES:
        try:
            __import__(name)
        except Exception:  # noqa: BLE001 - any failure at all means the skip was warranted
            continue
        problems.append(f"{name} imports on Python {target_python()} after all, so it should not be skipped")
    return problems


def django_rendering_problems() -> list:
    """Render through the add-on's own Django setup, rather than a copy of its configuration.

    Importing entry_point configures the template engine, which is what pulls in Django's template
    stack and with it asgiref.sync. Rendering as well runs django_cotton's compiler over the input.
    It renders plain HTML, not a component, so the component loader itself is not covered.
    """
    from ankihub.django import render_template_from_string

    rendered = render_template_from_string("<div>{{ value }}</div>", {"value": "rendered"})
    if "rendered" not in rendered:
        return [f"rendering a template produced unexpected output: {rendered!r}"]
    return []


def main() -> int:
    if sys.version_info[:2] != TARGET_PYTHON:
        sys.exit(f"must run on Python {target_python()}, got {'.'.join(str(p) for p in sys.version_info[:3])}")

    if not BUNDLE.is_dir():
        sys.exit(f"no vendored bundle at {BUNDLE}; run scripts/build.py first")

    vendored = set(bundle_top_level_modules()) - set(MODERN_ONLY_MODULES)

    already_loaded = vendored & loaded_top_level_names()
    if already_loaded:
        sys.exit(f"{sorted(already_loaded)} were imported before this check ran, so it would prove nothing")

    # Anki imports its own HTTP stack while loading the add-on manager, before any add-on runs, and
    # whatever it has loaded by then wins over the bundle for the rest of the process.
    import aqt.addons  # noqa: F401

    from_anki = vendored & loaded_top_level_names()

    # Loads the bundle modules, which is what the checks below inspect.
    load_addon()

    failures = (
        wrong_bundle_module_origins(vendored, from_anki)
        + importable_modern_only_modules()
        + unparsable_bundle_modules()
    )
    if not failures:
        failures += django_rendering_problems()  # only meaningful once the origins are right

    if failures:
        print("the built add-on did not load as expected:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"the built add-on imports and renders on Python {target_python()} under aqt {aqt.appVersion}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
