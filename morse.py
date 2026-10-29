"""morse — generate and play Morse code tones using only the standard library.

Quick start:

    import morse
    morse.play("SOS")                      # play through the speakers
    morse.play_feed("https://news.ycombinator.com/rss", limit=5)
    morse.stop()                           # from another thread or a UI button
    morse.encode("Hello")                  # '.... . .-.. .-.. ---'
    morse.decode(".... ..")                # 'HI'
    morse.save_wav("CQ CQ", "cq.wav")      # write a WAV file

Timing follows the PARIS standard: one "unit" lasts 1.2 / wpm seconds.
A dot is 1 unit, a dash 3, the gap inside a character 1, between
characters 3, and between words 7. Passing ``farnsworth_wpm`` stretches
the gaps between characters and words while keeping the characters
themselves at full speed, which is the usual way to learn.

Playback works in a terminal (Linux, macOS, Windows) and in Pythonista on
iOS. This module has no user interface; see morse_cli.py and
morse_pythonista.py for those.
"""

from __future__ import annotations

import io
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import wave
import xml.etree.ElementTree as ET

__all__ = ["MORSE_CODE", "encode", "decode", "generate", "to_wav_bytes",
           "save_wav", "play", "play_feed", "fetch_titles", "stop", "is_url"]

MORSE_CODE = {
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".", "F": "..-.",
    "G": "--.", "H": "....", "I": "..", "J": ".---", "K": "-.-", "L": ".-..",
    "M": "--", "N": "-.", "O": "---", "P": ".--.", "Q": "--.-", "R": ".-.",
    "S": "...", "T": "-", "U": "..-", "V": "...-", "W": ".--", "X": "-..-",
    "Y": "-.--", "Z": "--..",
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.", "!": "-.-.--",
    "/": "-..-.", "(": "-.--.", ")": "-.--.-", "&": ".-...", ":": "---...",
    ";": "-.-.-.", "=": "-...-", "+": ".-.-.", "-": "-....-", "_": "..--.-",
    '"': ".-..-.", "$": "...-..-", "@": ".--.-.",
}
_REVERSE = {code: char for char, code in MORSE_CODE.items()}

SAMPLE_RATE = 44100

# Set by stop(); checked while generating and playing.
_stop_event = threading.Event()


# --- Text <-> Morse ---------------------------------------------------------

def encode(text: str, *, strict: bool = False) -> str:
    """Convert text to Morse. Letters are separated by spaces, words by ' / '.

    Characters with no Morse equivalent are skipped, or raise ValueError
    when ``strict`` is true.
    """
    words = []
    for word in text.upper().split():
        letters = []
        for ch in word:
            if ch in MORSE_CODE:
                letters.append(MORSE_CODE[ch])
            elif strict:
                raise ValueError(f"No Morse code for {ch!r}")
        if letters:
            words.append(" ".join(letters))
    return " / ".join(words)


def decode(code: str) -> str:
    """Convert Morse (letters split by spaces, words by '/') back to text.

    Unknown sequences become '?'.
    """
    words = []
    for word in code.strip().split("/"):
        letters = [_REVERSE.get(sym, "?") for sym in word.split()]
        if letters:
            words.append("".join(letters))
    return " ".join(words)


# --- Audio generation -------------------------------------------------------

def _timeline(code: str, wpm: float, farnsworth_wpm: float | None):
    """Yield (is_tone, seconds) pairs for a Morse string."""
    unit = 1.2 / wpm
    # Farnsworth: stretch the inter-character and inter-word gaps only.
    if farnsworth_wpm and farnsworth_wpm < wpm:
        gap_unit = (60 / farnsworth_wpm - 31 * unit) / 19
    else:
        gap_unit = unit

    words = [w.split() for w in code.strip().split("/") if w.split()]
    for wi, letters in enumerate(words):
        if wi:
            yield False, 7 * gap_unit
        for li, letter in enumerate(letters):
            if li:
                yield False, 3 * gap_unit
            for si, sym in enumerate(letter):
                if si:
                    yield False, unit
                if sym == ".":
                    yield True, unit
                elif sym == "-":
                    yield True, 3 * unit
                else:
                    raise ValueError(f"Invalid Morse symbol {sym!r}")


