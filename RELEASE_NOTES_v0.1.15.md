# Donovan Agent v0.1.15

This patch fixes Browser Companion setup on Windows.

## Fixed

- Donovan no longer opens `edge://extensions/`, `chrome://extensions/`, or similar browser pages through Windows' generic URL handler.
- Setup now launches the actual browser executable when possible, such as `msedge`, `chrome`, `firefox`, `brave`, `vivaldi`, or `opera`.
- This prevents the Windows "Get an app to open this link" popup during `/browser companion setup edge`.
- If Donovan cannot find the browser executable, it prints the extension page to open manually instead of triggering the popup.
- Playwright browser automation now runs on a dedicated worker thread so `browser_open` cannot crash the terminal prompt loop with `RuntimeError: loop ... is not the running loop` on Windows.
- Browser Companion setup guidance now avoids using `browser_open` just to open the browser extension page.
- Browser windows now come forward while Donovan is actively working in them and minimize again automatically when the browser work is finished.
- Browser Companion supports focus/minimize behavior through the extension, so already-open browser tabs behave the same way.

## Verification

- Full test suite passes with `pytest -q`: `438 passed`.
