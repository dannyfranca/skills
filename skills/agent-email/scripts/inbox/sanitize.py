"""Turn untrusted mail into plain text, codes, and flagged links."""

import re
import unicodedata
from urllib.parse import urlparse

from .markup import html_to_text

BODY_LIMIT = 4000
LINE_LIMIT = 200
MAX_CODES = 5
MAX_LINKS = 20
MAX_URL = 2000
# Mail for signups is a few kB, and parsing stays fast up to this size.
MAX_HTML = 500_000
MAX_ATTACHMENTS = 10
ATTACHMENT_NAME_LIMIT = 100

# Browsers read "\" in a URL as "/", so "https://evil.net\@site.com" opens evil.net. A URL stops at "\".
URL = re.compile(r"https?://[^\s<>\"'()\[\]{}\\]+", re.I)
TRAILING_PUNCTUATION = ".,;:!?"
DOMAIN_IN_TEXT = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,63}\b", re.I)
CODE_KEYWORD = r"(?:code|otp|pin|passcode|password|token|verification|verify|c[óo]digo)"
CODE_NEAR_KEYWORD = re.compile(
    rf"{CODE_KEYWORD}[^\n]{{0,40}}?\b((?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{4,8}}|\d{{3}}[- ]\d{{3}})\b", re.I
)
BARE_CODE = re.compile(r"\b\d{4,8}\b")
YEAR = re.compile(r"(?:19|20)\d\d")
# Unicode default-ignorable characters outside category C: they render as nothing.
INVISIBLE_MARKS = re.compile("[\u034f\u115f\u1160\u17b4\u17b5\u180b-\u180f\u3164\ufe00-\ufe0f\uffa0\U000e0100-\U000e01ef]")


def invisible(ch):
    return unicodedata.category(ch)[0] == "C" or bool(INVISIBLE_MARKS.match(ch))


def shown(text):
    """The text as a reader sees it: full-width letters as ASCII, invisible characters gone."""
    text = unicodedata.normalize("NFKC", text or "")
    return "".join(ch for ch in text if ch in "\n\t" or not invisible(ch))


def clean(text, limit=BODY_LIMIT):
    # NFKC first, so full-width brackets become "<" and ">" before they are neutralized.
    text = shown(text)
    # No "<" or ">" survives, so mail content cannot open or close a tag of the envelope.
    text = text.replace("<", "‹").replace(">", "›")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text) <= limit else text[:limit].rstrip() + " …[cut]"


def clean_line(text, limit=LINE_LIMIT):
    return clean((text or "").replace("\n", " "), limit)


def site(host):
    labels = (host or "").lower().rstrip(".").split(".")
    keep = 3 if len(labels) >= 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3 else 2
    return ".".join(labels[-keep:])


def codes(subject, text):
    without_urls = f"{subject}\n{URL.sub(' ', text)}"
    found = [match.group(1) for match in CODE_NEAR_KEYWORD.finditer(without_urls)]
    found += [match for match in BARE_CODE.findall(without_urls) if not YEAR.fullmatch(match)]
    return list(dict.fromkeys(found))[:MAX_CODES]


def host_of(url):
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""


def named_sites(label):
    hosts = [host_of(url) for url in URL.findall(label)] + DOMAIN_IN_TEXT.findall(URL.sub(" ", label))
    return {site(host) for host in hosts if host}


def flag(host, label, sender_domain):
    label_sites = named_sites(shown(label))
    # A label that also names the destination still names another site, and the reader may trust that one.
    if label_sites - {site(host)}:
        return "text-mismatch"
    return "same-domain" if site(host) == site(sender_domain) else "other-domain"


def bare_urls(text):
    return [match.rstrip(TRAILING_PUNCTUATION) for match in URL.findall(text)]


def links(anchors, text, sender_domain):
    # A URL shown as anchor text is not where the anchor goes. The anchor itself reports the mismatch.
    labels = {url for _, label in anchors for url in bare_urls(label or "")}
    candidates = list(anchors) + [(url, None) for url in bare_urls(text) if url not in labels]
    found = {}
    for url, label in candidates:
        url = url.strip()
        # Invisible or direction marks inside a URL make the shown link differ from the one that opens.
        host = host_of(url)
        if len(url) > MAX_URL or any(map(invisible, url)) or not URL.fullmatch(url) or not host:
            continue
        # One mismatched label is enough: the sender picks which copy of the link comes first.
        if found.get(url) != "text-mismatch":
            found[url] = flag(host, label, sender_domain)
    return [{"url": url, "flag": link_flag} for url, link_flag in found.items()][:MAX_LINKS]


def content(message, sender_domain):
    """The cleaned body, codes, and links of a message."""
    anchors = []
    if message.text:
        raw_text = message.text
    elif message.html:
        raw_text, anchors = html_to_text(message.html)
    else:
        raw_text = ""
    if message.text and message.html:
        anchors = html_to_text(message.html)[1]
    body = clean(raw_text)
    return {
        "body": body,
        "codes": codes(clean_line(message.subject), body),
        "links": links(anchors, raw_text, sender_domain),
        "attachments": [clean_line(name, ATTACHMENT_NAME_LIMIT) for name in message.attachments[:MAX_ATTACHMENTS]],
        "attachment_count": len(message.attachments),
    }
