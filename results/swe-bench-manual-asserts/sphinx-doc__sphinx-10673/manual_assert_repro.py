#!/usr/bin/env python3
"""Manual assert derived from the agent's own final_test.py + reproduce_issue.py.
The agent built a Sphinx project whose toctree references the built-in special
pages genindex / modindex / search, and checked the build output for the
warnings "nonexisting document 'genindex'" (etc.). With the bug those warnings
are emitted; after the fix they are not. We build with the warning stream
captured and assert none of those warning strings appear."""

import io
import tempfile
from pathlib import Path

from sphinx.application import Sphinx
from sphinx.util.docutils import docutils_namespace

with tempfile.TemporaryDirectory() as tmpdir:
    srcdir = Path(tmpdir) / "source"
    outdir = Path(tmpdir) / "build"
    doctreedir = outdir / ".doctrees"
    srcdir.mkdir(parents=True)

    (srcdir / "conf.py").write_text(
        "project = 'Test Project'\nextensions = []\nhtml_theme = 'default'\n"
    )
    (srcdir / "index.rst").write_text(
        "Test Documentation\n"
        "==================\n\n"
        ".. toctree::\n"
        "   :maxdepth: 1\n"
        "   :caption: Indices and tables\n\n"
        "   genindex\n"
        "   modindex\n"
        "   search\n\n"
        "Welcome to the test documentation.\n"
    )

    warning_stream = io.StringIO()
    with docutils_namespace():
        app = Sphinx(
            srcdir=str(srcdir),
            confdir=str(srcdir),
            outdir=str(outdir),
            doctreedir=str(doctreedir),
            buildername="html",
            warning=warning_stream,
            verbosity=0,
        )
        app.build()

    output = warning_stream.getvalue()
    print("BUILD WARNINGS:\n" + output)

    for name in ("genindex", "modindex", "search"):
        bad = f"nonexisting document '{name}'"
        assert bad not in output, f"unexpected warning present: {bad}"

print("ASSERT-OK")
