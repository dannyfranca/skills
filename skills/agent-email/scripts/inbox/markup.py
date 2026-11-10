"""Visible text and links of untrusted HTML mail."""

import math
import re
from collections import defaultdict
from html.parser import HTMLParser

# Elements whose content a browser does not render, or renders only where it lacks support for the element.
SKIPPED_TAGS = {
    "script", "style", "head", "title", "template", "noscript", "svg", "math",
    "datalist", "select", "meter", "progress", "object", "iframe", "canvas", "audio", "video", "noembed", "noframes", "rp",
    "frameset",
}
# The HTML parser closes an element only when no element of these kinds is open inside it.
SCOPE = {"applet", "caption", "html", "table", "td", "th", "marquee", "object", "template", "svg", "math"}
EXTRA_BARRIERS = {"li": {"ol", "ul"}, "p": {"button"}}
TABLE_TAGS = {"table", "tbody", "thead", "tfoot", "tr", "td", "th", "caption", "colgroup"}
TABLE_SCOPE = {"html", "table", "template"}
# The end tag of any other element closes it only when no element of these kinds is open inside it. For a formatting
# element the parser moves such an element out instead, and keeping both open can only hide more.
SPECIAL = SCOPE | TABLE_TAGS | {
    "address", "area", "article", "aside", "base", "basefont", "bgsound", "blockquote", "body", "br", "button",
    "center", "col", "dd", "details", "dir", "div", "dl", "dt", "embed", "fieldset", "figcaption", "figure", "footer",
    "form", "frame", "frameset", "h1", "h2", "h3", "h4", "h5", "h6", "head", "header", "hgroup", "hr", "iframe", "img",
    "input", "keygen", "li", "link", "listing", "main", "menu", "meta", "nav", "noembed", "noframes", "noscript", "ol",
    "p", "param", "plaintext", "pre", "script", "search", "section", "select", "source", "style", "summary", "textarea",
    "title", "track", "ul", "wbr", "xmp",
}
FORMATTING_TAGS = {"a", "b", "big", "code", "em", "font", "i", "nobr", "s", "small", "strike", "strong", "tt", "u"}
# A browser does not open formatting elements again past the start of one of these elements.
FORMATTING_MARKERS = {"applet", "caption", "marquee", "object", "td", "template", "th"}
# A real mail leaves a few small formatting elements open. Thousands, or large ones, only make each end tag parse them
# all again. Each element counts as at least this size.
MAX_REOPENED_SIZE = 100_000
REOPENED_ELEMENT_SIZE = 100
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# A browser ends an open paragraph before each of these elements starts. In quirks mode a table stays inside it, and
# to keep it there can only hide more.
PARAGRAPH_CLOSERS = {
    "address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl", "fieldset",
    "figcaption", "figure", "footer", "header", "hgroup", "main", "menu", "nav", "ol", "p", "search", "section",
    "summary", "ul", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "listing", "form", "li", "dd", "dt", "plaintext", "hr",
    "xmp",
}
BLOCK_TAGS = {
    "p", "div", "br", "tr", "li", "table", "section", "article", "blockquote", "dialog", "details", "summary", "hr",
    "h1", "h2", "h3", "h4", "h5", "h6",
}

IMPORTANT = re.compile(r"\s*!\s*important\s*$")
# HTML and CSS split on ASCII whitespace only. Any other character, such as a no-break space, is part of a name.
WHITESPACE = r"[ \t\n\r\f]"
CSS_ESCAPE = re.compile(rf"\\(?:([0-9a-f]{{1,6}}){WHITESPACE}?|(.))", re.I | re.S)
# An escape takes all hex digits it can, so that its digits and the name characters after it split one way only.
ESCAPE = rf"\\(?:[0-9a-f]{{1,6}}+{WHITESPACE}?|[^0-9a-f])"
# A string ends at a newline, where a browser drops it as a bad string. A comment that never closes runs to the end.
STRING = r"""(?:"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)"""
# One character class, so that no character matches two alternatives and a failing match does not backtrack.
IDENTIFIER = rf"((?:[-\w\x80-\U0010ffff]|{ESCAPE})+)"
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
# CSS 2 pseudo-elements also take a single colon.
PSEUDO_ELEMENT = re.compile(r"::|:(?:before|after|first-line|first-letter)\Z", re.I)
# These pseudo-elements are boxes of their own, so their styles leave the text of the element as it is.
GENERATED_BOX = re.compile(r"::?(?:before|after|marker|placeholder|backdrop|file-selector-button)\Z", re.I)
SELECTOR_TOKEN = re.compile(
    rf"({ESCAPE})|({PSEUDO_OR_ATTRIBUTE})|({WHITESPACE}*[ \t\n\r\f>+~]{WHITESPACE}*)|(,)|[^\\:\[ \t\n\r\f>+~,]+|.", re.I | re.S
)
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
BOX_SIZES = (
    "height", "width", "max-height", "max-width", "block-size", "inline-size", "max-block-size", "max-inline-size",
)
OFFSETS = (
    "left", "top", "right", "bottom", "inset", "text-indent", "margin", "margin-left", "margin-top", "translate",
    "inset-inline", "inset-block", "inset-inline-start", "inset-inline-end", "inset-block-start", "inset-block-end",
    "margin-inline", "margin-block", "margin-inline-start", "margin-block-start",
)
# A mask, a motion path, or an animation can hide or move text in ways the policy does not compute.
NONE_OR_HIDING = (
    "mask", "mask-image", "-webkit-mask", "-webkit-mask-image", "offset", "offset-path", "animation", "animation-name",
    "text-security",
)

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
ROOT_TAGS = ("html", "body")
FOREIGN_TAGS = {"svg", "math"}
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


