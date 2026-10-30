"""The pages in a real browser, with the kit: `make preview SHOTS=<dir>`.

Serves the preview pages (`/pages`, written by `make preview`) and the kit's
built files (`/kit`, a meridian-ui checkout's dist/) on loopback, the kit at
the path the dashboard serves it at, /.meridian/ui/<version>/, and opens each
page in headless Chromium (Playwright) at the three widths every page is held
to, 375, 582 and 1280 pixels. For each it checks that the kit's stylesheet and
script loaded and its components upgraded (om-status, om-pager), that the
page fits one screen by the kit's own overflow check (lib/fit.js,
fitProblems), and that a row's detail opens within the screen; and keeps a
screenshot of each in /out. Prints one line per check and exits non-zero if
any failed.

Run in mcr.microsoft.com/playwright/python, as core's browser runs are.
"""

import functools
import http.server
import os
import re
import sys
import threading

from playwright.sync_api import sync_playwright

PAGES = "/pages"
KIT = "/kit"
OUT = "/out"
SIZES = [("phone", 375, 812), ("narrow", 582, 900), ("desktop", 1280, 800)]
FIT = """async () => {
  const kit = document.querySelector('link[rel=stylesheet][href$="/meridian.css"]');
  const fit = await import(kit.href.replace(/meridian\\.css$/, "lib/fit.js"));
  return fit.fitProblems(fit.measureFit());
}"""
failures = []


def check(held, said):
    print(f"{'ok' if held else 'FAILED'}: {said}", flush=True)
    if not held:
        failures.append(said)


class Handler(http.server.SimpleHTTPRequestHandler):
    def translate_path(self, path):
        path = path.split("?", 1)[0]
        if path.startswith("/.meridian/ui/"):
            return os.path.join(KIT, path[len("/.meridian/ui/"):])
        return os.path.join(PAGES, path.lstrip("/"))

    def log_message(self, *_args):
        pass


def main():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 8765), functools.partial(Handler))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    pages = sorted(name for name in os.listdir(PAGES) if name.endswith(".html"))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name in pages:
            text = open(os.path.join(PAGES, name), encoding="utf-8").read()
            version = re.search(r'/\.meridian/ui/([^/]+)/meridian\.css', text)
            check(version and os.path.isdir(os.path.join(KIT, version.group(1))),
                  f"{name} links a kit this checkout has ({version.group(1) if version else 'none'})")
            for size, width, height in SIZES:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:8765/{name}")
                page.wait_for_load_state("networkidle")
                styled = page.evaluate(
                    "() => [...document.styleSheets].some(s => (s.href || '').endsWith('/meridian.css') && s.cssRules.length > 0)")
                check(styled, f"{name} at {size}: the kit's stylesheet loaded")
                for component in ("om-status", "om-pager"):
                    if page.locator(component).count():
                        upgraded = page.evaluate(
                            f"() => !!customElements.get('{component}') && document.querySelectorAll('{component}:not(:defined)').length === 0")
                        check(upgraded, f"{name} at {size}: {component} upgraded")
                if page.locator("om-pager").count():
                    shown = page.evaluate("() => document.querySelector('om-pager').shown")
                    check(bool(shown) and shown["last"] >= shown["first"],
                          f"{name} at {size}: om-pager pages the rows ({shown})")
                problems = page.evaluate(FIT)
                check(not problems, f"{name} at {size} fits one screen" + (": " + "; ".join(problems) if problems else ""))
                wide = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                check(wide <= 0, f"{name} at {size}: no horizontal scroll ({wide} px over)")
                check(not errors, f"{name} at {size}: no script error" + (f": {errors}" if errors else ""))
                page.screenshot(path=os.path.join(OUT, f"{name[:-5]}-{size}.png"))
                summary = page.locator("details.row-detail summary").first
                if summary.count() and summary.is_visible():
                    summary.click()
                    pop = page.locator("details.row-detail[open] .row-detail-pop").first
                    box = pop.bounding_box() if pop.count() else None
                    inside = bool(box) and box["x"] >= 0 and box["y"] >= 0 and \
                        box["x"] + box["width"] <= width + 1 and box["y"] + box["height"] <= height + 1
                    check(inside, f"{name} at {size}: a row's detail opens within the screen ({box})")
                    page.screenshot(path=os.path.join(OUT, f"{name[:-5]}-{size}-detail.png"))
                context.close()
        browser.close()
    server.shutdown()
    print(f"browser: {'FAILED' if failures else 'OK'}, screenshots in {OUT}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
