"""Command-line front end for morse.py. Press Ctrl-C to stop.

    python morse_cli.py "SOS" --wpm 20 --freq 700
    python morse_cli.py "CQ CQ" --out cq.wav
    python morse_cli.py https://news.ycombinator.com/rss --limit 5 --farnsworth 10
"""

import argparse

import morse


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="Play text or an RSS feed as Morse code.")
    p.add_argument("text", help="text to send, Morse with --code, or an RSS feed URL")
    p.add_argument("--wpm", type=float, default=15, help="speed in words per minute (default 15)")
    p.add_argument("--farnsworth", type=float, metavar="WPM", help="effective speed for spacing")
    p.add_argument("--freq", type=float, default=720, help="tone frequency in Hz (default 720)")
    p.add_argument("--volume", type=float, default=0.5, help="0.0 to 1.0 (default 0.5)")
    p.add_argument("--code", action="store_true", help="input is already Morse")
    p.add_argument("--limit", type=int, help="for feeds: play at most this many titles")
    p.add_argument("--hide", action="store_true", help="for feeds: show each title after it plays, not before")
    p.add_argument("--out", metavar="FILE", help="write a WAV file instead of playing")
    a = p.parse_args(argv)

    opts = dict(wpm=a.wpm, freq=a.freq, volume=a.volume, farnsworth_wpm=a.farnsworth)
    try:
        if morse.is_url(a.text):
            def on_item(i, total, title):
                if a.hide:
                    if i:
                        print(f"   {on_item.last}")
                    print(f"{i + 1}/{total}: ...")
                else:
                    print(f"{i + 1}/{total}: {title}")
                on_item.last = title
            on_item.last = None
            morse.play_feed(a.text, limit=a.limit, on_item=on_item, **opts)
            if a.hide and on_item.last:
                print(f"   {on_item.last}")
        elif a.out:
            morse.save_wav(a.text, a.out, is_code=a.code, **opts)
            print(f"Wrote {a.out}")
        else:
            print(a.text if a.code else morse.encode(a.text))
            morse.play(a.text, is_code=a.code, **opts)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
