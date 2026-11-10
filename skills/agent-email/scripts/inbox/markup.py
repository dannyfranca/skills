"""Visible text and links of untrusted HTML mail."""

import re
from collections import defaultdict
from html.parser import HTMLParser

# Elements whose content a browser does not render, or renders only where it lacks support for the element.
SKIPPED_TAGS = {
    "script", "style", "head", "title", "template", "noscript", "svg",
    "datalist", "iframe", "canvas", "audio", "video", "noembed", "noframes", "rp",
}
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "section", "article", "blockquote", "dialog", "details", "summary", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
}

IMPORTANT = re.compile(r"\s*!\s*important\s*$")
CSS_ESCAPE = re.compile(r"\\(?:([0-9a-f]{1,6})\s?|(.))", re.I | re.S)
ESCAPE = r"\\(?:[0-9a-f]{1,6}\s?|.)"
# A string ends at a newline, where a browser drops it as a bad string. A comment that never closes runs to the end.
STRING = r"""(?:"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)"""
IDENTIFIER = rf"((?:[\w-]|{ESCAPE})+)"
# Strings, URLs, escapes, and comments can hold braces, semicolons, and comment marks that are no CSS syntax.
CSS_TOKEN = re.compile(rf"""(/\*.*?(?:\*/|\Z))|{IDENTIFIER}(\()?|{STRING}|[{{}};]|[^{{}};"'\\/\w-]+|.""", re.I | re.S)
# An unquoted url() runs to its ")" whatever it holds, and a bad one runs to the end. A quoted one is a function.
UNQUOTED_URL = re.compile(r"""\s*+(?!["'])(?:\\.|[^)\\])*\)?""", re.S)
# A number starts no word, so the "-2" of a font named "Roboto-2x" is no size.
NUMBER = re.compile(r"(?<![\w.#-])([+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?)([a-z%]*)")
SCALE = re.compile(r"scale[xyz3d]*\(([^)]*)\)")
OPACITY_FILTER = re.compile(r"opacity\(([^)]*)\)")
HEX_ALPHA = re.compile(r"#(?:[0-9a-f]{3}([0-9a-f])|[0-9a-f]{6}([0-9a-f]{2}))")
COLOR_FUNCTION = re.compile(r"(?:rgb|hsl|hwb|lab|lch|oklab|oklch)a?\((.*)\)")
# An unclosed bracket runs to the end, so a long run of "[" is one token and not one rescan per bracket.
PSEUDO_OR_ATTRIBUTE = rf"""::?[\w-]+(?:\([^)]*(?:\)|\Z))?|\[(?:{STRING}|\\.|[^\]"'\\])*(?:\]|\Z)"""
SELECTOR_TOKEN = re.compile(rf"({ESCAPE})|({PSEUDO_OR_ATTRIBUTE})|(\s*[\s>+~]\s*)|(,)|[^\\:\[\s>+~,]+|.", re.I | re.S)
TAG = re.compile(IDENTIFIER, re.I | re.S)
# One scan, so that the "." of an escaped "\." starts no class.
COMPOUND_PART = re.compile(rf"#{IDENTIFIER}|\.{IDENTIFIER}|{ESCAPE}|.", re.I | re.S)
# "&" stands for the parent rule's element, which is any element here.
SIMPLE_COMPOUND = re.compile(rf"(?:{IDENTIFIER}|[*&])?(?:[#.]{IDENTIFIER}|[*&])*", re.I | re.S)

FAINT = 0.1
FAR_PX = 500
EM_PX = 16
# Font-relative units count at the default font size, and viewport and container units at a 320px phone screen.
UNIT_PX = {
    "px": 1, "em": EM_PX, "rem": EM_PX, "lh": EM_PX, "rlh": EM_PX, "ic": EM_PX, "ric": EM_PX,
    "ex": EM_PX / 2, "rex": EM_PX / 2, "ch": EM_PX / 2, "rch": EM_PX / 2, "cap": EM_PX / 2, "rcap": EM_PX / 2,
    "pt": 96 / 72, "pc": 16, "in": 96, "cm": 96 / 2.54, "mm": 96 / 25.4, "q": 96 / 101.6,
}
VIEWPORT_UNIT = re.compile(r"(?:[sld]?v|cq)(?:w|h|i|b|min|max)")
VIEWPORT_UNIT_PX = 3.2
# A percentage font size is of the parent font. A percentage box size is of a 600px mail column.
FONT_PERCENT_PX = EM_PX / 100
BOX_PERCENT_PX = 6
BOX_SIZES = ("height", "width", "max-height", "max-width")
OFFSETS = ("left", "top", "right", "bottom", "inset", "text-indent", "margin", "margin-left", "margin-top", "translate")
# A mask or a motion path can hide or move text in ways the policy does not compute.
NONE_OR_HIDING = ("mask", "mask-image", "-webkit-mask", "-webkit-mask-image", "offset", "offset-path")

