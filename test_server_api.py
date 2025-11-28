"""Test SelfAI Server API endpoints."""
import asyncio
import subprocess
import time
import sys
from playwright.async_api import async_playwright


async def test_server_dashboard():
    """Test the dashboard served by the HTTP server."""
    # Start the server in background
    print("Starting server...")
    server_proc = subprocess.Popen(
        [sys.executable, '-m', 'selfai', 'serve', '8788'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait for server to start
    time.sleep(2)

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()

            # Capture console messages
            console_msgs = []
            page.on('console', lambda msg: console_msgs.append(f"{msg.type}: {msg.text}"))

            print("\n=== Server Dashboard Test ===\n")

            # Test 1: Load dashboard from server
            print("1. Loading dashboard from server...")
            try:
                await page.goto('http://localhost:8788/', timeout=5000)
                await page.wait_for_load_state('networkidle')
                title = await page.title()
                print(f"   PASS: Page loaded - {title}")
            except Exception as e:
                print(f"   FAIL: Could not load page - {e}")
                return False

            # Test 2: Check approve buttons exist
            approve_buttons = await page.query_selector_all('.btn-approve')
            print(f"2. Found {len(approve_buttons)} approve buttons")

            if not approve_buttons:
                print("   No tasks in plan_review status to test")
                await browser.close()
                return True

            # Test 3: Test approve button - should make API call
            print("3. Testing approve button with API call...")

            # Get the first task ID from the button's onclick
            btn = approve_buttons[0]
            onclick = await btn.get_attribute('onclick')
            # Extract task ID from "approvePlan(56)"
            task_id = onclick.split('(')[1].split(')')[0]
            print(f"   Task ID: {task_id}")

            # Set up dialog handler to accept confirm
            page.on('dialog', lambda d: asyncio.create_task(d.accept()))

            # Click approve
            await btn.click()

            # Wait for API response and page reload
            await page.wait_for_timeout(2000)

            # Check console for success/error
            api_success = any('approved' in msg.lower() for msg in console_msgs)
            api_error = any('error' in msg.lower() or 'failed' in msg.lower() for msg in console_msgs)

            print(f"   Console messages: {console_msgs[-5:] if console_msgs else 'none'}")

            # Test 4: Verify task status changed via API
            print("4. Verifying task status via API...")
            response = await page.evaluate('''
                async () => {
                    try {
                        const resp = await fetch('/api/task/''' + task_id + '''');
                        return await resp.json();
                    } catch (e) {
                        return {error: e.message};
                    }
                }
            ''')

            if response.get('error'):
                print(f"   FAIL: API error - {response['error']}")
            else:
                status = response.get('status', 'unknown')
                print(f"   Task status: {status}")
                if status == 'approved':
                    print("   PASS: Task was approved successfully!")
                elif status == 'plan_review':
                    print("   FAIL: Task still in plan_review - approve didn't work")
                else:
                    print(f"   INFO: Task has different status: {status}")

            # Test 5: Check toast notification appeared
            print("5. Checking for toast notification...")
            toasts = await page.query_selector_all('div[style*="position:fixed"]')
            if toasts:
                for toast in toasts:
                    text = await toast.inner_text()
                    print(f"   Toast: {text}")
            else:
                print("   No toast visible (may have already disappeared)")

            await browser.close()
            print("\n=== Test Complete ===")
            return True

    finally:
        # Kill server
        print("\nStopping server...")
        server_proc.terminate()
        server_proc.wait()


if __name__ == '__main__':
    asyncio.run(test_server_dashboard())