def _tone(n: int, amp: float, step: float, ramp: int) -> bytes:
    """n samples of sine wave with a short fade in and out (avoids clicks)."""
    r = min(ramp, n // 2)
    samples = []
    for i in range(n):
        env = 1.0
        if i < r:
            env = i / r
        elif i >= n - r:
            env = (n - 1 - i) / r
        samples.append(int(amp * env * math.sin(step * i)))
    return struct.pack(f"<{n}h", *samples)


def generate(text: str, *, wpm: float = 20, freq: float = 600,
             volume: float = 0.5, farnsworth_wpm: float | None = None,
             sample_rate: int = SAMPLE_RATE, is_code: bool = False) -> bytes:
    """Return 16-bit mono PCM samples for ``text``.

    Set ``is_code=True`` to pass a ready-made Morse string ('... --- ...')
    instead of plain text.
    """
    if wpm <= 0:
        raise ValueError("wpm must be positive")
    code = text if is_code else encode(text)
    amp = max(0.0, min(1.0, volume)) * 32767
    ramp = int(sample_rate * 0.005)  # 5 ms
    step = 2 * math.pi * freq / sample_rate

    # There are only two tone lengths (dot and dash), so build each once.
    tones: dict[int, bytes] = {}
    out = bytearray()
    for is_tone, seconds in _timeline(code, wpm, farnsworth_wpm):
        n = int(round(seconds * sample_rate))
        if is_tone:
            if n not in tones:
                tones[n] = _tone(n, amp, step, ramp)
            out += tones[n]
        else:
            out += bytes(2 * n)
    # Trailing silence so players don't cut off the last tone.
    out += bytes(2 * int(sample_rate * 0.1))
    return bytes(out)


def to_wav_bytes(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV container."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buf.getvalue()


def save_wav(text: str, path: str, **kwargs) -> str:
    """Write ``text`` as Morse audio to a WAV file. Takes the same options as generate()."""
    rate = kwargs.get("sample_rate", SAMPLE_RATE)
    with open(path, "wb") as f:
        f.write(to_wav_bytes(generate(text, **kwargs), rate))
    return path


# --- Playback backends ------------------------------------------------------
#
# Each backend starts playing a WAV file and returns two functions:
# is_done() and halt(). _play_wav_file() polls is_done() while watching
# for stop(), and always calls halt() on the way out, including on Ctrl-C
# or Pythonista's stop button (both raise KeyboardInterrupt).

def _start_pythonista(path, seconds):
    import sound
    player = sound.Player(path)
    player.play()
    started = time.monotonic()
    return (lambda: time.monotonic() - started >= seconds), player.stop


def _start_winsound(path, seconds):
    import winsound
    winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC)
    started = time.monotonic()
    return ((lambda: time.monotonic() - started >= seconds),
            lambda: winsound.PlaySound(None, 0))


def _start_subprocess(path, seconds):
    if sys.platform == "darwin":
        players = [["afplay"]]
    else:
        players = [["paplay"], ["pw-play"], ["aplay", "-q"],
                   ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]]
    for cmd in players:
        if shutil.which(cmd[0]):
            proc = subprocess.Popen(cmd + [path])
            break
    else:
        raise RuntimeError("No audio player found (tried: "
                           + ", ".join(c[0] for c in players) + ")")

    def halt():
        if proc.poll() is None:
            proc.terminate()
            proc.wait()
    return (lambda: proc.poll() is not None), halt


def _in_pythonista() -> bool:
    try:
        import sound
    except ImportError:
        return False
    return hasattr(sound, "Player")


def _play_wav_file(path: str, seconds: float) -> bool:
    """Play a WAV file, blocking until it ends. Returns False if stopped."""
    if _in_pythonista():
        start = _start_pythonista
    elif sys.platform == "win32":
        start = _start_winsound
    else:
        start = _start_subprocess
    is_done, halt = start(path, seconds)
    try:
        while not is_done():
            if _stop_event.wait(0.05):
                return False
        return True
    finally:
        halt()


def _play(text: str, **kwargs) -> bool:
    """Play one piece of text without resetting the stop flag."""
    if _stop_event.is_set():
        return False
    rate = kwargs.get("sample_rate", SAMPLE_RATE)
    pcm = generate(text, **kwargs)
    if _stop_event.is_set():   # stop() pressed while generating
        return False
    fd, path = tempfile.mkstemp(suffix=".wav")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(to_wav_bytes(pcm, rate))
        return _play_wav_file(path, len(pcm) / 2 / rate)
    finally:
        os.remove(path)


# --- Public playback API ----------------------------------------------------

def stop() -> None:
    """Stop whatever play() or play_feed() is doing, as soon as possible.

    Safe to call from any thread, such as a UI button handler. The
    interrupted call returns False (play) or the number of items finished
    (play_feed).
    """
    _stop_event.set()


def is_url(text: str) -> bool:
    """True if text looks like an http(s) URL."""
    parsed = urllib.parse.urlparse(text.strip())
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def play(text: str, **kwargs) -> bool:
    """Play ``text`` as Morse through the speakers. Blocks until done.

    If ``text`` is an http(s) URL, it's treated as an RSS feed and passed to
    play_feed(). Otherwise takes the same options as generate().

    Returns True if it played to the end, False if stop() interrupted it.
    """
    if is_url(text):
        play_feed(text, **kwargs)
        return not _stop_event.is_set()
    _stop_event.clear()
    return _play(text, **kwargs)


def fetch_titles(url: str, limit: int | None = None) -> list[str]:
    """Return the item titles from an RSS feed."""
    with urllib.request.urlopen(url, timeout=15) as resp:
        root = ET.parse(resp).getroot()
    titles = [t for t in (item.findtext("title") for item in root.iter("item")) if t]
    return titles[:limit] if limit else titles


def play_feed(url: str, *, limit: int | None = None, on_item=None,
              pause: float = 2.0, **kwargs) -> int:
    """Play each title in an RSS feed as Morse. Blocks until done or stopped.

    ``on_item(index, total, title)`` is called before each title plays,
    which is handy for showing progress. ``pause`` is the silence in
    seconds between titles. Other options go to generate().
    Returns the number of titles that played to the end.
    """
    _stop_event.clear()
    titles = fetch_titles(url, limit)
    played = 0
    for i, title in enumerate(titles):
        if i and _stop_event.wait(pause):
            break
        if on_item:
            on_item(i, len(titles), title)
        if not _play(title, **kwargs):
            break
        played += 1
    return played