# The values a visibility property may have for the policy to read them: words, numbers, hex colours, strings,
# and the functions the policy evaluates or that cannot hide text. Any other value, such as calc(), var(),
# matrix(), or blur(), counts as hiding.
NUMBER_TOKEN = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?[a-z%]*"
ATOM = rf"(?:{NUMBER_TOKEN}|-?[^\W\d][\w-]*|#[0-9a-f]{{3,8}}|\"[^\"]*\"|'[^']*')"
SEPARATOR = r"\s*[\s,/]\s*"
FUNCTION = (
    r"(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch|scale[xyz3d]*|translate[xyz3d]*|rotatez?"
    r"|opacity|brightness|contrast|grayscale|sepia|saturate|hue-rotate|invert|drop-shadow|rect)"
    rf"\(\s*(?:{ATOM}(?:{SEPARATOR}{ATOM})*)?\s*\)"
)
TERM = rf"(?:{FUNCTION}|{ATOM})"
READABLE_VALUE = re.compile(rf"{TERM}(?:{SEPARATOR}{TERM})*")
MAX_VALUE = 200

ANY_ELEMENT = (None, None, frozenset())
# A real mail hides a few blocks. Thousands of distinct hiding rules only make every element check them all.
MAX_HIDING_TARGETS = 200


def unescape(css):
    def character(match):
        if match.group(2) is not None:
            return match.group(2)
        code = int(match.group(1), 16)
        return chr(code) if 0 < code <= 0x10FFFF and not 0xD800 <= code <= 0xDFFF else "\ufffd"

    return CSS_ESCAPE.sub(character, css)


def css_tokens(css):
    # CSS preprocessing makes CR, CRLF, and form feed a newline, so each one also ends a string.
    css = css.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    position = 0
    while position < len(css):
        match = CSS_TOKEN.match(css, position)
        position = match.end()
        if match.group(1):
            yield " "
        elif match.group(3) and unescape(match.group(2)).lower() == "url":
            url = UNQUOTED_URL.match(css, position)
            position = url.end() if url else position
            yield match.group(0) + (url.group(0) if url else "")
        else:
            yield match.group(0)


def declarations(css):
    # A semicolon inside a string splits it too, which can only add declarations and never hide a real one.
    for declaration in "".join(css_tokens(css)).split(";"):
        name, colon, value = declaration.partition(":")
        name, value = unescape(name).strip().lower(), IMPORTANT.sub("", " ".join(unescape(value).lower().split()))
        if colon and name:
            yield name, value


def numbers(value):
    return [(float(number), unit) for number, unit in NUMBER.findall(value)]


def length_px(number, unit, percent_px):
    if unit == "%":
        return number * percent_px
    if VIEWPORT_UNIT.fullmatch(unit):
        return number * VIEWPORT_UNIT_PX
    # A unit the policy does not know gives no size, so the length reads as too small to see.
    return number * UNIT_PX.get(unit, 0)


def tiny(value, percent_px):
    return any(number <= 0 or (unit and length_px(number, unit, percent_px) <= 1) for number, unit in numbers(value))


