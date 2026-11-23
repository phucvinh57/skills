"""Where a posting hires, in code: a small gazetteer of countries and regions, and the clear limit
phrases of a posting ("authorized to work in the US", "must be based in Canada", "EU only").
`score.hard_filter` uses it against the preferences' `work_from`, the countries the user may
legally work from.

    places_in("Remote - US or Canada")            # [("country", "United States"), ("country", "Canada")]
    covers(("region", "APAC"), ["Vietnam"])       # True: Vietnam is in APAC
    restriction_in_jd("You must be authorized to work in the U.S.")   # [("country", "United States")]
    location_is_not_a_limit("Remote (outside US)")  # True: the location check is skipped

- Names match as whole words, longest first, and a match consumes its text: "Latin America" is the
  region, not the country "America" inside it.
- Short codes that are also English words or common abbreviations (`US`, `UK`, `VN`, `EU`, `SEA`)
  match in upper case only, so "join us" names no place.
- Only a few clear phrases count as a limit; a posting that merely names a country ("offices in the
  US and Vietnam", "a US-based company") limits nothing. A clause about time zones or working hours
  is not about where the hire may live. "<place> only" counts only in a sentence about hiring and
  without a negation before it.
- A work_from entry the gazetteer does not know is covered by every region (it may be in it) and by
  a country of its own name only: only a clear mismatch may dismiss a posting.

A place is a tuple `(kind, name)`: `("country", "Vietnam")`, `("region", "APAC")`, `ANYWHERE`.
"""
import re

ANYWHERE = ("anywhere", "anywhere")


def country(name):
    return ("country", name)


def region(name):
    return ("region", name)


def place_name(place):
    return place[1]


