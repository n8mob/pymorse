"""Pythonista front end for morse.py: big Play / Stop / Repeat buttons.

Put this next to morse.py in Pythonista and run it. Each headline is shown
only after it finishes playing, so you can check your copy. Closing the
window also stops the audio.
"""

import threading

import ui
from objc_util import on_main_thread

import morse

FEED_URL = "https://news.ycombinator.com/rss"
LIMIT = 10
WPM_CHOICES = [10, 15, 20, 25]
SPACING_CHOICES = ["Normal", "10 wpm", "5 wpm"]   # Farnsworth spacing
FREQ = 600


class MorsePlayer(ui.View):
    def __init__(self):
        self.name = "Morse Feed"
        self.background_color = "white"
        self.last_title = None
        self.worker = None
        self.stopped = False

        self.wpm = ui.SegmentedControl(segments=[str(w) for w in WPM_CHOICES])
        self.wpm.selected_index = 1
        self.spacing = ui.SegmentedControl(segments=SPACING_CHOICES)
        self.spacing.selected_index = 1

        self.status = self._label(18, "Ready")
        self.reveal = self._label(22, "")

        self.play_btn = self._button("Play feed", "#2e7d32", self.play_feed)
        self.repeat_btn = self._button("Repeat", "#1565c0", self.repeat)
        self.stop_btn = self._button("STOP", "#c62828", self.stop)

        for v in (self.wpm, self.spacing, self.status, self.reveal,
                  self.play_btn, self.repeat_btn, self.stop_btn):
            self.add_subview(v)
        self._set_busy(False)

    def _label(self, size, text):
        return ui.Label(text=text, font=("<system>", size),
                        alignment=ui.ALIGN_CENTER, number_of_lines=0)

    def _button(self, title, color, action):
        return ui.Button(title=title, action=action, background_color=color,
                         tint_color="white", corner_radius=16,
                         font=("<system-bold>", 28))

    def layout(self):
        pad, w = 16, self.width - 32
        y = pad
        for ctl in (self.wpm, self.spacing):
            ctl.frame = (pad, y, w, 36)
            y += 48
        self.status.frame = (pad, y, w, 30)
        y += 36
        self.reveal.frame = (pad, y, w, 120)
        y += 132
        # Buttons fill the rest; Stop gets the most room.
        h = self.height - y - pad
        half = (w - pad) / 2
        self.play_btn.frame = (pad, y, half, h * 0.35)
        self.repeat_btn.frame = (pad * 2 + half, y, half, h * 0.35)
        self.stop_btn.frame = (pad, y + h * 0.35 + pad, w, h * 0.65 - pad)

    def will_close(self):
        morse.stop()

    # --- settings ---

    def _options(self):
        wpm = WPM_CHOICES[self.wpm.selected_index]
        fw = [None, 10, 5][self.spacing.selected_index]
        return dict(wpm=wpm, farnsworth_wpm=fw, freq=FREQ)

    # --- UI updates (always on the main thread) ---

    @on_main_thread
    def _set_busy(self, busy):
        self.play_btn.enabled = not busy
        self.repeat_btn.enabled = not busy and self.last_title is not None
        self.stop_btn.enabled = busy
        for b in (self.play_btn, self.repeat_btn, self.stop_btn):
            b.alpha = 1.0 if b.enabled else 0.4

    @on_main_thread
    def _show(self, status=None, reveal=None):
        if status is not None:
            self.status.text = status
        if reveal is not None:
            self.reveal.text = reveal

    # --- actions ---

    def _run(self, job):
        """Run playback in a background thread so the buttons stay live."""
        if self.worker and self.worker.is_alive():
            return
        self.stopped = False
        self._set_busy(True)

        def target():
            try:
                job()
            except Exception as e:
                self._show(status=f"Error: {e}")
            finally:
                self._set_busy(False)
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def play_feed(self, sender):
        opts = self._options()

        def job():
            self._show(status="Fetching feed…", reveal="")

            def on_item(i, total, title):
                if self.last_title:
                    self._show(reveal=self.last_title)
                self.last_title = title
                self._show(status=f"Playing {i + 1} of {total}")
            morse.play_feed(FEED_URL, limit=LIMIT, on_item=on_item, **opts)
            if not self.stopped:
                self._show(status="Finished", reveal=self.last_title or "")
        self._run(job)

    def repeat(self, sender):
        if not self.last_title:
            return
        opts, title = self._options(), self.last_title

        def job():
            self._show(status="Repeating…", reveal="")
            if morse.play(title, **opts):
                self._show(status="Ready", reveal=title)
        self._run(job)

    def stop(self, sender):
        self.stopped = True
        morse.stop()
        self._show(status="Stopped", reveal=self.last_title or "")


if __name__ == "__main__":
    MorsePlayer().present("fullscreen")
