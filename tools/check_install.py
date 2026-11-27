#!/usr/bin/env python3
"""Check an installed copy of skydrop, with the Python it was installed with:

    PYTHON tools/check_install.py APP_FOLDER VERSION

APP_FOLDER holds the installed skydrop package: the installers put it in
~/.local/share/skydrop/app (Linux, macOS) or %LOCALAPPDATA%\\skydrop\\app
(Windows), and PYTHON is skydrop's private Python beside it (venv/bin/python,
or venv\\Scripts\\python.exe). The copy passes when it is VERSION, every one of
its modules imports, and Pillow draws the labels in pictures with the DejaVu
fonts that come with skydrop. CI runs this after installing the macOS and
Windows downloads from release/ on a Mac and on a Windows PC.
"""

import importlib
import os
import pkgutil
import sys

LABEL_FONTS = {                       # (bold, condensed): the font those labels are drawn in
    (True, False): "DejaVuSans-Bold.ttf",
    (False, False): "DejaVuSans.ttf",
    (True, True): "DejaVuSansCondensed-Bold.ttf",
    (False, True): "DejaVuSansCondensed.ttf",
}


def same(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def problems(app, version):
    """What's wrong with the copy of skydrop in APP: an empty list when nothing is."""
    sys.path.insert(0, os.path.abspath(app))
    try:
        import skydrop
    except Exception as exc:
        return [f"skydrop doesn't import from {app}: {exc!r}"]
    if not same(os.path.dirname(skydrop.__file__), os.path.join(app, "skydrop")):
        return [f"skydrop was imported from {os.path.dirname(skydrop.__file__)}, not from {app}"]
    found = []
    if skydrop.VERSION != version:
        found.append(f"it is version {skydrop.VERSION}, not {version}")
    for name in sorted(m.name for m in pkgutil.iter_modules(skydrop.__path__)):
        if name == "__main__":
            continue
        try:
            importlib.import_module(f"skydrop.{name}")
        except Exception as exc:      # whatever goes wrong, report it and go on to the next
            found.append(f"skydrop.{name} doesn't import: {exc!r}")
    try:
        from skydrop import gfx
    except Exception:
        return found                  # reported above
    if not gfx.HAVE_PIL:
        return found + ["Pillow doesn't load, so skydrop can only draw with text characters"]
    for (bold, condensed), name in LABEL_FONTS.items():
        path = getattr(gfx.font(14, bold=bold, condensed=condensed), "path", None)
        if not isinstance(path, str) or not same(path, os.path.join(gfx.BUNDLED_FONTS, name)):
            got = path if isinstance(path, str) else "Pillow's built-in font"
            found.append(f"labels that should be in skydrop's own {name} get {got}")
    return found


def main(argv):
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    app, version = argv[1], argv[2]
    found = problems(app, version)
    for line in found:
        print(f"check_install: {line}", file=sys.stderr)
    if found:
        return 1
    print(f"check_install: skydrop {version} in {app} is complete "
          f"(Python {sys.version_info[0]}.{sys.version_info[1]}, {sys.platform})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