def small_factor(value):
    return any(abs(number / 100 if unit == "%" else number) < FAINT for number, unit in numbers(value))


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
        if len(channels) == 3:
            return 1.0
        # A missing alpha renders as zero. Any other form, such as a relative colour, has an alpha the policy
        # does not compute.
        return fraction(channels[3]) if len(channels) == 4 and numbers(channels[3]) else 0.0
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
def hiding(name, value):
    if name not in VISIBILITY_PROPERTIES:
        return False
    if len(value) > MAX_VALUE or not READABLE_VALUE.fullmatch(value):
        return True
    return (
        (name == "display" and value == "none")
        or (name == "visibility" and value in ("hidden", "collapse"))
        or (name in ("font-size", "font") and tiny(value, FONT_PERCENT_PX))
        or (name == "opacity" and fraction(value) < FAINT)
        or (name in ("color", "-webkit-text-fill-color") and alpha(value) < FAINT)
        or (name in BOX_SIZES and tiny(value, BOX_PERCENT_PX))
        or (name == "clip" and value != "auto")
        or (name == "clip-path" and value != "none")
        or (name == "content-visibility" and value not in ("visible", "auto"))
        or (name in NONE_OR_HIDING and value != "none")
        or (name == "transform" and any(small_factor(arguments) for arguments in SCALE.findall(value)))
        or (name == "scale" and small_factor(value))
        or (name == "zoom" and fraction(value) < FAINT)
        # Only a turn about the z axis keeps text facing the reader.
        or (name == "rotate" and len(value.split()) > 1 and not value.startswith("z "))
        or (name == "filter" and any(fraction(argument) < FAINT for argument in OPACITY_FILTER.findall(value)))
        or (name in OFFSETS + ("transform",) and far_away(value))
    )


VISIBILITY_PROPERTIES = {
    "display", "visibility", "font-size", "font", "opacity", "color", "-webkit-text-fill-color",
    "clip", "clip-path", "transform", "filter", "content-visibility", "scale", "zoom", "rotate",
    *BOX_SIZES, *OFFSETS, *NONE_OR_HIDING,
}


def hides(css):
    # Any hiding declaration counts, also one that a later declaration overrides: a browser drops a later
    # value it cannot parse, and only a full CSS parser knows which values those are.
    return any(hiding(name, value) for name, value in declarations(css))


def compound_target(compound):
    tag = TAG.match(compound)
    parts = COMPOUND_PART.findall(compound)
    ids = [unescape(id) for id, _ in parts if id]
    classes = frozenset(unescape(name) for _, name in parts if name)
    return unescape(tag.group(0)).lower() if tag else None, ids[0] if ids else None, classes


def selector_target(compounds, combinators):
    targets = [compound_target(compound) for compound in compounds]
    index = len(targets) - 1
    # "a *" hides what is inside "a", so hiding "a" hides the same text. Siblings of "a" are not inside it.
    while index and targets[index] == ANY_ELEMENT and not {"+", "~"} & set(combinators[index - 1]):
        index -= 1
    return targets[index]


def selector_targets(selectors):
    """The (tag, id, classes) of each element that a CSS selector list picks, without its ancestors."""
    compounds, combinators, compound, combinator, simple = [], [], "", "", True
    for match in SELECTOR_TOKEN.finditer(selectors + ","):
        escape, pseudo_or_attribute, next_combinator, comma = match.groups()
        if escape or not (pseudo_or_attribute or next_combinator is not None or comma):
            compound += match.group(0)
            continue
        if pseudo_or_attribute:
            # Pseudo-classes and attributes are ignored: an element with any of them can be the target.
            compound += "*"
            simple = simple and not (pseudo_or_attribute.startswith(":") and "(" in pseudo_or_attribute)
            continue
        if compound:
            simple = simple and bool(SIMPLE_COMPOUND.fullmatch(compound))
            if compounds:
                combinators.append(combinator)
            compounds.append(compound)
            compound, combinator = "", ""
        if comma:
            # Functional pseudo-classes and namespaces can pick elements that no tag, id, or class names.
            # Such a rule hides every element: the sender controls the selector, so it may not narrow the check.
            if compounds or not simple:
                yield selector_target(compounds, combinators) if simple else ANY_ELEMENT
            compounds, combinators, combinator, simple = [], [], "", True
        else:
            combinator += next_combinator.strip()