# Countries: (name, aliases matched in any case, codes matched in upper case only). A few big cities
# say the country. Ambiguous names (Georgia, Jordan, Washington) are left out.
COUNTRIES = [
    ("United States", [
        "united states", "united states of america", "usa", "u.s.a.", "u.s.a", "u.s.", "u.s", "america",
        "new york", "nyc", "san francisco", "bay area", "silicon valley", "seattle", "austin", "boston", "chicago",
        "los angeles", "denver", "atlanta", "miami", "california", "texas", "florida", "massachusetts", "colorado",
        "illinois", "new jersey", "new mexico", "new england",
    ], ["US"]),
    ("United Kingdom", ["united kingdom", "u.k.", "great britain", "britain", "england", "scotland", "wales", "london",
                        "manchester", "edinburgh"], ["UK", "GB"]),
    ("Canada", ["canada", "toronto", "vancouver", "montreal", "ottawa", "calgary"], []),
    ("Vietnam", ["vietnam", "viet nam", "việt nam", "ho chi minh city", "ho chi minh", "hcmc", "saigon", "hanoi",
                 "ha noi", "hà nội", "da nang", "danang", "đà nẵng"], ["VN", "HCM"]),
    ("Singapore", ["singapore"], ["SG"]),
    ("Australia", ["australia", "sydney", "melbourne", "brisbane", "perth", "new south wales"], []),
    ("New Zealand", ["new zealand", "auckland", "wellington"], ["NZ"]),
    ("India", ["india", "bangalore", "bengaluru", "mumbai", "new delhi", "delhi", "hyderabad", "pune", "chennai"], []),
    ("Pakistan", ["pakistan", "karachi", "lahore"], []),
    ("Bangladesh", ["bangladesh", "dhaka"], []),
    ("Sri Lanka", ["sri lanka", "colombo"], []),
    ("Nepal", ["nepal", "kathmandu"], []),
    ("Japan", ["japan", "tokyo", "osaka"], []),
    ("South Korea", ["south korea", "korea", "seoul"], []),
    ("China", ["china", "mainland china", "beijing", "shanghai", "shenzhen"], []),
    ("Hong Kong", ["hong kong"], ["HK"]),
    ("Taiwan", ["taiwan", "taipei"], []),
    ("Philippines", ["philippines", "manila"], []),
    ("Indonesia", ["indonesia", "jakarta", "bali"], []),
    ("Thailand", ["thailand", "bangkok"], []),
    ("Malaysia", ["malaysia", "kuala lumpur"], []),
    ("Cambodia", ["cambodia", "phnom penh"], []),
    ("Laos", ["laos", "vientiane"], []),
    ("Myanmar", ["myanmar", "burma", "yangon"], []),
    ("Brunei", ["brunei"], []),
    ("Germany", ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt"], []),
    ("France", ["france", "paris"], []),
    ("Netherlands", ["netherlands", "the netherlands", "holland", "amsterdam", "rotterdam"], []),
    ("Belgium", ["belgium", "brussels"], []),
    ("Spain", ["spain", "madrid", "barcelona"], []),
    ("Portugal", ["portugal", "lisbon", "porto"], []),
    ("Italy", ["italy", "milan", "rome"], []),
    ("Poland", ["poland", "warsaw", "krakow", "kraków"], []),
    ("Ireland", ["ireland", "dublin"], []),
    ("Sweden", ["sweden", "stockholm"], []),
    ("Denmark", ["denmark", "copenhagen"], []),
    ("Norway", ["norway", "oslo"], []),
    ("Finland", ["finland", "helsinki"], []),
    ("Switzerland", ["switzerland", "zurich", "zürich", "geneva"], []),
    ("Austria", ["austria", "vienna"], []),
    ("Czech Republic", ["czech republic", "czechia", "prague"], []),
    ("Romania", ["romania", "bucharest"], []),
    ("Ukraine", ["ukraine", "kyiv", "kiev"], []),
    ("Greece", ["greece", "athens"], []),
    ("Estonia", ["estonia", "tallinn"], []),
    ("Latvia", ["latvia", "riga"], []),
    ("Lithuania", ["lithuania", "vilnius"], []),
    ("Hungary", ["hungary", "budapest"], []),
    ("Bulgaria", ["bulgaria"], []),
    ("Croatia", ["croatia", "zagreb"], []),
    ("Slovakia", ["slovakia", "bratislava"], []),
    ("Slovenia", ["slovenia", "ljubljana"], []),
    ("Luxembourg", ["luxembourg"], []),
    ("Cyprus", ["cyprus", "limassol", "nicosia"], []),
    ("Malta", ["malta", "valletta"], []),
    ("Brazil", ["brazil", "brasil", "são paulo", "sao paulo", "rio de janeiro"], []),
    ("Mexico", ["mexico", "méxico", "mexico city"], []),
    ("Argentina", ["argentina", "buenos aires"], []),
    ("Colombia", ["colombia", "bogota", "bogotá", "medellin", "medellín"], []),
    ("Chile", ["chile", "santiago"], []),
    ("Peru", ["peru", "lima"], []),
    ("Uruguay", ["uruguay", "montevideo"], []),
    ("Costa Rica", ["costa rica"], []),
    ("Israel", ["israel", "tel aviv"], []),
    ("United Arab Emirates", ["united arab emirates", "uae", "dubai", "abu dhabi"], []),
    ("Saudi Arabia", ["saudi arabia", "riyadh"], []),
    ("Qatar", ["qatar", "doha"], []),
    ("Turkey", ["turkey", "türkiye", "istanbul"], []),
    ("Egypt", ["egypt", "cairo"], []),
    ("South Africa", ["south africa", "cape town", "johannesburg"], []),
    ("Nigeria", ["nigeria", "lagos"], []),
    ("Kenya", ["kenya", "nairobi"], []),
    ("Ghana", ["ghana", "accra"], []),
    ("Ethiopia", ["ethiopia", "addis ababa"], []),
    ("Morocco", ["morocco", "casablanca", "rabat"], []),
]

SOUTHEAST_ASIA = ["Vietnam", "Singapore", "Philippines", "Indonesia", "Thailand", "Malaysia", "Cambodia", "Laos",
                  "Myanmar", "Brunei"]
SOUTH_ASIA = ["India", "Pakistan", "Bangladesh", "Sri Lanka", "Nepal"]
EAST_ASIA = ["Japan", "South Korea", "China", "Hong Kong", "Taiwan"]
OCEANIA = ["Australia", "New Zealand"]
# The 27 member states.
EU = [
    "Austria", "Belgium", "Bulgaria", "Croatia", "Cyprus", "Czech Republic", "Denmark", "Estonia", "Finland", "France",
    "Germany", "Greece", "Hungary", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg", "Malta", "Netherlands",
    "Poland", "Portugal", "Romania", "Slovakia", "Slovenia", "Spain", "Sweden",
]
REST_OF_EUROPE = ["United Kingdom", "Norway", "Switzerland", "Ukraine"]
MIDDLE_EAST = ["Israel", "United Arab Emirates", "Saudi Arabia", "Qatar", "Turkey", "Egypt"]
NORTH_AFRICA = ["Egypt", "Morocco"]
AFRICA = ["South Africa", "Nigeria", "Kenya", "Ghana", "Ethiopia", "Egypt", "Morocco"]
LATAM = ["Mexico", "Brazil", "Argentina", "Colombia", "Chile", "Peru", "Uruguay", "Costa Rica"]
NORTH_AMERICA = ["United States", "Canada", "Mexico"]

# Regions: (name, aliases, upper-case codes, member countries).
REGIONS = [
    ("Southeast Asia", ["southeast asia", "south-east asia", "south east asia"], ["SEA"], SOUTHEAST_ASIA),
    ("APAC", ["apac", "asia-pacific", "asia pacific", "asia/pacific", "asia & pacific"], [],
     SOUTHEAST_ASIA + SOUTH_ASIA + EAST_ASIA + OCEANIA),
    ("Asia", ["asia"], [], SOUTHEAST_ASIA + SOUTH_ASIA + EAST_ASIA),
    ("EU", ["european union"], ["EU"], EU),
    ("Europe", ["europe"], [], EU + REST_OF_EUROPE),
    ("Middle East", ["middle east"], [], MIDDLE_EAST),
    ("MENA", ["mena"], [], MIDDLE_EAST + NORTH_AFRICA),
    ("Africa", ["africa"], [], AFRICA),
    ("EMEA", ["emea"], [], EU + REST_OF_EUROPE + MIDDLE_EAST + AFRICA),
    ("LATAM", ["latam", "latin america", "south america", "central america"], [], LATAM),
    ("North America", ["north america"], [], NORTH_AMERICA),
    ("Americas", ["americas", "the americas"], [], NORTH_AMERICA + LATAM),
]

# Names that mean every country.
ANYWHERE_NAMES = ["worldwide", "world-wide", "world wide", "anywhere", "anywhere in the world", "global", "globally"]

_MEMBERS = {name: members for name, _, _, members in REGIONS}


def members(place):
    """The countries the place stands for; None for ANYWHERE (all of them)."""
    kind, name = place
    if kind == "anywhere":
        return None
    if kind == "country":
        return [name]
    return list(_MEMBERS.get(name, []))


def _build_patterns():
    """Every name of every place as `(text, exact, place)`, longest first. `exact` names (upper-case
    codes) match the case as written; the others are lower case."""
    out = []

    def add(aliases, upper, place):
        out.extend((alias.lower(), False, place) for alias in aliases)
        out.extend((code, True, place) for code in upper)

    for name, aliases, upper in COUNTRIES:
        add(aliases, upper, country(name))
    for name, aliases, upper, _ in REGIONS:
        add(aliases, upper, region(name))
    add(ANYWHERE_NAMES, [], ANYWHERE)
    out.sort(key=lambda pattern: -len(pattern[0].encode("utf-8")))
    return out


PATTERNS = _build_patterns()

_ASCII_LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}


