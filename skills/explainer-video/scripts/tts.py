import hashlib
import os
import re
from pathlib import Path

import soundfile as sf

HOME = Path(os.environ.get("EXPLAINER_HOME", Path.home() / ".cache" / "explainer-video"))
CACHE = HOME / "tts-cache"
VOICE = os.environ.get("EXPLAINER_VOICE", "af_heart")
SAMPLE_RATE = 24000


def plain(tex):
    """Caption TeX to the words a narrator reads."""
    s = tex.replace(r"$\to$", " to ").replace(r"$\Rightarrow$", " so ")
    s = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", s)
    s = s.replace(r"\\", " ").replace(r"\_", " ").replace(r"\%", " percent").replace("$", "")
    return re.sub(r"\s+", " ", s).strip()


_kokoro = None


def _synth(text):
    global _kokoro
    if _kokoro is None:
        from kokoro_onnx import Kokoro
        _kokoro = Kokoro(str(HOME / "kokoro" / "kokoro-v1.0.onnx"), str(HOME / "kokoro" / "voices-v1.0.bin"))
    samples, sr = _kokoro.create(text, voice=VOICE, speed=1.0, lang="en-us")
    assert sr == SAMPLE_RATE
    return samples


def speak(text):
    """Returns (wav_path, seconds). Cached per voice and text, shared by every video."""
    CACHE.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{VOICE}|{text}".encode()).hexdigest()[:20]
    path = CACHE / f"{digest}.wav"
    if not path.exists():
        # Scenes render in parallel processes; the rename keeps a half-written file out of reach.
        tmp = path.with_suffix(f".{os.getpid()}.tmp.wav")
        sf.write(tmp, _synth(text), SAMPLE_RATE)
        tmp.rename(path)
    return str(path), sf.info(path).duration
