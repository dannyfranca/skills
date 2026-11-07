from common import *

TITLE = "Buckets"

KEY = YELLOW_C
BUCKET = BLUE_C


class Buckets(Narrated):
    def construct(self):
        heading = self.chapter("1. Buckets")
        buckets = VGroup(*[
            Node(str(i), color=BUCKET, w=1.4, h=0.8, state="empty") for i in range(5)
        ]).arrange(RIGHT, buff=0.35).shift(DOWN * 0.4)
        self.play(LaggedStart(*[FadeIn(b, shift=UP * 0.2) for b in buckets], lag_ratio=0.15))
        self.say(r"Start with five empty buckets, numbered zero to four.")

        key = tag("ada", KEY, 22).move_to(UP * 1.6 + LEFT * 2)
        rule = MathTex(r"\mathrm{hash}(\texttt{ada}) \bmod 5 = 3", color=GREY_A).next_to(key, RIGHT, 0.5)
        self.play(FadeIn(key, shift=DOWN * 0.2))
        self.play(Write(rule))
        self.say(
            r"A hash function turns the key into a number. The remainder after division by five picks the bucket.",
            wait=0.4,
        )

        others = VGroup(*[b for i, b in enumerate(buckets) if i != 3])
        self.play(key.animate.next_to(buckets[3], UP, 0.2), others.animate.fade(0.7))
        self.play(buckets[3].to("1 key", KEY))
        ring = halo(buckets[3])
        self.play(Create(ring))
        self.say(
            r"To find \texttt{ada} again, compute the same number and look in one bucket only.",
            spoken="To find Ada again, compute the same number and look in one bucket only.",
            wait=1.0,
        )
        self.clear_all()
