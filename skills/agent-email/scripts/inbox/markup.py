"""Visible text and links of untrusted HTML mail."""

import re
from html.parser import HTMLParser

SKIPPED_TAGS = {"script", "style", "head", "title", "template", "noscript", "svg"}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {"p", "div", "br", "tr", "li", "table", "section", "article", "blockquote", "hr", "h1", "h2", "h3", "h4", "h5", "h6"}

CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)
IMPORTANT = re.compile(r"\s*!\s*important\s*$")
NUMBER = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))([a-z%]*)")
SCALE = re.compile(r"scale[xyz3d]*\(([^)]*)\)")
HEX_ALPHA = re.compile(r"#(?:[0-9a-f]{3}([0-9a-f])|[0-9a-f]{6}([0-9a-f]{2}))")
COLOR_FUNCTION = re.compile(r"(?:rgb|hsl)a?\((.*)\)")
COMBINATOR = re.compile(r"\s*[\s>+~]\s*")
SIBLING = re.compile(r"[+~]\s*\*\s*$")
PSEUDO_OR_ATTRIBUTE = re.compile(r"::?[\w-]+(?:\([^)]*\))?|\[[^\]]*\]")

FAINT = 0.1
FAR_PX = 500
EM_PX = 16
BOX_SIZES = ("height", "width", "max-height", "max-width")
OFFSETS = ("left", "top", "right", "bottom", "text-indent", "margin-left", "margin-top")

ANY_ELEMENT = (None, None, frozenset())


def declarations(css):
    """The value of each property, as a browser picks it: the last one, unless an earlier one is !important."""
    values, important = {}, set()
    for declaration in CSS_COMMENT.sub("", css).split(";"):
        name, colon, value = declaration.partition(":")
        name, value = name.strip().lower(), value.strip().lower()
        if not colon or not name:
            continue
        is_important = bool(IMPORTANT.search(value))
        if name in important and not is_important:
            continue
        values[name] = IMPORTANT.sub("", value)
        if is_important:
            important.add(name)
    return values


def numbers(value):
    return [(float(number), unit) for number, unit in NUMBER.findall(value)]


def tiny(value):
    return any(number <= 0 or (number <= 1 and unit == "px") for number, unit in numbers(value))


def fraction(value):
    found = numbers(value)
    if not found:
        return 1.0
    number, unit = found[0]
    return number / 100 if unit == "%" else number


def alpha(color):
    if color == "transparent":
        return 0.0
    if match := HEX_ALPHA.fullmatch(color):
        return int(match.group(1) * 2 if match.group(1) else match.group(2), 16) / 255
    if match := COLOR_FUNCTION.fullmatch(color):
        channels = re.split(r"\s*[,/]\s*|\s+", match.group(1).strip())
        return fraction(channels[3]) if len(channels) == 4 else 1.0
    return 1.0


def far_away(value):
    return any(
        number <= -100 if unit == "%" else number * (EM_PX if unit in ("em", "rem") else 1) <= -FAR_PX
        for number, unit in numbers(value)
    )


# Text that a human cannot see is the usual place for instructions aimed at an agent. This check is defense in
# depth, not the boundary: text in the colour of its background needs a renderer to detect, so every mail still
# reaches the agent inside the untrusted envelope and through the guard. Where a style can hide text, the check
# reads it as hidden: a lost code costs one retry, an unseen instruction can cost the account.
def hides(css):
    style = declarations(css)
    return (
        style.get("display") == "none"
        or style.get("visibility") in ("hidden", "collapse")
        or tiny(style.get("font-size", "1"))
        or tiny(style.get("font", "1"))
        or fraction(style.get("opacity", "1")) < FAINT
        or alpha(style.get("color", "")) < FAINT
        or alpha(style.get("-webkit-text-fill-color", "")) < FAINT
        or any(tiny(style.get(size, "1")) for size in BOX_SIZES)
        or style.get("clip", "auto") != "auto"
        or style.get("clip-path", "none") != "none"
        or any(tiny(arguments) for arguments in SCALE.findall(style.get("transform", "")))
        or any(far_away(style.get(offset, "")) for offset in OFFSETS + ("transform",))
    )


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


def css_rules(css):
    # A split on braces stays linear, where a rule regex rescans the rest of a brace-less stylesheet at every offset.
    for chunk in css.split("}"):
        head, brace, body = chunk.rpartition("{")
        if brace:
            yield head.rpartition("{")[2], body


def hiding_targets(stylesheets):
    # Rules inside @media count too, and ancestors, pseudo-classes, and attributes are ignored:
    # the sender controls all of them, so the parser hides too much rather than too little.
    css = CSS_COMMENT.sub(" ", " ".join(stylesheets))
    targets = [
        selector_target(selector)
        for selectors, body in css_rules(css)
        if hides(body)
        for selector in selectors.split(",")
    ]
    return [target for target in targets if target]


def first_values(attrs):
    # A browser keeps the first of duplicate attributes, so a later empty style cannot unhide the element.
    attributes = {}
    for name, value in attrs:
        attributes.setdefault(name, value)
    return attributes


class _StyleCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside, self.stylesheets = False, []

    def handle_starttag(self, tag, attrs):
        self.inside = tag == "style"

    def handle_endtag(self, tag):
        self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.stylesheets.append(data)


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
        attributes = first_values(attrs)
        hidden = self.hidden(tag, attributes)
        self.stack.append((tag, hidden))
        self.hidden_depth += hidden
        if tag == "a" and not self.hidden_depth:
            # A browser closes an open anchor when the next one starts.
            self.finish_anchor()
            self.anchor = (attributes.get("href") or "", [])

    def handle_startendtag(self, tag, attrs):
        # HTML ignores "/>" on elements that are not void, so <div style="display:none"/> stays open.
        self.handle_starttag(tag, attrs)

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
    # A stylesheet applies to the whole document, also to elements before it, so it is read first.
    styles = _StyleCollector()
    styles.feed(html)
    styles.close()
    extractor = _TextExtractor(hiding_targets(styles.stylesheets))
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts), extractor.anchors
