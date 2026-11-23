"""New terms, found in code over every item: the words the skill vocabulary does not know.

- a term is a run of one to three words of a document, in lower case, counted once per document.
  It cannot cross a punctuation mark or a line, cannot start or end with a stopword, is not a
  number, has at least `MIN_TERM_CHARS` characters, and is not one the skill vocabulary knows;
- a document is its own words (`body`): the lines the collectors wrote (the thread title of a
  posting, the points of a story, a YC company's batch, size and flags) and every address are
  dropped first, so the adapters' own wording cannot become a "trend";
- strata are never pooled: a term is compared only against the same kind of document. Each stratum
  has two sides: for postings the window's baseline months against its target months, for every
  other kind the previous period's items against this period's;
- a stratum that cannot be compared is not tested (fewer than `MIN_STRATUM_DOCS` documents on a
  side, or a volume that differs by more than `MAX_VOLUME_RATIO`): it lists no term at all, and
  the report says it was not compared;
- a term is tested with at least `MIN_TERM_DOCS` documents across both sides, by the log-odds test
  of the skills with an informative prior, all of a stratum's tests one family; a term that
  survives with a positive score is `rising`;
- a shorter term is dropped when a longer one containing it has the same counts on both sides."""
import re

from . import stats

MIN_STRATUM_DOCS = 20
MAX_VOLUME_RATIO = 2.0
MIN_TERM_DOCS = 8
TERM_PRIOR = 20.0
MAX_TERMS_PER_STRATUM = 10
MAX_WORDS = 3
MIN_TERM_CHARS = 3
STRATA = ("hiring_posting", "hn_story", "yc", "feed", "other")

DISCUSSION_MARK = " on Hacker News. Discussion: "
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
_BREAK = re.compile("[\n\r|,;:()\\[\\]{}!?\"'*‘’“”–—•]")

# Function words and the filler of recruiting and web pages: no term starts or ends with one (a
# term may hold one inside: "ruby on rails").
STOPWORDS = frozenset("""
a about above across after again against all almost along already also although always am among an and another
any anyone anything are around as at away be because been before being below between both but by can cannot
could did do does doing done down during each either else enough etc even ever every few for from further get
gets getting give go goes going got had has have having he her here hers herself him himself his how however i
if in into is it its itself just keep let like made make makes many may me might more most much must my myself
need needs neither never no nor not now of off often on once one only onto or other others otherwise our ours
ourselves out over own per perhaps please plus rather really same see seem several shall she should since so
some something sometimes still such take than that the their theirs them themselves then there therefore these
they thing things this those though through throughout thus to together too toward towards under unless until
up upon us use used uses using very via want wants was way ways we well were what whatever when whenever where
whether which while who whom whose why will with within without would yet you your yours yourself yourselves
ability able apply applying application applications candidate candidates company contact currently day days
email experience experienced experiences expertise good great help helping hire hired hires hiring hn include
includes including interested job jobs join joining knowledge less looking month months opportunity
opportunities position positions required requirement requirements resume responsibilities role roles send
skill skills strong team teams work worked working works year years aren couldn didn doesn don hasn haven isn
ll re shouldn ve wasn weren won wouldn click comment comments cookies privacy read share subscribe
""".split())


def _yc_line(line):
    if line.startswith("Y Combinator company,"):
        # `Y Combinator company, <batch>.` and then the company's one-liner, on the same line.
        rest = line[len("Y Combinator company,"):]
        return rest.split(".", 1)[1] if "." in rest else ""
    for label in ("Sector:", "Tags:", "Location:", "Stage:"):
        if line.startswith(label):
            return line[len(label):]
    return None if line.startswith(("Team size:", "Status:", "Hiring:", "Website:")) else line


def body(text, stratum):
    """The item's own words: its saved text without what the adapter of `stratum` adds and without
    any address."""
    out, first = [], True
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if first:
            first = False
            if stratum == "hiring_posting" and line.lower().startswith("# hacker news,"):
                continue
            kept = line[2:] if line.startswith("# ") else line
        elif stratum == "hn_story" and DISCUSSION_MARK in line:
            continue
        elif stratum == "yc":
            kept = _yc_line(line)
        else:
            kept = line
        if kept and kept.strip():
            out.append(_URL.sub(" ", kept) + "\n")
    return "".join(out)


def segments(text):
    """Stretches of a text a term cannot cross: lines, separating punctuation, and sentences."""
    for part in _BREAK.split(text):
        for piece in part.split(". "):
            yield piece


def _edge(word):
    """May the word stand at either end of a term: not a stopword, not a single character, not a number."""
    numeric = any(c.isdigit() for c in word) and all(c.isdigit() or c in ".-+#" for c in word)
    return not numeric and word not in STOPWORDS and len(word) > 1