VENDOR_PREFIX = re.compile(r"^-(?:webkit|moz|ms|o)-")


def declarations(css):
    # A semicolon inside a string splits it too, which can only add declarations and never hide a real one.
    for declaration in "".join(css_tokens(css)).split(";"):
        name, colon, value = declaration.partition(":")
        name, value = unescape(name).strip().lower(), IMPORTANT.sub("", " ".join(unescape(value).lower().split()))
        # Browsers still read some prefixed names as the standard property. Reading all of them so can only hide more.
        standard = VENDOR_PREFIX.sub("", name)
        if colon and name:
            yield standard if standard in VISIBILITY_PROPERTIES or standard in FACTORS else name, value


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


def text_alpha(value):
    return None if value in ("inherit", "unset", "currentcolor") else alpha(value)


# A fill in the current colour paints the text colour, which the colour factor already holds.
def fill_alpha(value):
    return 1.0 if value == "currentcolor" else text_alpha(value)


# A clipping ancestor hides text pushed out in either direction, and a page shows no more than a few hundred pixels.
def far_away(value):
    return any(
        abs(number) >= 100 if unit == "%" else abs(length_px(number, unit, 0)) >= FAR_PX
        for number, unit in numbers(value)
    )


def readable(value):
    return len(value) <= MAX_VALUE and READABLE_VALUE.fullmatch(value)


# Text that a human cannot see is the usual place for instructions aimed at an agent. This check is defense in
# depth, not the boundary: text in the colour of its background needs a renderer to detect, so every mail still
# reaches the agent inside the untrusted envelope and through the guard. Where a style can hide text, the check
# reads it as hidden: a lost code costs one retry, an unseen instruction can cost the account.
def hiding(name, value):
    if name not in VISIBILITY_PROPERTIES:
        return False
    if not readable(value):
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
        or (name in ("scale", "font-size-adjust") and small_factor(value))
        or (name == "zoom" and fraction(value) < FAINT)
        # Only a turn about the z axis keeps text facing the reader.
        or (name == "rotate" and len(value.split()) > 1 and not value.startswith("z "))
        or (name == "filter" and any(fraction(argument) < FAINT for argument in OPACITY_FILTER.findall(value)))
        or (name in OFFSETS + ("transform",) and far_away(value))
    )


VISIBILITY_PROPERTIES = {
    "display", "visibility", "font-size", "font", "font-size-adjust", "opacity", "color", "-webkit-text-fill-color",
    "clip", "clip-path", "transform", "filter", "content-visibility", "scale", "zoom", "rotate",
    *BOX_SIZES, *OFFSETS, *NONE_OR_HIDING,
}


def hides(css):
    # Any hiding declaration counts, also one that a later declaration overrides: a browser drops a later
    # value it cannot parse, and only a full CSS parser knows which values those are.
    return any(hiding(name, value) for name, value in declarations(css))


# Content on an element replaces all of its text. On a pseudo-element it only adds text, as in the common clearfix.
def replaces(css):
    return any(name == "content" and value not in KEPT_CONTENT for name, value in declarations(css))


KEPT_CONTENT = ("normal", "none", "initial", "inherit", "unset", "revert", "revert-layer")


