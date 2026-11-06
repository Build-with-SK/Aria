"""Exercise local voice synthesis and a fresh headless Chromium process."""
import asyncio
import json
from pathlib import Path
import sys


async def browser_check():
    from playwright.async_api import async_playwright
    async with async_playwright() as runtime:
        browser = await runtime.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto("about:blank")
        await browser.close()
    print(json.dumps({"headless_chromium": "started and closed"}), flush=True)


def main():
    sys.path.insert(0, str(Path.home() / "apps/sentinel"))
    from sentinel import speech
    engine = speech.load()
    samples, rate = engine.create("The server is ready.", voice="bm_george", lang="en-gb")
    if len(samples) < rate / 2:
        raise RuntimeError("Voice synthesis returned too little audio")
    print(json.dumps({"voice": "bm_george", "sample_rate": rate,
                      "audio_seconds": round(len(samples) / rate, 2)}), flush=True)
    asyncio.run(browser_check())


if __name__ == "__main__":
    main()
