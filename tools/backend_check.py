"""Static check for KINO's Python modules, without any dependency: names a
function or a module uses as globals but that no module binds (no definition,
no import, no star import, not a builtin). Python's own symbol tables
(`symtable`) give the scopes, so local names never hide a missing global.

Run with an isolated HOME: star-imported modules are imported to list their
names.
"""
import builtins
import importlib
import json
import re
import sys
import symtable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Names that exist at run time without being bound in the source.
RUNTIME_NAMES = {"__file__", "__name__", "__doc__", "__spec__", "__loader__", "__package__", "__builtins__",
                 "__path__", "__annotations__", "__dict__", "__module__", "__qualname__", "__class__"}


def module_files(root=ROOT):
    return sorted(path for path in (root / "src").glob("*.py"))


def star_names(module_name):
    module = importlib.import_module(module_name)
    names = getattr(module, "__all__", None)
    return set(names) if names is not None else {name for name in dir(module) if not name.startswith("_")}


def bound_at_module(table):
    return {symbol.get_name() for symbol in table.get_symbols()
            if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace() or symbol.is_parameter()}


def global_references(table, scope="<module>"):
    """(scope, name) for every name a scope reads as a global."""
    found = []
    for symbol in table.get_symbols():
        if not symbol.is_referenced():
            continue
        is_module = table.get_type() == "module"
        if (is_module and not (symbol.is_assigned() or symbol.is_imported())) or (not is_module and symbol.is_global()):
            found.append((scope, symbol.get_name()))
    for child in table.get_children():
        found.extend(global_references(child, f"{scope}.{child.get_name()}" if scope != "<module>" else child.get_name()))
    return found


def declared_global(table):
    """Names assigned through `global x` in functions: bound at module level at run time."""
    names = set()
    for symbol in table.get_symbols():
        if symbol.is_declared_global() and symbol.is_assigned():
            names.add(symbol.get_name())
    for child in table.get_children():
        names |= declared_global(child)
    return names


def check(root=ROOT):
    sys.path.insert(0, str(root))
    known_builtins = set(dir(builtins)) | RUNTIME_NAMES
    problems = []
    for path in module_files(root):
        source = path.read_text(encoding="utf-8")
        table = symtable.symtable(source, str(path), "exec")
        available = bound_at_module(table) | declared_global(table) | known_builtins
        for match in re.finditer(r"^\s*from\s+([\w.]+)\s+import\s+\*", source, re.M):
            try:
                available |= star_names(match[1])
            except Exception as error:  # a module that cannot import is a problem too
                problems.append({"file": path.name, "scope": "<module>", "name": f"import * from {match[1]}: {error}"})
        for scope, name in global_references(table):
            if name not in available:
                problems.append({"file": path.name, "scope": scope, "name": name})
    return problems


if __name__ == "__main__":
    print(json.dumps(check(), indent=2, ensure_ascii=False))