# A mail without a doctype renders in quirks mode, where class and ID selectors ignore ASCII case. The policy cannot
# trust the doctype the sender writes, so it matches them in any ASCII case.
ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def compound_target(compound):
    tag = TAG.match(compound)
    parts = COMPOUND_PART.findall(compound)
    ids = [unescape(id).translate(ASCII_LOWER) for id, _ in parts if id]
    classes = frozenset(unescape(name).translate(ASCII_LOWER) for _, name in parts if name)
    return unescape(tag.group(0)).lower() if tag else None, ids[0] if ids else None, classes


def selector_target(compounds, combinators):
    targets = [compound_target(compound) for compound in compounds]
    index = len(targets) - 1
    # "a *" hides what is inside "a", so hiding "a" hides the same text. Siblings of "a" are not inside it.
    while index and targets[index] == ANY_ELEMENT and not {"+", "~"} & set(combinators[index - 1]):
        index -= 1
    return targets[index]


def selector_targets(selectors):
    """The (tag, id, classes) of each element that a CSS selector list picks, without its ancestors, and whether
    the selector picks a pseudo-element of it."""
    compounds, combinators, compound, combinator, simple, pseudo_element, box = [], [], "", "", True, False, False
    for match in SELECTOR_TOKEN.finditer(selectors + ","):
        escape, pseudo_or_attribute, next_combinator, comma = match.groups()
        if escape or not (pseudo_or_attribute or next_combinator is not None or comma):
            compound += match.group(0)
            continue
        if pseudo_or_attribute:
            # Pseudo-classes and attributes are ignored: an element with any of them can be the target.
            compound += "*"
            simple = simple and not (pseudo_or_attribute.startswith(":") and "(" in pseudo_or_attribute)
            pseudo_element = pseudo_element or bool(PSEUDO_ELEMENT.match(pseudo_or_attribute))
            box = box or bool(GENERATED_BOX.match(pseudo_or_attribute))
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
            if (compounds or not simple) and not box:
                yield (selector_target(compounds, combinators) if simple else ANY_ELEMENT), pseudo_element
            compounds, combinators, combinator, simple, pseudo_element, box = [], [], "", True, False, False
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


def scale_factor(arguments):
    return min((abs(number / 100 if unit == "%" else number) for number, unit in numbers(arguments)), default=1.0)


# A font size in a font unit is relative to the parent font, and in a root unit to the root font. Any other size
# is absolute and replaces the parent font.
FONT_UNITS = ("em", "ex", "ch", "cap", "ic", "lh", "%")
ROOT_UNITS = ("rem", "rlh", "rex", "rch", "rcap", "ric")


def font_factor(value):
    if "smaller" in value:
        return 5 / 6
    relative = [length_px(number, unit, FONT_PERCENT_PX) for number, unit in numbers(value) if unit in FONT_UNITS]
    return relative[0] / EM_PX if relative else None


def font_px(value):
    absolute = [
        length_px(number, unit, 0) for number, unit in numbers(value) if unit and unit not in FONT_UNITS + ROOT_UNITS
    ]
    return absolute[0] / EM_PX if absolute else None


def font_root(value):
    relative = [length_px(number, unit, 0) for number, unit in numbers(value) if unit in ROOT_UNITS]
    return relative[0] / EM_PX if relative else None


def clips(value):
    return 0.0 if {"hidden", "clip"} & set(value.split()) else 1.0


def bounds(value):
    return 1.0 if value in ("auto", "none") else 0.0


# The share of a text that a reader sees comes from these properties. Opacity and shrinking multiply down the tree
# and across properties, so elements or properties that are each readable can together be too faint or too small.
# A growing element counts as no change. A box that clips and has a set height can hold its text below the box.
# A font size is a share of the default size.
FACTORS = {
    "opacity": {"opacity": fraction},
    "filter": {"filter": lambda value: math.prod(map(fraction, OPACITY_FILTER.findall(value)))},
    "transform": {"transform": lambda value: math.prod(map(scale_factor, SCALE.findall(value)))},
    "scale": {"scale": scale_factor},
    "zoom": {"zoom": fraction},
    **dict.fromkeys(("font-size", "font"), {"font-size": font_factor, "font-px": font_px, "font-root": font_root}),
    **dict.fromkeys(("overflow", "overflow-x", "overflow-y", "overflow-block", "overflow-inline"), {"overflow": clips}),
    **dict.fromkeys(("height", "max-height", "block-size", "max-block-size"), {"height": bounds}),
    "color": {"color": text_alpha},
    "-webkit-text-fill-color": {"fill": fill_alpha},
}


