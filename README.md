# JobPilot Auto

A personal job-search pipeline for working-student (Werkstudent) roles in Germany. It finds postings, checks them against the candidate's real profile, writes a tailored cover letter **only from confirmed facts**, sends e-mail applications within strict limits, and tracks every application until a reply arrives.

The interface and the in-app messages are in Russian; generated letters are in German or English, matching the posting.

## What it does

```
discover → verify → prioritise → prepare → send → confirm → reply
```

| Stage | How |
|---|---|
| Discover | Bundesagentur für Arbeit Jobsuche API, company ATS feeds (Personio, Greenhouse, Recruitee, Lever, SmartRecruiters, Ashby, Workable), manually added leads |
| Verify | Re-reads the posting at the source, skips closed or expired ones, de-duplicates across sources and against past applications |
| Prioritise | Rule-based scoring by tasks (not just job title): *close* / *adjacent* fit, missing hard requirements (e.g. a mandatory tool or language level) are listed as gaps |
| Prepare | Cover letter assembled from `profile/facts.toml` paragraphs; a deny-list blocks claims the candidate cannot prove; PDF letter rendered without external libraries |
| Send | Gmail SMTP, max 3 per run / 5 per day, one company at most once per 30 days. Postings that say e-mail applications are not accepted are routed to the portal instead |
| Confirm | "Attempted", "sent" and "result unknown" are separate states; an interrupted send is never retried blindly |
| Reply | IMAP check: confirmations, rejections, invitations and bounces are matched to the right application |

### Application forms (optional)

`jp fill ID` opens the employer's form in the installed Edge/Chrome via Playwright: it clicks "Apply", picks "necessary cookies only", fills contact fields, uploads CV / cover letter / certificates and recognises common applicant-tracking systems (concludis, Personio, d.vinci, SuccessFactors and generic forms). By default, salary, work-permit and free-text questions are left to the user and highlighted. CAPTCHAs are never touched. The form is submitted only after the user confirms, and an application counts as submitted only when the site shows a confirmation.

- Optional `[forms] available_until = ""`: availability end date in `DD.MM.YYYY`; empty leaves it to the user (date inputs receive `YYYY-MM-DD`).
- Optional `[forms] salary_hourly = 0`: gross hourly salary in EUR; a positive number enables autofill and selects an offered hourly period; annual salary questions always stay for the user.

## Stack

- Python 3.11+, standard library only for the core (`sqlite3`, `smtplib`, `imaplib`, `tomllib`, `urllib`)
- Optional: Playwright for form autofill
- SQLite journal with a full event history per posting
- Windows helpers (`setup.bat`, `run.bat`, Task Scheduler at 08:15 and 15:15)
- 60 unit and browser tests (`python -m unittest discover -s tests`)

## Quick start

```bash
cp config.example.toml config.toml              # your contacts, search areas, limits
cp profile/facts.example.toml profile/facts.toml  # ONLY facts you can prove
cp .env.example .env                            # Gmail app password, never committed
python -m jobpilot run                          # dry run by default: nothing is sent
python -m jobpilot status
```

On Windows, `setup.bat` does the copying, runs the tests and opens `.env`. Switch `send_mode` to `"live"` in `config.toml` only after reviewing a few generated letters.

## Privacy by design

`config.toml`, `profile/facts.toml`, `.env`, `documents/` and `data/` are git-ignored. The repository contains a fictional example persona (Max Mustermann) only.

A more detailed guide in Russian: [README.ru.md](README.ru.md).
