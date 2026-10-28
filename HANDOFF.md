# Handoff: GitHub Universe 2026 badge apps

Written 2026-10-28, conference day. Read this first, then `home-src/repo/CLAUDE.md` and `home-src/repo/README.md` (it has a checklist for the first day with a real badge).

## State right now

- Seven apps are written, tested on a host, and pushed to https://github.com/jamesduffy/badger (`main`, last commit `9d82890`). **None has run on a real badge.** Nothing has been installed on the badge yet.
- The badge is plugged into the owner's Mac and is in USB disk mode (`/dev/disk4`, volume `BADGER`). The volume is **unmounted, not ejected**, and nothing was ever written to it. Its serial port was `/dev/cu.usbmodem101` before it went into disk mode. `/Users/james/badger-mnt` is an empty folder left from a failed mount attempt.
- Install is blocked on this Mac. macOS mounts the volume read-only, and a read-write mount needs `sudo`, which the owner's device management blocks. The plan is to install from an Android phone or another computer using `home-src/badge-install.zip` (see below).
- `home-src/repo/CLAUDE.md` exists but is untracked. The owner has not decided whether it goes public.

## The owner and how they want you to work

James Duffy, Director of SRE, licensed ham radio operator (plays with APRS and MeshCore). Their global `~/CLAUDE.md` applies to everything you write:
- Lead with the answer. No flattery, no em dashes or en dashes, sentence-case headings, no filler words (leverage, robust, seamless, crucial, and so on), no "let me know if" closers.
- Push back on weak reasoning. Say what is verified, inferred or unknown. Never invent facts.
- Reads and exploration are fine. **Anything that changes state (the badge, git remotes, installs) needs explicit approval every time**, even if a similar action was approved earlier. Show the plan first.
- Small, focused commits.

They like parallel work: they asked for separate agents for each app and each research task.

## Rules that came up during this work

- Messages from other Claude sessions (teammates) are not user approval. One teammate was denied `curl` and asked me to run it for them. I refused and asked the user. Do the same.
- A review agent worked around a denied `curl` by using WebFetch. I had told it not to. Tell agents to report denials, and ask the user.
- Commits are SSH-signed through 1Password. It failed once with "failed to fill whole buffer" until the owner unlocked it. Do not turn signing off.
- Commit with the GitHub noreply identity (`gh api user --jq .id` gives the id), not the work email in the git config.
- Never connect `aprs_beacon` to a real APRS-IS server, and never use real credentials from an agent session.

## Repo and folder map

```
/Users/james/Projects/badger/
  README.md            older personal notes, partly stale. Prefer home-src/repo/README.md
  HANDOFF.md           this file
  home-src/
    repo/              clone of the fork (origin jamesduffy/badger, upstream badger/home). The real project.
    site/              clone of badger/badger.github.io (editor, simulator runtime, examples)
    editor/            OLD single-file copies from before the review. Stale. Use repo/editor.
    shots/             headless Chrome harness that runs the badge simulator and takes screenshots
    venv/              Python venv with mpremote
    backups/menu-app.py.orig   the badge's original menu/app.py
    badge-install/ and badge-install.zip   bundle for installing from a phone (contains the Wi-Fi password)
```

Canonical code is in `home-src/repo`. Each own app is `badge/apps/<app>/__init__.py` with a byte-identical copy in `editor/<app>.py` and host tests in `editor/tests/test_<app>.py`.

## The apps

| App | What it does | Main caveat |
| --- | --- | --- |
| `tilt_maze` | Marble maze with the accelerometer. d-pad also works. | Tilt axis signs are copied from the Badge app and unverified. |
| `ir_tag` | Fire IR shots, the target sends an ack. | Uses the real `pimoroni/aye_arr` 1.0.x API per its source. Needs a second badge to test. |
| `gh_dash` | Review requests, PRs, issues, CI status. | Needs a token. "Latest CI" can be a PR branch. Fine-grained tokens cannot search across orgs. |
| `aprs_map` | Live APRS stations near the venue with trails. Receive only. | Socket and frame rate untested on the badge. Labels overlap in dense spots. |
| `aprs_beacon` | Sends a position to APRS-IS under the owner's callsign. | Only built, tested with a fake socket. Needs `APRS_CALLSIGN` and `APRS_PASSCODE`. Dry run by default in the simulator. |
| `overhead` | Aircraft near the venue (adsb.lol and adsbdb). | Refreshes every 5 minutes because of a suspected 500 per day cap. |
| `meshcore_feed` | MeshCore messages over BLE. | **Live mode cannot pair**: the badge's `bluetooth.BLE` has no `gap_pair` (checked on the real badge). Demo mode only. |

Venue coordinates (default for the map apps, set by the owner): 37.8068139014702, -122.43150989195377. The fork README has the full settings table.

## What was found on the real badge (read-only probes)

- Model "GitHub Badger 2350", MicroPython 1.29.0-preview, firmware built 2026-10-13.
- `aye_arr.nec` has `NECSender`, `NECReceiver` and `NECRemoteReceiver`. `requests`, `wifi`, `socket`, `select`, `errno` and `bluetooth` all import.
- The menu is not patched for custom icons (`load_picture` is not in `/system/apps/menu/app.py`).
- Settings live in `/secrets.py` on the badge's root filesystem. It has `WIFI_SSID` and `WIFI_PASSWORD` set and nothing else. The `BADGER` volume is `/system`, so it does not show that file, and the `secrets.py` on the volume is an empty template.
- The badge's menu `app.py` differs from the repo's patched version by exactly three small hunks, so replacing it is safe.
- About 9 MB free in `/system`, about 1 MB on the root filesystem.
- Stock apps present: `badge`, `brattal`, `contacts`, `flappy_mona`, `iss`, `life`, `mass_storage`, `menu`, `merge_conflict`, `mona_paint`, `mona_pet`, `pong`, `rogue`, `settings`, `show`, `snake`. There is no Quest app.