def merged(*found):
    # The lowest value of each property counts, across declarations and rules: a browser drops a value it cannot
    # parse, and only it knows which.
    factors = {}
    for group, factor in (item for each in found for item in each.items()):
        factors[group] = min(factor, factors.get(group, 1.0))
    return factors


FONT_GROUPS = ("font-size", "font-px", "font-root")
INHERITED_GROUPS = FONT_GROUPS + ("color", "fill")
ALL_GROUPS = {group: None for groups in FACTORS.values() for group in groups}


# "unset" and "revert" inherit a property that inherits. On any other property they set a value that shows text.
def inherits(group, value):
    return value == "inherit" or (value in ("unset", "revert", "revert-layer") and group in INHERITED_GROUPS)


def factors(css):
    found = {}
    for name, value in declarations(css):
        # "all" takes only keywords. Those that do not inherit reset each property to a value that shows text.
        for group, factor in (ALL_GROUPS if name == "all" else FACTORS.get(name, {})).items():
            if inherits(group, value):
                found[("inherit", group)] = 0.0
                continue
            if not factor:
                continue
            share = factor(value) if readable(value) else 0.0
            if share is not None:
                found[group] = min(max(share, 0.0), 1.0, found.get(group, 1.0))
    return found


def share(factors):
    if factors.get("overflow", 1.0) == 0 and factors.get("height", 1.0) == 0:
        return 0.0
    return math.prod(
        factor for group, factor in factors.items()
        if group not in ("overflow", "height", "font-size", "font-px", "font-root", "color", "fill")
    )


# The fill paints the text and hides its colour. A faint colour still counts, as the hides check also reads it.
def painted(level):
    presence, font, colour, fill, _ = level
    return presence * font * min(colour, fill)


def font_scale(factors, parent, root):
    sizes = {"font-size": parent * factors.get("font-size", 1), "font-px": factors.get("font-px"),
             "font-root": root * factors.get("font-root", 1)}
    return min((size for group, size in sizes.items() if group in factors), default=parent)


def target_factors(stylesheets):
    # Ancestors, pseudo-classes, and attributes are ignored: the sender controls all of them,
    # so the parser hides too much rather than too little. A hiding rule leaves nothing to see.
    found = defaultdict(dict)
    for stylesheet in stylesheets:
        for selectors, declarations in css_rules(stylesheet):
            hidden = hides(declarations)
            replacing, rule = (False, {}) if hidden else (replaces(declarations), factors(declarations))
            for target, pseudo_element in selector_targets(selectors) if hidden or replacing or rule else ():
                if hidden or (replacing and not pseudo_element):
                    found[target] = merged(found[target], {"hidden": 0.0})
                elif rule:
                    found[target] = merged(found[target], rule)
    if len(found) > MAX_HIDING_TARGETS:
        return {ANY_ELEMENT: merged(*found.values())}
    return dict(found)


# A browser reads a NUL in an attribute value or a stylesheet as U+FFFD, and so do the selectors that match it.
def without_nul(value):
    return value and value.replace("\x00", "\ufffd")


def first_values(attrs):
    # A browser keeps the first of duplicate attributes, so a later empty style cannot unhide the element.
    attributes = {}
    for name, value in attrs:
        attributes.setdefault(name, without_nul(value))
    return attributes


class _HtmlParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)

    def handle_startendtag(self, tag, attrs):
        # HTML ignores "/>" on elements that are not void: <div style="display:none"/> stays open,
        # and <style/> still starts a stylesheet. <plaintext/> still takes the rest of the input as text, and the tags
        # after <textarea/> are text up to its end tag.
        self.handle_starttag(tag, attrs)
        if tag in self.CDATA_CONTENT_ELEMENTS or tag == "plaintext":
            self.set_cdata_mode(tag)
        elif tag in self.RCDATA_CONTENT_ELEMENTS:
            self.set_cdata_mode(tag, escapable=True)


