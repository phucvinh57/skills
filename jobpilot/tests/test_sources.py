"""The job-source adapters, read from fixtures a loopback server answers with (no network)."""
import contextlib
import datetime
import json
import os
import time
import unittest
import urllib.parse

from tests.support import Loopback

from jp import http as net
from jp import sources
from jp.cli import Fail
from jp.sources import base, boards, hn_hiring, jsonld, sitemap, smartrecruiters, web, workday, yc
from jp.sources import greenhouse as greenhouse_module
from jp.sources import recruitee as recruitee_module
from jp.sources.ashby import Ashby
from jp.sources.base import NeedsBrowser, board_host, board_slug, mentions, one_line, page_of, parse_url, title_fits_roles
from jp.sources.career_page import CareerPage
from jp.sources.greenhouse import Greenhouse
from jp.sources.hn_hiring import HnHiring
from jp.sources.lever import Lever
from jp.sources.personio import Personio, parse_positions
from jp.sources.recruitee import Recruitee
from jp.sources.smartrecruiters import SmartRecruiters
from jp.sources.workday import Board, Workday
from jp.sources.yc import Yc

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "sources")
JSON = "application/json"


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return handle.read()


def titles(page):
    return [c["title"] for c in page["candidates"]]


def url(text):
    return parse_url(text)


@contextlib.contextmanager
def served(routes, origins=None):
    """A loopback server answering `routes`; reads of the `origins` (origin -> path prefix on the
    server) go to it."""
    with Loopback(routes) as server:
        saved = dict(base.ORIGINS)
        for origin, prefix in (origins or {}).items():
            base.ORIGINS[origin] = server.base + prefix
        try:
            yield server
        finally:
            base.ORIGINS.clear()
            base.ORIGINS.update(saved)


def paths(server):
    return [path for _, path, _ in server.requests]


class Pages:
    """Web pages by address, answered by a loopback server: `pages` maps an address to its body or
    to a status; `moved` maps an address to where a read of it ends up after a redirect. `http` only
    reads what is served here, and `asked()` lists the addresses read."""

    def __init__(self, pages, moved=None):
        self.pages, self.moved = dict(pages), dict(moved or {})

    def _prefix(self, address):
        parts = urllib.parse.urlsplit(address)
        return f"{parts.scheme}://{parts.netloc}", "/" + parts.netloc

    def _path(self, address):
        origin, prefix = self._prefix(address)
        return prefix + (address[len(origin):] or "/")

    def __enter__(self):
        routes = {}
        for address, body in self.pages.items():
            kind = "application/xml" if address.endswith(".xml") else "text/html"
            routes[self._path(address)] = (body, "text/plain", "no") if isinstance(body, int) else (200, kind, body)
        self._server = Loopback(routes)
        server = self._server.__enter__()
        for address, target in self.moved.items():
            routes[self._path(target)] = routes[self._path(address)]
            routes[self._path(address)] = (302, "text/plain", "", {"Location": server.base + self._path(target)})
        self._saved = dict(base.ORIGINS)
        for address in list(self.pages) + list(self.moved.values()):
            origin, prefix = self._prefix(address)
            base.ORIGINS[origin] = server.base + prefix
        outer = self

        class LocalOnly(net.Http):
            def request(self, method, address, headers=None, body=None):
                if not address.startswith(server.base):
                    raise net.HttpError("not a fixture: the tests read nothing else", status=404)
                return super().request(method, address, headers, body)

        self.http = LocalOnly(host_delay=0)
        self._outer = outer
        return self

    def __exit__(self, *exc):
        base.ORIGINS.clear()
        base.ORIGINS.update(self._saved)
        self._server.__exit__(*exc)

    def asked(self):
        return [base._unroute(self._server.base + path) for _, path, _ in self._server.requests]


# ------------------------------------------------------------------------------------ the basics

class Helpers(unittest.TestCase):
    def test_board_slugs_from_urls_and_names(self):
        gh = ("greenhouse.io",)
        self.assertEqual(board_slug("acme", gh), "acme")
        self.assertEqual(board_slug("https://boards.greenhouse.io/acme", gh), "acme")
        self.assertEqual(board_slug("job-boards.greenhouse.io/acme/jobs/12", gh), "acme")
        self.assertEqual(board_slug("https://boards.greenhouse.io/embed/job_board?for=acme", gh), "acme")
        self.assertEqual(board_slug("a.b-c_d", gh), "a.b-c_d", "dots inside a name are fine")
        with self.assertRaises(Fail) as caught:
            board_slug("https://evil.test/acme", gh)
        self.assertIn("evil.test", caught.exception.message)
        for bad in ("", "a b", ".", "..", ".hidden", "https://boards.greenhouse.io/..", "https://boards.greenhouse.io/.",
                    "https://boards.greenhouse.io/embed/job_board?for=..", "https://boards.greenhouse.io/",
                    "https://boards.greenhouse.io/acme%2Fevil", "https://boards.greenhouse.io/embed/job_board?for=a%2Fb"):
            with self.assertRaises(Fail, msg=bad):
                board_slug(bad, gh)

    def test_board_hosts_from_subdomains_and_names(self):
        rc = ("recruitee.com",)
        self.assertEqual(board_host("acme", rc), ("acme", "recruitee.com"))
        self.assertEqual(board_host(" Acme-Labs_2 ", rc), ("acme-labs_2", "recruitee.com"), "lower-cased")
        self.assertEqual(board_host("acme.recruitee.com", rc)[0], "acme")
        self.assertEqual(board_host("https://ACME.recruitee.com/o/backend-engineer?x=1", rc)[0], "acme")
        pe = ("jobs.personio.de", "jobs.personio.com")
        self.assertEqual(board_host("acme", pe), ("acme", "jobs.personio.de"), "a bare name gets the first suffix")
        self.assertEqual(board_host("https://acme.jobs.personio.com/job/1", pe), ("acme", "jobs.personio.com"))
        self.assertEqual(board_host("acme.jobs.personio.de", pe), ("acme", "jobs.personio.de"))
        for bad in ("", ".", "..", "a b", "acme/..", "recruitee.com", ".recruitee.com", "a.b.recruitee.com",
                    "...recruitee.com", "recruitee.com.evil.test", "acme.recruitee.com.evil.test",
                    "https://evil.test/acme.recruitee.com", "acmerecruitee.com", "https://acme.recruitee.com@evil.test/",
                    "javascript:alert(1)"):
            with self.assertRaises(Fail, msg=bad):
                board_host(bad, rc)
        with self.assertRaises(Fail) as caught:
            board_host("acme.personio.de", pe)
        self.assertIn("<company>.jobs.personio.de or <company>.jobs.personio.com", caught.exception.message)
        with self.assertRaises(Fail):
            board_host("acme.jobs.personio.de.evil.test", pe)

    def test_titles_are_matched_to_roles_loosely(self):
        roles = ["Senior Backend Engineer"]
        self.assertTrue(title_fits_roles("Software Engineer, Payments", roles))
        self.assertTrue(title_fits_roles("Engineering Manager", roles), "engineer / engineering")
        self.assertTrue(title_fits_roles("Backend Developer", roles))
        self.assertFalse(title_fits_roles("Account Executive", roles))
        self.assertFalse(title_fits_roles("Senior Recruiter", roles), "seniority words do not count")
        self.assertTrue(title_fits_roles("Anything", []), "no roles, no thinning")
        self.assertTrue(title_fits_roles("Anything", ["Senior"]), "only noise words: no thinning")
        self.assertIs(sources.title_fits_roles, title_fits_roles)

    def test_terms_match_as_words(self):
        self.assertTrue(mentions("We use Rust and Go.", "go"))
        self.assertFalse(mentions("Google builds things", "go"))
        self.assertTrue(mentions("C++ and .NET shop", "c++"))
        self.assertTrue(mentions("a .NET shop", ".net"))
        self.assertFalse(mentions("anything", ""))
        self.assertTrue(sources.mentions("Go, Rust", "GO"))

    def test_pages_are_cut_by_offset_cursor(self):
        items = list(range(5))
        self.assertEqual(page_of(items, None, 2), ([0, 1], "2"))
        self.assertEqual(page_of(items, "2", 2), ([2, 3], "4"))
        self.assertEqual(page_of(items, "4", 2), ([4], None))
        self.assertEqual(page_of(items, "9", 2), ([], None))
        self.assertEqual(page_of([], None, 2), ([], None))
        for bad in ("x", "-1", "1.5"):
            with self.assertRaises(Fail, msg=bad):
                page_of(items, bad, 2)

    def test_text_helpers(self):
        self.assertEqual(base.unescape_markup("&lt;p&gt;a &amp; b&lt;/p&gt;"), "<p>a & b</p>")
        text = base.html_text("<p>Hello <b>world</b></p><ul><li>one</li><li>two</li></ul>")
        self.assertTrue("Hello" in text and "one" in text and "two" in text, text)
        self.assertEqual(one_line("a \n b   c", 10), "a b c")
        self.assertEqual(one_line("abcdefghij", 4), "abcd...")

    def test_addresses_are_read_like_a_browser_reads_them(self):
        self.assertEqual(str(url("HTTPS://Acme.Test:443/a/./b/../c?q=a b#f")), "https://acme.test/a/c?q=a%20b#f")
        self.assertEqual(str(url("http://acme.test")), "http://acme.test/")
        self.assertEqual(url("https://acme.test/x/%2e%2e/%2E./y").path, "/y")
        self.assertEqual(url("https://user:pw@acme.test:8443/x").port, 8443)
        self.assertEqual(url("https://acme.test/x").join("../jobs/1#apply").path, "/jobs/1")
        for bad in ("acme.test/x", "https://", "https://a b/x", "https://javascript:x", "https://acme.test:99999/", "https://a%2eb/", ""):
            self.assertIsNone(url(bad), bad)
        self.assertFalse(url("ftp://acme.test/x").is_http)

    def test_the_cache_loads_once_and_does_not_keep_a_failure(self):
        cache, loads = base.Cached(60), []
        for _ in range(3):
            self.assertEqual(cache.get(lambda: loads.append(1) or 7), 7)
        self.assertEqual(len(loads), 1)
        expired = base.Cached(0)
        expired.get(lambda: 1)
        self.assertEqual(expired.get(lambda: 2), 2, "an expired entry is reloaded")
        with self.assertRaises(Fail):
            base.Cached(60).get(lambda: (_ for _ in ()).throw(Fail("down")))


