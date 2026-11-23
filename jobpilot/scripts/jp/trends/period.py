"""The period a trend report covers: an ISO week (`2026-W46`), a month (`2026-M11`), a quarter
(`2026-Q4`) or a year (`2026`). One report per period; a rerun replaces it."""
import datetime
import re

from ..cli import Fail

UTC = datetime.timezone.utc
KINDS = ("weekly", "monthly", "quarterly", "yearly")
_WORDS = {"week": "week", "month": "month", "quarter": "quarter", "year": "year",
          "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}


def now():
    """The current time (UTC). The one clock of the trends code, so tests can set it."""
    return datetime.datetime.now(UTC)


def today():
    return now().date()


class Period:
    """`unit` is week / month / quarter / year; `number` the week, month or quarter (0 for a year)."""

    def __init__(self, unit, year, number=0):
        self.unit, self.year, self.number = unit, year, number

    def __eq__(self, other):
        return isinstance(other, Period) and (self.unit, self.year, self.number) == (other.unit, other.year, other.number)

    def __hash__(self):
        return hash((self.unit, self.year, self.number))

    def __repr__(self):
        return f"Period({self.label()})"

    # --- constructors ---

    @staticmethod
    def week_of(date):
        iso = date.isocalendar()
        return Period("week", iso[0], iso[1])

    @staticmethod
    def month_of(date):
        return Period("month", date.year, date.month)

    @staticmethod
    def quarter_of(date):
        return Period("quarter", date.year, (date.month - 1) // 3 + 1)

    @staticmethod
    def year_of(date):
        return Period("year", date.year)

    @staticmethod
    def of(unit, date):
        return {"week": Period.week_of, "month": Period.month_of, "quarter": Period.quarter_of, "year": Period.year_of}[unit](date)

    @staticmethod
    def parse(label):
        """Inverse of `label()`; None for anything else (including a week, month or quarter that
        does not exist)."""
        label = (label or "").strip()
        if re.fullmatch(r"[0-9]{4}", label):
            return Period("year", int(label))
        found = re.fullmatch(r"([0-9]{4})-(W|M|Q)([0-9]+)", label)
        if not found:
            return None
        unit = {"W": "week", "M": "month", "Q": "quarter"}[found.group(2)]
        period = Period(unit, int(found.group(1)), int(found.group(3)))
        return period if period._first_day() is not None else None

    # --- names ---

    def label(self):
        if self.unit == "week":
            return f"{self.year}-W{self.number:02d}"
        if self.unit == "month":
            return f"{self.year}-M{self.number:02d}"
        if self.unit == "quarter":
            return f"{self.year}-Q{self.number}"
        return f"{self.year}"

    def kind(self):
        return {"week": "weekly", "month": "monthly", "quarter": "quarterly", "year": "yearly"}[self.unit]

    def quarter_label(self):
        """`Q4`: the quarter of the period (of the Thursday, for a week). Empty for a year."""
        if self.unit == "quarter":
            return f"Q{self.number}"
        if self.unit == "year":
            return ""
        if self.unit == "month":
            month = self.number
        else:
            try:
                month = datetime.date.fromisocalendar(self.year, self.number, 4).month
            except ValueError:
                month = 1
        return f"Q{(month - 1) // 3 + 1}"

    # --- dates ---

    def _first_day(self):
        try:
            if self.unit == "week":
                return datetime.date.fromisocalendar(self.year, self.number, 1)
            if self.unit == "month":
                return datetime.date(self.year, self.number, 1)
            if self.unit == "quarter":
                if not 1 <= self.number <= 4:
                    return None
                return datetime.date(self.year, (self.number - 1) * 3 + 1, 1)
            return datetime.date(self.year, 1, 1)
        except ValueError:
            return None

    def start(self):
        """First instant of the period (UTC)."""
        day = self._first_day() or datetime.date(1970, 1, 1)
        return datetime.datetime(day.year, day.month, day.day, tzinfo=UTC)

    def end(self):
        """First instant after the period (UTC)."""
        if self.unit == "week":
            return self.start() + datetime.timedelta(days=7)
        if self.unit == "month":
            year, month = (self.year + 1, 1) if self.number >= 12 else (self.year, self.number + 1)
        elif self.unit == "quarter":
            year, month = (self.year + 1, 1) if self.number >= 4 else (self.year, self.number * 3 + 1)
        else:
            year, month = self.year + 1, 1
        return datetime.datetime(year, month, 1, tzinfo=UTC)

    def previous(self):
        """The period of the same kind just before this one (across the year's end too)."""
        if self.unit == "week":
            monday = self._first_day()
            return Period.week_of(monday - datetime.timedelta(days=7)) if monday else self
        if self.unit == "month":
            return Period("month", self.year - 1, 12) if self.number <= 1 else Period("month", self.year, self.number - 1)
        if self.unit == "quarter":
            return Period("quarter", self.year - 1, 4) if self.number <= 1 else Period("quarter", self.year, self.number - 1)
        return Period("year", self.year - 1)

    def contains(self, moment):
        return moment is not None and self.start() <= moment < self.end()


def resolve(text):
    """A period from what a command was given: a label, or `week|month|quarter|year` for the
    current one."""
    word = _WORDS.get((text or "").strip().lower())
    if word:
        return Period.of(word, today())
    period = Period.parse(text)
    if period is None:
        raise Fail(f"not a period: {text!r}",
                   hint="use 2026-W46, 2026-M11, 2026-Q4 or 2026, or week | month | quarter | year for the current one")
    return period


def report_title(period):
    """`Weekly digest 2026-W46`, `Monthly digest 2026-M11`, `Quarterly summary 2026-Q4`, `Yearly summary 2026`."""
    name = {"week": "Weekly digest", "month": "Monthly digest", "quarter": "Quarterly summary", "year": "Yearly summary"}
    return f"{name[period.unit]} {period.label()}"


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")


def long_date(date):
    """`9 November 2026` (no locale: the report reads the same everywhere)."""
    return f"{date.day} {_MONTHS[date.month - 1]} {date.year}"


def period_title(label):
    """What the period covers, in words: `Week of 9 November 2026`, `November 2026`, `Q4 2026`, `2026`."""
    period = Period.parse(label)
    if period is None:
        return " ".join((label or "").split())
    if period.unit == "week":
        return "Week of " + long_date(period.start())
    if period.unit == "month":
        return f"{_MONTHS[period.number - 1]} {period.year}"
    if period.unit == "quarter":
        return f"Q{period.number} {period.year}"
    return str(period.year)