class _StyleCollector(_HtmlParser):
    def __init__(self):
        super().__init__()
        self.inside, self.stylesheets, self.templates = False, [], 0
        self.root_attributes = {tag: {} for tag in ROOT_TAGS}
        self.foreign, self.unresolved = False, False

    def handle_starttag(self, tag, attrs):
        self.inside = tag == "style"
        self.templates += tag == "template"
        self.foreign |= tag in FOREIGN_TAGS and not self.templates
        # A browser adds the attributes of each html or body tag to the one element, and the first value of each
        # wins. A late tag also styles the content before it. Inside a template it ignores these tags.
        if tag in ROOT_TAGS and not self.templates:
            for name, value in attrs:
                self.add_root_attribute(self.root_attributes[tag], name, value)

    def add_root_attribute(self, attributes, name, value):
        if name not in attributes:
            attributes[name] = value
        elif not self.foreign or attributes[name] == value:
            return
        # In svg or math such a tag makes a foreign element and leaves the root alone. The policy does not track
        # where foreign content ends, so it takes the styles of both tags, and the lowest value of each wins.
        elif name == "style":
            attributes[name] = f"{attributes[name] or ''};{value or ''}"
        elif name == "class":
            attributes[name] = f"{attributes[name] or ''} {value or ''}"
        else:
            self.unresolved = True

    def handle_endtag(self, tag):
        self.inside = False
        if tag == "template":
            self.templates = max(0, self.templates - 1)

    def handle_data(self, data):
        if self.inside:
            self.stylesheets.append(without_nul(data))