class Factory(unittest.TestCase):
    def checked(self, kind, address):
        found = sources.check_source(kind, address)
        self.assertEqual(found["kind"], kind, address)
        return found["url"], found["name"]

    def test_the_kinds_and_their_labels(self):
        self.assertEqual(sources.KINDS, ("greenhouse", "lever", "ashby", "smartrecruiters", "recruitee", "personio",
                                         "workday", "hn_hiring", "yc", "career_page"))
        self.assertEqual(set(sources.LABELS), set(sources.KINDS))
        self.assertEqual((sources.LABELS["career_page"], sources.LABELS["smartrecruiters"]), ("Job page", "SmartRecruiters"))

    def test_a_new_source_is_checked_and_named_before_it_is_stored(self):
        self.assertEqual(self.checked("greenhouse", " https://boards.greenhouse.io/acme "), ("https://boards.greenhouse.io/acme", "acme"))
        self.assertEqual(self.checked("lever", "acme"), ("acme", "acme"))
        self.assertEqual(self.checked("ashby", "jobs.ashbyhq.com/acme")[1], "acme")
        self.assertEqual(self.checked("hn_hiring", "ignored"), ("", "Hacker News: Who is hiring"))
        self.assertEqual(self.checked("hn_hiring", "")[0], "")
        self.assertEqual(self.checked("workday", " acme.wd5.myworkdayjobs.com/en-US/External "),
                         ("acme.wd5.myworkdayjobs.com/en-US/External", "acme"))
        self.assertEqual(self.checked("smartrecruiters", "https://jobs.smartrecruiters.com/AcmeGroup")[1], "AcmeGroup")
        self.assertEqual(self.checked("smartrecruiters", "AcmeGroup"), ("AcmeGroup", "AcmeGroup"))
        self.assertEqual(self.checked("recruitee", " https://acme.recruitee.com/ "), ("https://acme.recruitee.com/", "acme"))
        self.assertEqual(self.checked("personio", "acme.jobs.personio.com")[1], "acme")
        self.assertEqual(self.checked("personio", "acme")[1], "acme")
        self.assertEqual(self.checked("yc", ""), (yc.DEFAULT_URL, "Y Combinator jobs"))
        self.assertEqual(self.checked("yc", "workatastartup.com/jobs")[0], "workatastartup.com/jobs")
        self.assertEqual(self.checked("career_page", "https://www.acme.test/careers"), ("https://www.acme.test/careers", "acme.test"))
        for kind, address in (
            ("greenhouse", ""), ("lever", "https://evil.test/acme"), ("career_page", "javascript:x"),
            ("career_page", "https://co.uk/jobs"), ("career_page", "localhost/jobs"), ("career_page", ""),
            ("career_page", "ftp://x.test"), ("yc", "https://evil.test"), ("yc", "https://notycombinator.com/jobs"),
            ("yc", "https://ycombinator.com.evil.test/"), ("linkedin", "https://www.linkedin.com/jobs"),
            ("smartrecruiters", ""), ("smartrecruiters", ".."), ("smartrecruiters", "https://smartrecruiters.com.evil.test/Acme"),
            ("recruitee", ""), ("recruitee", "recruitee.com.evil.test"), ("recruitee", "a.b.recruitee.com"),
            ("recruitee", "https://evil.test/o/x"), ("personio", ".."), ("personio", "acme.jobs.personio.de.evil.test"),
            ("personio", "acme.personio.de"), ("personio", "jobs.personio.de"), ("workday", ""),
            ("workday", "https://acme.wd5.myworkdayjobs.com/.."), ("workday", "myworkdayjobs.com.evil.test/External"),
        ):
            with self.assertRaises(Fail, msg=f"{kind} {address}") as caught:
                sources.check_source(kind, address)
            self.assertTrue(caught.exception.hint, f"{kind} {address}")

    def test_any_page_of_a_real_site_is_a_job_page(self):
        for address, name in (
            ("https://www.linkedin.com/jobs/search?keywords=rust", "linkedin.com"),
            ("https://acme.github.io/", "acme.github.io"),
            ("https://github.io/jobs", "github.io"),
            ("https://apply.workable.com/acme/", "apply.workable.com"),
            ("https://www.greenhouse.io/careers", "greenhouse.io"),
        ):
            self.assertEqual(self.checked("career_page", address), (address, name))

    def test_a_company_board_given_as_a_job_page_becomes_that_boards_source(self):
        for address, kind, stored, name in (
            ("https://boards.greenhouse.io/acme", "greenhouse", "boards.greenhouse.io/acme", "acme"),
            ("boards.greenhouse.io/acme/jobs/4011001", "greenhouse", "boards.greenhouse.io/acme", "acme"),
            ("https://jobs.lever.co/acme", "lever", "jobs.lever.co/acme", "acme"),
            ("https://jobs.ashbyhq.com/acme", "ashby", "jobs.ashbyhq.com/acme", "acme"),
            ("https://jobs.smartrecruiters.com/AcmeGroup", "smartrecruiters", "jobs.smartrecruiters.com/AcmeGroup", "AcmeGroup"),
            ("https://acme.recruitee.com/", "recruitee", "acme.recruitee.com", "acme"),
            ("https://acme.jobs.personio.de/", "personio", "acme.jobs.personio.de", "acme"),
            ("https://acme.wd5.myworkdayjobs.com/en-US/External", "workday", "https://acme.wd5.myworkdayjobs.com/External", "acme"),
        ):
            self.assertEqual(sources.check_source("career_page", address), {"kind": kind, "url": stored, "name": name}, address)
        # A board's domain without a company board in the address is just a page.
        self.assertEqual(sources.check_source("career_page", "https://boards.greenhouse.io/")["kind"], "career_page")

    def test_the_factory_builds_by_kind_and_explains_failures(self):
        def build(kind, address, name=""):
            return sources.adapter_for({"kind": kind, "url": address, "name": name})

        for kind, address, adapter in (
            ("greenhouse", "acme", Greenhouse), ("lever", "https://jobs.lever.co/acme", Lever),
            ("ashby", "jobs.ashbyhq.com/acme", Ashby), ("hn_hiring", "", HnHiring),
            ("smartrecruiters", "jobs.smartrecruiters.com/Acme", SmartRecruiters), ("recruitee", "acme.recruitee.com", Recruitee),
            ("personio", "acme.jobs.personio.com", Personio), ("workday", "https://acme.wd5.myworkdayjobs.com/External", Workday),
            ("yc", "", Yc), ("career_page", "acme.test/careers", CareerPage),
        ):
            built = build(kind, address)
            self.assertIsInstance(built, adapter)
            self.assertEqual(built.board_hints(), [])
        self.assertEqual(build("greenhouse", "acme").company, "acme", "no name: the board's")
        self.assertEqual(build("greenhouse", "acme", " Acme Robotics ").company, "Acme Robotics")
        self.assertEqual(build("personio", "acme.jobs.personio.com").origin, "https://acme.jobs.personio.com")
        self.assertEqual(build("recruitee", "Acme").origin, "https://acme.recruitee.com")
        self.assertEqual(build("career_page", "https://www.acme.test/careers").company, "www.acme.test")
        self.assertEqual(build("career_page", "https://www.acme.test/careers", "Acme").company, "Acme")
        for kind, address, part in (
            ("greenhouse", "", "no company board"), ("recruitee", "recruitee.com.evil.test", "Recruitee source"),
            ("personio", "..", "Personio source"), ("smartrecruiters", "https://evil.test/Acme", "SmartRecruiters source"),
            ("workday", "https://acme.wd5.myworkdayjobs.com.evil.test/External", "not a Workday board"),
            ("nope", "", "no adapter for source kind `nope`"), ("career_page", "", "no page address"),
            ("career_page", "ftp://x.test", "http"), ("yc", "https://evil.test/jobs", "not a ycombinator.com"),
        ):
            with self.assertRaises(Fail, msg=f"{kind} {address}") as caught:
                build(kind, address)
            self.assertIn(part, caught.exception.message)

    def test_the_module_bases_point_the_factory_at_a_test_server(self):
        listing = {"jobs": [{"title": "Rust Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"}]}
        offers = {"offers": [{"slug": "dev", "title": "Developer"}]}
        with served({"/v1/boards/acme/jobs": (200, JSON, listing), "/api/offers/": (200, JSON, offers)}) as server:
            previous = greenhouse_module.BASE, recruitee_module.ORIGIN
            greenhouse_module.BASE = recruitee_module.ORIGIN = server.base
            try:
                board = sources.adapter_for({"kind": "greenhouse", "url": "acme", "name": "Acme"})
                self.assertEqual(titles(board.search({})), ["Rust Engineer"])
                offers_board = sources.adapter_for({"kind": "recruitee", "url": "acme", "name": ""})
                self.assertEqual(titles(offers_board.search(None)), ["Developer"])
            finally:
                greenhouse_module.BASE, recruitee_module.ORIGIN = previous


# ------------------------------------------------------------------------------ the API adapters

class GreenhouseBoard(unittest.TestCase):
    LIST, JOB = "/v1/boards/acme/jobs", "/v1/boards/acme/jobs/4011001"

    def routes(self):
        return {self.LIST: (200, JSON, fixture("greenhouse_jobs.json")), self.JOB: (200, JSON, fixture("greenhouse_job.json"))}

    def adapter(self, server):
        return Greenhouse("acme", "Acme (source name)", base=server.base + "/")

    def test_lists_the_board_and_drops_postings_without_a_link(self):
        with served(self.routes()) as server:
            page = self.adapter(server).search({}, None, 10)
            self.assertEqual(titles(page), ["Senior Backend Engineer", "Account Executive, Enterprise", "Software Engineer, Platform",
                                            "Engineering Manager, Fleet", "Staff Data Engineer"])
            first = page["candidates"][0]
            self.assertEqual(first, {"url": "https://boards.greenhouse.io/acme/jobs/4011001?gh_src=abc123", "title": "Senior Backend Engineer",
                                     "company": "Acme Robotics", "location": "Remote - Vietnam", "snippet": ""})
            self.assertEqual(page["candidates"][2]["company"], "Acme (source name)", "no company on the posting: the source's name")
            self.assertEqual(page["candidates"][4]["location"], "", "a null location is empty")
            self.assertIsNone(page["next"])
            self.assertEqual(paths(server), [self.LIST])

    def test_preferences_thin_out_the_board_and_pages_follow_the_cursor(self):
        with served(self.routes()) as server:
            board, prefs = self.adapter(server), {"roles": ["Backend Engineer"]}
            first = board.search(prefs, None, 2)
            self.assertEqual((len(first["candidates"]), first["next"]), (2, "2"))
            second = board.search(prefs, first["next"], 2)
            self.assertEqual((len(second["candidates"]), second["next"]), (2, None))
            self.assertEqual(second, board.search(prefs, "2", 2), "the same cursor gives the same page")
            every = titles(first) + titles(second)
            self.assertNotIn("Account Executive, Enterprise", every)
            self.assertIn("Engineering Manager, Fleet", every)
            self.assertEqual(len(server.requests), 1, "one listing request serves every page")

    def test_a_posting_is_read_from_its_job_endpoint(self):
        with served(self.routes()) as server:
            board = self.adapter(server)
            page = board.search({}, None, 10)
            text = board.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Senior Backend Engineer\n\nCompany: Acme Robotics\nLocation: Remote - Vietnam\n\n"), text)
            self.assertTrue("Rust & Go" in text and "Kubernetes on AWS" in text, text)
            self.assertFalse("<p>" in text or "&lt;" in text, text)
            with self.assertRaises(Fail) as caught:
                board.posting(page["candidates"][2])
            self.assertEqual(caught.exception.extra.get("status"), 404)
            self.assertIsNone(board.posting({"url": "https://careers.acme.test/open"}), "no job id: the caller reads the page")

    def test_job_ids_come_from_the_path_or_the_gh_jid_parameter(self):
        job_id = greenhouse_module._job_id
        self.assertEqual(job_id("https://boards.greenhouse.io/acme/jobs/4011001?gh_src=x"), "4011001")
        self.assertEqual(job_id("https://careers.acme.test/open?gh_jid=77"), "77")
        self.assertIsNone(job_id("https://careers.acme.test/open"))
        self.assertIsNone(job_id("javascript:alert(1)"))

    def test_a_broken_board_is_an_error_naming_the_step(self):
        with served({self.LIST: (200, JSON, {"nope": 1}), "/v1/boards/down/jobs": (503, "text/plain", "busy"),
                     "/v1/boards/html/jobs": (200, "text/html", "<html>")}) as server:
            with self.assertRaises(Fail) as caught:
                self.adapter(server).search({}, None, 5)
            self.assertIn("no job list", caught.exception.message)
            with self.assertRaises(Fail) as caught:
                Greenhouse("down", "Down", base=server.base).search({})
            self.assertNotIsInstance(caught.exception, NeedsBrowser, "an API that is busy is not a page to open")
            self.assertIn("reading the Greenhouse board", caught.exception.message)
            self.assertEqual(caught.exception.extra.get("status"), 503)
            with self.assertRaises(Fail) as caught:
                Greenhouse("html", "Html", base=server.base).search({})
            self.assertIn("not JSON", caught.exception.message)


class LeverBoard(unittest.TestCase):
    URL = "/v0/postings/globex?mode=json"

    def test_lists_postings_with_locations_and_snippets(self):
        with served({self.URL: (200, JSON, fixture("lever_postings.json"))}) as server:
            page = Lever("globex", "Globex Corp", base=server.base).search({}, None, 10)
            go, sales, sre = page["candidates"]
            self.assertEqual((go["title"], go["company"], go["location"]), ("Backend Engineer (Go)", "Globex Corp", "Ho Chi Minh City"))
            self.assertTrue(go["snippet"].startswith("Globex is a payments company"), go["snippet"])
            self.assertEqual(go["url"], "https://jobs.lever.co/globex/5f2a1c9e-0000-4000-8000-000000000001")
            self.assertEqual(sales["location"], "London")
            self.assertEqual(sre["location"], "Remote, Singapore", "all locations when there is no main one")
            self.assertIsNone(page["next"])
            self.assertEqual(paths(server), [self.URL])

    def test_postings_are_read_from_the_cached_listing(self):
        with served({self.URL: (200, JSON, fixture("lever_postings.json"))}) as server:
            lever = Lever("globex", "Globex", base=server.base + "/")
            page = lever.search({"roles": ["Software Engineer"]}, None, 10)
            self.assertEqual(titles(page), ["Backend Engineer (Go)", "Site Reliability Engineer"], "no sales in an engineering search")
            first = lever.search({"roles": ["Software Engineer"]}, None, 1)
            self.assertEqual((titles(first), first["next"]), (["Backend Engineer (Go)"], "1"))
            text = lever.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Backend Engineer (Go)\n\nCompany: Globex\nLocation: Ho Chi Minh City\n\n"), text)
            self.assertTrue("move money between banks" in text and "3+ years of Go" in text and "remote-friendly hours" in text, text)
            self.assertIsNone(lever.posting({"url": "https://jobs.lever.co/globex/zzz"}))
            self.assertEqual(len(server.requests), 1, "listing, search and postings: one request")

    def test_the_remote_flag_shows_in_the_location(self):
        body = [{"id": "1", "text": "Dev", "hostedUrl": "https://jobs.lever.co/x/1", "categories": {"location": "Hanoi"},
                 "workplaceType": "remote", "descriptionPlain": "d"}]
        with served({"/v0/postings/x?mode=json": (200, JSON, body)}) as server:
            self.assertEqual(Lever("x", "X", base=server.base).search({}, None, 5)["candidates"][0]["location"], "Hanoi (Remote)")

    def test_an_unexpected_answer_is_an_error(self):
        with served({self.URL: (200, JSON, {"ok": False})}) as server:
            with self.assertRaises(Fail) as caught:
                Lever("globex", "G", base=server.base).search({}, None, 5)
            self.assertIn("not a list", caught.exception.message)


class AshbyBoard(unittest.TestCase):
    URL = "/posting-api/job-board/initech?includeCompensation=true"

    def test_lists_listed_jobs_with_remote_and_pay(self):
        with served({self.URL: (200, JSON, fixture("ashby_board.json"))}) as server:
            ashby = Ashby("initech", "Initech", base=server.base)
            page = ashby.search({}, None, 10)
            self.assertEqual(titles(page), ["Staff Software Engineer, Infrastructure", "Product Designer"], "unlisted jobs are skipped")
            self.assertEqual([c["location"] for c in page["candidates"]], ["Remote - EMEA", "Austin, TX"])
            self.assertTrue(page["candidates"][0]["snippet"].startswith("Initech makes workflow software"))
            self.assertEqual((titles(ashby.search({}, None, 1)), ashby.search({}, None, 1)["next"]), ([titles(page)[0]], "1"))
            text = ashby.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Staff Software Engineer, Infrastructure\n\nCompany: Initech\nLocation: Remote - EMEA\n"), text)
            self.assertTrue("Compensation: $180K - $220K" in text and "storage layer written in Rust" in text, text)
            self.assertIsNone(ashby.posting({"url": "https://jobs.ashbyhq.com/initech/nope"}))
            # The posting is found when the caller stripped tracking parameters from the address.
            stripped = {"url": "https://jobs.ashbyhq.com/initech/7b1d0000-0000-4000-8000-0000000000a2"}
            self.assertIn("Design things.", ashby.posting(stripped))
            self.assertEqual(paths(server), [self.URL])

    def test_a_board_that_is_down_is_an_error_naming_the_step(self):
        with served({}) as server:
            with self.assertRaises(Fail) as caught:
                Ashby("initech", "Initech", base=server.base).search({}, None, 5)
            self.assertIn("reading the Ashby job board", caught.exception.message)


