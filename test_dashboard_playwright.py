"""Playwright test for SelfAI Dashboard."""
import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

async def test_dashboard():
    dashboard_path = Path.cwd() / '.selfai_data' / 'dashboard.html'

    if not dashboard_path.exists():
        print("ERROR: Dashboard file not found!")
        return False

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Capture dialog events (for prompt())
        dialog_messages = []
        page.on('dialog', lambda dialog: handle_dialog(dialog, dialog_messages))

        # Load the dashboard
        await page.goto(f'file://{dashboard_path}')
        await page.wait_for_load_state('networkidle')

        print("=== Dashboard Test Results ===\n")

        # Test 1: Check if page loaded
        title = await page.title()
        print(f"1. Page title: {title}")

        # Test 2: Check for double-escaped content in HTML source
        content = await page.content()
        # Check specifically in plan-cell text, not in JSON
        cells = await page.query_selector_all('.plan-cell')
        has_escape_issue = False
        for cell in cells[:3]:
            text = await cell.inner_text()
            if '\\n' in text or '\\\\' in text:
                has_escape_issue = True
                print(f"2. FAIL: Escaped content in cell: {text[:80]}...")
                break
        if not has_escape_issue:
            print("2. PASS: No escaped content in plan previews")

        # Test 3: Check approve buttons exist
        approve_buttons = await page.query_selector_all('.btn-approve')
        print(f"3. Found {len(approve_buttons)} approve buttons")

        # Test 4: Test approve button click (now uses prompt())
        if approve_buttons:
            btn = approve_buttons[0]
            btn_text = await btn.inner_text()
            print(f"4. Testing approve button: '{btn_text}'")

            await btn.click()
            await page.wait_for_timeout(300)

            # Check if prompt dialog was shown
            if dialog_messages:
                print(f"   PASS: Prompt shown with: '{dialog_messages[-1]}'")
            else:
                print("   FAIL: No prompt dialog shown")

        # Test 5: Check View Plan button and modal
        view_buttons = await page.query_selector_all('.btn-view')
        print(f"5. Found {len(view_buttons)} View Plan buttons")

        if view_buttons:
            btn = view_buttons[0]
            await btn.click()
            await page.wait_for_timeout(300)

            # Check modal is visible
            modal = await page.query_selector('#planModal')
            if modal:
                display = await modal.evaluate('el => getComputedStyle(el).display')
                print(f"   Modal display after click: {display}")

                if display == 'flex':
                    # Check plan content
                    plan_content = await page.query_selector('#planContent')
                    if plan_content:
                        text = await plan_content.inner_text()
                        if len(text) > 10:
                            print(f"   PASS: Plan content displays correctly")
                            print(f"   First 150 chars: {text[:150]}...")
                        else:
                            print(f"   FAIL: Plan content is empty or too short")
                else:
                    print(f"   FAIL: Modal not visible (display: {display})")

        # Test 6: Check for JavaScript errors
        errors = []
        def on_console(msg):
            if msg.type == 'error':
                errors.append(msg.text)
        page.on('console', on_console)
        await page.reload()
        await page.wait_for_timeout(500)
        if errors:
            print(f"6. FAIL: JavaScript errors: {errors}")
        else:
            print("6. PASS: No JavaScript errors")

        await browser.close()
        print("\n=== Test Complete ===")
        return True

async def handle_dialog(dialog, messages):
    messages.append(dialog.message)
    await dialog.accept()

if __name__ == '__main__':
    asyncio.run(test_dashboard())