class Corpus:
    """The documents of one stratum on its two sides (0 = baseline, 1 = target)."""

    def __init__(self, stratum, units):
        self.stratum, self.units = stratum, units
        self.docs = [0, 0]
        self.texts = []

    def add(self, side, text, tokens):
        runs = []
        for segment in segments(body(text, self.stratum)):
            words = tuple(lower for _, lower in tokens(segment))
            if words:
                runs.append(words)
        self.docs[side] += 1
        self.texts.append((side, runs))

    def comparable(self):
        before, now = self.docs
        if before < MIN_STRATUM_DOCS or now < MIN_STRATUM_DOCS:
            return False
        before, now = before / max(self.units[0], 1), now / max(self.units[1], 1)
        return max(before, now) <= MAX_VOLUME_RATIO * min(before, now)

    def frequent_runs(self):
        """Per run of one to three words, the documents naming it on each side, for the runs in at
        least `MIN_TERM_DOCS` documents. A longer run is counted only when both its shorter halves
        are frequent: it cannot be in more documents than they are."""
        found, shorter = {}, set()
        for size in range(1, MAX_WORDS + 1):
            counts = {}
            for side, runs in self.texts:
                seen = set()
                for words in runs:
                    for at in range(len(words) - size + 1):
                        gram = words[at:at + size]
                        if size > 1 and not (gram[:-1] in shorter and gram[1:] in shorter):
                            continue
                        seen.add(gram)
                for gram in seen:
                    counts.setdefault(gram, [0, 0])[side] += 1
            counts = {gram: k for gram, k in counts.items() if k[0] + k[1] >= MIN_TERM_DOCS}
            shorter = set(counts)
            found.update(counts)
        return found

    def candidates(self, vocab):
        """The terms in enough documents that the vocabulary does not know, with their counts
        `[baseline, target]`, by term."""
        rows = {}
        for gram, k in self.frequent_runs().items():
            if not (_edge(gram[0]) and _edge(gram[-1])):
                continue
            term = " ".join(gram)
            if len(term) >= MIN_TERM_CHARS and not vocab.find(term):
                rows[term] = k
        subsumed = set()
        for term, k in rows.items():
            words = term.split(" ")
            for size in range(1, len(words)):
                for start in range(len(words) - size + 1):
                    part = " ".join(words[start:start + size])
                    if rows.get(part) == k:
                        subsumed.add(part)
        return sorted((term, k) for term, k in rows.items() if term not in subsumed)

    def terms(self, vocab):
        """The stratum's terms to show and whether it was compared. A compared stratum shows its
        tested risers first, by score, then, to fill, the terms that grew most without passing the
        test (not marked rising). Never the most frequent ones: those are the common words of the
        kind of item. A stratum that was not compared shows none."""
        if not self.comparable():
            return [], False
        before, now = self.docs
        candidates = self.candidates(vocab)
        scores = [stats.log_odds_z(k[1], now, k[0], before, TERM_PRIOR) for _, k in candidates]
        survives = stats.benjamini_hochberg(scores, stats.FALSE_DISCOVERY_RATE)
        rows = [(term, k, z, alive and z > 0.0) for (term, k), z, alive in zip(candidates, scores, survives)]
        order = lambda row: (-row[2], row[0])  # noqa: E731
        risers = sorted((row for row in rows if row[3]), key=order)[:MAX_TERMS_PER_STRATUM]
        level = sorted((row for row in rows if not row[3] and row[2] > 0.0), key=order)[:MAX_TERMS_PER_STRATUM - len(risers)]
        return [{"term": term, "items": k[1], "stratum": self.stratum, "z_centi": stats.centi(z), "rising": rising}
                for term, k, z, rising in risers + level], True


class Corpora:
    """The documents of every stratum, each kept apart. For a window of `baseline_months` before
    the period's own `target_months`: the postings' sides span that many months each, every other
    stratum's side is one period."""

    def __init__(self, baseline_months, target_months):
        self.corpora = {stratum: Corpus(stratum, (baseline_months, target_months) if stratum == "hiring_posting" else (1, 1))
                        for stratum in STRATA}

    def add(self, stratum, side, text):
        from .count import tokens

        self.corpora[stratum].add(1 if side == "target" else 0, text, tokens)

    def documents(self, stratum, side):
        return self.corpora[stratum].docs[1 if side == "target" else 0]

    def report(self, vocab):
        """`(terms of every stratum in display order, strata with items this period that were not compared)`."""
        found, not_comparable = [], []
        for stratum in STRATA:
            corpus = self.corpora[stratum]
            terms, comparable = corpus.terms(vocab)
            found.extend(terms)
            if not comparable and corpus.docs[1] > 0:
                not_comparable.append(stratum)
        return found, not_comparable
