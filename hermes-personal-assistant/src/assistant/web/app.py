"""Public sign-up site.

1. `/` – enter a phone number so Photon will route that number to the bot (grants nothing else).
2. The person texts the bot, which replies with a personal sign-up link.
3. `/signup` – finish sign-up from that link, then connect Google Calendar.

The account's phone number always comes from the signup token (issued only to that phone over
iMessage), never from a form, so nobody can claim someone else's number.
"""

import logging
import re
import threading
import time
from collections import deque
from html import escape
from typing import Callable
from zoneinfo import available_timezones

from fastapi import FastAPI, Form, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from assistant.messaging.photon_users import PhotonError
from assistant.storage.users import User, UserRepo
from assistant.tools import preferences

log = logging.getLogger(__name__)

StartLogin = Callable[[str, str], str]
FinishLogin = Callable[[str, str, str], str]
RegisterPhone = Callable[[str], str | None]

TIMEZONES = sorted(tz for tz in available_timezones() if "/" in tz and not tz.startswith(("Etc/", "SystemV/")))

STYLE = """
body{font-family:-apple-system,system-ui,sans-serif;max-width:480px;margin:40px auto;padding:0 20px;color:#1d1d1f;line-height:1.5}
h1{font-size:1.6em}label{display:block;margin-top:16px;font-weight:600}
input,select{width:100%;padding:10px;margin-top:6px;font-size:1em;border:1px solid #ccc;border-radius:10px;box-sizing:border-box}
.btn{display:inline-block;margin-top:24px;padding:12px 20px;background:#0a84ff;color:#fff;border:0;border-radius:12px;
font-size:1em;text-decoration:none;cursor:pointer}.hint{color:#666;font-size:.9em;font-weight:400}
.error{background:#fde8e8;color:#9b1c1c;padding:10px 14px;border-radius:10px}
footer{margin-top:48px;color:#666;font-size:.85em}footer a{color:#666}h2{font-size:1.15em;margin-top:28px}
"""
FOOTER = "<footer><a href='/privacy'>Privacy policy</a> · <a href='/terms'>Terms of service</a></footer>"
POLICY_DATE = "October 4, 2026"
SITE_VERIFICATION_NAME = re.compile(r"google[0-9a-f]{8,32}")


def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<meta name='referrer' content='no-referrer'><title>{escape(title)}</title><style>{STYLE}</style></head>"
        f"<body>{body}{FOOTER}</body></html>"
    )
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


def _sms_link(bot_phone: str) -> str:
    return "sms:+" + re.sub(r"\D", "", bot_phone)


def _contact(contact_email: str | None) -> str:
    if contact_email:
        return f"<a href='mailto:{escape(contact_email)}'>{escape(contact_email)}</a>"
    return "the developer email shown on the Google sign-in screen"


def _privacy_body(contact: str) -> str:
    return f"""<h1>Privacy policy</h1>
<p class="hint">Last updated {POLICY_DATE}</p>
<p>This assistant is a personal project built for a hackathon. You text it over iMessage to ask about your
Google Calendar, travel times, places nearby, and reminders. This page explains what it stores and who sees it.</p>
<h2>What we store</h2>
<ul>
  <li>Your phone number, name, timezone, and the settings you choose (home address, travel mode, buffer
  minutes, reminder and daily briefing times).</li>
  <li>Your Google sign-in token, encrypted, so the assistant can reach your calendar.</li>
  <li>Notes you ask the assistant to remember, encrypted.</li>
  <li>Reminders you set: the event's id and times, not its title or details.</li>
  <li>Recent conversation history, so the assistant can follow the conversation.</li>
</ul>
<h2>Google Calendar data</h2>
<p>The assistant reads your primary calendar only when you ask something that needs it (or to send reminders
and briefings you turned on). It adds, changes, or deletes events only after you confirm in a message. Calendar
data isn't stored beyond what's listed above, and it is never sold, shared for advertising, or used to train
AI models.</p>
<p>The assistant's use and transfer of information received from Google APIs adheres to the
<a href="https://developers.google.com/terms/api-services-user-data-policy">Google API Services User Data
Policy</a>, including the Limited Use requirements.</p>
<h2>Services that process your data</h2>
<ul>
  <li><b>Photon</b> delivers iMessages between you and the assistant.</li>
  <li><b>An AI model provider (Nous Research)</b> reads your messages, and the calendar details needed to
  answer them, to write replies.</li>
  <li><b>Google Maps</b> receives addresses only when you ask about travel time or places.</li>
</ul>
<h2>Your choices</h2>
<ul>
  <li>Change your settings or turn off reminders and briefings by texting the assistant.</li>
  <li>Revoke calendar access anytime at
  <a href="https://myaccount.google.com/permissions">myaccount.google.com/permissions</a>.</li>
  <li>To delete your account and all data stored about you, contact {contact}.</li>
</ul>
<h2>Contact</h2>
<p>Questions: {contact}.</p>"""


