# Handoff – Personal AI Assistant

Last updated: 2026-10-04, ~12:45 AM ET. Read this plus `.cursor/rules/*.mdc` before doing anything, and keep it updated when you finish something.

**Hackathon demo: Sunday 2026-10-04 at 12:00 PM.** Multi-user iMessage assistant: people sign up on a web page, connect Google Calendar, then text the bot about their schedule.

## Status

Everything below is built, tested (412 tests: `.\.venv\Scripts\python.exe -m pytest` from `hermes-personal-assistant`), and live.

| Feature | Tools | Verified by texting the bot? |
|---|---|---|
| Sign-up + Google connect (multi-user) | – | Yes, with a second person |
| Calendar Q&A | `get_events`, `get_current_time` | Yes |
| Preferences | `get_preferences`, `set_preference` | Yes |
| When to leave (Routes API) | `plan_departure` (asks for `origin`; `"home"` uses the saved address) | Yes |
| Calendar editing (yes required in a later message) | `create_event`, `update_event`, `delete_event`, `confirm_change` | Yes |
| Per-user memory | `remember`, `forget` | Yes (`remember`); cross-user check not yet shown |
| Places nearby (Places API (New)) | `find_places` (never assumes home; the bot asks where) | Yes |
| Event reminders (texts sent by our scheduler) | `set_reminder`, `list_reminders`, `cancel_reminder`, `set_preference default_reminder_minutes` | Yes, one real reminder delivered |
| Help | plain "help" → fixed list in the persona | Not yet |

## Before the demo

1. **Commit and push.** Calendar editing, places, origin, reminders, and cleanup are uncommitted. Keep phone numbers and the ngrok domain out of committed files.
2. **Reboot test**: restart, log in, wait ~1 minute, run `ops/demo_up.ps1`, text the bot. This is the first real test of the tasks running non-elevated after the permission fix (see Gotchas).
3. **Pause Windows Update** until after the demo; the tasks only start after a login.
4. **Google OAuth consent screen**: confirm it's published ("In production"). In Testing mode only listed test users can sign in and refresh tokens expire after 7 days.
5. **Reconnect Google** once on every existing account so editing works (the first edit request sends the link).
6. **Fix Ethan's home address** (it has no city or state, so Maps can't find it): text the bot the full address.
7. **QR code** to the landing page.

### Demo script

1. Sign up a fresh phone live from the QR code; ask "what's on today?".
2. "Find good ramen in Kerrytown that's open now" → "how long to get there from home?" → "add dinner there at 7" → "yes".
3. "Remind me 15 minutes before it" on an event ~20 minutes out, and let the text arrive during the demo.
4. "Remember I need extra time to park downtown", send `/new`, ask "what do you know about me?", then show a second phone gets nothing.

### Roadmap (pitch, don't build)

Daily briefing (possible now, same delivery path as reminders), reminders not tied to a calendar event, a "time to leave" reminder using `plan_departure`, and closing the bot to strangers after the demo (`PHOTON_ALLOW_ALL_USERS=false`).

## Running it

Everything runs as Windows scheduled tasks that start at logon (30 s delay), run hidden, and restart on failure. **Don't start the app or ngrok from a terminal**, or the ports and ngrok's single free tunnel will collide.

| Task | What it runs | Log |
|---|---|---|
| `Hermes_Gateway` | Hermes (installed by Hermes) | `%LOCALAPPDATA%\hermes\logs\` |
| `Assistant_App` | `ops/run_assistant.ps1`: loops `python -m assistant.run`, restarting 10 s after any exit | `data/logs/assistant.log` |
| `Assistant_Ngrok` | `ops/run_ngrok.ps1`: loops `ngrok http --domain=<your-ngrok-domain> 8787` | `data/logs/ngrok.log` |

Scripts in `hermes-personal-assistant/ops/` (run with `powershell -ExecutionPolicy Bypass -File <script>`):
- `demo_up.ps1`: starts all three tasks, then checks Hermes, `:8788/health`, `:8787/`, and the public URL. **Run before the demo.**
- `stop_services.ps1`: stops the app and ngrok, including child processes. **To restart after code changes**: this, then `demo_up.ps1`.
- `install_tasks.ps1` (re-runnable) / `uninstall_tasks.ps1`.
- `launch_hidden.vbs`: runs a `.ps1` with no window. Task paths point at this repo, so moving it means re-running `install_tasks.ps1`.

Sleep on AC power is disabled (`powercfg /change standby-timeout-ac 0`; it was 10 minutes before).

## How it works

```
iPhone ⇄ Photon (Spectrum, shared pool) ⇄ Hermes gateway (local, scheduled task Hermes_Gateway)
              AIAgent (Nous model) + assistant_bridge plugin (reads the trusted sender phone)
                    │ POST 127.0.0.1:8788/tools/<name> and /context   {sender, args} + Bearer BRIDGE_TOKEN
                    ▼
   our app: python -m assistant.run → bridge API 127.0.0.1:8788 + sign-up site 127.0.0.1:8787
                                     + reminder thread ──(hermes.cmd send)──► Hermes gateway ──► Photon
                    ▼
   SQLite data/assistant.db + Google Calendar / Routes / Places APIs
