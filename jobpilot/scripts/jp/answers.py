"""Remembered application answers (`answers.json`): question -> answer.

    {"notice period": {"question": "Notice period?", "answer": "2 weeks", "updated_at": "..."}}

The key is the question's loose form (`normalize_key`), so "Notice period?" and "notice period" are
one answer. Only the user's own words are stored here: `apply done --remember` or `answers set`.
"""
from . import state

FILE = "answers.json"


def normalize_key(text):
    """Lower-case alphanumeric words of a question: the key of a remembered answer, and how a
    form label is matched to one."""
    out = []
    for char in text or "":
        if char.isalnum():
            out.append(char.lower())
        elif not out or out[-1] != " ":
            out.append(" ")
    return "".join(out).strip()


def load():
    """`{key: {question, answer, updated_at}}`, oldest first."""
    saved = state.read_json(FILE, {})
    return saved if isinstance(saved, dict) else {}


def pairs():
    """Every remembered `(question, answer)`, oldest first."""
    return [(row.get("question", key), row.get("answer", "")) for key, row in load().items() if isinstance(row, dict)]


def find(question):
    """The remembered row for this question, or None."""
    row = load().get(normalize_key(question))
    return row if isinstance(row, dict) else None


def save(question, answer):
    """Remember `answer` to `question`; an earlier answer with the same key is replaced in place.
    Returns the key, or None when the question or the answer is empty."""
    question, answer = (question or "").strip(), (answer or "").strip()
    key = normalize_key(question)
    if not key or not answer:
        return None
    saved = load()
    saved[key] = {"question": question, "answer": answer, "updated_at": state.now()}
    state.write_json(FILE, saved)
    return key


def forget(question):
    """Drop the remembered answer; False when there was none."""
    saved = load()
    if saved.pop(normalize_key(question), None) is None:
        return False
    state.write_json(FILE, saved)
    return True
