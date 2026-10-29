"""
Browser Automation & Headless Web Agent Module for Memorae AI
Powered by Playwright async engine.
Enables autonomous web navigation, price checks, snapshot extraction, and ordering flows.
"""

import asyncio
import os
import re
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Browser, Page

_BROWSER_INSTANCE: Optional[Browser] = None
_PLAYWRIGHT_CONTEXT = None

async def get_browser() -> Browser:
    """Singleton async browser instance to optimize launch overhead."""
    global _BROWSER_INSTANCE, _PLAYWRIGHT_CONTEXT
    if _BROWSER_INSTANCE is None or not _BROWSER_INSTANCE.is_connected():
        _PLAYWRIGHT_CONTEXT = await async_playwright().start()
        _BROWSER_INSTANCE = await _PLAYWRIGHT_CONTEXT.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
            ]
        )
    return _BROWSER_INSTANCE

async def browse_and_extract_content(url: str, wait_for_selector: Optional[str] = None, timeout_ms: int = 25000) -> Dict[str, Any]:
    """
    Navigates to any target URL, waits for dynamic content/JavaScript to hydrate,
    and extracts clean text, meta-tags, and interactive elements.
    """
    browser = await get_browser()
    page: Page = await browser.new_page(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        viewport={"width": 1280, "height": 800}
    )
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if wait_for_selector:
            try:
                await page.wait_for_selector(wait_for_selector, timeout=8000)
            except Exception:
                pass
        else:
            await page.wait_for_timeout(2000)

        title = await page.title()
        
        # Extract meaningful visible text
        body_text = await page.evaluate("""() => {
            const scripts = document.querySelectorAll('script, style, noscript, nav, footer, svg');
            scripts.forEach(s => s.remove());
            return document.body.innerText || '';
        }""")
        
        # Clean text
        clean_text = re.sub(r'\\s+', ' ', body_text).strip()[:4000]

        return {
            "status": "success",
            "url": page.url,
            "title": title,
            "status_code": response.status if response else 200,
            "content_snippet": clean_text
        }
    except Exception as e:
        return {
            "status": "error",
            "url": url,
            "message": f"Browser navigation error: {str(e)}"
        }
    finally:
        await page.close()

async def search_and_compare_products(query: str, site: str = "amazon") -> Dict[str, Any]:
    """
    Autonomous product search and price extractor using headless browser.
    """
    search_url = f"https://www.google.com/search?q={query.replace(' ', '+')}+{site}+india"
    res = await browse_and_extract_content(search_url)
    return res