Browser → ngrok (<your-ngrok-domain>) → :8787 → Google OAuth
```

### Sign-up flow (security-relevant, don't weaken)

1. `/` asks for a phone number → `POST /start` registers it as a **Photon project user** (`messaging/photon_users.py`, idempotent, rate-limited to 30/hour) → shows "Text <assignedPhoneNumber>".
   - On the Pro plan (shared pool), Photon **only routes senders registered as project users**, and each user is assigned a pool number that may differ from the default bot line (`BOT_PHONE`).
   - Registering grants nothing except the ability to text the bot.
2. The person texts the bot. The plugin's `pre_llm_call` hook calls `/context`, which says "NOT signed up" and includes a personal link `/signup?t=<token>`. The model relays it.
   - The token (table `signup_tokens`) is tied to the **trusted iMessage sender**, valid for 1 hour, and deleted once Google is connected.
3. The form (name, timezone auto-detected, optional home address, travel mode) **never asks for a phone number**, so nobody can claim someone else's number. Submitting it leads to Google OAuth (single-use `state`, 15 min), then `/done`.
4. Calendar "not connected / expired" errors include a fresh personal link for reconnecting.

### Identity and isolation rules

- The user is identified only by `HERMES_SESSION_USER_ID` from Hermes's session context, never from tool arguments. Every tool receives the resolved `User`.
- The plugin refuses non-Photon platforms and group chats, so a calendar is never read into a group.
- Hermes built-in memory and `session_search` are **off**, because they're shared across all users. Per-chat history still works.
- The persona is one shared `agent.system_prompt`. Per-user facts come from the "[Assistant account status]" note that `ToolService.context` adds to each user message, including that user's saved memory notes and default reminder setting.
- Memory notes are written only by their owner (`remember`) and read back only into that owner's context. The persona says to save only when asked or when the user clearly states a lasting preference, and never to save calendar text or secrets.
- **Calendar writes need the user's yes in a later message, enforced by the server.** `create_event` / `update_event` / `delete_event` only store a pending change. `confirm_change` refuses unless the user has sent a message since the change was proposed. `/context` (called once per incoming message by `pre_llm_call`) counts turns in `conversation_turns`, so text planted in an event can't get a change proposed and confirmed in one turn. Don't move the turn counter anywhere that runs per model call.
- **`MEDIA:` is defanged everywhere.** Hermes treats `MEDIA:<path>` in a message as "attach this local file". `ToolService._defang` breaks it up in every tool result and the `/context` note, and `hermes_send.safe_text` does the same for reminders, so a calendar title like `MEDIA:C:\...\.env` can't leak files.

## Code map (`hermes-personal-assistant/src/assistant/`)

- `run.py`: loads settings; starts the bridge and the site (each with its own SQLite connection; the bridge's repos share one) and the reminder thread (its own connection). Builds `PhotonUsers` when Photon credentials are found, and the Maps clients when `GOOGLE_MAPS_API_KEY` is set. Run from `src`: `..\.venv\Scripts\python.exe -m assistant.run`.
- `bridge/server.py`: `POST /tools/{tool}`, `POST /context`, `GET /health`. Bearer token checked in constant time, calls serialized with a lock.
- `bridge/service.py`: `ToolService.call(tool, sender, args)` and `context(sender)`. Unknown sender gets `not_signed_up` plus a personal link. Refreshed Google tokens are saved back. All tools are described under "Tools" below.
- `web/app.py`: `create_web_app(...)` with Google login and `register_phone` injected, so tests use fakes. Plain HTML via `html.escape`, plus a `RateLimiter`.
- `messaging/photon_users.py`: Spectrum users API (`https://spectrum.photon.codes/projects/{id}/users/`, Basic auth with project id and secret). Response shape: `{"data": {"users": [{phoneNumber, assignedPhoneNumber, ...}]}}`.
- `messaging/hermes_send.py`: `HermesSender.send(phone, text)` runs `hermes.cmd send --to photon:any;-;<E.164> --file <temp utf-8 file> --json` with stdin closed and no window. The body goes through a file because `hermes.cmd` is a batch file and cmd.exe would interpret `&`, `|` and so on. The phone must match E.164. Failures log Hermes's stdout/stderr with numbers masked.
- `scheduler/reminders.py`: `ReminderScheduler.run_forever` (daemon thread). Every 30 s it sends due reminders; every 5 min it creates automatic reminders for today's and tomorrow's timed events of users with `default_reminder_minutes`, and prunes old rows. Before sending it re-reads the event: cancelled/deleted → skipped, moved → rescheduled, already started → skipped. Failed sends and Google outages retry next tick. The text is a fixed template (no model call): title, start time, "in N min", and location (online meetings say "online meeting", never the link).
- `config/settings.py`: project `.env`, plus Photon credentials read from **Hermes's** `.env` (then `auth.json`) at startup. Never copy or print them.
- `storage/db.py`: migrations via `PRAGMA user_version`. 5 shipped (3 `memories`; 4 `conversation_turns`, `pending_changes`; 5 `users.default_reminder_minutes`, `reminders`). **Append, never edit.**
- `storage/users.py`: `UserRepo` (users, Fernet-encrypted Google token, OAuth state, sign-up tokens, `list_with_default_reminders`).
- `storage/memories.py`: `MemoryRepo`. Notes are Fernet-encrypted with the same key; 300 characters per note, 30 per user.
- `storage/pending_changes.py`: `PendingChangeRepo` (`start_turn`, `current_turn`, `create`, `get`, `delete`). Changes expire after 10 minutes. Ids use `AUTOINCREMENT` so an expired id is never reused.
- `storage/reminders.py`: `ReminderRepo`. Stores only event id, times, minutes before, `is_default`, and status (`pending`/`sent`/`skipped`/`cancelled`); titles are re-read from Google. Unique per (user, event, minutes). `add` re-arms; `ensure_default` is `INSERT OR IGNORE`, so a cancelled automatic reminder isn't recreated. Max 50 pending per user. `due(now)` is cross-user and only for the scheduler.
- Every storage query that serves a user filters on `user_id`.
- `tools/` (no imports from bridge, web, scheduler, or messaging):
  - `calendar_service`: primary calendar only. Event descriptions are deliberately not fetched (prompt-injection surface). Writes always use `sendUpdates="none"`. `CalendarEvent.recurring_series` marks a series itself (refused for edits; single occurrences are fine).
  - `calendar_edits` (pure): title/location cleaning, local-time parsing (`YYYY-MM-DDTHH:MM`, or dates with `all_day`), default 60 min, timed events ≤ 14 days, all-day ≤ 31 days, Google start/end bodies (all-day end is exclusive), and read-back summaries.
  - `google_auth`: web flow requests only `calendar.events`. Tokens load with **their own saved scopes** (old `calendar.readonly` tokens still read). `can_edit_calendar` gates writes. `exchange_code` saves the **granted** scopes and rejects a login where calendar access was unticked. `OAUTHLIB_RELAX_TOKEN_SCOPE` is set because Google returns earlier grants too.
  - `preferences`: `normalize_phone` (E.164; bare 10 digits = US), `parse_whole_number` (models send `20.0`), and validators, including `validate_reminder_minutes` (1–1440).
  - `clock`.
  - `maps_service`: `GoogleRoutesClient.get_travel_time` (`computeRoutes`, field mask `routes.duration`, `TRAFFIC_AWARE` for drive). Omits `departureTime` within a minute of now, because Routes rejects past times.
  - `departure_planner` (pure): leave = start − buffer − travel, rounded down, in UTC. Re-estimates until the guess moves ≤ 2 min (max 3 Maps calls). Statuses: `on_time`, `leave_now`, `late`.
  - `places_service`: `GooglePlacesClient` (`places:searchText`, same Maps key). `locate(address)` geocodes with a 1-result Text Search; `search(...)` uses a `locationBias` circle and an explicit field mask (no reviews or summaries). Pure helpers `build_search_request`, `parse_places`, `distance_miles`.

