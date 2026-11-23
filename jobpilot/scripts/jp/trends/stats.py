"""The statistics behind the counts: how sure a share is, and whether it moved.

Everything here is a pure function of counts (`k` documents naming a thing out of `n` read), so
the same postings always give the same numbers; no model has a part in it.

- level: `wilson`, the 95% interval of a share;
- sudden change: `log_odds_z`, the target months against the baseline months;
- slow drift: `trend_z`, a trend over every month of the window (Cochran-Armitage);
- many tests at once: `benjamini_hochberg`, which of them survive false-discovery control;
- the call: `call_direction`."""
import math

Z95 = 1.959_963_985
# The share of false discoveries the things called in one family of tests may hold, on average.
FALSE_DISCOVERY_RATE = 0.10
# A step must move the share by at least this much (per mille of the documents: one point).
MIN_CHANGE_PERMILLE = 10
# With at least this many documents on each side, no surviving test reads as "steady"; with fewer
# the data could not have shown a movement, and the direction is "unclear".
MIN_SIDE_DOCS = 30


def round_half_away(value):
    """Round to the nearest whole number, halves away from zero."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def permille(part, whole):
    """`part / whole` in per mille, rounded half up; 0 without a whole."""
    return (part * 1000 + whole // 2) // whole if whole else 0


def permille_text(value):
    """A per mille as a percent with one decimal: `9.4%`."""
    return f"{value // 10}.{value % 10}%"


def level_text(target_months, target_permille, low_permille, high_permille):
    """A skill's level in the period's own months, in words: `("9.4%", "7.1% to 12.3%")`; None
    when those months held no document to take a share of."""
    if target_months == 0 or high_permille == 0:
        return None
    return permille_text(target_permille), f"{permille_text(low_permille)} to {permille_text(high_permille)}"


def wilson(k, n):
    """The 95% Wilson interval of the share `k / n`, in per mille, as `(low, high)`."""
    if n == 0:
        return 0, 0
    k = min(k, n)
    p = k / n
    z2 = Z95 * Z95
    denom = 1.0 + z2 / n
    centre = (p + z2 / (2.0 * n)) / denom
    half = Z95 * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n)) / denom
    per_mille = lambda x: round_half_away(min(max(x, 0.0), 1.0) * 1000.0)  # noqa: E731
    return per_mille(centre - half), per_mille(centre + half)


def log_odds_z(k_t, n_t, k_b, n_b, prior):
    """How far the share in the target (`k_t / n_t`) is from the share in the baseline, as a
    z-score of the log-odds ratio (positive: the target's share is higher). Both sides are first
    pulled towards the pooled share by a prior worth `prior` documents."""
    if n_t == 0 or n_b == 0:
        return 0.0
    k_t, k_b = float(min(k_t, n_t)), float(min(k_b, n_b))
    pooled = (k_t + k_b) / (n_t + n_b)
    yes, no = prior * pooled, prior * (1.0 - pooled)
    if yes <= 0.0 or no <= 0.0:
        return 0.0
    t_yes, t_no, b_yes, b_no = k_t + yes, n_t - k_t + no, k_b + yes, n_b - k_b + no
    delta = math.log(t_yes / t_no) - math.log(b_yes / b_no)
    return delta / math.sqrt(1.0 / t_yes + 1.0 / t_no + 1.0 / b_yes + 1.0 / b_no)


def trend_z(k, n):
    """Is the share `k[i] / n[i]` going up or down over the months (oldest first)? The
    Cochran-Armitage trend test with the month's index as its score (positive: the share grows)."""
    months = min(len(k), len(n))
    total = float(sum(n[:months]))
    if total == 0.0:
        return 0.0
    hits = float(sum(min(k[i], n[i]) for i in range(months)))
    p = hits / total
    mean = sum(n[i] * i for i in range(months)) / total
    t = spread = 0.0
    for i in range(months):
        x = i - mean
        t += min(k[i], n[i]) * x
        spread += n[i] * x * x
    variance = p * (1.0 - p) * spread
    return t / math.sqrt(variance) if variance > 0.0 else 0.0


def erfc(x):
    """The complementary error function for `x >= 0` (the Chebyshev fit of Numerical Recipes)."""
    t = 1.0 / (1.0 + 0.5 * x)
    poly = -1.265_512_23 + t * (1.000_023_68 + t * (0.374_091_96 + t * (0.096_784_18 + t * (-0.186_288_06 + t * (
        0.278_868_07 + t * (-1.135_203_98 + t * (1.488_515_87 + t * (-0.822_152_23 + t * 0.170_872_77))))))))
    return t * math.exp(-x * x + poly)


def p_value(z):
    """The two-sided p-value of a z-score. A NaN counts as no evidence (1)."""
    return 1.0 if math.isnan(z) else erfc(abs(z) / math.sqrt(2.0))


def benjamini_hochberg(z, q):
    """Which of the tests (z-scores, in their order) survive when the expected share of false
    discoveries among the survivors is held at `q`."""
    order = sorted(((p_value(score), at) for at, score in enumerate(z)))
    m = float(len(order))
    last = None
    for rank, (p, _) in enumerate(order):
        if p <= (rank + 1.0) / m * q:
            last = rank
    survives = [False] * len(z)
    if last is not None:
        for _, at in order[:last + 1]:
            survives[at] = True
    return survives


def centi(z):
    """A z-score in hundredths."""
    if math.isnan(z):
        return 0
    if math.isinf(z):
        return 2**31 - 1 if z > 0 else -2**31
    return max(min(round_half_away(z * 100.0), 2**31 - 1), -2**31)


def call_direction(step_z, trend_z_, step_survives, trend_survives, change_permille, min_side_documents):
    """The direction the evidence supports. Either test may call a movement once it survived the
    false-discovery control (the step also needs `MIN_CHANGE_PERMILLE`); two surviving tests that
    point opposite ways are `unclear`. Without a surviving test it is `steady` when both sides
    hold `MIN_SIDE_DOCS` documents, `unclear` otherwise."""
    up = lambda score: "rising" if score > 0.0 else "falling"  # noqa: E731
    step = up(step_z) if step_survives and abs(change_permille) >= MIN_CHANGE_PERMILLE else None
    trend = up(trend_z_) if trend_survives else None
    if step and trend and step != trend:
        return "unclear"
    if step or trend:
        return step or trend
    return "steady" if min_side_documents >= MIN_SIDE_DOCS else "unclear"
