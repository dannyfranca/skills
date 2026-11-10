"""Visible text and links of untrusted HTML mail."""

import re
from html.parser import HTMLParser

SKIPPED_TAGS = {"script", "style", "head", "title", "template", "noscript", "svg"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "section", "article", "blockquote", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}
# Text that a human cannot see is the usual place for instructions aimed at an agent.
ZERO_ALPHA = r"\s*0*\.?0+%?\s*\)"
HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|(?:font-size|opacity|max-height)\s*:\s*[+-]?\.?0(?![.\d]*[1-9])"
    r"|(?<![\w-])color\s*:\s*(?:transparent"
    rf"|(?:rgb|hsl)a?\((?:[^,)]*,){{3}}{ZERO_ALPHA}|(?:rgb|hsl)a?\([^)/]*/{ZERO_ALPHA}"
    r"|#[0-9a-f]{3}0\b|#[0-9a-f]{6}00\b)",
    re.I,
)

STYLE_BLOCK = re.compile(r"<style\b[^>]*>(.*?)</style\s*>", re.I | re.S)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
COMBINATOR = re.compile(r"\s*[\s>+~]\s*")
SIBLING = re.compile(r"[+~]\s*\*\s*$")
PSEUDO_OR_ATTRIBUTE = re.compile(r"::?[\w-]+(?:\([^)]*\))?|\[[^\]]*\]")


ANY_ELEMENT = (None, None, frozenset())


def compound_target(compound):
    compound = PSEUDO_OR_ATTRIBUTE.sub("", compound)
    tag = re.match(r"[a-z][a-z0-9]*", compound, re.I)
    ids = re.findall(r"#([\w-]+)", compound)
    return tag.group(0).lower() if tag else None, ids[0] if ids else None, frozenset(re.findall(r"\.([\w-]+)", compound))


def selector_target(selector):
    """The (tag, id, classes) of the element that a CSS selector picks, without its ancestors."""
    selector = selector.strip()
    targets = [compound_target(compound) for compound in COMBINATOR.split(selector) if compound]
    if not targets:
        return None
    # "a *" hides what is inside "a", so hiding "a" hides the same text. Siblings of "a" are not inside it.
    if targets[-1] == ANY_ELEMENT and not SIBLING.search(selector):
        targets = [target for target in targets if target != ANY_ELEMENT] or [ANY_ELEMENT]
    return targets[-1]


def hides(declarations):
    return bool(HIDDEN_STYLE.search(CSS_COMMENT.sub("", declarations)))


def hiding_targets(html):
    # Rules inside @media count too, and ancestors, pseudo-classes, and attributes are ignored:
    # the sender controls all of them, so the parser hides too much rather than too little.
    css = CSS_COMMENT.sub(" ", " ".join(STYLE_BLOCK.findall(html)))
    targets = [
        selector_target(selector)
        for selectors, declarations in CSS_RULE.findall(css)
        if hides(declarations)
        for selector in selectors.split(",")
    ]
    return [target for target in targets if target]


class _TextExtractor(HTMLParser):
    def __init__(self, hiding):
        super().__init__(convert_charrefs=True)
        self.hiding = hiding
        self.stack, self.hidden_depth, self.parts, self.anchors, self.anchor = [], 0, [], [], None

    def hidden(self, tag, attributes):
        classes = set((attributes.get("class") or "").split())
        styled_hidden = any(
            target_tag in (None, tag) and target_id in (None, attributes.get("id")) and target_classes <= classes
            for target_tag, target_id, target_classes in self.hiding
        )
        return (
            tag in SKIPPED_TAGS
            or "hidden" in attributes
            or attributes.get("aria-hidden") == "true"
            or hides(attributes.get("style") or "")
            or styled_hidden
        )

    def handle_starttag(self, tag, attrs):
        if tag in BLOCK_TAGS and not self.hidden_depth:
            self.parts.append("\n")
        if tag in VOID_TAGS:
            return
        attributes = dict(attrs)
        hidden = self.hidden(tag, attributes)
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
            if open_tag == "a":
                self.finish_anchor()
            if open_tag == tag:
                break
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def finish_anchor(self):
        if self.anchor:
            self.anchors.append((self.anchor[0], "".join(self.anchor[1])))
            self.anchor = None

    def close(self):
        super().close()
        # HTMLParser closes no tag at the end of input, and a browser still makes the open anchor a link.
        self.finish_anchor()

    def handle_data(self, data):
        if self.hidden_depth:
            return
        self.parts.append(data)
        if self.anchor:
            self.anchor[1].append(data)


def html_to_text(html):
    extractor = _TextExtractor(hiding_targets(html))
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts), extractor.anchors