### Tools (`ToolService`)

- `plan_departure`: `origin` is required in practice (an address/place, or `"home"` / `"my home"` for the saved address, reported as "home address on file" and never echoed); without it the bot is told to ask. `event_id` optional (default: next timed, non-online event today with a location). `destination` is used only when the event has no physical location. `arrive_by` (local `YYYY-MM-DDTHH:MM`, future, ≤ 31 days) replaces the event start; required for all-day events. `destination` + `arrive_by` without `event_id` plans an off-calendar trip. Online meetings are never sent to Maps.
- `create_event` / `update_event` (only changed fields; moving the start keeps the length) / `delete_event` / `confirm_change(change_id)`: any event on the primary calendar, including invites; Google 403 on confirm becomes "only the organizer can change it".
- `remember(note)` / `forget(memory_id | "all")`: `context` lists notes newest first, capped at 2,000 characters.
- `find_places`: `query` plus `event_id` (that event's physical location) or `near`; **never uses the home address**. Radius by travel mode (walk 3 km, bicycle/transit 8 km, drive 15 km). Optional `open_now`, `min_rating` (1–5, floored to half steps), `max_price` (1–4). Returns up to 3 places with rating, price, open now, distance, and `maps_url` (Google's terms require showing it). Results are never stored.
- `set_reminder(event_id, minutes_before)`: timed, future events only, and the reminder time must still be ahead. `list_reminders` adds titles from one calendar read. `cancel_reminder(reminder_id | "all")`. `set_preference default_reminder_minutes` (0/"off" disables) deletes pending automatic reminders so the scheduler recreates them with the new lead time.

