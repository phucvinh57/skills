"""`answers`: the application answers the user asked to remember."""
from .. import answers
from ..cli import Fail, command, group


def _list(args):
    saved = answers.load()
    return {"answers": [dict(row, key=key) for key, row in saved.items() if isinstance(row, dict)]}


def _get(args):
    row = answers.find(args.question)
    return {"key": answers.normalize_key(args.question), "found": bool(row), "answer": row}


def _set(args):
    key = answers.save(args.question, args.answer)
    if not key:
        raise Fail("a question and an answer are both needed")
    return {"key": key, "saved": answers.find(args.question)}


def _forget(args):
    return {"key": answers.normalize_key(args.question), "forgotten": answers.forget(args.question)}


def register(groups):
    commands = group(groups, "answers", "Remembered application answers (the user's own words).")
    command(commands, "list", _list, "Every remembered answer, oldest first.")
    for name, run, text in (("get", _get, "The remembered answer to a question."),
                            ("set", _set, "Remember the user's answer to a question."),
                            ("forget", _forget, "Forget the answer to a question.")):
        parser = command(commands, name, run, text)
        parser.add_argument("--question", required=True)
        if name == "set":
            parser.add_argument("--answer", required=True)