def _ascii_lower(text):
    """Lower case for A-Z only, so every offset in the result is an offset in `text`."""
    return text.translate(_ASCII_LOWER)


def _occurrences(hay, needle):
    start = hay.find(needle)
    while start != -1:
        yield start
        start = hay.find(needle, start + len(needle))


def find_places(text):
    """Every place named in `text` as `(start, end, place)`, in order of appearance."""
    lower = _ascii_lower(text)
    found = []
    for name, exact, place in PATTERNS:
        hay = text if exact else lower
        for start in _occurrences(hay, name):
            end = start + len(name)
            before_ok = start == 0 or not hay[start - 1].isalnum()
            # A word boundary after it, and an upper-case code is not a currency ("US$").
            after = hay[end] if end < len(hay) else ""
            after_ok = not after or (not after.isalnum() and not (exact and after == "$"))
            free = all(end <= s or start >= e for s, e, _ in found)
            if before_ok and after_ok and free:
                found.append((start, end, place))
    found.sort(key=lambda item: item[0])
    return found


def _dedup(places):
    out = []
    for place in places:
        if place not in out:
            out.append(place)
    return out


def places_in(text):
    """The places `text` names, in order, each once."""
    return _dedup(place for _, _, place in find_places(text or ""))


def _resolve(entry):
    """What a user-typed `work_from` entry stands for: the place it is a name of (in any case),
    else the places it mentions ("Hanoi, Vietnam")."""
    entry = entry.strip()
    for name, _, place in PATTERNS:
        if _ascii_lower(name) == _ascii_lower(entry) or name == entry.lower():
            return [place]
    return places_in(entry)


