import ast
from pathlib import Path


def _extract_functions(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(f"{node.name}.{item.name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not any(node in ast.walk(c) for c in ast.walk(tree) if isinstance(c, ast.ClassDef)):
                names.append(node.name)
    return names


def _extract_imports(tree: ast.Module) -> list[str]:
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return imports


def _extract_fixture_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                deco_source = ast.dump(decorator)
                if "fixture" in deco_source:
                    names.append(node.name)
    return names


def scan_repo(repo_path: Path) -> dict:
    modules = []
    fixtures = []
    directories = set()

    for py_file in sorted(repo_path.rglob("*.py")):
        rel_path = py_file.relative_to(repo_path)
        if any(part in (".git", "__pycache__") for part in rel_path.parts):
            continue

        directories.add(rel_path.parts[0]) if len(rel_path.parts) > 1 else None

        source = py_file.read_text()
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        if py_file.name == "conftest.py":
            fixture_names = _extract_fixture_names(tree)
            for name in fixture_names:
                fixtures.append({"name": name, "file": str(rel_path)})
            continue

        modules.append({
            "path": str(rel_path),
            "functions": _extract_functions(tree),
            "imports": _extract_imports(tree),
        })

    return {
        "modules": modules,
        "fixtures": fixtures,
        "directories": sorted(directories),
    }