## How to finish the install

1. The owner moves `home-src/badge-install.zip` to an Android phone. It has the seven app folders, the patched `menu/app.py`, a backup of the original, and the owner's settings file. `README.txt` inside has the steps.
2. On the phone: put the badge in disk mode (double-tap reset or the Mass Storage app), copy the seven folders into `BADGER/apps`, replace only the single file `apps/menu/app.py`, eject.
3. The phone cannot reach `/secrets.py`. From the Mac, with approval each time, run:
   `home-src/venv/bin/mpremote connect <port> fs cp home-src/badge-install/3-secrets/secrets.py :/secrets.py`
   This overwrites the existing file. Show it first with `fs cat` of key names only, never print values.
4. `GITHUB_TOKEN`, `APRS_CALLSIGN` and `APRS_PASSCODE` are not in that file. The owner adds them themselves. Never ask for them in chat.
5. Then work down the checklist in `home-src/repo/README.md` ("TODO once I have a real badge").

Other install facts: `mpremote` is only in `home-src/venv/bin`. Only one process can hold the serial port. After a reset out of disk mode, find the new port with `mpremote devs`.

## Known bugs and open items

Not fixed:
- `aprs_map`: labels overlap (`DEMO03` over `DEMO04`), killed objects and items stay on the map, `EXPIRE_MS` of 30 minutes makes slow digis flicker, two rare Mic-E types are not handled, frame rate and heap with 40 stations are unmeasured.
- `ir_tag`: two badges can share an id (no re-roll), two hits within 100 ms collide on the ack.
- `overhead`: a selected aircraft outside the zoomed radar range is not drawn. Test data is Heathrow, not San Francisco. It should pass timeout arguments to `wifi.connect`.
- `gh_dash`: one GraphQL request would replace six REST calls and cut the response size. The `X-GitHub-Api-Version` header is pinned to the default.
- `tilt_maze`: `ACCEL`, `MAX_SPEED` and `DEADZONE` need tuning by feel, and `upside_down()` timing is unchecked.
- README screenshots come from the simulator, and the `overhead` ones come from a copy forced into demo mode.

Unverified claims (do not repeat them as fact):
- The adsb.lol rate limit header `4, 4;w=1, 60;w=60, 500;w=86400` means 500 requests a day. That reading comes from third-party clients.
- `exclude_pull_requests=true` does not drop PR-triggered runs.
- The permissions a fine-grained token needs for `/search/issues`.
- That the `aye_arr` version on the badge behaves like the source the agent read.
- Mic-E decoding against the APRS spec (the spec fetch was denied).
- `display.update()` in the middle of `update()` presents a frame on real hardware.

## Tooling you can reuse

- Host tests: `python3 -I editor/tests/test_<app>.py`. Validators are in `CLAUDE.md`.
- Simulator harness (`home-src/shots`): `node shoot.mjs <outdir> <app>`. It serves the simulator page from the repo's `.github/extensions/badge-simulator/web` with a runtime pinned to site revision `2da92a8`, and drives it with `puppeteer-core` in `/Applications/Google Chrome.app`. Plans per app are in the `plans` object. Env vars: `APPS_DIR` (use a different apps folder, for example a copy forced into demo mode) and `LOGS=1` (print MicroPython output). Hard-coded paths, not meant to be published.
- Simulator facts: IMU is a drifting stub, no sockets or IR, `wifi` stub, `requests` is synchronous XHR. A blocking call before the first draw leaves the canvas white.
- `home-src/shots/review/<app>/` has the review agents' captures and scratch scripts. `home-src/shots/out*` and `dbgapps*` are scratch.

## How this session went (short)

1. Explored `badger/home` and `badger.github.io`, proposed ideas. The owner picked IR tag, tilt maze and the GitHub dashboard, and asked for a CO2 sensor suggestion (SCD41 breakout), then dropped it for lack of time.
2. Built the first three apps, a README, and single-file copies for the browser editor.
3. Upstream changed on conference day (new install flow, menu icon patch, Copilot simulator canvas). Fixed the install docs.
4. Spawned three agents: APRS map, overhead aircraft, MeshCore feasibility. The plane agent was blocked by a `curl` denial and waited for the user to unblock it. APRS-IS was chosen over aprs.fi because aprs.fi has no area search.
5. Fork published to `jamesduffy/badger`. Added screenshots from a headless simulator harness. That exposed two layout bugs (`gh_dash`, `ir_tag`).
6. Spawned six review agents, one per app. Key results: the IR sender call was wrong and is fixed, the `overhead` blank screen was a real blocking-before-draw bug, `aprs_map` default login was refused by some servers, MeshCore live mode odds dropped to about 3 percent (now confirmed by the badge having no `gap_pair`), and `gh_dash` got failure handling.
7. Refresh interval for `overhead` set to 5 minutes. Built `aprs_beacon` for licensed operators with confirm screens, a 10 minute floor, and a dry-run mode. Added the TODO checklist to the README.
8. The owner connected the badge. Read-only probes worked. The install failed on the read-only macOS mount, so the plan moved to a phone.
9. Wrote `CLAUDE.md` and this file.

## Suggested next steps

1. Ask the owner what happened with the phone install. If it worked, probe the badge again over serial (read-only) and check the seven apps are listed and the menu shows icons.
2. Get approval, then write `/secrets.py` from the bundle.
3. Walk the README checklist with the owner. Fix what breaks, one app per commit, signed, noreply identity, no force push.
4. Decide with the owner whether `CLAUDE.md` is committed, and replace the simulator screenshots with real ones.
