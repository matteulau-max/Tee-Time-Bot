"""
Inspect the live EZLinks booking page and dump all interactive elements.
Run once to identify the correct selectors for ezlinks_booker.py.

Usage:
    python tools/inspect_selectors.py
"""

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

URL = "https://marineparkridepp.ezlinksgolf.com/index.html#!/search"
OUT = Path("selector_dump.json")


async def main() -> None:
    async with async_playwright() as pw:
        # Use the pre-installed Chromium binary
        import shutil, os
        chrome_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH") or shutil.which("chromium") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        browser = await pw.chromium.launch(headless=True, executable_path=chrome_path)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            ignore_https_errors=True,
        )
        page = await context.new_page()

        print(f"Navigating to {URL} ...")
        await page.goto(URL, wait_until="networkidle", timeout=30_000)
        # Give Angular extra time to bootstrap
        await asyncio.sleep(4)

        dump = await page.evaluate("""() => {
            function attrs(el) {
                const result = {};
                for (const a of el.attributes) result[a.name] = a.value;
                return result;
            }

            return {
                url: location.href,
                title: document.title,
                inputs: Array.from(document.querySelectorAll('input')).map(el => ({
                    tag: 'input',
                    id: el.id, name: el.name, type: el.type,
                    placeholder: el.placeholder, value: el.value,
                    visible: el.offsetParent !== null,
                    attrs: attrs(el),
                })),
                selects: Array.from(document.querySelectorAll('select')).map(el => ({
                    tag: 'select',
                    id: el.id, name: el.name,
                    visible: el.offsetParent !== null,
                    options: Array.from(el.options).map(o => o.text),
                    attrs: attrs(el),
                })),
                buttons: Array.from(document.querySelectorAll('button, input[type=submit], a[ng-click]')).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    id: el.id, text: el.innerText.trim().slice(0, 80),
                    classes: el.className,
                    visible: el.offsetParent !== null,
                    attrs: attrs(el),
                })),
                iframes: Array.from(document.querySelectorAll('iframe')).map(el => ({
                    src: el.src, id: el.id, title: el.title,
                })),
                ngControllers: Array.from(document.querySelectorAll('[ng-controller]')).map(el => ({
                    controller: el.getAttribute('ng-controller'),
                    tag: el.tagName,
                })),
                angularModels: Array.from(document.querySelectorAll('[ng-model]')).map(el => ({
                    model: el.getAttribute('ng-model'),
                    tag: el.tagName, id: el.id, type: el.type || '',
                    visible: el.offsetParent !== null,
                })),
            };
        }""")

        OUT.write_text(json.dumps(dump, indent=2))
        print(f"Saved to {OUT}")

        # Print a quick summary
        print(f"\nURL:      {dump['url']}")
        print(f"Title:    {dump['title']}")
        print(f"Inputs:   {len(dump['inputs'])}")
        print(f"Selects:  {len(dump['selects'])}")
        print(f"Buttons:  {len(dump['buttons'])}")
        print(f"iframes:  {len(dump['iframes'])}")
        print(f"ng-model: {len(dump['angularModels'])}")

        print("\n--- ng-model bindings ---")
        for m in dump["angularModels"]:
            print(f"  [{m['tag']}] model={m['model']}  id={m['id']}  type={m['type']}  visible={m['visible']}")

        print("\n--- Buttons ---")
        for b in dump["buttons"]:
            if b["text"]:
                print(f"  [{b['tag']}] '{b['text']}'  id={b['id']}  visible={b['visible']}")

        print("\n--- Inputs ---")
        for i in dump["inputs"]:
            print(f"  type={i['type']}  id={i['id']}  name={i['name']}  placeholder={i['placeholder']!r}  visible={i['visible']}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
