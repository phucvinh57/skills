"""Command line: finds every module in `jp.commands`, lets it register a group, prints JSON.

A command is a function `run(args) -> object`. What it returns is printed as JSON on stdout.
`Fail` becomes `{"error": ..., "hint": ...}` with exit code 1, so the agent can read both cases.
"""
import argparse
import importlib
import json
import pkgutil
import sys


class Fail(Exception):
    """An error the user or the agent can act on."""

    def __init__(self, message, hint=None, **extra):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.extra = extra


def emit(value):
    json.dump(value, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")


def group(groups, name, help_text):
    """Add a command group (`jobpilot.py <name> <command>`) and return its sub-commands."""
    parser = groups.add_parser(name, help=help_text, description=help_text)
    commands = parser.add_subparsers(dest="command", metavar="<command>")
    commands.required = True
    return commands


def command(commands, name, run, help_text):
    """Add one command to a group; returns its parser so the caller adds arguments."""
    parser = commands.add_parser(name, help=help_text, description=help_text)
    parser.set_defaults(run=run)
    return parser


def read_text_arg(args, allow_stdin=True):
    """Text given as `--text`, `--file` or stdin (`--file -`). None when none was given."""
    text = getattr(args, "text", None)
    if text:
        return text
    path = getattr(args, "file", None)
    if path == "-" and allow_stdin:
        return sys.stdin.read()
    if path:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    return None


def read_json_arg(path):
    """A JSON document from a file path or stdin (`-`)."""
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError:
        raise Fail(f"no such file: {path}")
    except json.JSONDecodeError as err:
        raise Fail(f"{path} is not valid JSON: {err}")


def build_parser():
    parser = argparse.ArgumentParser(prog="jobpilot.py", description="JobPilot skill helpers (JSON out).")
    groups = parser.add_subparsers(dest="group", metavar="<group>")
    groups.required = True
    from . import commands as package

    for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
        module = importlib.import_module(f"{package.__name__}.{info.name}")
        if hasattr(module, "register"):
            module.register(groups)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = args.run(args)
    except Fail as err:
        body = {"error": err.message}
        if err.hint:
            body["hint"] = err.hint
        body.update(err.extra)
        emit(body)
        return 1
    except BrokenPipeError:
        return 0
    if result is not None:
        emit(result)
    return 0