def _overlap(a, b):
    first, second = members(a), members(b)
    if first is None or second is None:
        return True
    return any(member in second for member in first)


def covers(place, work_from):
    """Whether someone who may work from the countries `work_from` (user-typed names) can take a
    job in `place`: a country they name, a region one of them is in, or anywhere. For an entry the
    gazetteer does not know, any region counts and a country only by the same name. No `work_from`
    is no limit."""
    entries = [entry for entry in work_from if entry.strip()]
    if place == ANYWHERE or not entries:
        return True
    for entry in entries:
        resolved = _resolve(entry)
        if not resolved:
            if place[0] == "region" or _ascii_lower(place_name(place)) == _ascii_lower(entry.strip()):
                return True
        elif any(_overlap(place, other) for other in resolved):
            return True
    return False


# --- limits stated in a posting ------------------------------------------------------------------

CLAUSE_CHARS = 200  # longest clause read after a limit phrase
LOOKBEHIND_CHARS = 60  # how far before a limit phrase a negation or sponsorship word cancels it

# Phrases after which the rest of the sentence says where the hire may be.
_CLAUSE_STARTS = re.compile(
    r"\b(?:(?:authori[sz]ed|eligible|legally\s+able|legally\s+permitted|permitted)\s+to\s+work\s+(?:in|from|within)"
    r"|must\s+(?:currently\s+)?(?:reside|live|be\s+located|be\s+based|be\s+a\s+resident)\s+(?:in|within)"
    r"|(?:only|exclusively)\s+(?:hiring|recruiting|accepting\s+(?:candidates|applicants|applications)"
    r"|open\s+to\s+(?:candidates|applicants))\s+(?:in|from|based\s+in|located\s+in|who\s+live\s+in|residing\s+in))\b",
    re.IGNORECASE,
)
# "<place> only" / "<place>-only".
_ONLY_WORD = re.compile(r"(?:\s*-\s*|\s+)only\b", re.IGNORECASE)
# "<place>-based candidates only", "<place> residents only".
_PEOPLE_ONLY = re.compile(
    r"(?:\s*-\s*based|\s+based)?\s+(?:candidates|applicants|applications|residents|citizens|talent|individuals)\s+only\b",
    re.IGNORECASE,
)
# Words that make a limit phrase say the opposite ("no need to be authorized to work in the US").
_NEGATED = re.compile(r"\b(?:not|no|never|without|sponsors?|sponsorship)\b|n't\b", re.IGNORECASE)
# A clause about time zones or hours, not about where the hire lives.
_ABOUT_HOURS = re.compile(r"\b(?:time\s*zones?|timezones?|tz|hours|overlap)\b", re.IGNORECASE)
# Words that make a "<place> only" say the opposite ("We hire worldwide, not Bay Area only").
_NEGATED_ONLY = re.compile(r"\b(?:not|no|never|without)\b|n't\b", re.IGNORECASE)
# Words that make a sentence about who is hired, so its "<place> only" is a limit.
_ABOUT_HIRING = re.compile(
    r"\b(?:hire[sd]?|hiring|candidates?|applicants?|based|residents?|residing|located|location|work|remote|role"
    r"|position|eligible)\b",
    re.IGNORECASE,
)
# A word that leaves a place out ("outside US", "Non-US", "ex-US", "excluding US").
_EXCLUDING = re.compile(r"\b(?:outside|non|ex|excluding|except|not)\b", re.IGNORECASE)


def _sentence_start(text, at):
    head = text[:at]
    breaks = max(head.rfind(mark) for mark in "\n!?;") + 1
    stop = head.rfind(". ")
    return max(breaks, stop + 2 if stop != -1 else 0)