### Plugin, tests, git

- `hermes_plugin/assistant_bridge/`: stdlib-only plugin (tool schemas plus the `_account_context` hook). Junctioned into Hermes at `%LOCALAPPDATA%\hermes\plugins\assistant_bridge`, so **moving or renaming the repo breaks it**.
- `tests/`: no network. `conftest.py` provides in-memory repos. Includes tests that users can't see each other's data.
- Git: public GitHub repo. `.env` and `data/` are gitignored; keep phone numbers and the ngrok domain out of committed files.

### Project `.env` (`hermes-personal-assistant\.env`, gitignored)

| Key | Notes |
|---|---|
| `ASSISTANT_SECRET_KEY` | Fernet key for Google tokens and notes. **Changing it makes every stored token unreadable.** Required. |
| `BRIDGE_TOKEN` | Must equal `ASSISTANT_BRIDGE_TOKEN` in Hermes's `.env`. Required. |
| `PUBLIC_BASE_URL` | `https://<your-ngrok-domain>`. `ops/run_ngrok.ps1` and `ops/demo_up.ps1` read the ngrok domain from here. |
| `BOT_PHONE` | The bot's iMessage line, shown on the sign-up page if Photon assigns none. Required. |
| `GOOGLE_MAPS_API_KEY` | Routes API and Places API (New). |
| optional | `ASSISTANT_TIMEZONE`, `WEB_PORT` 8787, `BRIDGE_PORT` 8788, `ASSISTANT_DB_PATH`, `HERMES_CMD` (default `%LOCALAPPDATA%\hermes\bin\hermes.cmd`; reminders are off with a warning if it's missing) |

`data/` (gitignored): `assistant.db`, `logs/`, `web_client.json` (Google **Web** OAuth client, which the site uses), and `credentials.json` (old Desktop client, unused unless a token still references it).

## External setup

- **Google Cloud:** one project with the Calendar, Routes, and Places API (New) enabled (if the Maps key gets API restrictions, allow both Routes and Places (New)). The consent screen has the `calendar.events` scope (added 2026-10-03); `calendar.readonly` can be removed once everyone has reconnected. The Web client's redirect URI is `https://<your-ngrok-domain>/oauth/google/callback`. An unverified app shows a warning screen (users click Advanced, then Go to…) and is capped at 100 users.
- **ngrok:** free static domain `<your-ngrok-domain>`, reachable from any network. Tunnel **only port 8787, never 8788.** Visitors see an ngrok interstitial page once.
- **Photon:** Spectrum **Pro**, shared pool. Ethan's number is in `PHOTON_ALLOWED_USERS` in Hermes's `.env`; his assigned line is in `BOT_PHONE` in the project `.env`. Limits: 50 new conversations per line per day, 5,000 messages per day. Shared lines can't message a number that has never texted the bot ("target not allowed") but can message existing conversations, which is how reminders work. Groups aren't supported.

## Hermes (native install, not in this repo)

- Home: `%LOCALAPPDATA%\hermes`. CLI: `%LOCALAPPDATA%\hermes\bin\hermes.cmd`, not on PATH. Model: `nous` / `stealth/space-bunny-alpha`.
- **Restart after any config or plugin change:** `hermes.cmd gateway restart`. Logs in `...\hermes\logs\`: `gateway.log` (messages), `agent.log` (tool calls), `errors.log`.
- **Secrets** live in `...\hermes\.env` and `auth.json`. Never print or read their values. Check whether a key exists with `Select-String -Quiet`.
- `.env` (relevant keys): `ASSISTANT_BRIDGE_TOKEN`, `PHOTON_PROJECT_ID` / `PHOTON_PROJECT_SECRET`, `PHOTON_ALLOW_ALL_USERS=true` (opens DMs to everyone; checked before `PHOTON_ALLOWED_USERS`). To shut the bot to strangers: set it to `false` and restart.
- `config.yaml` settings that matter:
  - `platform_toolsets.photon: [todo, clarify, assistant, no_mcp]`. **Never** add terminal, file, browser, code, web, memory or session_search for Photon.
  - `memory.memory_enabled: false`, `memory.user_profile_enabled: false`.
  - `agent.system_prompt`: the shared persona. **Update it when tools are added**, including the help list near the end. Don't add `channel_overrides` (they replace the prompt for that chat), and keep `display.personality` unset (it overrides `agent.system_prompt`).
  - `platforms.photon.allow_admin_from` / `group_allow_admin_from: [<Ethan's number>]`, `user_allowed_commands: ["new"]`. **Without an admin list, every user can run any slash command**, including `/model` and `/personality`, which rewrites the global persona. That's also why there's no `/help` for users: help is a plain-message rule in the persona.
- Backups: one `config.yaml.bak-before-<change>` per persona edit (newest: `bak-before-help`; original: `bak-before-photon-lockdown`), plus `.env.bak-before-open-access`.
- Don't re-run `hermes photon setup` unless needed.

## Gotchas for agents

- **Hermes was installed elevated**, so its bundled Python/Node, `cache\*` and `pending_messages` were Administrators-only. The agent's terminal is elevated, so manual tests passed while the non-elevated scheduled tasks got "Access is denied" (this broke the first real reminder, and would have broken `Hermes_Gateway` after a reboot). Fixed 2026-10-04 with `icacls "%LOCALAPPDATA%\hermes" /grant ethan:(OI)(CI)M /T`. Re-run it if a Hermes update reinstalls tools elevated. **Test as the tasks run (non-elevated)**, e.g. from a temporary scheduled task, not just from the agent terminal.
- `hermes.cmd gateway restart` from the elevated agent terminal starts the gateway elevated ("direct spawn"), not through the task, which hides permission problems.
- Grep and Glob only search the workspace. Search Hermes with `rg` from `$env:LOCALAPPDATA\hermes\hermes-agent`, using **single-quoted** patterns.
- PowerShell mangles `python -c "..."`. Pipe a here-string into `python -` instead. To import Hermes, use its Python (`%LOCALAPPDATA%\hermes\tools\python-3.14.7+20260901-win32-x64\python.exe`), set `HERMES_DISABLE_LAZY_INSTALLS=1` and `HERMES_HOME`, prepend `...\hermes\hermes-agent` to `sys.path`, and `import hermes_bootstrap` first. Hermes's Python has no PyYAML; check `config.yaml` with the project venv.
- Plugin hooks can be tested without texting: `gateway.session_context.set_session_vars(platform="photon", chat_type="dm", user_id=...)`, then `hermes_cli.lifecycle.invoke_hook("pre_llm_call", ...)`.
- Shell cwd persists between calls, so `cd` explicitly before using relative paths.
- Appending to `.env` files: use `[IO.File]::AppendAllText` with `UTF8Encoding($false)`. `Set-Content -Encoding utf8` writes a BOM.
- Restarting our app: `ops/stop_services.ps1`, then `ops/demo_up.ps1`. Killing only the `python -m assistant.run` process also works; the supervisor restarts it within about 10 seconds.
- **Tool schema types matter.** Hermes validates tool arguments with jsonschema and turns `"1"` into `1` for integer fields, but never numbers into strings. Declare numeric parameters as `integer`, or `["string", "number"]` / `["integer", "string"]` when mixed. Rejected calls show up in `agent.log` as "failed argument validation".
- `hermes.cmd plugins show` lists "Listens: (none)" even though our hook is registered. That field only covers manifest events.
- Test fixtures: `FakeCalendar` stores the event dicts you pass in, so copy shared module-level events before mutating them.
