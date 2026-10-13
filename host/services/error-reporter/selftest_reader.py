#!/usr/bin/env python3
"""Hold `not_a_reader()` to the two things it has to get right at once.

The filter decides whether a report describes a person using the site, so it
must catch every crawler and headless client and it must never catch a browser.
Those pull in opposite directions and the whole rule is one regex, which makes
it easy to widen it for one crawler and quietly start dropping a reader: the
token "bot" is one character away from CUBOT, which is a phone somebody browses
on.

So the corpus below is written as two lists and both halves are the test.
CRAWLERS must be caught. READERS must not. A change that satisfies one and not
the other is not an improvement.

    python3 selftest_reader.py

Loads the regex out of `error_reporter.py` itself rather than importing it,
because importing it reads a config file and starts a server.
"""

import re
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent / "error_reporter.py"


def load():
    """The real `not_a_reader()` and its regex, lifted out of the shipped file."""
    src = SOURCE.read_text()
    fragment = src[src.index("NOT_A_READER = re.compile("):src.index("def origin_allowed(")]
    namespace = {"re": re}
    exec(compile(fragment, str(SOURCE), "exec"), namespace)
    return namespace["not_a_reader"]


# Every one of these fetched a site and is not somebody reading it. The first
# three are verbatim from the tickets that prompted the filter.
CRAWLERS = [
    ("Applebot, which filed #555 and took a release",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Safari/605.1.15 "
     "(Applebot/0.1; +http://www.apple.com/go/applebot)"),
    ("headless Chrome, which filed #556 and #557 two seconds apart",
     "Mozilla/5.0 (X11; CrOS x86_64 14541.0.0) AppleWebKit/537.36 "
     "(KHTML, like Gecko) HeadlessChrome/122.0.0.0 Safari/537.36"),
    ("Googlebot, which filed #554",
     "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"),
    ("Bingbot", "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"),
    ("YandexBot", "Mozilla/5.0 (compatible; YandexBot/3.0; +http://yandex.com/bots)"),
    ("DuckDuckBot", "DuckDuckBot/1.1; (+http://duckduckgo.com/duckduckbot.html)"),
    ("PetalBot, which closes on a semicolon rather than a slash",
     "Mozilla/5.0 (compatible; PetalBot;+https://webmaster.petalsearch.com/site/petalbot)"),
    ("AhrefsBot", "Mozilla/5.0 (compatible; AhrefsBot/7.0; +http://ahrefs.com/robot/)"),
    ("SemrushBot", "Mozilla/5.0 (compatible; SemrushBot/7~bl; +http://www.semrush.com/bot.html)"),
    ("Baiduspider, which says spider rather than bot",
     "Mozilla/5.0 (compatible; Baiduspider/2.0; +http://www.baidu.com/search/spider.html)"),
    ("Yahoo Slurp, which says neither", "Mozilla/5.0 (compatible; Yahoo! Slurp)"),
    ("Facebook's link preview fetcher", "facebookexternalhit/1.1"),
    ("Lighthouse, which is a real Chrome nobody is watching",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Chrome-Lighthouse"),
    ("a bare name ending in bot", "SomeIndexerBot"),
    ("curl", "curl/8.4.0"),
    ("wget", "Wget/1.21.4"),
    ("python", "python-requests/2.31.0"),
    ("a Go client", "Go-http-client/2.0"),
]

# Every one of these is somebody looking at the site. None may be caught.
READERS = [
    ("iPhone Chrome, from #553",
     "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6_1 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) CriOS/152.0.7977.64 Mobile/15E148 Safari/604.1"),
    ("Windows Chrome, from #550",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/142.0.7444.162 Safari/537.36"),
    ("Mac Safari, from #545",
     "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/26.5 Safari/605.1.15"),
    ("Windows Chrome, from #533",
     "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"),
    ("a CUBOT phone, which carries the token this rule is named after",
     "Mozilla/5.0 (Linux; Android 10; CUBOT NOTE 20 Build/QP1A.190711.020) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"),
    ("the same phone written with an underscore",
     "Mozilla/5.0 (Linux; Android 11; CUBOT_X30 Build/RP1A.200720.011) "
     "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"),
    ("Firefox", "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0"),
    ("Edge", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0"),
    ("Android Chrome", "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"),
    ("iPad Safari", "Mozilla/5.0 (iPad; CPU OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
     "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1"),
    ("a caller that sent none at all, which passes the way a missing Origin does", ""),
    ("a caller that sent nothing at all", None),
]

failures = []


def check(ok, what):
    print("  %s  %s" % ("ok  " if ok else "FAIL", what))
    if not ok:
        failures.append(what)


def main():
    not_a_reader = load()

    print("a crawler is caught:")
    for what, agent in CRAWLERS:
        check(not_a_reader(agent), what)

    print()
    print("a reader is left alone:")
    for what, agent in READERS:
        check(not not_a_reader(agent), what)

    print()
    if failures:
        print("%d failure(s)" % len(failures))
        return 1
    print("all clear")
    return 0


if __name__ == "__main__":
    sys.exit(main())