class SmartRecruitersBoard(unittest.TestCase):
    LIST = "/v1/companies/AcmeGroup/postings?limit=100&offset=0"
    NEXT = "/v1/companies/AcmeGroup/postings?limit=100&offset=100"
    JOB = "/v1/companies/AcmeGroup/postings/744000100000001"
    SECOND = {"offset": 100, "limit": 100, "totalFound": 150, "content": [
        {"id": "744000100000101", "name": "Platform Engineer", "location": {"fullLocation": "Berlin, Germany"}},
        {"id": "744000100000102", "name": "Office Manager", "location": {"fullLocation": "Berlin, Germany"}}]}
    ENGINEERS = {"roles": ["Backend Engineer"]}

    def routes(self):
        return {self.LIST: (200, JSON, fixture("smartrecruiters_postings.json")), self.NEXT: (200, JSON, self.SECOND),
                self.JOB: (200, JSON, fixture("smartrecruiters_posting.json"))}

    def adapter(self, server):
        return SmartRecruiters("AcmeGroup", "AcmeGroup (source name)", base=server.base + "/")

    def test_lists_postings_with_urls_companies_and_locations(self):
        with served(self.routes()) as server:
            page = self.adapter(server).search({}, None, 4)
            self.assertEqual(titles(page), ["Senior Backend Engineer (Rust)", "Account Executive, DACH", "Site Reliability Engineer",
                                            "Data Engineer"], "no title: dropped")
            first = page["candidates"][0]
            self.assertEqual(first, {"url": "https://jobs.smartrecruiters.com/AcmeGroup/744000100000001",
                                     "title": "Senior Backend Engineer (Rust)", "company": "Acme Group",
                                     "location": "Berlin, Berlin, Germany (Hybrid)", "snippet": "Mid-Senior Level, Full-time"})
            self.assertEqual(page["candidates"][2]["company"], "AcmeGroup (source name)", "no company name: the source's name")
            self.assertEqual(page["candidates"][2]["location"], "Lisbon, pt (Remote)", "no full location: its parts")
            self.assertEqual(page["candidates"][3]["location"], "Ho Chi Minh City, Vietnam")
            self.assertEqual(page["next"], "100:0", "the API has 150: the next API page")
            self.assertEqual(paths(server), [self.LIST])

    def test_roles_thin_the_list_and_pages_follow_the_cursor_across_api_pages(self):
        with served(self.routes()) as server:
            board = self.adapter(server)
            first = board.search(self.ENGINEERS, None, 2)
            self.assertEqual(titles(first), ["Senior Backend Engineer (Rust)", "Site Reliability Engineer"])
            self.assertEqual(first["next"], "0:2")
            again, second = board.search(self.ENGINEERS, "0:2", 2), board.search(self.ENGINEERS, "0:2", 2)
            self.assertEqual(again, second, "the same cursor gives the same page")
            self.assertEqual(titles(second), ["Data Engineer", "Platform Engineer"], "the rest of the first API page, then the second")
            self.assertIsNone(second["next"], "150 found: no third API page")
            self.assertEqual(paths(server), [self.LIST, self.NEXT], "each API page is read once")

    def test_a_search_reads_a_few_api_pages_at_most(self):
        # Five API pages (500 found), none with a fitting title.
        five = {f"/v1/companies/AcmeGroup/postings?limit=100&offset={i * 100}":
                (200, JSON, {"offset": i * 100, "limit": 100, "totalFound": 500, "content": [{"id": f"9{i}", "name": "Office Manager"}]})
                for i in range(5)}
        prefs = {"roles": ["Pastry Chef"]}
        with served(five) as server:
            board = self.adapter(server)
            page = board.search(prefs, None, 5)
            self.assertEqual((page["candidates"], page["next"]), ([], "300:0"), "three API pages read, the cursor points at the next")
            self.assertEqual(paths(server), list(five)[:3])
            rest = board.search(prefs, page["next"], 5)
            self.assertEqual((rest["candidates"], rest["next"]), ([], None), "the last two pages, then the end")
            self.assertEqual(len(server.requests), 5)
        with served(self.routes()) as server:
            page = self.adapter(server).search(prefs, None, 5)
            self.assertEqual((page["candidates"], page["next"]), ([], None), "two API pages, both read, nothing fits")
            for bad in ("x", "1:a", "1", "-1:0", f"{2 ** 70}:0"):
                with self.assertRaises(Fail, msg=bad):
                    self.adapter(server).search(prefs, bad, 5)

    def test_a_posting_is_read_from_its_detail_endpoint(self):
        with served(self.routes()) as server:
            board = self.adapter(server)
            page = board.search({}, None, 1)
            text = board.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Senior Backend Engineer (Rust)\n\nCompany: Acme Group\nLocation: Berlin, Berlin, Germany (Hybrid)"), text)
            self.assertTrue("Employment: Full-time" in text and "Experience: Mid-Senior Level" in text, text)
            self.assertTrue("## Job Description" in text and "payment services in Rust" in text and "PostgreSQL" in text, text)
            self.assertTrue("anvils & rockets" in text and "<p>" not in text, text)
            self.assertNotIn("Additional Information", text, "an empty section is left out")
            self.assertEqual(paths(server)[-1], self.JOB)
            # A posting URL with the title slug after the id works too; one without an id is not read.
            slugged = {"url": "https://jobs.smartrecruiters.com/AcmeGroup/744000100000001-senior-backend-engineer"}
            self.assertIsNotNone(board.posting(slugged))
            self.assertIsNone(board.posting({"url": "https://jobs.smartrecruiters.com/AcmeGroup/.."}))
            self.assertIsNone(board.posting({"url": "https://jobs.smartrecruiters.com/AcmeGroup/a_b"}))

    def test_an_unknown_shape_and_a_failed_request_name_the_source(self):
        with served({self.LIST: (200, JSON, {"message": "nope"}), self.JOB: (200, JSON, {"id": "1"})}) as server:
            with self.assertRaises(Fail) as caught:
                self.adapter(server).search({}, None, 5)
            self.assertIn("SmartRecruiters answer has no posting list", caught.exception.message)
            with self.assertRaises(Fail) as caught:
                self.adapter(server).posting({"url": "https://jobs.smartrecruiters.com/AcmeGroup/744000100000001"})
            self.assertIn("SmartRecruiters posting has no job ad", caught.exception.message)
        with served({self.LIST: (503, "text/plain", "busy")}) as server:
            with self.assertRaises(Fail) as caught:
                self.adapter(server).search({}, None, 5)
            self.assertIn("reading the SmartRecruiters postings", caught.exception.message)
            self.assertEqual(caught.exception.extra["status"], 503)

    def test_locations(self):
        location = smartrecruiters._location
        self.assertEqual(location({"city": "Hanoi", "country": "vn", "remote": True}), "Hanoi, vn (Remote)")
        self.assertEqual(location({"fullLocation": "Remote, EU", "remote": True}), "Remote, EU")
        self.assertEqual(location({"hybrid": True}), "Hybrid")
        self.assertEqual(location(None), "")


class RecruiteeBoard(unittest.TestCase):
    ORIGIN = "https://acme.recruitee.com"

    @contextlib.contextmanager
    def board(self, answer=None):
        answer = answer or (200, JSON, fixture("recruitee_offers.json"))
        with served({"/api/offers/": answer}, {self.ORIGIN: ""}) as server:
            yield Recruitee(self.ORIGIN + "/", "acme (source name)"), server

    def test_lists_offers_with_urls_companies_and_locations(self):
        with self.board() as (board, server):
            page = board.search({}, None, 10)
            self.assertEqual(titles(page), ["Backend Engineer", "Account Manager", "Platform Engineer", "Data Analyst"], "no slug: dropped")
            backend, sales, platform, analyst = page["candidates"]
            self.assertEqual(backend["url"], "https://careers.acme.test/o/backend-engineer", "the offer's own careers page")
            self.assertEqual((backend["company"], backend["location"]), ("Acme", "Amsterdam, Noord-Holland, Netherlands (Hybrid)"))
            self.assertTrue(backend["snippet"].startswith("At Acme we make anvils easy to order."), backend["snippet"])
            self.assertEqual(sales["url"], "https://acme.recruitee.com/o/account-manager", "a careers_url of another offer is not used")
            self.assertEqual((sales["company"], sales["location"]), ("acme (source name)", "Utrecht, Netherlands"))
            self.assertEqual(platform["url"], "https://acme.recruitee.com/o/platform-engineer", "only http(s)")
            self.assertEqual(platform["location"], "Remote")
            self.assertEqual(analyst["url"], "https://acme.recruitee.com/o/data-analyst", "a careers_url on a private host is not used")
            self.assertIsNone(page["next"])
            self.assertEqual(paths(server), ["/api/offers/"])

    def test_roles_thin_the_list_and_pages_follow_the_cursor(self):
        with self.board() as (board, server):
            prefs = {"roles": ["Software Engineer"]}
            first = board.search(prefs, None, 1)
            self.assertEqual((titles(first), first["next"]), (["Backend Engineer"], "1"))
            second = board.search(prefs, "1", 1)
            self.assertEqual(second, board.search(prefs, "1", 1), "the same cursor gives the same page")
            self.assertEqual((titles(second), second["next"]), (["Platform Engineer"], None), "no sales in an engineering search")
            self.assertEqual(len(server.requests), 1, "one listing request serves every page")

    def test_postings_are_read_from_the_cached_listing(self):
        with self.board() as (board, server):
            page = board.search({}, None, 10)
            text = board.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Backend Engineer\n\nCompany: Acme\nLocation: Amsterdam"), text)
            self.assertTrue("Department: Technology" in text and "Salary: 60000-80000 EUR per year" in text, text)
            self.assertTrue("order API in Go" in text and "## Requirements" in text and "You know PostgreSQL" in text, text)
            self.assertFalse("<p" in text or "<li>" in text, text)
            sales = board.posting(page["candidates"][1])
            self.assertFalse("Salary" in sales or "## Requirements" in sales, sales)
            self.assertIn("Kubernetes", board.posting({"url": "https://acme.recruitee.com/o/platform-engineer"}))
            self.assertIsNone(board.posting(page["candidates"][3]), "no description: the caller loads the page")
            self.assertIsNone(board.posting({"url": "https://acme.recruitee.com/o/zzz"}))
            self.assertEqual(len(server.requests), 1)

    def test_an_unknown_shape_and_a_failed_request_name_the_source(self):
        with self.board((200, JSON, [{"title": "x"}])) as (board, _):
            with self.assertRaises(Fail) as caught:
                board.search({}, None, 5)
            self.assertIn("Recruitee answer has no offer list", caught.exception.message)
        with self.board((429, "text/plain", "slow down")) as (board, _):
            with self.assertRaises(Fail) as caught:
                board.search({}, None, 5)
            self.assertIn("reading the Recruitee offers", caught.exception.message)
            self.assertEqual(caught.exception.extra["status"], 429)

    def test_careers_urls_and_salaries(self):
        posting_url, salary = recruitee_module.posting_url, recruitee_module.salary_of
        self.assertEqual(posting_url("https://careers.acme.test/o/dev/", "dev"), "https://careers.acme.test/o/dev/")
        self.assertIsNone(posting_url("https://careers.acme.test/o/dev-2", "dev"), "another offer")
        for unused in ("javascript:alert(1)//o/dev", "", "http://careers.acme.test/o/dev", "https://localhost/o/dev",
                       "https://jobs.localhost/o/dev", "https://intranet/o/dev", "https://careers.corp.internal/o/dev",
                       "https://127.0.0.1/o/dev", "https://10.0.0.5/o/dev", "https://8.8.8.8/o/dev", "https://[::1]/o/dev",
                       "https://careers.acme.test/x/../o/../o/other"):
            self.assertIsNone(posting_url(unused, "dev"), unused)
        self.assertEqual(salary({"min": 5000, "max": 5000, "currency": "EUR", "period": "month"}), "5000 EUR per month")
        self.assertEqual(salary({"min": None, "max": "90000"}), "90000")
        self.assertIsNone(salary({"min": None, "max": None, "currency": "EUR"}))
        self.assertIsNone(salary(None))


