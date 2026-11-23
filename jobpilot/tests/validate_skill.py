#!/usr/bin/env python3
"""Check a skill folder against the Agent Skills format (https://agentskills.io/specification):
frontmatter fields and limits, the folder name, links to files that exist, nothing but the standard
library imported by the scripts. Usage: validate_skill.py [skill folder]"""
import ast
import os
import re
import sys

OPTIONAL_IMPORTS = {"pypdf", "typst"}  # guarded imports with a fallback


def frontmatter(text):
    match = re.match(r"---\n(.*?)\n---\n", text, re.S)
    if not match:
        return None
    fields = {}
    for line in match.group(1).split("\n"):
        pair = re.match(r"([a-z-]+):\s*(.*)$", line)
        if pair:
            fields[pair.group(1)] = pair.group(2).strip().strip('"')
    return fields


def main(folder):
    problems = []
    with open(os.path.join(folder, "SKILL.md"), encoding="utf-8") as handle:
        text = handle.read()
    fields = frontmatter(text)
    if fields is None:
        problems.append("SKILL.md has no frontmatter")
        fields = {}
    name, description = fields.get("name", ""), fields.get("description", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or len(name) > 64:
        problems.append(f"name {name!r} is not lower-case words joined by single hyphens (max 64)")
    if name != os.path.basename(os.path.abspath(folder)):
        problems.append("name does not match the folder name")
    if not 1 <= len(description) <= 1024:
        problems.append(f"description has {len(description)} characters (1-1024)")
    if len(fields.get("compatibility", "")) > 500:
        problems.append("compatibility is over 500 characters")
    if text.count("\n") > 500:
        problems.append("SKILL.md is over 500 lines")

    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d not in ("tests", "__pycache__")]  # development files, not the skill
        for file in files:
            path = os.path.join(root, file)
            if file.endswith(".md"):
                with open(path, encoding="utf-8") as handle:
                    body = handle.read()
                for target in re.findall(r"\]\(((?:references|assets|scripts)/[^)#\s]+)", body):
                    if not os.path.exists(os.path.join(folder, target)):
                        problems.append(f"{os.path.relpath(path, folder)} links to a missing file: {target}")
                for target in set(re.findall(r"`?(references/[a-z-]+\.md)`?", body)):
                    if not os.path.exists(os.path.join(folder, target)):
                        problems.append(f"{os.path.relpath(path, folder)} names a missing file: {target}")
            if file.endswith(".py"):
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read(), path, feature_version=(3, 9))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.level == 0:
                        modules = [node.module or ""]
                    for module in modules:
                        top = module.split(".")[0]
                        if top not in sys.stdlib_module_names and top not in OPTIONAL_IMPORTS and top != "jp":
                            problems.append(f"{os.path.relpath(path, folder)} imports {top}, which is not in the standard library")
    for problem in problems:
        print("problem:", problem)
    if not problems:
        print(f"{name}: ok ({len(description)} characters of description)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
