# Personal AI Assistant

A multi-user calendar assistant you text over iMessage. People sign up on a web page, connect Google Calendar, then text the bot like a person:

- "What's on today?" / "What do I have Friday?"
- "When should I leave for my 3pm?" (live Google Maps traffic)
- "Find tacos near downtown" (Google Places)
- "Add lunch with Sam Friday at noon" (asks you to confirm first, and warns about overlaps with the nearest free times)
- "Invite sam@gmail.com to it" / "Invite Sam" once you've saved Sam's email (Google sends the invitation)
- "Remind me 15 minutes before my dentist" (the bot texts you at that time)
- "Text me my schedule every morning at 7:30" (daily briefing)
- "Remember I hate early meetings"

## How it works

```
iPhone ⇄ Photon (iMessage) ⇄ Hermes gateway (AI agent + assistant_bridge plugin)
                                   │  trusted sender phone + tool call
                                   ▼
                     this app: bridge API (127.0.0.1:8788)  ──► SQLite, Google Calendar / Routes / Places
                               sign-up site (:8787, public via ngrok) ──► Google OAuth
                               reminder/briefing scheduler ──► hermes send ──► Photon
```

- **Hermes** (Nous Research's agent gateway, installed separately) runs the agent and the iMessage connection. The `hermes_plugin/assistant_bridge` plugin relays tool calls to this app, identifying the user only from the gateway's trusted sender, never from model output.
- **This app** (`hermes-personal-assistant/src/assistant`) holds all the logic and data: per-user accounts, encrypted Google tokens, memory notes, pending calendar changes, and reminders.
- Calendar text is treated as data, never instructions. Calendar changes need the user's "yes" in a later message, enforced by the server.

## Development

```powershell
cd hermes-personal-assistant
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
.\.venv\Scripts\python.exe -m pytest
```

Run the app from `src` with `..\.venv\Scripts\python.exe -m assistant.run`. It needs a `.env` in `hermes-personal-assistant` (`ASSISTANT_SECRET_KEY`, `BRIDGE_TOKEN`, `BOT_PHONE`, `PUBLIC_BASE_URL`, and optionally `GOOGLE_MAPS_API_KEY` and `CONTACT_EMAIL`), and a Google OAuth web client at `data/web_client.json`. The sign-up site also serves the privacy policy (`/privacy`) and terms (`/terms`).

On the demo machine everything runs as Windows scheduled tasks; see `hermes-personal-assistant/ops/` and `HANDOFF.md` for operations, setup, and current status.
