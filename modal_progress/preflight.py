"""Finds local modules an app imports that Modal won't upload.

`modal run app.py` adds app.py to the container and nothing else from its
folder. A top-level `import helper` of a local helper.py works on the
laptop, then every container fails on startup with ModuleNotFoundError,
often after the GPUs are already booked. This reads the file before the
run and names those modules.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Missing:
    module: str
    path: Path
    line: int

    def message(self, app: Path) -> str:
        return (
            f"{app.name}:{self.line} imports {self.module} from {self.path.name}, which Modal won't upload, "
            f"so containers will fail with ModuleNotFoundError. "
            f'Add .add_local_python_source("{self.module}") to the image.'
        )


def _local(name: str, dirs: list[Path]) -> Path | None:
    for d in dirs:
        for candidate in (d / f"{name}.py", d / name):
            if candidate.is_file() or (candidate.is_dir() and any(candidate.glob("*.py"))):
                return candidate
    return None


def _strings(tree: ast.AST) -> set[str]:
    """Strings passed to a call, so a docstring that names a module doesn't count."""
    found = set()
    for call in ast.walk(tree):
        if isinstance(call, ast.Call):
            for arg in [*call.args, *(k.value for k in call.keywords)]:
                found |= {n.value for n in ast.walk(arg) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    return found


def _covered(name: str, strings: set[str]) -> bool:
    """The module's name in any string, as in add_local_python_source("helper"),
    add_local_dir("helper", ...) or pip_install("helper"). Loose on purpose:
    a missed warning costs less than a wrong one."""
    word = re.compile(rf"(?<![\w.-]){re.escape(name)}(?![\w-])")
    return any(word.search(s) for s in strings)


def check(app: str | Path) -> list[Missing]:
    app = Path(app).resolve()
    source = app.read_text(encoding="utf8")
    tree = ast.parse(source, filename=str(app))
    if not any(
        (isinstance(n, ast.Import) and any(a.name.split(".")[0] == "modal" for a in n.names))
        or (isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "modal")
        for n in ast.walk(tree)
    ):
        return []  # not a Modal app
    strings = _strings(tree)
    # A whole folder copied to /root covers every module in it.
    if "add_local_dir" in source and any(s.rstrip("/") == "/root" for s in strings):
        return []
    dirs = [app.parent]
    own = {app.stem}
    if (app.parent / "__init__.py").exists():
        own.add(app.parent.name)  # its own package
    found: list[Missing] = []
    seen: set[str] = set()
    # Only unconditional top-level imports: those run on container startup.
    # Imports under `if`, `try` or `with image.imports()` are guarded on purpose.
    for node in tree.body:
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        for full in names:
            name = full.split(".")[0]
            if name in seen or name in own or name == "modal" or name in sys.stdlib_module_names:
                continue
            seen.add(name)
            path = _local(name, dirs)
            if path is not None and not _covered(name, strings):
                found.append(Missing(name, path, node.lineno))
    return found


def target(args: list[str]) -> Path | None:
    """The app file in `modal run` arguments, or None when it is run as a module."""
    if "-m" in args:
        return None
    for a in args:
        file = a.split("::")[0]
        if file.endswith(".py") and Path(file).is_file():
            return Path(file)
    return None