class _TextExtractor(_HtmlParser):
    def __init__(self, target_factors, root_attributes):
        super().__init__()
        self.target_factors = target_factors
        self.stack, self.hidden_depth, self.parts, self.anchors, self.anchor = [], 0, [], [], None
        self.positions, self.hidden_positions = defaultdict(list), []
        self.reopen_budget = MAX_REOPENED_SIZE
        self.form_open = False
        self.root_font = 1.0
        # A browser makes the html and body elements even when the mail omits their tags, and stylesheets match them.
        for tag in ROOT_TAGS:
            self.handle_starttag(tag, root_attributes[tag].items())

    def presence(self, tag, attributes, parent=None):
        classes = set(re.split(WHITESPACE, (attributes.get("class") or "").translate(ASCII_LOWER)))
        element_id = (attributes.get("id") or "").translate(ASCII_LOWER) or None
        styled = (
            found
            for (target_tag, target_id, target_classes), found in self.target_factors.items()
            if target_tag in (None, tag) and target_id in (None, element_id) and target_classes <= classes
        )
        inherited, parent_font, parent_colour, parent_fill, parent_own = parent or (
            self.stack[-1][3] if self.stack else (1.0, 1.0, 1.0, 1.0, {})
        )
        found = merged(factors(attributes.get("style") or ""), *styled)
        # "inherit" takes the value the parent itself sets, or for an inherited property the value it passes on. The
        # policy cannot tell which declaration comes last across rules, so the lower of that and a local value counts.
        inheriting = {key[1] for key in found if isinstance(key, tuple)}
        for group in inheriting:
            del found[("inherit", group)]
            if group not in INHERITED_GROUPS:
                found[group] = min(found.get(group, 1.0), parent_own.get(group, 1.0))
        font = font_scale(found, parent_font, self.root_font)
        if inheriting.intersection(FONT_GROUPS):
            font = min(font, parent_font)
        # The text colour and fill are inherited each on its own, and a value on the element replaces it.
        colour, fill = (
            min(found.get(group, parent), parent) if group in inheriting else found.get(group, parent)
            for group, parent in (("color", parent_colour), ("fill", parent_fill))
        )
        return inherited * share(found), font, colour, fill, found

    def hidden(self, tag, attributes, level):
        return (
            tag in SKIPPED_TAGS
            or (tag == "dialog" and "open" not in attributes)
            or (self.in_closed_details() and (tag != "summary" or self.stack[-1][2]["summary_shown"]))
            or "hidden" in attributes
            # A popover starts closed, and only a script or a click opens it.
            or "popover" in attributes
            or attributes.get("aria-hidden") == "true"
            or hides(attributes.get("style") or "")
            or replaces(attributes.get("style") or "")
            or painted(level) < FAINT
        )

    def handle_starttag(self, tag, attrs):
        ignored_form = tag == "form" and self.form_open and not self.positions["template"]
        if tag in PARAGRAPH_CLOSERS and not ignored_form:
            self.handle_endtag("p")
        if tag in BLOCK_TAGS and not self.hidden_depth:
            self.parts.append("\n")
        if tag in VOID_TAGS:
            return
        if tag in ROOT_TAGS and self.positions[tag]:
            return
        # A browser ignores a form start while another form is open, except inside a template.
        if ignored_form:
            return
        if tag == "form" and not self.positions["template"]:
            self.form_open = True
        attributes = first_values(attrs)
        level = self.presence(tag, attributes)
        hidden = self.hidden(tag, attributes, level)
        if tag == "html":
            self.root_font = level[1]
        if self.in_closed_details() and tag == "summary":
            self.stack[-1][2]["summary_shown"] = True
        details = {"summary_shown": False} if tag == "details" and "open" not in attributes else None
        if tag == "template" and {"shadowrootmode", "shadowroot"} & attributes.keys() and not self.hidden_depth:
            self.hide_shadow_host()
        self.positions[tag].append(len(self.stack))
        if hidden:
            self.hidden_positions.append(len(self.stack))
        self.stack.append((tag, hidden, details, level, (len(self.parts), len(self.anchors)), attributes))
        self.hidden_depth += hidden
        if tag == "a" and not self.hidden_depth:
            # A browser closes an open anchor when the next one starts.
            self.finish_anchor()
            self.anchor = (attributes.get("href") or "", [])

    # A declarative shadow root replaces the content of its host, and its slots can show any part of that content.
    # The policy does not evaluate slots, so the host and its earlier content are hidden.
    def hide_shadow_host(self):
        host, _, details, level, (parts, anchors), attributes = self.stack[-1]
        del self.parts[parts:], self.anchors[anchors:]
        self.anchor = None
        self.stack[-1] = (host, True, details, level, (parts, anchors), attributes)
        self.hidden_positions.append(len(self.stack) - 1)
        self.hidden_depth += 1

    def handle_endtag(self, tag):
        # The end tag of a form lets a later form start, also when the browser ignores the end tag itself.
        if tag == "form" and not self.positions["template"]:
            self.form_open = False
        # Content after the end tag of html or body still goes into body.
        if not self.positions[tag] or tag in ROOT_TAGS:
            return
        # A browser ignores some end tags that would close other open elements. Which ones depends on the
        # element kinds, so an end tag that would close a hidden element on the way is ignored here.
        if self.hidden_positions and self.hidden_positions[-1] > self.positions[tag][-1]:
            return
        # A browser also ignores an end tag whose element is outside the scope that a table cell or similar opens.
        if tag in TABLE_TAGS:
            barriers = TABLE_SCOPE
        elif tag in SPECIAL:
            barriers = SCOPE | EXTRA_BARRIERS.get(tag, set())
        else:
            barriers = SPECIAL
        opened = self.positions[tag][-1]
        if any(found and found[-1] > opened for found in map(self.positions.get, barriers)):
            return
        # A browser takes only the form out of the open elements, and those inside it stay open. To keep the form
        # open as well can only hide more.
        if tag == "form" and self.stack[-1][0] != "form":
            return
        # A browser opens again each formatting element that the end tag of another element closes, so the text
        # after it still takes its styles. This stops at the start of a table cell or similar.
        reopened = []
        while self.stack:
            open_tag, hidden, _, _, _, attributes = self.stack.pop()
            self.positions[open_tag].pop()
            if hidden:
                self.hidden_positions.pop()
            self.hidden_depth -= hidden
            if open_tag == "a":
                self.finish_anchor()
            if open_tag == tag:
                break
            if open_tag in FORMATTING_MARKERS:
                reopened.clear()
            elif open_tag in FORMATTING_TAGS:
                reopened.append((open_tag, attributes))
        if tag in BLOCK_TAGS:
            self.parts.append("\n")
        if tag in FORMATTING_MARKERS:
            reopened.clear()
        if not reopened or self.reopen_budget < 0:
            return
        self.reopen_budget -= sum(
            max(REOPENED_ELEMENT_SIZE, sum(len(name) + len(value or "") for name, value in attributes.items()))
            for _, attributes in reopened
        )
        if self.reopen_budget < 0:
            # The policy cannot tell the styles of the rest of the mail without that work, so it withholds it.
            self.hidden_depth += 1
            return
        for open_tag, attributes in reversed(reopened):
            self.handle_starttag(open_tag, attributes.items())

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
        # After "<!--<script" a browser keeps the script open past the next "</script>". HTMLParser ends it there,
        # and only the script escape states tell which later text is still script, so the rest of the mail is hidden.
        comment = data.find("<!--")
        if self.stack and self.stack[-1][0] == "script" and comment >= 0 and "<script" in data[comment:].lower():
            self.hidden_depth += 1
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
    # The policy cannot tell which element holds this id or other attribute, so it withholds the mail.
    if styles.unresolved:
        return "", []
    extractor = _TextExtractor(target_factors(styles.stylesheets), styles.root_attributes)
    extractor.feed(html)
    extractor.close()
    return "".join(extractor.parts), extractor.anchors
