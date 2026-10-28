"""The login form stays above the fold at desktop and mobile viewport sizes."""
import pytest
from playwright.sync_api import expect


@pytest.mark.parametrize("width,height", [
    (1440, 900), (1366, 768), (1280, 720),
    (1024, 768), (390, 844), (375, 667),
])
def test_login_fields_and_submit_are_visible_without_scroll(pg_browser, tmp_path, width, height):
    page,base,workspace=pg_browser
    root=tmp_path
    page.context.clear_cookies()
    page.set_viewport_size({"width": width, "height": height})
    page.goto(base + "/")
    expect(page.locator("#authform")).to_be_visible()
    page.evaluate("document.fonts.ready")
    for selector in ('input[name="username"]', 'input[name="password"]', '.auth-submit'):
        bounds = page.locator(selector).bounding_box()
        assert bounds is not None, selector
        assert 0 <= bounds["x"] and bounds["x"] + bounds["width"] <= width, (selector, bounds)
        assert 0 <= bounds["y"] and bounds["y"] + bounds["height"] <= height, (selector, bounds)
    expect(page.locator(".auth-submit")).to_have_text("Sign in")
    assert page.evaluate("window.scrollY === 0 && document.documentElement.scrollWidth <= window.innerWidth")
    copy = page.locator(".auth-copy").bounding_box()
    card = page.locator(".auth-card").bounding_box()
    if width > 800:
        assert copy["x"] + copy["width"] <= card["x"]
        assert page.locator(".auth-copy").evaluate("e => getComputedStyle(e).gridRowStart") == "1"
        assert page.locator(".auth-card").evaluate("e => getComputedStyle(e).gridRowStart") == "1"
    else:
        assert card["y"] + card["height"] <= copy["y"]
    page.screenshot(path=str(root / f"login-{width}x{height}.png"))
    page.get_by_role("button", name="Create account", exact=True).click()
    expect(page.locator("#authtitle")).to_have_text("Create your account")
    expect(page.locator('input[name="password"]')).to_have_attribute("autocomplete", "new-password")
    page.get_by_role("button", name="Sign in", exact=True).first.click()
    expect(page.locator("#authtitle")).to_have_text("Sign in")
    expect(page.locator('input[name="password"]')).to_have_attribute("autocomplete", "current-password")