def _sentence_end(text, start):
    """The end of the sentence that starts at `start`: a line break, `!`, `?`, `;`, or a full stop
    followed by the end or by a space and a capital ("U.S. or Canada" goes on, "U.S. We offer" ends)."""
    for index in range(start, len(text)):
        char = text[index]
        if char in "\n!?;":
            return index
        if char == ".":
            after = text[index + 1:]
            trimmed = after.lstrip()
            spaced = len(trimmed) < len(after)
            if not after or (spaced and (not trimmed or trimmed[0].isupper())):
                return index
    return len(text)


def location_is_not_a_limit(location):
    """Whether a posting's location names its places without saying the hire must be there: a word
    that leaves a place out before it ("Remote (outside US)", "Remote - Non-US") or a time zone
    ("Remote - US time zones", "Remote (EST hours)")."""
    if _ABOUT_HOURS.search(location):
        return True
    return any(_EXCLUDING.search(location[:start]) for start, _, _ in find_places(location))


def _place_just_before(text, at):
    """The place named right before `at` in `text` ("Remote, US" -> US), with where its name starts."""
    window = text[max(0, at - 60):at]
    offset = at - len(window)
    while window and (window[-1].isspace() or window[-1] in "(-"):
        window = window[:-1]
    for start, end, place in reversed(find_places(window)):
        if end == len(window):
            return offset + start, place
    return None


def _only_limits(jd, place_at, end, bare):
    """A "<place> only" limits the hire unless a negation comes before the place in its sentence;
    a bare "only" (not "candidates only") also needs a word about hiring in the sentence."""
    start = _sentence_start(jd, place_at)
    if _NEGATED_ONLY.search(jd[start:place_at][-LOOKBEHIND_CHARS:]):
        return False
    return not bare or bool(_ABOUT_HIRING.search(jd[start:_sentence_end(jd, end)]))


class JdLimits:
    """The limits a posting states about where the hire may be: the text of its limit clauses and
    the places named right before an "only"."""

    def __init__(self, clauses, named):
        self.clauses = clauses
        self.named = named

    def places(self):
        """The places the limits name, each once."""
        found = [place for clause in self.clauses for place in places_in(clause)]
        return _dedup(found + self.named)

    def mentions(self, work_from):
        """Whether one of the limit clauses names a `work_from` entry by its own words (an entry the
        gazetteer may not know: "authorized to work in Togo or the US")."""
        from . import sources

        return any(sources.mentions(clause, entry) for clause in self.clauses for entry in work_from)


def jd_limits(jd):
    """Read the limits of `jd`: see `restriction_in_jd` for the phrases."""
    clauses = []
    for match in _CLAUSE_STARTS.finditer(jd):
        start = _sentence_start(jd, match.start())
        lead_in = jd[start:match.start()][-LOOKBEHIND_CHARS:]
        if _NEGATED.search(lead_in):
            continue
        clause = jd[match.end():_sentence_end(jd, match.end())][:CLAUSE_CHARS]
        if not _ABOUT_HOURS.search(clause):
            clauses.append(clause)
    named = []
    for match in _PEOPLE_ONLY.finditer(jd):
        before = _place_just_before(jd, match.start())
        if before and _only_limits(jd, before[0], match.end(), False):
            named.append(before[1])
    for match in _ONLY_WORD.finditer(jd):
        hyphen = "-" in match.group(0)
        after = jd[match.end():].lstrip(" \t")
        # "US only" ends a phrase; "the US only through partners" is not a limit. "US-only" always is.
        ends = not after or after[0] in ")],.;:|/!\n\r"
        if not (hyphen or ends):
            continue
        before = _place_just_before(jd, match.start())
        if before and _only_limits(jd, before[0], match.end(), True):
            named.append(before[1])
    return JdLimits(clauses, named)


def restriction_in_jd(jd):
    """The places a posting limits the hire to, in clear phrases only: "authorized (eligible,
    legally able, permitted) to work in ...", "must reside (live, be located, be based) in ...",
    "only hiring in ...", "US-based candidates only", and "US only" / "US-only" in a sentence about
    hiring. A limit phrase after "not", "without" or "sponsor" does not count, and nor does a clause
    about time zones or hours."""
    return jd_limits(jd).places()