def css_rules(css):
    """Each (selector list, declarations) of a stylesheet, also of rules nested in @media or in other rules."""
    selectors, declarations, segment = [], [], []
    for token in css_tokens(css):
        if token == "{":
            if selectors and declarations:
                yield selectors[-1], "".join(declarations)
            selectors.append("".join(segment))
            declarations, segment = [], []
        elif token == "}":
            if selectors:
                yield selectors.pop(), "".join(declarations)
            declarations, segment = [], []
        elif token == ";":
            declarations.append(token)
            segment = []
        else:
            declarations.append(token)
            segment.append(token)
    # A browser closes the blocks still open at the end of the stylesheet. Only the innermost has declarations left.
    if selectors:
        yield selectors[-1], "".join(declarations)


def hiding_targets(stylesheets):
    # Ancestors, pseudo-classes, and attributes are ignored: the sender controls all of them,
    # so the parser hides too much rather than too little.
    targets = {
        target
        for stylesheet in stylesheets
        for selectors, declarations in css_rules(stylesheet)
        if hides(declarations)
        for target in selector_targets(selectors)
    }
    return [ANY_ELEMENT] if len(targets) > MAX_HIDING_TARGETS else list(targets)


def first_values(attrs):
    # A browser keeps the first of duplicate attributes, so a later empty style cannot unhide the element.
    attributes = {}
    for name, value in attrs:
        attributes.setdefault(name, value)
    return attributes


class _HtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)

    def handle_startendtag(self, tag, attrs):
        # HTML ignores "/>" on elements that are not void: <div style="display:none"/> stays open,
        # and <style/> still starts a stylesheet.
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag)


class _StyleCollector(_HtmlParser):
    def __init__(self):
        super().__init__()
        self.inside, self.stylesheets = False, []

    def handle_starttag(self, tag, attrs):
        self.inside = tag == "style"

    def handle_endtag(self, tag):
        self.inside = False

    def handle_data(self, data):
        if self.inside:
            self.stylesheets.append(data)


class _TextExtractor(_HtmlParser):
    def __init__(self, hiding):
        super().__init__()
        self.hiding = hiding
        self.stack, self.hidden_depth, self.parts, self.anchors, self.anchor = [], 0, [], [], None
        self.positions, self.hidden_positions = defaultdict(list), []

    def hidden(self, tag, attributes):
        classes = set((attributes.get("class") or "").split())
        styled_hidden = any(
            target_tag in (None, tag) and target_id in (None, attributes.get("id")) and target_classes <= classes
            for target_tag, target_id, target_classes in self.hiding
        )
        return (
            tag in SKIPPED_TAGS
            or (tag == "dialog" and "open" not in attributes)
            or (self.in_closed_details() and (tag != "summary" or self.stack[-1][2]["summary_shown"]))
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
        if self.in_closed_details() and tag == "summary":
            self.stack[-1][2]["summary_shown"] = True
        details = {"summary_shown": False} if tag == "details" and "open" not in attributes else None
        self.positions[tag].append(len(self.stack))
        if hidden:
            self.hidden_positions.append(len(self.stack))
        self.stack.append((tag, hidden, details))
        self.hidden_depth += hidden
        if tag == "a" and not self.hidden_depth:
            # A browser closes an open anchor when the next one starts.
            self.finish_anchor()
            self.anchor = (attributes.get("href") or "", [])

    def handle_endtag(self, tag):
        if not self.positions[tag]:
            return
        # A browser ignores some end tags that would close other open elements. Which ones depends on the
        # element kinds, so an end tag that would close a hidden element on the way is ignored here.
        if self.hidden_positions and self.hidden_positions[-1] > self.positions[tag][-1]:
            return
        while self.stack:
            open_tag, hidden, _ = self.stack.pop()
            self.positions[open_tag].pop()
            if hidden:
                self.hidden_positions.pop()
            self.hidden_depth -= hidden
            if open_tag == "a":
                self.finish_anchor()
            if open_tag == tag:
                break
        if tag in BLOCK_TAGS:
            self.parts.append("\n")

    def in_closed_details(self):
        # A closed <details> shows only its <summary>.
        return bool(self.stack) and self.stack[-1][2] is not None

    def finish_anchor(self):
        if self.anchor:
            self.anchors.append((self.anchor[0], "".join(self.anchor[1])))
            self.anchor = None

    def close(self):
        super().close()
        # HTMLParser closes no tag at the end of input, and a browser still makes the open anchor a link.
        self.finish_anchor()

    def handle_data(self, data):
        if self.hidden_depth or self.in_closed_details():
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
