"""Compile a Typst template to PDF (and page images): the `typst` program when it is installed,
else the `typst` Python package (`pip install typst`, which bundles the compiler)."""
import glob
import os
import re
import shutil
import subprocess

from .cli import Fail

TIMEOUT = 180
INSTALL_HINT = (
    "install Typst (`pip install typst`, or the `typst` program from https://typst.app/open-source) "
    "to get a PDF; until then the HTML file can be printed to PDF from a browser"
)


class TypstError(Fail):
    """Typst refused the document. The message is its own error text, for a repair round."""


def binary():
    chosen = os.environ.get("JOBPILOT_TYPST")
    if chosen and os.path.isfile(chosen):
        return chosen
    found = shutil.which("typst")
    if found:
        return found
    home = os.path.expanduser("~")
    for folder in (os.path.join(os.environ.get("CARGO_HOME", os.path.join(home, ".cargo")), "bin"),
                   os.path.join(home, ".local", "bin"), "/usr/local/bin", "/usr/bin", "/snap/bin", "/opt/homebrew/bin"):
        for name in ("typst", "typst.exe"):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                return candidate
    return None


def _package():
    try:
        import typst  # type: ignore

        return typst
    except ImportError:
        return None


def available():
    return bool(binary() or _package())


def _clean_error(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text).strip()[:4000]


def _run(root, template, data_name, output, png):
    program = binary()
    if program:
        command = [program, "compile", "--root", root, "--input", f"data={data_name}"]
        if png:
            command += ["--format", "png", "--ppi", "110"]
        command += [template, output]
        try:
            done = subprocess.run(command, cwd=root, capture_output=True, timeout=TIMEOUT,
                                  env={**os.environ, "NO_COLOR": "1"})
        except subprocess.TimeoutExpired:
            raise Fail("typst took too long")
        if done.returncode != 0:
            raise TypstError("typst failed:\n" + _clean_error(done.stderr.decode("utf-8", errors="replace")))
        return
    package = _package()
    if not package:
        raise Fail("Typst is not installed", hint=INSTALL_HINT, no_typst=True)
    options = {"root": root, "sys_inputs": {"data": data_name}}
    if png:
        options.update(format="png", ppi=110)
    try:
        package.compile(template, output=output, **options)
    except Exception as err:  # the package raises its own error types
        raise TypstError("typst failed:\n" + _clean_error(str(err)))


def compile_pdf(root, template_name, data_name, pdf_name, pages=True):
    """Compile `<root>/<template_name>` with `data=<data_name>` to `<root>/<pdf_name>`.

    Template and data must both be inside `root`. With `pages`, also writes `page-N.png` previews.
    Returns `{"pdf": path, "pages": [png paths], "page_count": n}` (`page_count` is None without
    previews)."""
    template = os.path.join(root, template_name)
    pdf = os.path.join(root, pdf_name)
    _run(root, template, data_name, pdf, png=False)
    images = []
    if pages:
        for old in glob.glob(os.path.join(root, "page-*.png")):
            os.unlink(old)
        _run(root, template, data_name, os.path.join(root, "page-{p}.png"), png=True)
        images = sorted(glob.glob(os.path.join(root, "page-*.png")),
                        key=lambda p: int(re.search(r"page-(\d+)\.png$", p).group(1)))
    return {"pdf": pdf, "pages": images, "page_count": len(images) if pages else None}
