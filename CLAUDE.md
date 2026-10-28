# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A fork of `badger/home`, the GitHub Universe 2026 badge firmware tree (`origin` is `jamesduffy/badger`, `upstream` is `badger/home`), plus seven apps the owner wrote: `tilt_maze`, `ir_tag`, `gh_dash`, `aprs_map`, `aprs_beacon`, `overhead`, `meshcore_feed`. Every other folder in `badge/apps/` is upstream, leave it alone. `README.md` is the fork README, the original is `UPSTREAM_README.md`.

Work in `badge/` (Universe 2026). `badge25/` is the 2025 badge, never copy its APIs. Before changing an app read `badge/AGENTS.md`. Read `hardware/README.md` before touching GPIO, IR, I2C, ADC or power, and `hardware/USB_SERIAL.md` before talking to a connected badge. `.github/skills/hyb26-skill/` has helper scripts and API references.

## Commands

Run from the repo root. There is no build step and no linter config.

```bash
python3 -I editor/tests/test_<app>.py                      # one app's host tests (stdlib unittest)
python3 -I editor/tests/test_<app>.py Class.test_name      # a single test
python3 -m py_compile badge/apps/<app>/__init__.py
python3 .github/skills/hyb26-skill/scripts/validate_app.py badge/apps/<app>   # structure and API checks
python3 .github/scripts/validate_app_readmes.py badge/apps                    # CI check: README first sentence
```

Test files exist for all seven apps. A new app needs `__init__.py`, a 24x24 or larger square `icon.png`, and a `README.md` whose first sentence is 10 to 200 characters ending in punctuation.

## How the apps are structured

- Each app is one file with module-level code, an `update()` function and `run(update)`. The firmware injects `badge`, `screen`, `image`, `font`, `color`, `shape`, `vec2`, `rect`, `run`, `reset` and the `BUTTON_*` constants as globals, so the files cannot be imported on a host.
- `editor/<app>.py` is a copy of `badge/apps/<app>/__init__.py` for the browser editor and must stay byte-identical. After editing an app, `cp` it to `editor/`. Some tests assert this.
- Two test styles, both in `editor/tests/`. Most tests exec the whole app with mock globals. `aprs_map` and `aprs_beacon` keep their parsing and protocol code between `# ---- pure logic start` and `# ---- pure logic end` markers, and the tests exec only that block. Keep badge globals out of that block.
- Settings come from `/secrets.py` on the badge's own filesystem via `import secrets` and `getattr`. `badge/secrets.py` is an empty template and must stay empty. The settings each app reads are in the README table.
- `aprs_map`, `aprs_beacon` and `overhead` default to the venue coordinates and take `MAP_LAT` and `MAP_LON` as overrides. Each falls back to demo data when there is no network.
- Calls that block (`requests.get` takes about a second, DNS lookups block) must be preceded by a drawn frame with a message and `display.update()`. Without that the screen stays blank until the call returns. `overhead` had this bug.
- IR uses `pimoroni/aye_arr` 1.0.x: `NECSender(pin, pio, sm)` then `.start()`, and `NECRemoteReceiver` (falls back to `NECReceiver`). The owner's badge runs MicroPython 1.29.0-preview (2026-10-13) with those classes. Its `bluetooth.BLE` has no `gap_pair`, so `meshcore_feed` live mode almost certainly cannot pair. Demo mode works.
- `aprs_map` is receive only (login `BADGE`, passcode `-1`). `aprs_beacon` sends a position to APRS-IS under the owner's own callsign. Keep its guards: valid callsign, passcode set and not `-1`, a confirm screen before every send, automatic mode off at start, 10 minute floor. Do not run it against a real server or with real credentials from an agent session. Its tests use a fake socket.

## Simulator limits

The browser editor and `.github/extensions/badge-simulator` run the apps in a WASM MicroPython. The IMU is a stub, there is no IR, BLE or sockets, `wifi` is a stub, and `requests` is synchronous XHR. The Copilot canvas does not exist in Claude Code. Screenshots in `docs/screenshots/` came from a headless Chrome harness kept outside the repo (`../shots`, may not exist). Simulator results never prove hardware behavior.

## Deploying to a badge

- Find it with `mpremote devs` (USB `2e8a:1101`). Run one `mpremote` command at a time. `/system` is read-only over serial.
- Install through USB disk mode: `mpremote connect <port> exec "import powman; powman.reset_into_msc()"` (the serial error afterwards is expected). The `BADGER` volume root is the badge's `/system`, so apps go in `apps/` on the volume. Finish with eject and `mpremote connect <port> reset`.
- The stock menu ignores `icon.png`. If `'load_picture' in open('/system/apps/menu/app.py').read()` prints `False`, copy `badge/apps/menu/app.py` over it in the same session.
- Changing the badge needs the owner's go-ahead each time. Reads are fine.
- The owner's Mac is device-managed: macOS mounts the volume read-only and `sudo` is blocked. Use another computer or an Android phone. `../badge-install.zip` (outside the repo, contains a Wi-Fi password) is the bundle for that.

## Git

- Commits are SSH-signed through 1Password. If signing fails with "failed to fill whole buffer", ask the owner to unlock 1Password. Do not turn signing off.
- Use the GitHub noreply identity, not the work email in the git config: set `GIT_AUTHOR_NAME`, `GIT_COMMITTER_NAME`, `GIT_AUTHOR_EMAIL` and `GIT_COMMITTER_EMAIL` to `<id>+jamesduffy@users.noreply.github.com` (`gh api user --jq .id`).
- One commit per app. Push with `git push origin main`, never force, and only when asked.
- `editor/tests/data/aprs_is_capture.txt` (real APRS packets with other people's callsigns and positions) is excluded through `.git/info/exclude` and must not be committed. The test skips when it is missing.
- Upstream's PR template asks for no third-party characters, art, logos or trademarks.
