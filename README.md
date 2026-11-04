# Telegram to Apple Calendar bot

Type an event in Telegram, tap how often it repeats, and it lands in your iCloud calendar (so it shows on your iPhone, Mac and iPad). Before saving, the bot checks your calendars and warns you about clashes.

```
You:  Football next tuesday 8-10pm
Bot:  📅 Football
      Tue 10 Nov, 8pm to 10pm
      Repeat?  [Doesn't repeat] [Weekly] [Every 2 weeks] [Daily] [Monthly] [Cancel]
You:  (tap Weekly)
Bot:  ⚠️ Clashes in the next 3 months:
      • Tue 17 Nov: Dinner (7:30pm–9pm)
      Add anyway?  [Add anyway] [Cancel]
```

## What it understands

| You type | Result |
|---|---|
| `Football next tuesday 8-10pm` | Tue 10 Nov, 8pm to 10pm |
| `Dentist 12 nov 3pm` | 1 hour by default |
| `Dinner fri 7:30pm for 2h` | Custom length |
| `lunch tmr 11-1pm` | 11am to 1pm |
| `Party sat 10pm-1am` | Runs past midnight |
| `Standup 20/11 09:30-10:00` | Dates are day/month |
| `Review next week wed 3-4pm` | Wednesday of next week |
| `Sandra's birthday 14/12` | No time means all day |

Rules worth knowing:
- `next tue` means the first Tuesday after today (on a Tuesday, that's a week away). `tue` alone means the coming Tuesday, which can be today.
- A time with no date goes today, or tomorrow if it has already passed.
- Without am/pm, hours 1 to 6 are read as afternoon/evening and 7 to 11 as morning. Add `pm` if in doubt; the bot always shows what it understood before saving.

Commands: `/week` lists the next 7 days, `/undo` deletes the last event the bot added.

Clash checks look at every calendar on your account (or only those in `CHECK_CALENDARS`). For repeating events it checks the next 3 months. Events marked "Free" and all-day events are ignored unless you turn `ALL_DAY_COUNTS_AS_CONFLICT` on.

## Setup (about 10 minutes)

**1. Create the bot.** In Telegram, message **@BotFather**, send `/newbot`, pick a name, and copy the token.

**2. Get your Telegram ID.** Message **@userinfobot** and copy the number. This locks the bot to you.

**3. Make an Apple app-specific password.** Go to account.apple.com, Sign-In and Security, App-Specific Passwords, and generate one (e.g. "Telegram calendar"). Your normal Apple password won't work here, and you can revoke this one any time.

**4. Configure.**
```bash
cp .env.example .env
# fill in the token, your ID, Apple ID email, app password, and calendar name
```

**5. Run.**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python bot.py
```
Then open your bot in Telegram and send `/start`.

## Keeping it running

The bot only works while `python bot.py` is running somewhere.

- **Your Mac:** fine for trying it out, but it stops when the Mac sleeps.
- **A small always-on host:** any service that runs a Docker container works (a cheap VPS, Railway, Fly.io, or a Raspberry Pi at home). Use the included `Dockerfile` and set the `.env` values as environment variables. It uses polling, so no web address or webhook is needed.

## Security

The app-specific password can read your iCloud calendars and contacts, so keep `.env` private (it's in `.gitignore`) and keep `ALLOWED_TELEGRAM_IDS` set. If anything leaks, revoke the password at account.apple.com.

## Tests

```bash
pip install pytest && python -m pytest tests
```
