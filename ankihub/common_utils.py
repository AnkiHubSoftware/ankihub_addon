"""This module contains utility functions used by both ankihub addon and by ankihub client."""

import hashlib
import html
import re
from typing import Set

from anki.models import NotetypeDict
from anki.utils import strip_html

# Media extraction logic is ported from Anki - see rslib/src/text.rs

HTML_MEDIA_TAGS = re.compile(
    r"""(?xsi)
    # the start of the image, audio, object, or source tag
    <\b(?:img|audio|video|object|source)\b

    # any non-`>`, except inside `"` or `'`
    (?:
        [^>]
    |
        "[^"]+?"
    |
        '[^']+?'
    )+?

    # capture `src` or `data` attribute
    \b(?:src|data)\b=
    (?:
            # 1: double-quoted filename
            "
            ([^"]+?)
            "
            [^>]*>
        |
            # 2: single-quoted filename
            '
            ([^']+?)
            '
            [^>]*>
        |
            # 3: unquoted filename
            ([^ >]+?)
            (?:
                # then either a space and the rest
                \x20[^>]*>
                |
                # or the tag immediately ends
                >
            )
    )
    """
)

AV_TAGS = re.compile(
    r"""(?xs)
    \[sound:(.+?)\]     # 1 - the filename in a sound tag
    |
    \[anki:tts\]
        \[(.*?)\]       # 2 - arguments to tts call
        (.*?)           # 3 - field text
    \[/anki:tts\]"""
)

LATEX = re.compile(
    r"""(?xsi)
    \[latex\](.+?)\[/latex\]     # 1 - standard latex
    |
    \[\$\](.+?)\[/\$\]           # 2 - inline math
    |
    \[\$\$\](.+?)\[/\$\$\]       # 3 - math environment
    """
)
LATEX_NEWLINES = re.compile(
    r"""(?xi)
        <br( /)?>
        |
        <div>
    """
)

# Skip remote (http/https) filenames
REMOTE_FILENAME = re.compile(r"(?i)^https?://")


# Files included in CSS with a leading underscore
UNDERSCORED_CSS_IMPORTS = re.compile(
    r"""(?xi)
    (?:@import\s+           # import statement with a bare
        "(_[^"]*.css)"      # double quoted
        |                   # or
        '(_[^']*.css)'      # single quoted css filename
    )
    |                       # or
    (?:url\(\s*             # a url function with a
        "(_[^"]+)"          # double quoted
        |                   # or
        '(_[^']+)'          # single quoted
        |                   # or
        (_.+?)              # unquoted filename
    \s*\))
    """
)

# Strings, src and data attributes with a leading underscore
UNDERSCORED_REFERENCES = re.compile(
    r"""(?x)
        \[sound:(_[^]]+)\]  # a filename in an Anki sound tag
    |                       # or
        "(_[^"]+)"          # a double quoted
    |                       # or
        '(_[^']+)'          # single quoted string
    |                       # or
        \b(?:src|data)      # a 'src' or 'data' attribute
        =                   # followed by
        (_[^ >]+)           # an unquoted value
    """
)


def _decode_entities(text: str) -> str:
    if "&" not in text:
        return text
    return html.unescape(text).replace("\u00a0", " ")


def _is_local_filename(fname: str) -> bool:
    return not bool(REMOTE_FILENAME.match(fname.strip()))


def _extract_html_media_refs(text: str) -> Set[str]:
    result: Set[str] = set()
    for m in HTML_MEDIA_TAGS.finditer(text):
        fname = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        fname_decoded = _decode_entities(fname)
        if _is_local_filename(fname_decoded):
            result.add(fname_decoded)
    return result


def _extract_av_tags(text: str) -> Set[str]:
    result: Set[str] = set()
    for m in AV_TAGS.finditer(text):
        fname = m.group(1).strip()
        fname_decoded = _decode_entities(fname)
        if _is_local_filename(fname_decoded):
            result.add(fname_decoded)
    return result


def _extract_latex(text: str, svg: bool) -> Set[str]:
    result: Set[str] = set()
    for m in LATEX.finditer(text):
        g1 = m.group(1)
        g2 = m.group(2)
        g3 = m.group(3)
        if g1:
            latex = g1
        elif g2:
            latex = f"${g2}$"
        else:
            latex = rf"\begin{{displaymath}}{g3}\end{{displaymath}}"
        latex = _strip_html_for_latex(latex)
        result.add(_fname_for_latex(latex, svg))

    return result


def _strip_html_for_latex(latex: str) -> str:
    latex = LATEX_NEWLINES.sub(latex, "\n")
    return strip_html(latex)


def _fname_for_latex(latex: str, svg: bool) -> str:
    ext = "svg" if svg else "png"
    csum = hashlib.sha1(latex.encode()).hexdigest()
    return f"latex-{csum}.{ext}"


def _extract_underscored_css_imports(text: str) -> Set[str]:
    result: Set[str] = set()
    for m in UNDERSCORED_CSS_IMPORTS.finditer(text):
        fname = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or m.group(5) or "").strip()
        if fname:
            result.add(fname)
    return result


def _extract_underscored_references(text: str) -> Set[str]:
    result: Set[str] = set()
    for m in UNDERSCORED_REFERENCES.finditer(text):
        fname = (m.group(1) or m.group(2) or m.group(3) or m.group(4) or "").strip()
        if fname:
            fname_decoded = _decode_entities(fname)
            if _is_local_filename(fname_decoded):
                result.add(fname_decoded)
    return result


def _prefers_svg_latex(note_type: NotetypeDict) -> bool:
    return note_type.get("latexsvg", False)


def gather_media_names_from_note_type(note_type: NotetypeDict) -> Set[str]:
    """Gather media filenames with leading underscore from note type."""
    result: Set[str] = set()
    result.update(_extract_underscored_css_imports(note_type["css"]))
    for template in note_type["tmpls"]:
        for side in ("qfmt", "afmt"):
            result.update(_extract_underscored_references(template[side]))
    return result


def gather_media_names_from_note_field(html_content: str, note_type: NotetypeDict) -> Set[str]:
    """Gather local media filenames from field content."""
    result: Set[str] = set()
    result.update(_extract_html_media_refs(html_content))
    result.update(_extract_av_tags(html_content))
    result.update(_extract_latex(html_content, _prefers_svg_latex(note_type)))

    return result
