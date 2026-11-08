from common import *

TITLE = "Why hash maps are fast"


class Title(Narrated):
    def construct(self):
        title = sans("Why hash maps are fast", 56, weight=BOLD)
        sub = sans("Find one name among a million", 28, GREY_B).next_to(title, DOWN, 0.4)
        self.play(Write(title), run_time=1.2)
        self.play(FadeIn(sub, shift=UP * 0.2))
        self.voice("You have a million names. How do you find one of them without reading them all?")
        self.clear_all()
