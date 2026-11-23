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
from jp.sources import base, smartrecruiters, workday
from jp.sources import greenhouse as greenhouse_module
from jp.sources import recruitee as recruitee_module
from jp.sources.ashby import Ashby
from jp.sources.base import NeedsBrowser, board_host, board_slug, mentions, one_line, page_of, parse_url, title_fits_roles
from jp.sources.greenhouse import Greenhouse
from jp.sources.lever import Lever
from jp.sources.personio import Personio, parse_positions
from jp.sources.recruitee import Recruitee
from jp.sources.smartrecruiters import SmartRecruiters
from jp.sources.workday import Board, Workday

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
                                         "workday"))
        self.assertEqual(set(sources.LABELS), set(sources.KINDS))
        self.assertEqual(sources.LABELS["smartrecruiters"], "SmartRecruiters")

    def test_a_new_source_is_checked_and_named_before_it_is_stored(self):
        self.assertEqual(self.checked("greenhouse", " https://boards.greenhouse.io/acme "), ("https://boards.greenhouse.io/acme", "acme"))
        self.assertEqual(self.checked("lever", "acme"), ("acme", "acme"))
        self.assertEqual(self.checked("ashby", "jobs.ashbyhq.com/acme")[1], "acme")
        self.assertEqual(self.checked("workday", " acme.wd5.myworkdayjobs.com/en-US/External "),
                         ("acme.wd5.myworkdayjobs.com/en-US/External", "acme"))
        self.assertEqual(self.checked("smartrecruiters", "https://jobs.smartrecruiters.com/AcmeGroup")[1], "AcmeGroup")
        self.assertEqual(self.checked("smartrecruiters", "AcmeGroup"), ("AcmeGroup", "AcmeGroup"))
        self.assertEqual(self.checked("recruitee", " https://acme.recruitee.com/ "), ("https://acme.recruitee.com/", "acme"))
        self.assertEqual(self.checked("personio", "acme.jobs.personio.com")[1], "acme")
        self.assertEqual(self.checked("personio", "acme")[1], "acme")
        for kind, address in (
            ("greenhouse", ""), ("lever", "https://evil.test/acme"), ("linkedin", "https://www.linkedin.com/jobs"),
            ("smartrecruiters", ""), ("smartrecruiters", ".."), ("smartrecruiters", "https://smartrecruiters.com.evil.test/Acme"),
            ("recruitee", ""), ("recruitee", "recruitee.com.evil.test"), ("recruitee", "a.b.recruitee.com"),
            ("recruitee", "https://evil.test/o/x"), ("personio", ".."), ("personio", "acme.jobs.personio.de.evil.test"),
            ("personio", "acme.personio.de"), ("personio", "jobs.personio.de"), ("workday", ""),
            ("workday", "https://acme.wd5.myworkdayjobs.com/.."), ("workday", "myworkdayjobs.com.evil.test/External"),
        ):
            with self.assertRaises(Fail, msg=f"{kind} {address}") as caught:
                sources.check_source(kind, address)
            self.assertTrue(caught.exception.hint, f"{kind} {address}")

    def test_the_factory_builds_by_kind_and_explains_failures(self):
        def build(kind, address, name=""):
            return sources.adapter_for({"kind": kind, "url": address, "name": name})

        for kind, address, adapter in (
            ("greenhouse", "acme", Greenhouse), ("lever", "https://jobs.lever.co/acme", Lever),
            ("ashby", "jobs.ashbyhq.com/acme", Ashby),
            ("smartrecruiters", "jobs.smartrecruiters.com/Acme", SmartRecruiters), ("recruitee", "acme.recruitee.com", Recruitee),
            ("personio", "acme.jobs.personio.com", Personio), ("workday", "https://acme.wd5.myworkdayjobs.com/External", Workday),
        ):
            self.assertIsInstance(build(kind, address), adapter)
        self.assertEqual(build("greenhouse", "acme").company, "acme", "no name: the board's")
        self.assertEqual(build("greenhouse", "acme", " Acme Robotics ").company, "Acme Robotics")
        self.assertEqual(build("personio", "acme.jobs.personio.com").origin, "https://acme.jobs.personio.com")
        self.assertEqual(build("recruitee", "Acme").origin, "https://acme.recruitee.com")
        for kind, address, part in (
            ("greenhouse", "", "no company board"), ("recruitee", "recruitee.com.evil.test", "Recruitee source"),
            ("personio", "..", "Personio source"), ("smartrecruiters", "https://evil.test/Acme", "SmartRecruiters source"),
            ("workday", "https://acme.wd5.myworkdayjobs.com.evil.test/External", "not a Workday board"),
            ("nope", "", "no adapter for source kind `nope`"),
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


# ----------------------------------------------------------------------------- pages and their links

# ------------------------------------------------------------------------------- the page adapters

class PostingPage(unittest.TestCase):
    """`fetch_posting`: the generic read of a posting's own page."""

    SPA = "<html><body><div id=root></div><script src=app.js></script></body></html>"

    BODY = "We build a sync engine in Rust and need an engineer who likes databases, networks and clear writing. " * 8
    JOB = "https://acme.test/careers/backend-engineer"

    def job_page(self, head=""):
        return (f"<!doctype html><html><head><title>Backend Engineer</title>{head}</head><body><main><h1>Backend Engineer</h1>"
                f"<p>{self.BODY}</p></main></body></html>")

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

    def test_walls_and_javascript_pages_need_a_browser_and_other_failures_are_plain_errors(self):
        files = {f"https://acme.test/s{code}": code for code in (401, 403, 406, 429, 503)}
        files.update({"https://acme.test/gone": 404, "https://acme.test/spa": self.SPA})
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
