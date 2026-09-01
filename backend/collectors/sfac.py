from __future__ import annotations

import base64
import io
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from urllib.parse import unquote, urljoin, urlparse

import lxml.html
import pypdf
import requests

from backend.config import settings

logger = logging.getLogger(__name__)
LIST_PAGE = "https://sfacindia.com/List-of-FPO-Statewise.aspx"
SCRAPFLY_ENDPOINT = "https://api.scrapfly.io/scrape"
USER_AGENT = "AgriCRM-Collector/1.0 (Theta Analytics; +mailto:{contact}) python-requests"
CIN = re.compile(r"U(?:\s*\d){5}(?:\s*[A-Z]){2}(?:\s*\d){4}(?:\s*[A-Z]){3}(?:\s*\d){6}")
REG_DATE = re.compile(r"\b(\d{1,2})\.([A-Z][a-z]{2})\.(\d{2})\b")
PHONE = re.compile(r"\b(?:\+?91[-\s]?)?[6-9]\d{9}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
HONORIFIC = re.compile(r"\b(?:Shri|Sri|Smt|Mr|Mrs|Ms|Dr|Prof)\.?\s", re.IGNORECASE)
SUFFIX = re.compile(
    r"\b[\w.]*\s*Compan(?:y|ies)\b(?:\s*(?:Pvt\.?|Private)?\s*(?:Ltd\.?|Limited)\.?)?",
    re.IGNORECASE,
)
BOUNDARY = re.compile(
    r"^(?:Foundation|Services|Trust|Society|Sansthan|Samiti|Consortium|Federation|Institute|Mission|NGO|Agency|Cluster|Programme|Program|Initiative|Scheme|\d{1,4})$",
    re.IGNORECASE,
)
MONTHS = {
    name: index
    for index, name in enumerate(
        ("", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    )
    if name
}


class CollectorRefused(RuntimeError):
    pass


@dataclass
class Record:
    name: str
    fields: dict[str, object] = field(default_factory=dict)
    reference: str | None = None


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    content: bytes

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.request_count = 0
        self._last = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    @property
    def user_agent(self) -> str:
        return USER_AGENT.format(contact=settings.collector_contact_email)

    def allowed(self, url: str) -> bool:
        origin = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if origin not in self._robots:
            parser = urllib.robotparser.RobotFileParser()
            parser.set_url(urljoin(origin, "/robots.txt"))
            try:
                response = requests.get(
                    parser.url, headers={"User-Agent": self.user_agent}, timeout=self.timeout
                )
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                elif response.status_code >= 500:
                    parser = None
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(self.user_agent, url)

    def get(self, url: str) -> Response:
        if not self.allowed(url):
            raise CollectorRefused(f"robots.txt disallows {url}; collection stopped.")
        elapsed = time.monotonic() - self._last
        if elapsed < 1:
            time.sleep(1 - elapsed)
        self._last = time.monotonic()
        self.request_count += 1
        if not settings.scrapfly_api_key:
            response = requests.get(
                url, headers={"User-Agent": self.user_agent}, timeout=self.timeout
            )
            return Response(url, response.status_code, response.content)
        response = requests.get(
            SCRAPFLY_ENDPOINT,
            params={
                "key": settings.scrapfly_api_key,
                "url": url,
                "asp": "false",
                "render_js": "false",
                "country": "in",
            },
            headers={"User-Agent": self.user_agent},
            timeout=self.timeout,
        )
        if response.status_code != 200:
            return Response(url, response.status_code, response.content)
        payload = response.json().get("result", {})
        body = payload.get("content", "")
        content = (
            base64.b64decode(body)
            if payload.get("format") == "binary" or payload.get("content_encoding") == "base64"
            else body.encode()
        )
        return Response(url, int(payload.get("status_code", 200)), content)


def strip_personal(text: str) -> str:
    return HONORIFIC.split(EMAIL.sub("", PHONE.sub("", text)))[0].strip(" ,;|")


class SfacFpoCollector:
    source_code = "sfac_fpo_list"

    def __init__(self, states: list[str] | None = None, limit: int | None = None):
        self.states = {state.lower() for state in states} if states else None
        self.limit = limit
        self.fetcher = Fetcher()

    def state_lists(self) -> list[tuple[str, str, int | None]]:
        page = self.fetcher.get(LIST_PAGE)
        if not page.ok:
            raise CollectorRefused(f"SFAC index returned HTTP {page.status}.")
        doc = lxml.html.fromstring(page.text)
        found, seen = [], set()
        for anchor in doc.xpath("//a[@href]"):
            href = unquote(anchor.get("href", ""))
            if "List of FPOs in the State" not in href and "List-of-FPOs-in-the-State" not in href:
                continue
            label = anchor.text_content().strip()
            state = re.sub(r"\s*\(\d+\)\s*$", "", label).strip()
            expected = re.search(r"\((\d+)\)", label)
            url = urljoin(LIST_PAGE, anchor.get("href", "").split("?")[0])
            if not state or url in seen or (self.states and state.lower() not in self.states):
                continue
            seen.add(url)
            found.append((state, url, int(expected.group(1)) if expected else None))
        return found

    def collect(self) -> list[Record]:
        records: list[Record] = []
        for state, url, _expected in self.state_lists():
            response = self.fetcher.get(url)
            if not response.ok:
                logger.error("%s returned HTTP %s", url, response.status)
                continue
            try:
                text = "\n".join(
                    page.extract_text() or ""
                    for page in pypdf.PdfReader(io.BytesIO(response.content)).pages
                )
            except Exception as error:  # noqa: BLE001 — one malformed public PDF must not abort the national batch
                logger.error("Could not parse %s: %s", url, error)
                continue
            records.extend(self._parse_text(text, state, url))
            if self.limit and len(records) >= self.limit:
                return records[: self.limit]
        return records

    def _parse_text(self, text: str, state: str, reference: str) -> list[Record]:
        flat = re.sub(r"\s+", " ", text)
        matches = list(CIN.finditer(flat))
        output: list[Record] = []
        for index, match in enumerate(matches):
            before = flat[matches[index - 1].end() if index else 0 : match.start()]
            after = flat[
                match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(flat)
            ]
            name = self._company_name(before)
            if not name:
                continue
            date_match = REG_DATE.search(after)
            registration_date = None
            if date_match and date_match.group(2) in MONTHS:
                registration_date = f"20{date_match.group(3)}-{MONTHS[date_match.group(2)]:02d}-{int(date_match.group(1)):02d}"
            district_match = re.search(
                rf"\b\d{{1,4}}\s+{re.escape(state)}\s+([A-Za-z][\w.\-& ]{{2,40}}?)\s*[,(]", before
            )
            district = district_match.group(1).strip().rstrip(",") if district_match else None
            address = REG_DATE.sub("", after, count=1).strip()
            address = re.split(r"\bShri\.?\b|\bSmt\.?\b|\bMr\.?\s|\bMs\.?\s|\bDr\.?\s", address)[0][
                :300
            ].strip(" ,;|")
            fields = {
                "cin": re.sub(r"\s+", "", match.group()),
                "state_name": state,
                "district_name": district,
                "legal_form": "producer_company",
                "type": "fpo",
                "registration_date": registration_date,
                "address_line1": address or None,
            }
            output.append(
                Record(
                    strip_personal(name),
                    {k: strip_personal(v) if isinstance(v, str) else v for k, v in fields.items()},
                    reference,
                )
            )
        return [record for record in output if record.name]

    @staticmethod
    def _company_name(before: str) -> str | None:
        tail = re.sub(r"\s*Producer Company\s*$", "", before.strip(), flags=re.IGNORECASE)
        suffixes = list(SUFFIX.finditer(tail))
        if not suffixes:
            return None
        suffix = suffixes[-1]
        kept: list[str] = []
        for token in reversed(tail[: suffix.start()].split()[-9:]):
            if BOUNDARY.search(token) or token.endswith(")"):
                break
            kept.append(token)
        return (" ".join(reversed(kept)) + " " + suffix.group()).strip()[:200]