class PersonioBoard(unittest.TestCase):
    ORIGIN = "https://acme.jobs.personio.de"

    @contextlib.contextmanager
    def board(self, answer=None):
        answer = answer or (200, "application/xml", fixture("personio.xml"))
        with served({"/xml": answer}, {self.ORIGIN: ""}) as server:
            yield Personio(self.ORIGIN + "/", "acme (source name)"), server

    def test_lists_positions_with_urls_companies_and_offices(self):
        with self.board() as (board, server):
            page = board.search({}, None, 10)
            self.assertEqual(titles(page), ["Senior Backend Engineer (d/f/m)", "Business Development Representative (d/f/m)",
                                            "Data Engineer"], "a bad id: dropped")
            backend, sales, data = page["candidates"]
            self.assertEqual(backend, {"url": "https://acme.jobs.personio.de/job/2885001", "title": "Senior Backend Engineer (d/f/m)",
                                       "company": "Acme GmbH", "location": "Berlin, Munich",
                                       "snippet": "Product & Tech, experienced, full-time"})
            self.assertEqual((sales["company"], sales["location"]), ("acme (source name)", "Berlin"))
            self.assertEqual(data["url"], "https://acme.jobs.personio.de/job/2885003")
            self.assertIsNone(page["next"])
            self.assertEqual(paths(server), ["/xml"])

    def test_roles_thin_the_list_and_pages_follow_the_cursor(self):
        with self.board() as (board, server):
            prefs = {"roles": ["Backend Engineer"]}
            first = board.search(prefs, None, 1)
            self.assertEqual((titles(first), first["next"]), (["Senior Backend Engineer (d/f/m)"], "1"))
            second = board.search(prefs, "1", 1)
            self.assertEqual(second, board.search(prefs, "1", 1), "the same cursor gives the same page")
            self.assertEqual((titles(second), second["next"]), (["Data Engineer"], None), "no sales in an engineering search")
            self.assertEqual(len(server.requests), 1, "one feed request serves every page")

    def test_a_posting_has_the_feed_text_or_none_when_the_feed_has_no_description(self):
        with self.board() as (board, server):
            page = board.search({}, None, 10)
            text = board.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Senior Backend Engineer (d/f/m)\n\nCompany: Acme GmbH\nLocation: Berlin, Munich\n"), text)
            self.assertTrue("Department: Product & Tech" in text and "Employment: permanent" in text, text)
            self.assertTrue("## Your mission" in text and "ordering platform in" in text and "## Your profile" in text, text)
            self.assertTrue("PostgreSQL & Kafka" in text and "<li>" not in text, text)
            self.assertIsNone(board.posting(page["candidates"][1]), "empty descriptions: the caller loads the page")
            self.assertIsNone(board.posting(page["candidates"][2]), "an empty element too")
            self.assertIsNone(board.posting({"url": "https://acme.jobs.personio.de/job/999"}))
            self.assertEqual(len(server.requests), 1)

    def test_markup_outside_cdata_is_read_as_text(self):
        xml = ("<workzag-jobs><position><id>1</id><name>Dev</name><jobDescriptions><jobDescription><name>Role</name>"
               "<value><p>Write <b>Rust</b> &amp; Go</p></value></jobDescription></jobDescriptions></position></workzag-jobs>")
        heading, markup = parse_positions(xml)[0]["descriptions"][0]
        self.assertEqual(heading, "Role")
        self.assertTrue("Rust" in markup and "& Go" in markup, markup)

    def test_deep_nesting_and_entity_tricks_are_refused_quickly(self):
        started = time.monotonic()
        for inner in ("<value>", "<x>"):
            deep = "<workzag-jobs><position><jobDescriptions><jobDescription>" + inner * 1_000_000
            with self.assertRaises(Fail) as caught:
                parse_positions(deep)
            self.assertIn("Personio answer is not a job feed", caught.exception.message)
            self.assertIn("too deep", caught.exception.message)
        fine = ("<workzag-jobs><position><id>1</id><name>Dev</name><jobDescriptions><jobDescription><name>R</name><value>"
                + "<b>" * 50 + "a" + "</b>" * 50 + "</value></jobDescription></jobDescriptions></position></workzag-jobs>")
        self.assertIn("a", parse_positions(fine)[0]["descriptions"][0][1], "nesting below the limit is read")
        bomb = ('<?xml version="1.0"?><!DOCTYPE b [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                "<workzag-jobs><position><id>1</id><name>&b;</name></position></workzag-jobs>")
        with self.assertRaises(Fail):
            parse_positions(bomb)
        self.assertLess(time.monotonic() - started, 5)

    def test_an_unknown_shape_and_a_failed_request_name_the_source(self):
        for body in ('{"jobs": []}', "<html><body>Jobs</body></html>", "<workzag-jobs><position>"):
            with self.board((200, "application/xml", body)) as (board, _):
                with self.assertRaises(Fail, msg=body) as caught:
                    board.search({}, None, 5)
                self.assertIn("Personio answer is not", caught.exception.message)
        with self.board((200, "application/xml", '<?xml version="1.0"?><workzag-jobs></workzag-jobs>')) as (board, _):
            self.assertEqual(board.search({}, None, 5)["candidates"], [], "no openings is not an error")
        with self.board((502, "text/plain", "bad gateway")) as (board, _):
            with self.assertRaises(Fail) as caught:
                board.search({}, None, 5)
            self.assertIn("reading the Personio job feed", caught.exception.message)
            self.assertEqual(caught.exception.extra["status"], 502)


class WorkdayBoard(unittest.TestCase):
    HOME = "https://acme.wd5.myworkdayjobs.com"
    SEARCH = "/wday/cxs/acme/External/jobs"
    DETAIL = "/wday/cxs/acme/External/job/US-CA-Remote/Site-Reliability-Engineer_JR1000003"
    LIST = json.loads(fixture("workday_jobs.json"))

    def board(self):
        return Board.parse("https://acme.wd5.myworkdayjobs.com/en-US/External")

    @staticmethod
    def body(query, offset, limit):
        return {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": query}

    @staticmethod
    def listing(prefix, count, total):
        """A listing answer of `count` postings named `<prefix><i>`, out of `total`."""
        return {"total": total, "jobPostings": [
            {"title": f"Engineer {prefix}{i}", "externalPath": f"/job/X/{prefix}{i}", "locationsText": "X", "postedOn": "Posted Today"}
            for i in range(count)]}

    @contextlib.contextmanager
    def serving(self, posts, gets=None):
        """`posts`: `(request body, answer)` pairs for the search; `gets`: path -> answer."""
        def search(handler):
            asked = json.loads(handler.body)
            for body, answer in posts:
                if body == asked:
                    return 200, JSON, answer
            return 404, "text/plain", "no such search"

        routes = {self.SEARCH: search}
        routes.update({path: (200, JSON, answer) for path, answer in (gets or {}).items()})
        with served(routes, {self.HOME: ""}) as server:
            yield server

    def posted(self, server):
        return [json.loads(body) for method, path, body in server.requests if method == "POST" and path == self.SEARCH]

    def test_board_urls_are_read_strictly(self):
        board = self.board()
        self.assertEqual((board.host, board.tenant, board.site), ("acme.wd5.myworkdayjobs.com", "acme", "External"))
        self.assertEqual(Board.parse("Acme.WD12.myworkdayjobs.com/Careers_2/job/X/Y_1").site, "Careers_2", "a posting URL names its board")
        self.assertEqual(Board.parse("https://acme.wd1.myworkdayjobs.com/fr-FR/Jobs?q=x").site, "Jobs")
        for bad in (
            "", "acme", "https://acme.wd5.myworkdayjobs.com", "https://acme.wd5.myworkdayjobs.com/en-US",
            "https://acme.wd5.myworkdayjobs.com/..", "https://acme.wd5.myworkdayjobs.com/a%2Fb",
            "https://acme.wd5.myworkdayjobs.com/Ext.ernal", "https://acme.wd5.myworkdayjobs.com.evil.test/External",
            "https://acme.wd5.myworkdayjobs.com./External", "https://myworkdayjobs.com/External",
            "https://wd5.myworkdayjobs.com/External", "https://a.b.wd5.myworkdayjobs.com/External",
            "https://acme.www.myworkdayjobs.com/External", "https://acme.wd5.myworkdayjobs.com:8443/External",
            "https://user@acme.wd5.myworkdayjobs.com/External", "ftp://acme.wd5.myworkdayjobs.com/External",
            "https://acme.myworkdaysite.com/External", "https://acme.wd5.myworkdayjobs.com/%2e%2e/External/..",
        ):
            with self.assertRaises(Fail, msg=bad):
                Board.parse(bad)

    def test_lists_the_board_with_one_query_per_role(self):
        posts = [(self.body("Backend Engineer", 0, 20), self.LIST), (self.body("SRE", 0, 20), {"total": 0, "jobPostings": []})]
        with self.serving(posts) as server:
            board = Workday(self.board(), "Acme")
            prefs = {"roles": ["Backend Engineer", " backend engineer ", "", "SRE"]}
            page = board.search(prefs, None, 25)
            self.assertEqual(titles(page), ["Senior Backend Engineer, Payments", "Site Reliability Engineer"], "no sales, no untitled posting")
            self.assertEqual(page["candidates"][0], {
                "url": "https://acme.wd5.myworkdayjobs.com/External/job/Vietnam-Ho-Chi-Minh-City/Senior-Backend-Engineer--Payments_JR1000001",
                "title": "Senior Backend Engineer, Payments", "company": "Acme", "location": "Vietnam, Ho Chi Minh City",
                "snippet": "Posted 3 Days Ago"})
            self.assertEqual(page["next"], "1:0", "the whole first role fit one request; the second role is next")
            second = board.search(prefs, page["next"], 25)
            self.assertEqual((second["candidates"], second["next"]), ([], None))
            self.assertEqual(self.posted(server), [self.body("Backend Engineer", 0, 20), self.body("SRE", 0, 20)],
                             "the search text is the role, nothing else")

    def test_no_roles_is_one_empty_search_with_a_lower_cap(self):
        posts = [(self.body("", i * 20, 20), self.listing(f"p{i}-", 20, 1000)) for i in range(6)]
        with self.serving(posts) as server:
            board, cursor, seen = Workday(self.board(), "Acme"), None, 0
            while True:
                page = board.search({}, cursor, 25)
                seen += len(page["candidates"])
                cursor = page["next"]
                if cursor is None:
                    break
            self.assertEqual(seen, workday.MAX_OFFSET_NO_ROLES)
            self.assertEqual(len(self.posted(server)), workday.MAX_OFFSET_NO_ROLES // workday.PAGE_LIMIT)

    def test_a_fresh_adapter_gives_the_same_page_for_the_same_cursor(self):
        def job(title, path):
            return {"title": title, "externalPath": path, "locationsText": "A", "postedOn": "Posted Today"}

        rust = {"total": 2, "jobPostings": [job("Rust Engineer", "/job/A/Rust-Engineer_1"), job("Platform Engineer", "/job/A/Platform-Engineer_2")]}
        platform = {"total": 2, "jobPostings": [job("Platform Engineer", "/job/A/Platform-Engineer_2"), job("Platform Lead Engineer", "/job/A/Platform-Lead_3")]}
        prefs = {"roles": ["Rust Engineer", "Platform Engineer"]}
        with self.serving([(self.body("Rust Engineer", 0, 10), rust), (self.body("Platform Engineer", 0, 10), platform)]):
            first_run = Workday(self.board(), "Acme")
            first = first_run.search(prefs, None, 10)
            self.assertEqual(first["next"], "1:0")
            second = first_run.search(prefs, "1:0", 10)
            self.assertEqual(titles(second), ["Platform Engineer", "Platform Lead Engineer"], "the caller's dedup drops the repeat, not the adapter")
            # A later command builds a new adapter and starts from the saved cursor.
            resumed = Workday(self.board(), "Acme")
            self.assertEqual(resumed.search(prefs, "1:0", 10), second)
            self.assertEqual(resumed.search(prefs, None, 10), first)

    def test_pages_follow_the_cursor_and_a_cursor_gives_the_same_page(self):
        posts = [(self.body("Engineer", 0, 20), self.listing("a", 20, 45)), (self.body("Engineer", 20, 20), self.listing("b", 20, 45)),
                 (self.body("Engineer", 40, 20), self.listing("c", 5, 45))]
        with self.serving(posts):
            board, prefs = Workday(self.board(), "Acme"), {"roles": ["Engineer"]}
            one = board.search(prefs, None, 25)
            self.assertEqual((len(one["candidates"]), one["next"]), (20, "0:20"))
            two = board.search(prefs, "0:20", 25)
            self.assertEqual(two["next"], "0:40")
            self.assertEqual(board.search(prefs, "0:20", 25), two, "the same cursor gets the same page")
            three = board.search(prefs, "0:40", 25)
            self.assertEqual((len(three["candidates"]), three["next"]), (5, None), "the board has 45")
            self.assertEqual(board.search(prefs, "3:0", 25), {"candidates": [], "next": None}, "past the last role")
            with self.assertRaises(Fail):
                board.search(prefs, "x", 25)

    def test_a_posting_is_read_from_the_board_by_its_path(self):
        job = fixture("workday_job.json")
        for answer in (job, job.replace("https://acme.wd5.myworkdayjobs.com/External/job", "https://evil.test/job")):
            with self.serving([(self.body("", 0, 20), self.LIST)], {self.DETAIL: answer}) as server:
                board = Workday(self.board(), "Acme")
                page = board.search({}, None, 25)
                self.assertTrue(all(c["url"].startswith(self.HOME + "/External/job/") for c in page["candidates"]))
                sre = next(c for c in page["candidates"] if c["title"] == "Site Reliability Engineer")
                text = board.posting(sre)
                self.assertTrue(text.startswith("# Site Reliability Engineer\n\nCompany: Acme\nLocation: US, CA, Remote; US, TX, Remote; "
                                                "US, NY, Remote\nTime type: Full time\n\n"), text)
                self.assertTrue("Run Kubernetes clusters" in text and "Terraform and Go" in text and "What You'll Be Doing" in text, text)
                self.assertFalse("<" in text or "evil.test" in text, text)
                self.assertEqual(paths(server)[-1], self.DETAIL)
                elsewhere = dict(sre, url="https://other.wd5.myworkdayjobs.com/External/job/X/Y_1")
                self.assertIsNone(board.posting(elsewhere), "not a posting of this board")
                self.assertEqual(len(server.requests), 2, "nothing else was read")

    def test_listed_paths_cannot_leave_the_board(self):
        odd = {"total": 7, "jobPostings": [
            {"title": f"{letter} Engineer", "externalPath": path, "locationsText": "", "postedOn": ""}
            for letter, path in zip("ABCDEFG", ("/../../other/jobs", "//evil.test/x", "job/no-slash", "/%2e%2e/%2E%2e/other/jobs",
                                               "/.%2e/x", "/%2E./x", "/job/A/Fine_1"))]}
        with self.serving([(self.body("", 0, 20), odd)]):
            self.assertEqual(titles(Workday(self.board(), "Acme").search({}, None, 25)), ["G Engineer"])

    def test_built_addresses_are_checked_after_parsing(self):
        board = self.board()
        self.assertEqual(board.page_url("/job/A/X_1"), "https://acme.wd5.myworkdayjobs.com/External/job/A/X_1")
        self.assertEqual(board.detail_url("/job/A/X_1"), "https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External/job/A/X_1")
        for bad in ("/%2e%2e/x", "/%2E%2e/x", "/.%2e/x", "/%2e./x", "/a/../../x", "//evil.test/x", "@evil.test/x", "/x?y", "/x#y",
                    "/x\\y", "/x y", "/x\ty", "/xé"):
            self.assertEqual((board.page_url(bad), board.detail_url(bad)), (None, None), bad)
            self.assertFalse(workday.is_posting_path(bad), bad)
        # The shared builder refuses it as well.
        self.assertIsNone(board.under("/External", "/%2e%2e/evil"))
        # A posting URL that is not under the site is not read.
        self.assertIsNone(board.path_of("https://acme.wd5.myworkdayjobs.com/External/%2e%2e/x"))
        self.assertIsNone(board.path_of("https://acme.wd5.myworkdayjobs.com/Externalx/job/A/X_1"))
        self.assertEqual(board.path_of("https://acme.wd5.myworkdayjobs.com/External/job/A/X_1?source=x"), "/job/A/X_1")

    def test_an_unknown_shape_and_a_failed_request_are_clear_errors(self):
        with self.serving([(self.body("", 0, 20), {"total": 3, "items": []})], {self.DETAIL: {"job": {}}}):
            board = Workday(self.board(), "Acme")
            with self.assertRaises(Fail) as caught:
                board.search({}, None, 25)
            self.assertIn("Workday answered in an unknown format", caught.exception.message)
            sre = {"url": self.board().page_url("/job/US-CA-Remote/Site-Reliability-Engineer_JR1000003")}
            with self.assertRaises(Fail) as caught:
                board.posting(sre)
            self.assertIn("Workday answered in an unknown format", caught.exception.message)
        with self.serving([]):
            with self.assertRaises(Fail) as caught:
                Workday(self.board(), "Acme").search({}, None, 25)
            self.assertIn("reading the Workday board", caught.exception.message)
            self.assertEqual(caught.exception.extra["status"], 404)

    def test_the_origin_argument_points_the_api_calls_at_a_test_server(self):
        with served({self.SEARCH: (200, JSON, self.listing("a", 1, 1))}) as server:
            page = Workday(self.board(), "Acme", origin=server.base).search({}, None, 25)
            self.assertEqual(page["candidates"][0]["url"], "https://acme.wd5.myworkdayjobs.com/External/job/X/a0", "pages stay on the board")


class WhoIsHiring(unittest.TestCase):
    SEARCH = "/api/v1/search_by_date?query=%22who%20is%20hiring%22&tags=story,author_whoishiring&hitsPerPage=10"
    ITEM = "/api/v1/items/41000003"

    def routes(self):
        return {self.SEARCH: (200, JSON, fixture("hn_threads.json")), self.ITEM: (200, JSON, fixture("hn_thread.json"))}

    def test_one_top_level_comment_is_one_candidate_from_the_newest_hiring_thread(self):
        with served(self.routes()) as server:
            page = HnHiring(base=server.base).search({}, None, 10)
            self.assertEqual(paths(server), [self.SEARCH, self.ITEM], "the October thread, not 'Who wants to be hired?' or September")
            self.assertEqual(titles(page), [
                "Acme Robotics | Senior Rust Engineer | Remote (EU) | Full-time",
                "Sweet Dough | Pastry Chef | Paris, France | Onsite",
                "Globex (YC S20) | Backend Engineer, Go | Singapore | Hybrid",
            ], "replies and deleted comments are not candidates")
            acme = page["candidates"][0]
            self.assertEqual(acme["url"], "https://news.ycombinator.com/item?id=41000101")
            self.assertEqual((acme["company"], acme["location"]), ("Acme Robotics", "Remote (EU)"))
            self.assertTrue(acme["snippet"].startswith("We build warehouse robots"), acme["snippet"])
            self.assertEqual([c["location"] for c in page["candidates"][1:]], ["Onsite", "Hybrid"])

    def test_preferences_pick_comments_by_skill_word_or_role(self):
        with served(self.routes()) as server:
            adapter = HnHiring(base=server.base)
            page = adapter.search({"must_skills": ["Rust"]}, None, 10)
            self.assertEqual([c["company"] for c in page["candidates"]], ["Acme Robotics"])
            page = adapter.search({"nice_skills": ["go"], "roles": []}, None, 10)
            self.assertEqual([c["company"] for c in page["candidates"]], ["Globex (YC S20)"], "`go` is a word, not a piece of `Google`")
            page = adapter.search({"roles": ["Pastry Chef"]}, None, 10)
            self.assertEqual([c["company"] for c in page["candidates"]], ["Sweet Dough"], "a role word in the header line")
            first = adapter.search({}, None, 2)
            self.assertEqual((len(first["candidates"]), first["next"]), (2, "2"))
            self.assertEqual(len(adapter.search({}, "2", 2)["candidates"]), 1)

    def test_the_comment_is_the_posting(self):
        with served(self.routes()) as server:
            adapter = HnHiring(base=server.base)
            page = adapter.search({}, None, 10)
            text = adapter.posting(page["candidates"][0])
            self.assertTrue(text.startswith("# Acme Robotics | Senior Rust Engineer | Remote (EU) | Full-time\n\nCompany: Acme Robotics\n"
                                            "Location: Remote (EU)\n\nAcme Robotics | Senior Rust Engineer"), text)
            self.assertTrue("Rust services and tooling" in text and "https://acme.test/jobs" in text, text)
            with self.assertRaises(Fail) as caught:
                adapter.posting({"url": hn_hiring.ITEM_URL + "1"})
            self.assertIn("no longer", caught.exception.message)
            self.assertEqual(len(server.requests), 2, "the thread is loaded once")

    def test_no_hiring_thread_is_an_error(self):
        hits = {"hits": [{"objectID": "1", "title": "Ask HN: Who wants to be hired?", "created_at_i": 1}]}
        with served({self.SEARCH: (200, JSON, hits)}) as server:
            with self.assertRaises(Fail) as caught:
                HnHiring(base=server.base).search({}, None, 10)
            self.assertIn("no Who is hiring thread", caught.exception.message)


# ----------------------------------------------------------------------------- pages and their links

class WebRules(unittest.TestCase):
    def test_links_are_resolved_deduplicated_and_cleaned(self):
        html = """
            <a href="/careers/rust-engineer"><h3>Rust Engineer</h3><p>Remote</p></a>
            <a href='https://acme.test/careers/rust-engineer#apply'>Apply</a>
            <a class=x href=/careers/data-engineer>Data &amp; ML Engineer</a>
            <a href="mailto:jobs@acme.test">Mail us</a>
            <a href="javascript:void(0)">Menu</a>
            <a href="#top">Top</a>
            <a href="ftp://acme.test/x">ftp</a>
            <a href="/careers/empty"></a>
            <a href="https://other.test/jobs/1">Elsewhere</a>"""
        found = [(str(link), text) for link, text in web.links(html, url("https://acme.test/careers"))]
        self.assertEqual(found, [
            ("https://acme.test/careers/rust-engineer", "Rust Engineer Remote"),
            ("https://acme.test/careers/data-engineer", "Data & ML Engineer"),
            ("https://acme.test/careers/empty", ""),
            ("https://other.test/jobs/1", "Elsewhere"),
        ])

    def test_a_generic_text_is_replaced_by_a_better_one_for_the_same_address(self):
        html = '<a href="/jobs/1">Apply now</a> <a href="/jobs/1">Staff Engineer</a> <a href="/jobs/1">Learn more</a>'
        found = web.links(html, url("https://acme.test/jobs"))
        self.assertEqual([text for _, text in found], ["Staff Engineer"])
        self.assertTrue(web.is_generic("Apply Now!") and web.is_generic("  ") and web.is_generic("Read more"))
        self.assertFalse(web.is_generic("Apply Engineer"))

    def test_hrefs_that_are_not_pages_are_not_resolved(self):
        here = url("https://acme.test/careers")
        for href in ("", "#top", "javascript:void(0)", "MAILTO:jobs@acme.test", "tel:+1", "data:text/html,x", "ftp://acme.test/x"):
            self.assertIsNone(web.resolve(here, href), href)
        self.assertEqual(str(web.resolve(here, "//jobs.lever.co/acme")), "https://jobs.lever.co/acme")
        self.assertEqual(str(web.resolve(here, "open/1?x=1")), "https://acme.test/open/1?x=1")

    def test_the_same_site_rule_keeps_to_the_configured_site(self):
        site = url("https://www.acme.test/careers")
        for yes in ("https://acme.test/jobs/1", "https://www.acme.test/jobs/1", "https://careers.acme.test/jobs/1", "http://ACME.test/x",
                    "https://a.b.acme.test/x"):
            self.assertTrue(web.same_site(site, url(yes)), yes)
        for no in ("https://notacme.test/jobs/1", "https://acme.test.evil.test/jobs/1", "https://evil.test/acme.test",
                   "https://boards.greenhouse.io/acme/jobs/1", "https://acme.com/jobs/1"):
            self.assertFalse(web.same_site(site, url(no)), no)
        # A configured subdomain: its own subdomains are the same site, its parent and siblings are not.
        sub = url("https://careers.acme.test/")
        self.assertTrue(web.same_site(sub, url("https://careers.acme.test/x")) and web.same_site(sub, url("https://eu.careers.acme.test/x")))
        for other in ("https://acme.test/x", "https://www.acme.test/x", "https://blog.acme.test/x"):
            self.assertFalse(web.same_site(sub, url(other)), other)

    def test_a_public_suffix_or_a_single_word_is_not_a_site(self):
        for none in ("https://co.uk/", "https://com.vn/", "https://com/", "https://localhost/", "https://intranet/"):
            site = url(none)
            self.assertFalse(web.same_site(site, site), none)
            self.assertFalse(web.same_site(site, url(f"https://evil.{site.host}/jobs/1")), none)
            with self.assertRaises(Fail) as caught:
                web.check_site(site)
            self.assertIn("page that lists jobs", caught.exception.message)
        self.assertIsNone(web.not_a_site("acme.com"))
        self.assertIsNone(web.not_a_site("greenhouse.io"))
        self.assertIsNotNone(web.not_a_site("co.uk"))
        self.assertIsNone(web.not_a_site("127.0.0.1"), "an address is left to the network guard")
        self.assertEqual([web.registrable_domain(h) for h in ("careers.acme.com", "acme.com", "jobs.acme.co.uk", "mail.acme.com.vn")],
                         ["acme.com", "acme.com", "acme.co.uk", "acme.com.vn"])

    def test_a_shared_host_is_a_job_page_whose_site_is_its_exact_host(self):
        for shared in ("https://github.io/jobs", "https://vercel.app/", "https://www.netlify.app/", "https://herokuapp.com/",
                       "https://boards.greenhouse.io/acme", "https://jobs.lever.co/acme", "https://www.linkedin.com/jobs",
                       "https://apply.workable.com/acme/", "https://acme.wd1.myworkdayjobs.com/en-US/careers"):
            site = url(shared)
            host = site.host[4:] if site.host.startswith("www.") else site.host
            web.check_site(site)
            self.assertTrue(web.is_shared_host(host), shared)
            self.assertTrue(web.same_site(site, site), shared)
            self.assertTrue(web.same_site(site, url(f"https://www.{host}/jobs/view/1")), shared)
            self.assertFalse(web.same_site(site, url(f"https://evil.{host}/jobs/1")), shared)
        self.assertTrue(web.same_site(url("https://www.linkedin.com/jobs/search?keywords=rust"), url("https://linkedin.com/jobs/view/123")))
        self.assertFalse(web.same_site(url("https://www.linkedin.com/jobs"), url("https://de.linkedin.com/jobs/view/123")))
        # A customer's own subdomain on a platform, and a company domain under a public suffix, are sites.
        self.assertTrue(web.same_site(url("https://acme.github.io/"), url("https://acme.github.io/jobs/1")))
        self.assertFalse(web.same_site(url("https://acme.github.io/"), url("https://evil.github.io/jobs/1")))
        self.assertTrue(web.same_site(url("https://acme.co.uk/careers"), url("https://jobs.acme.co.uk/1")))
        self.assertFalse(web.same_site(url("https://acme.co.uk/careers"), url("https://other.co.uk/1")))
        for fine in ("https://acme.test/careers", "https://acme.github.io/", "https://careers.acme.co.uk/", "http://127.0.0.1:8080/jobs",
                     "https://greenhouse.io.acme.test/careers"):
            web.check_site(url(fine))
        for own in ("acme.github.io", "acme.com", "greenhouse.io.acme.test", "notlever.co"):
            self.assertFalse(web.is_shared_host(own), own)

    def test_postings_are_the_links_below_a_jobs_path(self):
        listing = url("https://acme.test/careers")
        for yes in ("https://acme.test/careers/senior-rust-engineer", "https://acme.test/jobs/4011001",
                    "https://acme.test/careers/engineering/backend-engineer", "https://acme.test/open-positions/data-engineer?gh_jid=1",
                    "https://acme.test/company/careers/role-123456", "https://careers.acme.test/jobs/abc"):
            self.assertTrue(web.looks_like_posting(listing, listing, url(yes)), yes)
        for no in ("https://acme.test/careers", "https://acme.test/careers/", "https://acme.test/about", "https://acme.test/blog/why-we-hire",
                   "https://acme.test/careers/benefits", "https://acme.test/jobs/search", "https://acme.test/careers/logo.png",
                   "https://acme.test/", "https://other.test/jobs/4011001", "https://acme.test/privacy"):
            self.assertFalse(web.looks_like_posting(listing, listing, url(no)), no)
        # The site is the configured one even when the listing was read at a redirected address.
        moved = url("https://evil.test/careers")
        self.assertFalse(web.looks_like_posting(listing, moved, url("https://evil.test/careers/rust-engineer")))
        self.assertTrue(web.looks_like_posting(listing, moved, url("https://acme.test/careers/rust-engineer")))

    def test_titles_come_from_the_address_without_the_id(self):
        def title(address):
            return web.title_from_url(url(address))

        self.assertEqual(title("https://yc.test/companies/aviator/jobs/aaMaqQu-software-engineering-intern"), "software engineering intern")
        self.assertEqual(title("https://x.test/jobs/4011001-senior-engineer"), "senior engineer")
        self.assertEqual(title("https://x.test/careers/senior-rust-engineer"), "senior rust engineer")
        self.assertIsNone(title("https://x.test/jobs/4011001"))
        self.assertIsNone(title("https://x.test/"))

    def test_page_addresses_are_normalised(self):
        self.assertEqual(str(web.normalize_page_url("acme.test/careers")), "https://acme.test/careers")
        self.assertEqual(str(web.normalize_page_url(" http://acme.test ")), "http://acme.test/")
        for bad in ("", "ftp://x.test", "file:///etc/passwd", "javascript:alert(1)"):
            with self.assertRaises(Fail, msg=bad):
                web.normalize_page_url(bad)


class JsonLd(unittest.TestCase):
    BASE = "https://acme.test/careers"

    @staticmethod
    def script(data):
        return f'<html><head><script type="application/ld+json">{data}</script></head><body></body></html>'

    def postings(self, html):
        return jsonld.job_postings(html, url(self.BASE))

    def titles(self, html):
        return [posting["title"] for posting in self.postings(html)]

    def test_postings_come_from_an_object_an_array_a_graph_and_an_item_list(self):
        one = '{"@context":"https://schema.org","@type":"JobPosting","title":"One","url":"/jobs/1"}'
        self.assertEqual(self.titles(self.script(one)), ["One"])
        array = '[{"@type":"JobPosting","title":"A"},{"@type":"Organization","name":"Acme"},{"@type":"JobPosting","title":"B"}]'
        self.assertEqual(self.titles(self.script(array)), ["A", "B"])
        graph = '{"@context":"https://schema.org","@graph":[{"@type":"WebPage"},{"@type":["JobPosting","Thing"],"title":"G"}]}'
        self.assertEqual(self.titles(self.script(graph)), ["G"], "@type as an array")
        items = """{"@type":"ItemList","itemListElement":[
            {"@type":"ListItem","position":1,"item":{"@type":"JobPosting","title":"L1"}},
            {"@type":"JobPosting","title":"L2"},
            {"@type":"ListItem","position":3,"url":"/x"}]}"""
        self.assertEqual(self.titles(self.script(items)), ["L1", "L2"])
        self.assertEqual(self.titles(self.script('{"@type":"http://schema.org/JobPosting","title":"Prefixed"}')), ["Prefixed"])
        # Several blocks, in page order; the type attribute in any case and quoting.
        html = self.script(one) + """<script TYPE='Application/LD+JSON'>{"@type":"JobPosting","title":"Two"}</script>""" + f"<script>{one}</script>"
        self.assertEqual(self.titles(html), ["One", "Two"])

    def test_bad_oversized_and_too_deep_blocks_are_skipped(self):
        good = '{"@type":"JobPosting","title":"Good"}'
        broken = '{"@type":"JobPosting","title":"Broken",}'
        huge = '{"@type":"JobPosting","title":"Huge","description":"%s"}' % ("x" * jsonld.MAX_BLOCK_BYTES)
        nested = "[" * 30000 + "]" * 30000
        self.assertEqual(self.titles(self.script(broken) + self.script(huge) + self.script(nested) + self.script(good)), ["Good"])
        self.assertEqual(self.titles(self.script('<!-- {"@type":"JobPosting","title":"Wrapped"} -->')), ["Wrapped"])
        self.assertEqual(self.titles("<p>no data</p>"), [])
        self.assertEqual(self.titles(self.script('{"@type":"Organization","name":"Acme"}')), [])
        many = "[" + ",".join([good] * (jsonld.MAX_POSTINGS + 20)) + "]"
        self.assertEqual(len(self.titles(self.script(many))), jsonld.MAX_POSTINGS)
        buried = '{"@graph":' * 8 + good + "}" * 8
        self.assertEqual(self.titles(self.script(buried)), [], "deeper than the walk goes")

    def test_fields_are_read_as_plain_text(self):
        data = """{"@type":"JobPosting","title":"Data &amp; ML Engineer","url":"/careers/data-ml#apply",
            "hiringOrganization":{"@type":"Organization","name":"Acme"},
            "jobLocation":[{"@type":"Place","address":{"addressLocality":"Hanoi","addressRegion":"Hanoi","addressCountry":{"@type":"Country","name":"VN"}}},
                           {"@type":"Place","address":{"addressLocality":"Berlin","addressCountry":"DE"}}],
            "jobLocationType":"TELECOMMUTE","employmentType":["FULL_TIME","CONTRACTOR"],
            "baseSalary":{"@type":"MonetaryAmount","currency":"USD","value":{"@type":"QuantitativeValue","minValue":100000,"maxValue":150000,"unitText":"YEAR"}},
            "datePosted":"2026-10-01","validThrough":"2026-12-31T23:59",
            "description":"<p>Build <b>pipelines</b>.</p><ul><li>Rust</li><li>SQL</li></ul>"}"""
        posting = self.postings(self.script(data))[0]
        self.assertEqual(str(posting["url"]), "https://acme.test/careers/data-ml")
        self.assertEqual((posting["title"], posting["company"], posting["location"]), ("Data & ML Engineer", "Acme", "Hanoi, VN; Berlin, DE"))
        self.assertTrue(posting["remote"])
        self.assertEqual((posting["employment_type"], posting["salary"]), ("Full time, Contractor", "USD 100000-150000 per year"))
        text = jsonld.posting_text(posting)
        self.assertTrue(text.startswith(
            "Title: Data & ML Engineer\nCompany: Acme\nLocation: Hanoi, VN; Berlin, DE\nRemote: yes\nEmployment type: Full time, Contractor\n"
            "Salary: USD 100000-150000 per year\nPosted: 2026-10-01\nValid through: 2026-12-31T23:59\n\n"), text)
        self.assertTrue("Build pipelines" in text and "Rust" in text and "<b>" not in text, text)

    def test_other_shapes_of_the_fields(self):
        data = """{"@type":"JobPosting","title":"T","hiringOrganization":"Acme Ltd","url":"javascript:alert(1)",
            "jobLocation":{"address":"Remote, Europe"},"employmentType":"PART_TIME",
            "baseSalary":{"currency":"EUR","value":{"value":"40","unitText":"HOUR"}},
            "description":"&lt;p&gt;Escaped &lt;b&gt;markup&lt;/b&gt;&lt;/p&gt;"}"""
        posting = self.postings(self.script(data))[0]
        self.assertIsNone(posting["url"], "only http(s) addresses")
        self.assertEqual((posting["company"], posting["location"], posting["employment_type"]), ("Acme Ltd", "Remote, Europe", "Part time"))
        self.assertEqual(posting["salary"], "EUR 40 per hour")
        self.assertFalse(posting["remote"])
        self.assertEqual(jsonld.description_text(posting), "Escaped markup")
        only = ('{"@type":"JobPosting","jobLocationType":["TELECOMMUTE"],"applicantLocationRequirements":{"@type":"Country","name":"USA"},'
                '"baseSalary":{"currency":"USD","value":90000}}')
        posting = self.postings(self.script(only))[0]
        self.assertTrue(posting["remote"])
        self.assertEqual((posting["location"], posting["salary"]), ("USA", "USD 90000"))
        self.assertEqual(jsonld.posting_text(posting), "Location: USA\nRemote: yes\nSalary: USD 90000")
        for amount, line in (({"value": {"minValue": 50.5, "maxValue": 60.0}}, "50.5-60"), ({"value": {"maxValue": 70}}, "70"),
                             ({"currency": "USD", "value": {"minValue": 5, "maxValue": 5}}, "USD 5"), ({"value": True}, ""), ("50000", "")):
            self.assertEqual(jsonld._salary(amount), line, amount)

    def test_a_posting_expires_after_its_valid_through(self):
        now = datetime.datetime(2026, 11, 14, 12, 0, tzinfo=datetime.timezone.utc)

        def expired(value):
            return jsonld.expired({"valid_through": value}, now)

        self.assertTrue(expired("2020-01-31"))
        self.assertTrue(expired("2026-11-13T23:59:59Z"))
        self.assertTrue(expired("2026-11-14T13:00:00+02:00"), "an offset is honoured: that was 11:00 UTC")
        self.assertFalse(expired("2026-11-14"), "a date runs to the end of that day")
        self.assertFalse(expired("2026-11-14T13:00:00+00:00"))
        self.assertFalse(expired("2026-11-14T12:30"), "no offset is read as UTC")
        self.assertFalse(expired("2099-01-01"))
        self.assertFalse(expired("") or expired("soon"), "no readable date never expires")

    def test_the_fixtures_read(self):
        self.assertEqual(len(self.postings(fixture("career_page_jsonld.html"))), 4)
        one = jsonld.job_postings(fixture("posting_jsonld.html"), url("https://acme.test/careers/senior-rust-engineer"))
        self.assertEqual(len(one), 1)
        self.assertGreater(len(jsonld.description_text(one[0])), 200)


class Sitemaps(unittest.TestCase):
    SITE = "https://acme.test/careers"
    MAP = "https://acme.test/sitemap.xml"

    def read(self, pages, limit=500):
        site = url(self.SITE)
        return sitemap.read_sitemap(base.Net(pages.http), site, site, "Acme", limit)

    def test_a_urlset_and_an_index_parse(self):
        is_index, urls = sitemap.parse(fixture("sitemap.xml"))
        self.assertEqual((is_index, len(urls)), (False, 11))
        self.assertEqual(urls[3], ("https://acme.test/careers/data-engineer", "2026-09-01"))
        self.assertIn("https://acme.test/careers/sales-lead?src=sitemap&lang=en", [loc for loc, _ in urls], "entities decoded")
        is_index, children = sitemap.parse(fixture("sitemap_index.xml"))
        self.assertEqual((is_index, len(children)), (True, 8))
        prefixed = '<?xml version="1.0"?><x:urlset xmlns:x="s"><x:url><x:loc>https://a.test/jobs/1</x:loc></x:url></x:urlset>'
        self.assertEqual(sitemap.parse(prefixed), (False, [("https://a.test/jobs/1", "")]))
        # WordPress (Yoast) puts image addresses inside each entry: only the entry's own `<loc>` counts.
        images = """<urlset xmlns:image="http://www.google.com/schemas/sitemap-image/1.1"><url><loc>https://a.test/jobs/1-rust-engineer</loc>
            <image:image><image:loc>https://a.test/wp-content/logo.png</image:loc></image:image><lastmod>2026-11-01</lastmod></url></urlset>"""
        self.assertEqual(sitemap.parse(images), (False, [("https://a.test/jobs/1-rust-engineer", "2026-11-01")]))
        for broken in ("", "<html><body>Not found</body></html>", "not xml at all", "<urlset>" + "<a>" * 200,
                       '<!DOCTYPE x [<!ENTITY a "b">]><urlset></urlset>'):
            with self.assertRaises(base.XmlError, msg=broken[:40]):
                sitemap.parse(broken)

    def test_a_sitemap_gives_same_site_postings_newest_first(self):
        with Pages({self.MAP: fixture("sitemap.xml")}) as pages:
            found = self.read(pages)
            self.assertEqual([(c["url"], c["title"]) for c in found], [
                ("https://acme.test/careers/senior-rust-engineer", "senior rust engineer"),
                ("https://careers.acme.test/jobs/771234-devops-engineer", "devops engineer"),
                ("https://acme.test/careers/data-engineer", "data engineer"),
                ("https://acme.test/careers/sales-lead?src=sitemap&lang=en", "sales lead"),
                ("https://acme.test/careers/product-designer", "product designer"),
            ])
            self.assertTrue(all(c["company"] == "Acme" and c["location"] == "" for c in found))
            self.assertEqual(pages.asked(), [self.MAP])
            self.assertEqual(len(self.read(pages, 2)), 2, "capped")
        # The address is the configured host's, whatever the careers page's path and query.
        self.assertEqual(str(sitemap.sitemap_url(url("http://jobs.acme.test:8080/en/careers?x=1#y"))), "http://jobs.acme.test:8080/sitemap.xml")

    def test_an_index_reads_at_most_three_job_sitemaps_on_the_site(self):
        child = "<urlset><url><loc>https://acme.test/jobs/1234567-backend-engineer</loc></url></urlset>"
        files = {self.MAP: fixture("sitemap_index.xml")}
        for address in ("https://acme.test/sitemap-pages.xml", "https://acme.test/sitemap-jobs.xml.gz", "https://evil.test/sitemap-jobs.xml",
                        "https://acme.test/sitemap-jobs-1.xml", "https://acme.test/sitemap-jobs-2.xml",
                        "https://careers.acme.test/careers-sitemap.xml", "https://acme.test/sitemap-jobs-4.xml"):
            files[address] = child
        with Pages(files) as pages:
            found = self.read(pages)
            self.assertEqual([(c["url"], c["title"]) for c in found], [("https://acme.test/jobs/1234567-backend-engineer", "backend engineer")],
                             "the same posting once")
            self.assertEqual(pages.asked(), [self.MAP, "https://acme.test/sitemap-jobs-1.xml", "https://acme.test/sitemap-jobs-2.xml",
                                             "https://careers.acme.test/careers-sitemap.xml"])

    def test_a_missing_broken_or_moved_sitemap_gives_nothing(self):
        with Pages({self.SITE: "<html></html>"}) as pages:
            self.assertEqual(self.read(pages), [], "404")
        with Pages({self.MAP: 403}) as pages:
            self.assertEqual(self.read(pages), [])
        with Pages({self.MAP: "<urlset><url><loc>https://acme.test/jobs/1-x</loc></url"}) as pages:
            self.assertEqual(self.read(pages), [])
        with Pages({self.MAP: "<!doctype html><html><body>Not here</body></html>"}) as pages:
            self.assertEqual(self.read(pages), [])
        with Pages({self.MAP: fixture("sitemap.xml")}, moved={self.MAP: "https://evil.test/sitemap.xml"}) as pages:
            self.assertEqual(self.read(pages), [], "a sitemap served by another site is not read")


class Boards(unittest.TestCase):
    def found(self, html):
        return [(hint["kind"], hint["url"]) for hint in boards.detect_boards(html, url("https://acme.test/careers"))]

    def one(self, href):
        found = self.found(f'<a href="{href}">Jobs</a>')
        return found[0] if found else None

    def test_every_pattern_gives_its_board(self):
        for href, kind, address in (
            ("https://boards.greenhouse.io/acme", "greenhouse", "boards.greenhouse.io/acme"),
            ("https://boards.greenhouse.io/acme/jobs/4011001?gh_jid=1", "greenhouse", "boards.greenhouse.io/acme"),
            ("https://job-boards.greenhouse.io/acme", "greenhouse", "job-boards.greenhouse.io/acme"),
            ("https://boards.greenhouse.io/embed/job_board?for=acme&amp;b=https://acme.test", "greenhouse", "boards.greenhouse.io/acme"),
            ("https://boards.greenhouse.io/embed/job_board/js?for=acme", "greenhouse", "boards.greenhouse.io/acme"),
            ("https://jobs.lever.co/acme/0b1c2d3e-aaaa-bbbb-cccc-123456789012", "lever", "jobs.lever.co/acme"),
            ("https://jobs.ashbyhq.com/acme", "ashby", "jobs.ashbyhq.com/acme"),
            ("https://jobs.smartrecruiters.com/AcmeGroup/7440001", "smartrecruiters", "jobs.smartrecruiters.com/AcmeGroup"),
            ("https://acme.recruitee.com/o/backend-engineer", "recruitee", "acme.recruitee.com"),
            ("https://acme.jobs.personio.de/job/1", "personio", "acme.jobs.personio.de"),
            ("https://ACME.jobs.personio.com/", "personio", "acme.jobs.personio.com"),
            ("https://acme.wd5.myworkdayjobs.com/en-US/External/job/x", "workday", "https://acme.wd5.myworkdayjobs.com/External"),
            ("//jobs.lever.co/acme", "lever", "jobs.lever.co/acme"),
        ):
            self.assertEqual(self.one(href), (kind, address), href)

    def test_look_alikes_and_vendor_pages_are_refused(self):
        for href in (
            "https://boards.greenhouse.io.evil.test/acme", "https://evil.test/boards.greenhouse.io/acme",
            "https://evil.test/?u=https://boards.greenhouse.io/acme", "https://boards.greenhouse.io@evil.test/acme",
            "https://user@boards.greenhouse.io/acme", "https://boards.greenhouse.io:8443/acme", "https://notgreenhouse.io/acme",
            "https://api.greenhouse.io/acme", "https://www.greenhouse.io/careers", "https://boards.greenhouse.io/",
            "https://boards.greenhouse.io/embed/job_board", "https://boards.greenhouse.io/embed/job_board?for=..",
            "https://jobs.lever.co/", "https://lever.co/acme", "https://jobs.lever.co.evil.test/acme", "https://www.recruitee.com/",
            "https://a.b.recruitee.com/", "https://recruitee.com.evil.test/", "https://www.personio.de/", "https://acme.personio.de/",
            "https://www.myworkdayjobs.com/acme", "https://acme.wd5.myworkdayjobs.com/",
            "https://acme.wd5.myworkdayjobs.com.evil.test/External", "ftp://jobs.lever.co/acme",
            "https://boards.greenhouse.io/embed/job_board?for=acme%3Ffor%3Devil", "https://boards.greenhouse.io/embed/job_board?for=acme%23x",
            "https://boards.greenhouse.io/embed/job_board?for=acme%2Fevil", "https://boards.greenhouse.io/embed/job_board?for=evil.test%2Fx",
            "https://boards.greenhouse.io/acme%2F..%2Fevil", "https://jobs.lever.co/acme%3Fx%3Dy", "https://jobs.ashbyhq.com/acme%23x",
            "https://jobs.smartrecruiters.com/acme%2Fevil", "javascript:location='https://jobs.lever.co/acme'",
            "https://jobs.lever.co/a%20b", "https://jobs.ashbyhq.com/...",
        ):
            self.assertIsNone(self.one(href), href)

    def test_the_fixture_gives_its_greenhouse_iframe_and_lever_link_once_each_and_they_pass_the_check(self):
        hints = boards.detect_boards(fixture("career_page_embed.html"), url("https://acme.test/careers"))
        self.assertEqual(hints, [{"kind": "greenhouse", "url": "boards.greenhouse.io/acme", "label": "Greenhouse"},
                                 {"kind": "lever", "url": "jobs.lever.co/acme", "label": "Lever"}])
        for hint in hints:
            self.assertEqual(sources.check_source(hint["kind"], hint["url"])["kind"], hint["kind"])

    def test_only_links_iframes_and_scripts_count(self):
        html = """
            <link rel="preconnect" href="https://jobs.lever.co/acme">
            <img src="https://jobs.ashbyhq.com/acme/logo.png">
            <p>See https://boards.greenhouse.io/acme</p>
            <a data-href="https://jobs.lever.co/beta" href="/careers">x</a>
            <script src='https://boards.greenhouse.io/embed/job_board/js?for=gamma'></script>
            <iframe title="Jobs" src=https://jobs.ashbyhq.com/delta></iframe>
            <a href="https://jobs.lever.co/epsilon">Lever</a>"""
        self.assertEqual(self.found(html), [("greenhouse", "boards.greenhouse.io/gamma"), ("ashby", "jobs.ashbyhq.com/delta"),
                                            ("lever", "jobs.lever.co/epsilon")])
        many = "".join(f'<a href="https://jobs.lever.co/c{i}">x</a>' for i in range(30))
        self.assertEqual(len(self.found(many)), boards.MAX_HINTS)

    def test_a_page_that_ended_on_a_board_suggests_that_board(self):
        page = web.Page("https://boards.greenhouse.io/acme", '<a href="https://jobs.lever.co/acme">x</a>')
        self.assertEqual([hint["kind"] for hint in boards.page_hints(page)], ["greenhouse", "lever"])
        self.assertEqual(boards.page_hints(web.Page("not a url", "")), [])

    def test_hosts_name_their_board_kind(self):
        for host, kind in (("boards.greenhouse.io", "greenhouse"), ("greenhouse.io", "greenhouse"), ("jobs.lever.co", "lever"),
                           ("jobs.ashbyhq.com", "ashby"), ("jobs.smartrecruiters.com", "smartrecruiters"),
                           ("acme.recruitee.com", "recruitee"), ("acme.jobs.personio.de", "personio"),
                           ("acme.jobs.personio.com.", "personio"), ("acme.wd5.myworkdayjobs.com", "workday")):
            self.assertEqual(boards.kind_for_host(host), kind, host)
        for host in ("greenhouse.io.evil.test", "notlever.co", "acme.test", "linkedin.com", ""):
            self.assertIsNone(boards.kind_for_host(host), host)

    def test_the_status_names_the_board(self):
        self.assertIsNone(boards.no_postings_status([]))
        hints = boards.detect_boards(fixture("career_page_embed.html"), url("https://acme.test/careers"))
        self.assertEqual(boards.no_postings_status(hints),
                         "No postings on the page. It links to a Greenhouse board: add it as a Greenhouse source")


# ------------------------------------------------------------------------------- the page adapters

class JobPage(unittest.TestCase):
    LIST = "https://acme.test/careers"
    MAP = "https://acme.test/sitemap.xml"
    SPA = "<html><body><div id=root></div><script src=app.js></script></body></html>"

    def adapter(self, pages, address=None, company="Acme"):
        address = self.LIST if address is None else address
        return CareerPage({"kind": "career_page", "url": address, "name": company}, company, pages.http)

    def urls(self, adapter):
        return [c["url"] for c in adapter.search({}, None, 20)["candidates"]]

    def test_only_same_site_posting_links_become_candidates(self):
        with Pages({self.LIST: fixture("career_page.html")}) as pages:
            page = self.adapter(pages).search({}, None, 20)
            self.assertEqual([(c["url"], c["title"]) for c in page["candidates"]], [
                ("https://acme.test/careers/senior-rust-engineer", "Senior Rust Engineer Remote (Vietnam)"),
                ("https://acme.test/careers/data-engineer", "Data Engineer"),
                ("https://careers.acme.test/jobs/4011001", "Staff Platform Engineer"),
                ("https://acme.test/jobs/771234-devops-engineer", "devops engineer"),
            ])
            self.assertTrue(all(c["company"] == "Acme" and c["location"] == "" and c["snippet"] == "" for c in page["candidates"]))
            self.assertEqual(pages.asked(), [self.LIST], "a page with postings needs nothing else")

    def test_roles_thin_out_the_page_and_pages_follow_the_cursor(self):
        with Pages({self.LIST: fixture("career_page.html")}) as pages:
            adapter, prefs = self.adapter(pages), {"roles": ["Rust Engineer"]}
            first = adapter.search(prefs, None, 2)
            self.assertEqual((len(first["candidates"]), first["next"]), (2, "2"))
            self.assertIsNone(adapter.search(prefs, first["next"], 2)["next"])
            self.assertEqual(len(pages.asked()), 1, "one read serves every page")

    def test_json_ld_postings_come_first_and_keep_to_the_site(self):
        with Pages({self.LIST: fixture("career_page_jsonld.html"), self.MAP: fixture("sitemap.xml")}) as pages:
            page = self.adapter(pages).search({}, None, 20)
            # Not the expired posting, not the one on another site, not the page's plain link.
            self.assertEqual([(c["url"], c["title"], c["location"]) for c in page["candidates"]], [
                ("https://acme.test/careers/senior-rust-engineer", "Senior Rust Engineer", "Ho Chi Minh City, VN"),
                ("https://careers.acme.test/jobs/4011001", "Data Engineer", "Berlin, Germany"),
            ])
            self.assertTrue(all(c["company"] == "Acme" for c in page["candidates"]))
            self.assertEqual(pages.asked(), [self.LIST], "the sitemap of a JSON-LD page is never read")

    def test_a_page_without_postings_reads_the_sitemap(self):
        with Pages({self.LIST: self.SPA, self.MAP: fixture("sitemap.xml")}) as pages:
            page = self.adapter(pages).search({}, None, 20)
            self.assertEqual(len(page["candidates"]), 5)
            self.assertEqual(page["candidates"][0]["url"], "https://acme.test/careers/senior-rust-engineer", "newest first")
            self.assertEqual(pages.asked(), [self.LIST, self.MAP])

    def test_a_page_javascript_draws_needs_a_browser(self):
        for files in ({self.LIST: self.SPA}, {self.LIST: self.SPA, self.MAP: "<urlset></urlset>"}):
            with Pages(files) as pages:
                adapter = self.adapter(pages)
                with self.assertRaises(NeedsBrowser) as caught:
                    adapter.search({}, None, 20)
                self.assertIn("JavaScript", caught.exception.message)
                self.assertTrue(caught.exception.extra["needs_browser"])
                self.assertEqual((caught.exception.extra["url"], caught.exception.extra["boards"]), (self.LIST, []))
                self.assertIn("browsing tool", caught.exception.hint)
                self.assertEqual(pages.asked(), [self.LIST, self.MAP], "the sitemap was tried first")
                self.assertEqual(adapter.board_hints(), [])

    def test_a_wall_needs_a_browser_and_is_not_remembered(self):
        with Pages({self.LIST: 403}) as pages:
            adapter = self.adapter(pages)
            for _ in range(2):
                with self.assertRaises(NeedsBrowser) as caught:
                    adapter.search({}, None, 20)
                self.assertEqual(caught.exception.extra["status"], 403)
                self.assertIn("refuses automated reads", caught.exception.message)
            self.assertEqual(pages.asked().count(self.LIST), 2, "the next search asks again")
        # A wall on the page, postings in the sitemap: those are the candidates.
        with Pages({self.LIST: 429, self.MAP: fixture("sitemap.xml")}) as pages:
            self.assertEqual(len(self.urls(self.adapter(pages))), 5)
        # Any other failure is an error of its own, and nothing else is tried.
        with Pages({self.MAP: fixture("sitemap.xml")}) as pages:
            with self.assertRaises(Fail) as caught:
                self.adapter(pages).search({}, None, 20)
            self.assertNotIsInstance(caught.exception, NeedsBrowser)
            self.assertEqual((caught.exception.extra["status"], pages.asked()), (404, [self.LIST]))

    def test_a_page_on_a_shared_host_never_reads_the_hosts_sitemap(self):
        # apply.workable.com lists every company: its sitemap is not this source's.
        listing = "https://apply.workable.com/acme/"
        other = "<urlset><url><loc>https://apply.workable.com/otherco/j/123456/</loc></url></urlset>"
        with Pages({listing: self.SPA, "https://apply.workable.com/sitemap.xml": other}) as pages:
            with self.assertRaises(NeedsBrowser):
                self.adapter(pages, listing).search({}, None, 20)
            self.assertEqual(pages.asked(), [listing], "no sitemap read")

    def test_postings_the_json_ld_calls_closed_do_not_come_back_through_the_fallbacks(self):
        closed = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Platform Engineer",'
                  '"url":"/careers/platform-engineer","validThrough":"2020-01-31"}</script>')
        # The page's links.
        html = closed + '<a href="/careers/platform-engineer">Platform Engineer</a><a href="/careers/rust-engineer">Rust Engineer</a>'
        with Pages({self.LIST: html}) as pages:
            self.assertEqual(self.urls(self.adapter(pages)), ["https://acme.test/careers/rust-engineer"])
        # The sitemap.
        listed = ("<urlset><url><loc>https://acme.test/careers/platform-engineer</loc></url>"
                  "<url><loc>https://acme.test/careers/data-engineer</loc></url></urlset>")
        with Pages({self.LIST: closed, self.MAP: listed}) as pages:
            self.assertEqual(self.urls(self.adapter(pages)), ["https://acme.test/careers/data-engineer"])

    def test_json_ld_on_a_listing_that_moved_to_another_site_is_ignored(self):
        with Pages({}) as pages:
            adapter = self.adapter(pages)
        moved = web.Page("https://evil.test/careers", fixture("career_page_jsonld.html"))
        self.assertEqual(adapter._candidates(moved, set()), [])
        # Later, the dated posting has closed; the undated one stays.
        later = datetime.datetime(2100, 1, 1, tzinfo=datetime.timezone.utc)
        page, closed = web.Page(self.LIST, fixture("career_page_jsonld.html")), set()
        self.assertEqual([c["title"] for c in adapter._structured(page, url(self.LIST), later, closed)], ["Data Engineer"])
        self.assertEqual(closed, {"https://acme.test/careers/senior-rust-engineer", "https://acme.test/careers/platform-engineer"})
        # JSON-LD with no posting on the site: the page's links are read instead.
        html = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Elsewhere","url":"https://evil.test/jobs/1"}</script>'
                '<a href="/careers/rust-engineer">Rust Engineer</a>')
        self.assertEqual([c["url"] for c in adapter._candidates(web.Page(self.LIST, html), set())], ["https://acme.test/careers/rust-engineer"])

    def test_links_resolve_against_the_page_that_was_read(self):
        html = ('<a href="/jobs/8812345">Backend Engineer</a><a href="https://acme.test/jobs/9912345">Other host, same site</a>'
                '<a href="https://evil.test/jobs/1234567">Evil</a>')
        with Pages({}) as pages:
            adapter = self.adapter(pages)
        found = adapter._candidates(web.Page("https://careers.acme.test/open", html), set())
        self.assertEqual([c["url"] for c in found], ["https://careers.acme.test/jobs/8812345", "https://acme.test/jobs/9912345"])

    def test_a_listing_that_redirected_keeps_the_configured_site(self):
        html = ('<a href="/careers/rust-engineer">Rust Engineer</a><a href="https://evil.test/jobs/1234567">Evil</a>'
                '<a href="https://acme.test/jobs/8812345">Acme job</a>')
        # The configured page answers from evil.test: its links are evil.test's, and nothing is a candidate.
        with Pages({self.LIST: html}, moved={self.LIST: "https://evil.test/careers"}) as pages:
            with self.assertRaises(NeedsBrowser):
                self.adapter(pages).search({}, None, 20)
        # Redirected within the site (to a subdomain of it), links on the configured site still count.
        with Pages({self.LIST: html}, moved={self.LIST: "https://careers.acme.test/open"}) as pages:
            self.assertEqual(self.urls(self.adapter(pages)), ["https://careers.acme.test/careers/rust-engineer", "https://acme.test/jobs/8812345"])

    def test_a_page_without_postings_keeps_the_boards_it_points_to(self):
        embed = fixture("career_page_embed.html")
        with Pages({self.LIST: embed, self.MAP: "<urlset></urlset>"}) as pages:
            adapter = self.adapter(pages)
            self.assertEqual(adapter.board_hints(), [], "nothing read yet")
            with self.assertRaises(NeedsBrowser) as caught:
                adapter.search({}, None, 20)
            hints = [{"kind": "greenhouse", "url": "boards.greenhouse.io/acme", "label": "Greenhouse"},
                     {"kind": "lever", "url": "jobs.lever.co/acme", "label": "Lever"}]
            self.assertEqual(adapter.board_hints(), hints)
            self.assertEqual(caught.exception.extra["boards"], hints)
            self.assertIn("add it as a Greenhouse source (`boards.greenhouse.io/acme`)", caught.exception.hint)
            self.assertEqual(pages.asked(), [self.LIST, self.MAP], "only suggested: no board was read")
        # A page with postings keeps none, even when it also links to a board.
        with Pages({self.LIST: embed + '<a href="/careers/rust-engineer">Rust Engineer</a>'}) as pages:
            adapter = self.adapter(pages)
            self.assertEqual(len(self.urls(adapter)), 1)
            self.assertEqual(adapter.board_hints(), [])

    def test_any_page_of_a_real_site_is_accepted_and_a_bad_address_names_the_source(self):
        with Pages({}) as pages:
            for address in ("https://www.linkedin.com/jobs", "https://github.io/careers", "https://acme.github.io/careers",
                            "https://careers.acme.co.uk/", "https://boards.greenhouse.io/acme"):
                self.adapter(pages, address)
            for bad, part in (("https://co.uk/careers", "page that lists jobs"), ("https://localhost/careers", "page that lists jobs"),
                              ("", "no page address"), ("ftp://x.test/careers", "http"), ("javascript:alert(1)", "not a valid address")):
                with self.assertRaises(Fail, msg=bad) as caught:
                    self.adapter(pages, bad)
                self.assertTrue(caught.exception.message.startswith("job page source") and part in caught.exception.message, caught.exception.message)

    def test_an_aggregator_page_names_each_postings_own_company(self):
        html = """<script type="application/ld+json">[
            {"@type":"JobPosting","title":"Rust Engineer","url":"/jobs/view/1","hiringOrganization":{"@type":"Organization","name":"Beta Corp"}},
            {"@type":"JobPosting","title":"Data Engineer","url":"/jobs/view/2"}
        ]</script>"""
        listing = "https://www.linkedin.com/jobs/search?keywords=rust"
        with Pages({listing: html}) as pages:
            page = self.adapter(pages, listing, "linkedin.com").search({}, None, 20)
            self.assertEqual([(c["title"], c["company"]) for c in page["candidates"]],
                             [("Rust Engineer", "Beta Corp"), ("Data Engineer", "linkedin.com")])


class YCombinator(unittest.TestCase):
    WAAS = "https://www.workatastartup.com/jobs"

    def adapter(self, pages, address=""):
        return Yc({"kind": "yc", "url": address, "name": "YC"}, pages.http)

    def test_the_public_board_lists_every_job_link_with_its_company(self):
        with Pages({yc.DEFAULT_URL: fixture("yc_jobs.html")}) as pages:
            adapter = self.adapter(pages)
            page = adapter.search({}, None, 50)
            self.assertEqual([(c["company"], c["title"], c["url"]) for c in page["candidates"]], [
                ("Aviator", "Software Engineering Intern", "https://www.ycombinator.com/companies/aviator/jobs/aaMaqQu-software-engineering-intern"),
                ("Candid Health", "Senior Software Engineer", "https://www.ycombinator.com/companies/candid-health/jobs/WIMeHdk-senior-software-engineer"),
                ("Glimpse 2", "founding engineer", "https://www.ycombinator.com/companies/glimpse-2/jobs/5hmOmrr-founding-engineer"),
            ], "company pages, the apply sub-page, other sites and the nav are not jobs")
            self.assertEqual(titles(adapter.search({"roles": ["Founding Engineer"]}, None, 1)), ["Software Engineering Intern"])
            self.assertEqual(adapter.search({"roles": ["Founding Engineer"]}, None, 1)["next"], "1")
            self.assertIsNone(adapter.posting(page["candidates"][0]), "the caller reads the posting's page")
            self.assertEqual(pages.asked(), [yc.DEFAULT_URL])

    def test_work_at_a_startup_refuses_plain_requests_so_it_needs_a_browser(self):
        with Pages({self.WAAS: 406}) as pages:
            with self.assertRaises(NeedsBrowser) as caught:
                self.adapter(pages, self.WAAS).search({}, None, 50)
            self.assertEqual(caught.exception.extra["status"], 406)
            self.assertIn("never try to get around", caught.exception.hint)

    def test_job_links_of_work_at_a_startup(self):
        html = ('<a href="/jobs/55012">Backend Engineer</a><a href="/companies/acme-robotics/jobs/ZxCvB-staff-engineer">Staff Engineer</a>'
                '<a href="/companies/acme-robotics">Acme</a><a href="https://evil.test/jobs/1">x</a>')
        found = yc.candidates(web.Page(self.WAAS, html), url(self.WAAS))
        self.assertEqual([(c["company"], c["title"]) for c in found], [("", "Backend Engineer"), ("Acme Robotics", "Staff Engineer")])

    def test_only_yc_addresses_are_accepted(self):
        with Pages({}) as pages:
            self.adapter(pages, "https://www.ycombinator.com/jobs/role/software-engineer")
            self.adapter(pages, "workatastartup.com/jobs")
            for bad in ("https://evil.test/jobs", "https://notycombinator.com/jobs", "ftp://ycombinator.com", "https://ycombinator.com.evil.test/"):
                with self.assertRaises(Fail, msg=bad):
                    self.adapter(pages, bad)

    def test_a_page_on_another_site_has_no_candidates(self):
        site, job = url(yc.DEFAULT_URL), '<a href="/companies/x/jobs/1-a-b">Job</a>'
        self.assertEqual(yc.candidates(web.Page("https://evil.test/jobs", job), site), [])
        # A page of the other YC site than the configured one is not the configured listing either.
        self.assertEqual(yc.candidates(web.Page(self.WAAS, job), site), [])
        same = web.Page(yc.DEFAULT_URL, job + '<a href="https://www.workatastartup.com/companies/y/jobs/2-c-d">Other site</a>')
        self.assertEqual([c["url"] for c in yc.candidates(same, site)], ["https://www.ycombinator.com/companies/x/jobs/1-a-b"],
                         "links to the other YC site are not followed")

    def test_a_listing_without_job_links_or_moved_to_another_site_needs_a_browser(self):
        job = '<a href="/companies/x/jobs/1-a-b">Job</a>'
        for files, moved in (({yc.DEFAULT_URL: "<div id=root></div>"}, None), ({yc.DEFAULT_URL: job}, {yc.DEFAULT_URL: "https://evil.test/jobs"})):
            with Pages(files, moved) as pages:
                with self.assertRaises(NeedsBrowser) as caught:
                    self.adapter(pages).search({}, None, 50)
                self.assertEqual(caught.exception.extra["url"], yc.DEFAULT_URL)


class PostingPage(unittest.TestCase):
    """`fetch_posting`: the generic read of a posting's own page."""

    BODY = "We build a sync engine in Rust and need an engineer who likes databases, networks and clear writing. " * 8
    JOB = "https://acme.test/careers/backend-engineer"

    def job_page(self, head=""):
        return (f"<!doctype html><html><head><title>Backend Engineer</title>{head}</head><body><main><h1>Backend Engineer</h1>"
                f"<p>{self.BODY}</p></main></body></html>")

    def teaser_page(self, description):
        data = {"@type": "JobPosting", "title": "Backend Engineer", "jobLocation": {"address": {"addressLocality": "Hanoi", "addressCountry": "VN"}},
                "baseSalary": {"currency": "USD", "value": {"minValue": 50000, "maxValue": 70000, "unitText": "YEAR"}},
                "validThrough": "2020-01-31", "description": description}
        return self.job_page(f'<script type="application/ld+json">{json.dumps(data)}</script>')

    def read(self, pages, address=None, **fields):
        found = {"url": address or self.JOB, "title": "", "company": "", "location": "", "snippet": ""}
        found.update(fields)
        return sources.fetch_posting(found, pages.http)

    def test_a_page_becomes_text_under_the_candidates_header(self):
        with Pages({self.JOB: self.job_page(), "https://acme.test/plain": "Senior engineer wanted. " * 40}) as pages:
            text = self.read(pages, title="Backend Engineer", company="Acme", location="Remote")
            self.assertTrue(text.startswith("# Backend Engineer\n\nCompany: Acme\nLocation: Remote\n\n"), text)
            self.assertIn("sync engine in Rust", text)
            self.assertNotIn("Title:", text)
            self.assertEqual(pages._server.requests[0][0], "GET")
            plain = self.read(pages, "https://acme.test/plain", title="Engineer")
            self.assertTrue(plain.startswith("# Engineer\n\nCompany: \nLocation: \n\nSenior engineer wanted."), plain)

    def test_a_page_with_one_job_posting_is_read_from_its_json_ld(self):
        posting = fixture("posting_jsonld.html")
        another = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Another","description":'
                   '"<p>Another job with a long enough description to count as one.</p>"}</script></head>')
        teaser = posting.replace('"description": "<p>Acme', '"description": "<p>Join our Rust team in Saigon.</p>", "x": "<p>Acme', 1)
        with Pages({"https://acme.test/one": posting, "https://acme.test/two": posting.replace("</head>", another),
                    "https://acme.test/teaser-empty-page": teaser, "https://acme.test/teaser": self.teaser_page("<p>Join our sync team.</p>"),
                    self.JOB: self.job_page()}) as pages:
            text = self.read(pages, "https://acme.test/one")
            self.assertTrue(text.startswith(
                "# Senior Rust Engineer\n\nCompany: Acme\nLocation: Ho Chi Minh City, HCMC, VN\nRemote: yes\nEmployment type: Full time\n"
                "Salary: USD 60000-90000 per year\nPosted: 2026-10-20\nValid through: 2099-12-31\n\n"), text)
            self.assertTrue("replication protocol" in text and "Profile and tune SQLite" in text and "Cookie settings" not in text, text)
            # What the listing said about the job comes first; the page fills in the rest.
            text = self.read(pages, "https://acme.test/one", title="Rust Engineer (Senior)", company="Acme Inc")
            self.assertTrue(text.startswith("# Rust Engineer (Senior)\n\nCompany: Acme Inc\nLocation: Ho Chi Minh City, HCMC, VN\nRemote: yes\n"), text)
            # A teaser description: the page itself is read, with the header first (an expired
            # posting is still read; its header says so).
            text = self.read(pages, "https://acme.test/teaser")
            self.assertTrue(text.startswith("# Backend Engineer\n\nCompany: \nLocation: Hanoi, VN\nSalary: USD 50000-70000 per year\n"
                                            "Valid through: 2020-01-31\n\n"), text)
            self.assertTrue("sync engine in Rust" in text and "Join our sync team" not in text, text)
            # Two postings, or a teaser on a page with no text of its own: the page itself is read,
            # and that page is mostly empty without its JSON-LD, so it needs a browser.
            for address in ("https://acme.test/two", "https://acme.test/teaser-empty-page"):
                with self.assertRaises(NeedsBrowser, msg=address) as caught:
                    self.read(pages, address)
                self.assertIn("JavaScript", caught.exception.message)

    def test_what_the_page_data_gives(self):
        address = "https://acme.test/careers/senior-rust-engineer"
        posting, full = web.structured(fixture("posting_jsonld.html"), address)
        self.assertEqual((posting["title"], full), ("Senior Rust Engineer", True))
        posting, full = web.structured(self.teaser_page("Short."), address)
        self.assertEqual((posting["location"], full), ("Hanoi, VN", False))
        self.assertEqual(web.structured(self.job_page(), address), (None, False))
        self.assertEqual(web.structured(fixture("posting_jsonld.html"), "not a url"), (None, False))
        self.assertEqual(web.structured(fixture("career_page_jsonld.html"), address), (None, False), "a listing")
        empty = '<script type="application/ld+json">{"@type":"JobPosting","description":"Hi"}</script>'
        self.assertEqual(web.structured(empty, address), (None, False), "nothing to add")

    def test_walls_and_javascript_pages_need_a_browser_and_other_failures_are_plain_errors(self):
        files = {f"https://acme.test/s{code}": code for code in (401, 403, 406, 429, 503)}
        files.update({"https://acme.test/gone": 404, "https://acme.test/spa": JobPage.SPA})
        with Pages(files) as pages:
            for code in (401, 403, 406, 429, 503):
                with self.assertRaises(NeedsBrowser) as caught:
                    self.read(pages, f"https://acme.test/s{code}")
                self.assertEqual((caught.exception.extra["status"], caught.exception.extra["needs_browser"]), (code, True))
            with self.assertRaises(NeedsBrowser) as caught:
                self.read(pages, "https://acme.test/spa")
            self.assertIn("JavaScript", caught.exception.message)
            with self.assertRaises(Fail) as caught:
                self.read(pages, "https://acme.test/gone")
            self.assertNotIsInstance(caught.exception, NeedsBrowser)
            self.assertEqual(caught.exception.extra["status"], 404)
            for bad in ("file:///etc/passwd", "ftp://x.test/job", "not a url", ""):
                with self.assertRaises(Fail, msg=bad):
                    self.read(pages, bad)

    def test_a_redirect_is_followed_and_a_private_address_is_never_read(self):
        with Pages({self.JOB: self.job_page()}, moved={self.JOB: "https://jobs.acme.test/backend"}) as pages:
            self.assertIn("sync engine in Rust", self.read(pages, title="Backend Engineer"))
        # Outside the tests' loopback allowance, the client refuses this machine and private networks.
        for private in ("http://127.0.0.1:1/job", "http://localhost/job", "http://10.0.0.5/job", "http://intranet/job"):
            with self.assertRaises(Fail, msg=private) as caught:
                sources.fetch_posting({"url": private}, net.Http(host_delay=0))
            self.assertIn("private network", caught.exception.message)


if __name__ == "__main__":
    unittest.main()