def _terms_body(contact: str) -> str:
    return f"""<h1>Terms of service</h1>
<p class="hint">Last updated {POLICY_DATE}</p>
<p>This assistant is a free hackathon project. By signing up you agree to these terms.</p>
<ul>
  <li>It is provided "as is", without warranties. It can be wrong about times, travel, or places, so
  double-check anything important.</li>
  <li>You are responsible for changes you confirm to your calendar.</li>
  <li>Use it only for your own phone number and calendar, and don't try to misuse, overload, or break it.</li>
  <li>The service may change, pause, or shut down at any time, and accounts may be removed.</li>
  <li>Your data is handled as described in the <a href="/privacy">privacy policy</a>.</li>
</ul>
<p>Questions: {contact}.</p>"""


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float, clock: Callable[[], float] = time.monotonic):
        self._max, self._window, self._clock = max_events, window_seconds, clock
        self._events: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._events and now - self._events[0] > self._window:
                self._events.popleft()
            if len(self._events) >= self._max:
                return False
            self._events.append(now)
            return True


def create_web_app(
    users: UserRepo,
    public_base_url: str,
    bot_phone: str,
    default_timezone: str,
    start_google_login: StartLogin,
    finish_google_login: FinishLogin,
    register_phone: RegisterPhone | None = None,
    registration_limiter: RateLimiter | None = None,
    contact_email: str | None = None,
    google_site_verification: str | None = None,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = threading.Lock()
    limiter = registration_limiter or RateLimiter(max_events=30, window_seconds=3600)
    redirect_uri = f"{public_base_url.rstrip('/')}/oauth/google/callback"
    text_bot = f"<a class='btn' href='{escape(_sms_link(bot_phone))}'>Text {escape(bot_phone)}</a>"
    # With per-user pool numbers, people may text a different number than bot_phone.
    back_to_chat = "" if register_phone else text_bot

    def landing(error: str | None = None, phone: str = "") -> HTMLResponse:
        intro = ("<h1>Your calendar, over iMessage</h1>"
                 "<p>Text the assistant to ask what's on your calendar, when you should leave, and more.</p>")
        if register_phone is None:
            return _page("Personal assistant", intro + "<p>To get started, send it any message. "
                         f"It will reply with your personal sign-up link.</p>{text_bot}")
        error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
        body = f"""{intro}{error_html}
<form method="post" action="/start">
  <label>Your iPhone number<input name="phone" type="tel" required autocomplete="tel"
    placeholder="+1 555 123 4567" value="{escape(phone)}"></label>
  <button class="btn" type="submit">Get started</button>
</form>
<p class="hint">We'll show you the number to text. Your account is only created after you text it from this phone.</p>"""
        return _page("Personal assistant", body, status=400 if error else 200)

    def link_expired() -> HTMLResponse:
        return _page(
            "Link expired",
            "<h1>This link has expired</h1><p>Sign-up links work for 1 hour. "
            f"Text the assistant again and it will send you a fresh one.</p>{back_to_chat}",
            status=400,
        )

    def signup_form(token: str, user: User | None, values: dict, error: str | None = None) -> HTMLResponse:
        def value(key: str, fallback: str = "") -> str:
            if key in values:
                return escape(values[key])
            current = getattr(user, key, None) if user else None
            return escape(str(current)) if current else escape(fallback)

        mode = values.get("travel_mode") or (user.travel_mode if user else "drive")
        modes = "".join(
            f"<option value='{m}'{' selected' if m == mode else ''}>{m.capitalize()}</option>"
            for m in preferences.TRAVEL_MODES
        )
        zones = "".join(f"<option value='{escape(tz)}'>" for tz in TIMEZONES)
        heading = "Update your settings" if user else "Set up your assistant"
        error_html = f"<p class='error'>{escape(error)}</p>" if error else ""
        body = f"""
<h1>{heading}</h1>
<p>Almost done. Tell the assistant a bit about you, then connect your Google Calendar.</p>
{error_html}
<form method="post" action="/signup">
  <input type="hidden" name="t" value="{escape(token)}">
  <label>Name<input name="name" required maxlength="{preferences.MAX_NAME_LENGTH}" value="{value('name')}"></label>
  <label>Timezone<input id="tz" name="timezone" list="zones" required value="{value('timezone')}"></label>
  <datalist id="zones">{zones}</datalist>
  <label>Home address <span class="hint">(optional, for "when should I leave?")</span>
    <input name="home_address" maxlength="{preferences.MAX_ADDRESS_LENGTH}" value="{value('home_address')}"></label>
  <label>How do you usually get around?<select name="travel_mode">{modes}</select></label>
  <label>Daily briefing <span class="hint">(optional: a text with that day's events at this time; you can
    change or stop it anytime by texting the assistant)</span>
    <input name="briefing_time" type="time" value="{value('briefing_time')}"></label>
  <button class="btn" type="submit">Continue to Google Calendar</button>
</form>
<p class="hint">The assistant can see and edit events on your primary calendar. It always asks you before
  adding, changing, or deleting anything.</p>
<script>
  var tz = document.getElementById("tz");
  if (!tz.value) {{ tz.value = Intl.DateTimeFormat().resolvedOptions().timeZone || "{escape(default_timezone)}"; }}
</script>"""
        return _page(heading, body, status=400 if error else 200)

    @app.get("/", response_class=HTMLResponse)
    def home() -> HTMLResponse:
        return landing()

    @app.get("/privacy", response_class=HTMLResponse)
    def privacy() -> HTMLResponse:
        return _page("Privacy policy", _privacy_body(_contact(contact_email)))

    @app.get("/terms", response_class=HTMLResponse)
    def terms() -> HTMLResponse:
        return _page("Terms of service", _terms_body(_contact(contact_email)))

    filename = (google_site_verification or "").strip().removesuffix(".html")
    if filename and not SITE_VERIFICATION_NAME.fullmatch(filename):
        log.warning("Ignoring GOOGLE_SITE_VERIFICATION: it should look like google1234abcd.html")
    elif filename:
        # Search Console's "HTML file" check for a URL-prefix property: proves we own this domain.
        @app.get(f"/{filename}.html", response_class=PlainTextResponse)
        def google_site_verification_file() -> str:
            return f"google-site-verification: {filename}.html"

    @app.get("/start")
    def start_page() -> RedirectResponse:
        """Refreshing or sharing the "text this number" page lands here; send them back to the phone form."""
        return RedirectResponse("/", status_code=303)

    @app.post("/start", response_class=HTMLResponse)
    def start(phone: str = Form(default="")) -> HTMLResponse:
        if register_phone is None:
            return landing()
        try:
            normalized = preferences.normalize_phone(phone)
        except ValueError as exc:
            return landing(str(exc), phone)
        if not limiter.allow():
            return landing("Lots of people are signing up right now. Try again in a few minutes.", phone)
        try:
            number = register_phone(normalized) or bot_phone
        except PhotonError as exc:
            return landing(str(exc), phone)

        link = f"<a class='btn' href='{escape(_sms_link(number))}'>Text {escape(number)}</a>"
        return _page(
            "Text the assistant",
            f"<h1>You're on the list</h1><p>From <b>{escape(normalized)}</b>, text <b>{escape(number)}</b> "
            "anything, like \"hi\". The assistant will reply with your personal sign-up link.</p>"
            f"{link}<p class='hint'>Save it as a contact – this is the number your assistant texts from. "
            "If nothing comes back, make sure iMessage on your iPhone sends from your phone number "
            "(Settings → Messages → Send &amp; Receive).</p>",
        )

    @app.get("/signup", response_class=HTMLResponse)
    def signup_page(t: str = Query(default="")) -> HTMLResponse:
        with lock:
            phone = users.phone_for_signup_token(t) if t else None
            user = users.get_by_phone(phone) if phone else None
        if phone is None:
            return link_expired()
        return signup_form(t, user, {})

    @app.post("/signup", response_model=None)
    def signup_submit(
        t: str = Form(default=""),
        name: str = Form(default=""),
        timezone: str = Form(default=""),
        home_address: str = Form(default=""),
        travel_mode: str = Form(default="drive"),
        briefing_time: str = Form(default=""),
    ) -> HTMLResponse | RedirectResponse:
        values = {"name": name, "timezone": timezone, "home_address": home_address, "travel_mode": travel_mode,
                  "briefing_time": briefing_time}
        with lock:
            phone = users.phone_for_signup_token(t) if t else None
            user = users.get_by_phone(phone) if phone else None
        if phone is None:
            return link_expired()

        try:
            prefs = {
                "name": preferences.validate_preference("name", name),
                "travel_mode": preferences.validate_preference("travel_mode", travel_mode),
                "briefing_time": preferences.validate_preference("briefing_time", briefing_time),
            }
            tz = preferences.validate_preference("timezone", timezone)
            if home_address.strip():
                prefs["home_address"] = preferences.validate_preference("home_address", home_address)
        except ValueError as exc:
            return signup_form(t, user, values, error=str(exc))

        with lock:
            user = users.upsert(phone, tz, **prefs)
            state = users.create_oauth_state(user.id)
        return RedirectResponse(start_google_login(redirect_uri, state), status_code=303)

    @app.get("/oauth/google/callback", response_class=HTMLResponse)
    def google_callback(state: str = "", code: str = "", error: str = "") -> HTMLResponse:
        with lock:
            user_id = users.consume_oauth_state(state) if state else None
        if user_id is None:
            return link_expired()
        if error or not code:
            return _page(
                "Calendar not connected",
                "<h1>Calendar not connected</h1><p>Google sign-in was cancelled. "
                f"Text the assistant to get a new link and try again.</p>{back_to_chat}",
                status=400,
            )

        try:
            token_json = finish_google_login(redirect_uri, state, code)
        except Exception:
            log.exception("Google token exchange failed for user %s", user_id)
            return _page(
                "Calendar not connected",
                "<h1>Couldn't connect your calendar</h1><p>Make sure you allow calendar access on the Google screen. "
                f"Text the assistant to get a new link and try again.</p>{back_to_chat}",
                status=502,
            )

        with lock:
            users.set_google_token(user_id, token_json)
            user = users.get(user_id)
            users.delete_signup_tokens(user.phone)
        return RedirectResponse("/done", status_code=303)

    @app.get("/done", response_class=HTMLResponse)
    def done() -> HTMLResponse:
        return _page(
            "You're all set",
            "<h1>You're all set 🎉</h1><p>Your Google Calendar is connected. "
            f"Text the assistant something like <b>\"what's on my calendar today?\"</b></p>{back_to_chat}",
        )

    return app
