"""The YC company directory: the newest batches, one item per company (sector, tags, size, hiring
flag), read from the static `yc-oss` mirror (`config.ENDPOINTS["yc"]`)."""
import re

from .. import http as net
from . import config
from . import text as tx

# Batches listed with fewer companies are announcements, not batches yet.
MIN_BATCH_COMPANIES = 10
# Where a company's page lives; the item URL (and so the citation) is built from its slug.
COMPANY_URL = "https://www.ycombinator.com/companies/"
_SEASONS = ("winter", "spring", "summer", "fall")


def join(base, path):
    return base.rstrip("/") + "/" + path.lstrip("/")


def batch_order(slug):
    """`summer-2026`: its year and season (winter < spring < summer < fall)."""
    season, _, year = slug.partition("-")
    if season not in _SEASONS or not re.fullmatch(r"-?[0-9]+", year):
        return None
    return int(year), _SEASONS.index(season)


def newest_batches(meta, count):
    batches = []
    for slug, info in ((meta or {}).get("batches") or {}).items():
        size = info.get("count") if isinstance(info, dict) else None
        order = batch_order(slug)
        if isinstance(size, int) and size >= MIN_BATCH_COMPANIES and order is not None:
            batches.append((order, slug))
    batches.sort(reverse=True)
    return [slug for _, slug in batches[:count]]


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def company_item(company):
    """`(rank, item)`: 0 = hiring, 1 = top company, 2 = the rest. None for a broken entry."""
    if not isinstance(company, dict):
        return None
    name, slug = _text(company.get("name")), company.get("slug")
    if not name or not isinstance(slug, str) or not re.fullmatch(r"[A-Za-z0-9-]+", slug):
        return None
    text = f"# {name}\n\nY Combinator company, {_text(company.get('batch')) or 'unknown batch'}."
    if _text(company.get("one_liner")):
        text += " " + _text(company.get("one_liner"))
    long = tx.clean(company["long_description"]) if isinstance(company.get("long_description"), str) else ""
    if long:
        text += "\n\n" + tx.cap(long, 4000)
    facts = []
    industry, sub = company.get("industry"), company.get("subindustry")
    if isinstance(industry, str):
        facts.append(f"Sector: {industry} ({sub})" if isinstance(sub, str) and sub else f"Sector: {industry}")
    tags = company.get("tags")
    if isinstance(tags, list) and tags:
        facts.append("Tags: " + ", ".join(str(tag) for tag in tags))
    if isinstance(company.get("team_size"), int) and not isinstance(company.get("team_size"), bool):
        facts.append(f"Team size: {company['team_size']}")
    for label, key in (("Location", "all_locations"), ("Stage", "stage"), ("Status", "status")):
        if isinstance(company.get(key), str) and company[key]:
            facts.append(f"{label}: {company[key]}")
    hiring = company.get("isHiring") is True
    facts.append("Hiring: " + ("yes" if hiring else "no"))
    if isinstance(company.get("website"), str) and company["website"]:
        facts.append(f"Website: {company['website']}")
    text += "\n\n" + "\n".join(facts)
    rank = 0 if hiring else 1 if company.get("top_company") is True else 2
    return rank, {"url": COMPANY_URL + slug, "title": name, "published_at": tx.from_timestamp(company.get("launched_at")),
                  "text": text}


def take_new(known, items, limit):
    """The new items (not known, not repeated) up to `limit`, and how many new ones past it were cut."""
    seen, new = set(), []
    for item in items:
        if item["url"] not in known and item["url"] not in seen:
            seen.add(item["url"])
            new.append(item)
    return new[:limit], max(len(new) - limit, 0)


def collect(ctx):
    """The new companies of the newest batches (hiring and top companies first), and how many new
    companies past `MAX_ITEMS` were cut."""
    root = config.ENDPOINTS["yc"]
    meta = net.client().get_json(join(root, "meta.json"))
    ranked, failed = [], None
    for slug in newest_batches(meta, config.YC_BATCHES):
        try:
            companies = net.client().get_json(join(root, f"batches/{slug}.json"))
        except net.HttpError as err:
            failed = failed or err
            continue
        # One broken company must not hide the rest of the batch.
        for company in companies if isinstance(companies, list) else []:
            found = company_item(company)
            if found:
                ranked.append(found)
    if failed is not None and not ranked:
        raise failed
    ranked.sort(key=lambda pair: pair[0])  # stable: hiring first, so the cap keeps the most informative
    return take_new(ctx.known, [item for _, item in ranked], config.MAX_ITEMS)
