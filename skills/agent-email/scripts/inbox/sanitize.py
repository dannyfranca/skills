"""Turn untrusted mail into plain text, codes, and flagged links."""

import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import urlparse

BODY_LIMIT = 4000
LINE_LIMIT = 200
MAX_CODES = 5
MAX_LINKS = 20
MAX_URL = 2000
MAX_ATTACHMENTS = 10
ATTACHMENT_NAME_LIMIT = 100

SKIPPED_TAGS = {"script", "style", "head", "title", "template", "noscript", "svg"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "section", "article", "blockquote", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
# Text that a human cannot see is the usual place for instructions aimed at an agent.
HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|(?:font-size|opacity|max-height)\s*:\s*0(?![.\d]*[1-9])", re.I
)

URL = re.compile(r"https?://[^\s<>\"'()\[\]{}]+", re.I)
TRAILING_PUNCTUATION = ".,;:!?"
COMMON_TLDS = "com|net|org|io|co|dev|app|ai|me|info|biz|xyz|email|[a-z]{2}"
DOMAIN_IN_TEXT = re.compile(rf"\b(?:[a-z0-9-]+\.)+(?:{COMMON_TLDS})\b", re.I)
CODE_KEYWORD = r"(?:code|otp|pin|passcode|password|token|verification|verify|c[óo]digo)"
CODE_NEAR_KEYWORD = re.compile(
    rf"{CODE_KEYWORD}[^\n]{{0,40}}?\b((?=[A-Za-z0-9]*\d)[A-Za-z0-9]{{4,8}}|\d{{3}}[- ]\d{{3}})\b", re.I
)
BARE_CODE = re.compile(r"\b\d{4,8}\b")
YEAR = re.compile(r"(?:19|20)\d\d")


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.hidden_depth, self.parts, self.anchors, self.anchor = [], 0, [], [], None

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS and not self.hidden_depth:
            self.parts.append("\n")
        if tag in VOID_TAGS:
            return
        attributes = dict(attrs)
        hidden = (
            tag in SKIPPED_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or bool(HIDDEN_STYLE.search(attributes.get("style") or ""))
        )
        self.stack.append((tag, hidden))
        self.hidden_depth += hidden
        if tag == "a" and not self.hidden_depth:
            self.anchor = (attributes.get("href") or "", [])

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if not any(open_tag == tag for open_tag, _ in self.stack):
            return
        while self.stack:
            open_tag, hidden = self.stack.pop()
            self.hidden_depth -= hidden
            if open_tag == "a" and self.anchor:
                self.anchors.append((self.anchor[0], "".join(self.anchor[1])))
                self.anchor = None
            if open_tag == tag:
                break
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.hidden_depth:
            return
        self.parts.append(data)
        if self.anchor:
            self.anchor[1].append(data)


def html_to_text(html):
    extractor = _TextExtractor()
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts), extractor.anchors


def clean(text, limit=BODY_LIMIT):
    # NFKC first, so full-width brackets become "<" and ">" before they are neutralized.
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
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


def flag(url, label, sender_domain):
    host = urlparse(url).hostname or ""
    label_sites = {site(domain) for domain in DOMAIN_IN_TEXT.findall(label or "")}
    if label_sites and site(host) not in label_sites:
        return "text-mismatch"
    return "same-domain" if site(host) == site(sender_domain) else "other-domain"


def links(anchors, text, sender_domain):
    candidates = list(anchors) + [(match.rstrip(TRAILING_PUNCTUATION), None) for match in URL.findall(text)]
    seen, result = set(), []
    for url, label in candidates:
        url = url.strip()
        # Invisible or direction marks inside a URL make the shown link differ from the one that opens.
        invisible = any(unicodedata.category(ch)[0] == "C" for ch in url)
        if url in seen or len(url) > MAX_URL or invisible or not URL.fullmatch(url):
            continue
        seen.add(url)
        result.append({"url": url, "flag": flag(url, label, sender_domain)})
    return result[:MAX_LINKS]


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
