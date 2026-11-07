import os

from manim import *

from tts import plain, speak

BG = "#171717"
config.background_color = BG

MONO = "DejaVu Sans Mono"
SANS = "Fira Sans"

CAPTIONS = os.environ.get("EXPLAINER_CAPTIONS", "on") != "off"


def mono(s, fs=22, color=WHITE, **kw):
    return Text(s, font=MONO, font_size=fs, color=color, **kw)


def sans(s, fs=24, color=WHITE, **kw):
    return Text(s, font=SANS, font_size=fs, color=color, **kw)


def tag(label, color=GREY_B, fs=15):
    t = mono(label, fs, color)
    r = RoundedRectangle(
        corner_radius=0.1, width=t.width + 0.26, height=0.3,
        stroke_color=color, stroke_width=1.5, fill_color=BG, fill_opacity=1,
    )
    t.move_to(r)
    return VGroup(r, t)


def character(color, label=None, r=0.42):
    head = Circle(radius=r, fill_color=color, fill_opacity=1, stroke_width=0)
    eyes = VGroup()
    for x in (-1, 1):
        white = Circle(radius=r * 0.26, fill_color=WHITE, fill_opacity=1, stroke_width=0)
        pupil = Dot(radius=r * 0.12, color=BLACK).shift(RIGHT * r * 0.06 + UP * r * 0.03)
        eyes.add(VGroup(white, pupil).shift(x * RIGHT * r * 0.38 + UP * r * 0.22))
    g = VGroup(head, eyes)
    if label:
        g.add(sans(label, 22, color).next_to(head, DOWN, 0.15))
    return g


def card(name, color=GREY_B, w=2.4, h=0.55, fs=18):
    body = RoundedRectangle(
        corner_radius=0.08, width=w, height=h, stroke_color=color, stroke_width=2,
        fill_color=color, fill_opacity=0.12,
    )
    label = mono(name, fs).move_to(body)
    if label.width > w - 0.2:
        label.scale_to_fit_width(w - 0.2)
    return VGroup(body, label)


def folder(label, color=GREY_B, w=2.8, h=1.7):
    body = RoundedRectangle(
        corner_radius=0.12, width=w, height=h, stroke_color=color,
        fill_color=color, fill_opacity=0.12,
    )
    flap = RoundedRectangle(
        corner_radius=0.06, width=w * 0.4, height=0.32, stroke_color=color,
        fill_color=color, fill_opacity=0.2,
    )
    flap.next_to(body, UP, buff=-0.06).align_to(body, LEFT).shift(RIGHT * 0.15)
    t = sans(label, 24).move_to(body)
    return VGroup(flap, body, t)


def lock(color=GOLD_C, s=1.0):
    body = RoundedRectangle(
        corner_radius=0.04, width=0.34, height=0.27, fill_color=color,
        fill_opacity=1, stroke_width=0,
    )
    shackle = Arc(radius=0.11, start_angle=0, angle=PI, stroke_color=color, stroke_width=5)
    shackle.next_to(body, UP, buff=-0.01)
    return VGroup(shackle, body).scale(s)


def check(color=GREEN_C, s=1.0):
    return Tex(r"\checkmark", color=color).scale(s)


def cross(color=RED_C, s=1.0):
    return MathTex(r"\times", color=color).scale(s)


class Node(VGroup):
    """A titled box with an optional subtitle and a state tag on its bottom edge."""

    def __init__(self, title, subtitle=None, color=BLUE_C, w=1.95, h=0.9, state=None, state_color=GREY_B):
        super().__init__()
        self.box = RoundedRectangle(
            corner_radius=0.12, width=w, height=h, stroke_color=color,
            stroke_width=3, fill_color=color, fill_opacity=0.1,
        )
        self.title = mono(title, 24, weight=BOLD).move_to(self.box)
        self.add(self.box, self.title)
        if subtitle:
            self.title.shift(UP * 0.15)
            self.subtitle = sans(subtitle, 17, GREY_A).next_to(self.title, DOWN, 0.06)
            self.add(self.subtitle)
        if state:
            self.tag = tag(state, state_color).move_to(self.box.get_bottom())
            self.add(self.tag)

    def to(self, state, color=GREY_B):
        return Transform(self.tag, tag(state, color).move_to(self.tag))


def halo(mob, color=YELLOW):
    target = mob.box if isinstance(mob, Node) else mob
    return SurroundingRectangle(target, color=color, buff=0.12, corner_radius=0.18, stroke_width=4)


class Narrated(Scene):
    """Scene whose captions are spoken. A new line waits until the previous one finished,
    so animations play under the voice and two lines never overlap."""

    caption = None
    speech_end = 0.0

    def voice(self, spoken):
        self.await_speech()
        path, dur = speak(spoken)
        self.add_sound(path)
        self.speech_end = self.time + dur + 0.25

    def await_speech(self):
        gap = self.speech_end - self.time
        if gap > 0.01:
            self.wait(gap)

    def say(self, text, wait=0.6, run_time=0.7, spoken=None):
        self.voice(spoken or plain(text))
        if not CAPTIONS:
            if wait:
                self.wait(wait)
            return
        new = Tex(text, font_size=34, color=GREY_A).to_edge(DOWN, buff=0.35)
        if new.width > 13.2:
            new.scale_to_fit_width(13.2)
        anims = [FadeIn(new, shift=UP * 0.12)]
        if self.caption is not None:
            anims.append(FadeOut(self.caption, shift=UP * 0.12))
        self.play(*anims, run_time=run_time)
        self.caption = new
        if wait:
            self.wait(wait)

    def unsay(self):
        if self.caption is not None:
            self.play(FadeOut(self.caption), run_time=0.4)
            self.caption = None

    def chapter(self, text):
        h = Tex(text, font_size=44).to_corner(UL, buff=0.45)
        line = Line(LEFT, RIGHT, color=GREY_C, stroke_width=2).match_width(h).next_to(h, DOWN, 0.1).align_to(h, LEFT)
        self.play(Write(h), Create(line), run_time=1.0)
        return VGroup(h, line)

    def clear_all(self, run_time=0.8):
        self.await_speech()
        mobs = list(self.mobjects)
        if mobs:
            self.play(*[FadeOut(m) for m in mobs], run_time=run_time)
        self.caption = None

    def tear_down(self):
        self.await_speech()
        super().tear_down()
