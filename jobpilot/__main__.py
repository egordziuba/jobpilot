"""Командная строка: python -m jobpilot <команда>"""
from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
from pathlib import Path

from .config import load_settings
from .store import STATUS_RU, Store

ROOT = Path(__file__).resolve().parent.parent


def setup_logging(root: Path) -> None:
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    fh = logging.handlers.RotatingFileHandler(logs / "jobpilot.log", maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter("%(message)s"))
    root_log = logging.getLogger()
    root_log.setLevel(logging.INFO)
    root_log.handlers = [fh, sh]


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    p = argparse.ArgumentParser(prog="jobpilot", description="JobPilot Auto — поиск вакансий и заявки")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="полный цикл")
    r.add_argument("--dry-run", action="store_true", help="не отправлять работодателям, даже если send_mode = live")
    sub.add_parser("status", help="сводка по статусам")
    ls = sub.add_parser("list", help="список вакансий")
    ls.add_argument("status", nargs="?", default="")
    sh = sub.add_parser("show", help="вакансия и письмо")
    sh.add_argument("id")
    ap = sub.add_parser("approve", help="разрешить отправку письма")
    ap.add_argument("id")
    sk = sub.add_parser("skip", help="не подавать")
    sk.add_argument("id")
    mk = sub.add_parser("mark", help="изменить статус (applied_manually, interview, offer, rejected, response, closed)")
    mk.add_argument("id")
    mk.add_argument("status", choices=["applied_manually", "submitted", "interview", "offer", "rejected", "response", "closed", "sent"])
    sub.add_parser("test-mail", help="тестовое письмо самому себе")
    sub.add_parser("todo", help="пройтись по вакансиям, где нужен ты")
    fl = sub.add_parser("fill", help="открыть форму работодателя и заполнить её автоматически")
    fl.add_argument("id")
    fl.add_argument("--url", default=None, help="адрес формы, если программа знает не тот")
    args = p.parse_args(argv)

    settings = load_settings(ROOT)
    setup_logging(ROOT)

    if args.cmd == "run":
        from .pipeline import run
        if args.dry_run:
            settings.cfg.setdefault("run", {})["send_mode"] = "dry_run"
        res = run(settings)
        print(f"Режим: {res.mode}. Просмотрено {res.collected}, новых {res.new}, отсеяно {res.filtered}, "
              f"в очередь {res.queued}, ждут тебя {res.prepared}, отправлено {res.sent}, неясно {res.unknown}.")
        for n in res.notes:
            print("•", n)
        for e in res.errors:
            print("! ", e)
        return 0 if not res.errors else 1

    if args.cmd == "test-mail":
        from . import mailer
        if not settings.smtp_ready:
            print("В .env не заполнены SMTP_USER и SMTP_PASSWORD.")
            return 1
        mailer.send_self(settings, "JobPilot Auto: тест", "Если ты читаешь это письмо, отправка через Gmail работает.")
        print("Тестовое письмо отправлено на", settings.applicant["email"])
        return 0

    store = Store(settings.db_path)
    try:
        if args.cmd == "todo":
            return todo(settings, store)
        if args.cmd == "fill":
            from .forms import runner
            if not runner.available():
                print("Нет модуля Playwright. Запусти setup.bat ещё раз — он его поставит.")
                return 1
            status = runner.fill_job(settings, store, args.id, url=args.url)
            print("Статус:", STATUS_RU.get(status, status))
            return 0
        if args.cmd == "status":
            rows = store.db.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status ORDER BY n DESC").fetchall()
            for row in rows:
                print(f"{STATUS_RU.get(row['status'], row['status']):<34} {row['n']}")
            print("Режим:", settings.run.get("send_mode"))
        elif args.cmd == "list":
            rows = store.by_status(args.status) if args.status else store.all()
            for row in rows:
                if not args.status and row["status"] == "filtered":
                    continue
                print(f"{row['id']:<38} {row['status']:<14} {row['tier'] or '':<9} {row['score'] or 0:>5}  {row['company'][:28]:<28} {row['title'][:60]}")
        elif args.cmd == "show":
            row = store.get(args.id)
            if not row:
                print("Не найдено")
                return 1
            for k in ("company", "title", "status", "reason", "tier", "score", "channel", "email", "location", "deadline", "source_url", "apply_url"):
                print(f"{k:>11}: {row[k]}")
            for x in json.loads(row["reqs"] or "[]"):
                print(f"   [{x['s']}] {x['t']}")
            if row["body"]:
                print("\nТема:", row["subject"], "\n")
                print(row["body"])
            print("\nИстория:")
            for ev in store.events(args.id):
                print(f"  {ev['at']}  {ev['status']}  {ev['detail']}")
        elif args.cmd == "approve":
            row = store.get(args.id)
            if not row or row["status"] not in ("prepared", "verified"):
                print("Можно одобрить только подготовленную вакансию (prepared).")
                return 1
            if row["channel"] != "email" or not row["email"] or not row["body"]:
                print("У этой вакансии нет e-mail или письма — подай через портал и отметь mark … applied_manually.")
                return 1
            store.update(args.id, "queued", "одобрено вручную", approved=1)
            print("Добавлено в очередь. Уйдёт при следующем запуске в режиме live.")
        elif args.cmd == "skip":
            store.update(args.id, "skipped", "пропущено вручную")
            print("Пропущено.")
        elif args.cmd == "mark":
            row = store.get(args.id)
            if not row:
                print("Не найдено")
                return 1
            extra = {}
            if args.status in ("applied_manually", "submitted", "sent") and not row["sent_at"]:
                from datetime import datetime
                extra["sent_at"] = datetime.now().isoformat(timespec="seconds")  # для защиты от повторной подачи
            store.update(args.id, args.status, "отмечено вручную", **extra)
            print("Статус:", STATUS_RU.get(args.status, args.status))
    finally:
        store.close()
    return 0


def _copy(text: str) -> bool:
    import subprocess
    if sys.platform != "win32":
        return False
    try:
        subprocess.run(["clip"], input=text.encode("utf-16"), check=True)
        return True
    except Exception:
        return False


def todo(settings, store) -> int:
    """По очереди: открыть ссылку, скопировать письмо, показать резюме, спросить результат."""
    import subprocess
    import webbrowser
    rows = sorted(store.by_status("prepared"), key=lambda r: (r["tier"] != "close", -(r["score"] or 0)))
    if not rows:
        print("Сейчас ничего не ждёт твоего решения.")
        return 0
    print(f"Ждут тебя: {len(rows)}. Варианты — под каждой вакансией, q — выйти.\n")
    for i, r in enumerate(rows, 1):
        print("=" * 70)
        print(f"[{i}/{len(rows)}] {r['company']} — {r['title']}")
        print(f"   {r['location'] or ''}  ·  балл {r['score']}  ·  {r['reason']}")
        for x in json.loads(r["reqs"] or "[]"):
            mark = {"ok": "+", "gap": "!", "unk": "?"}.get(x["s"], " ")
            print(f"   {mark} {x['t']}")
        if r["channel"] == "email":
            print(f"\n   Письмо на {r['email']}, тема: {r['subject']}")
            print("   1 — отправить при следующем запуске   2 — не подавать   v — показать письмо")
            while True:
                ans = input("   > ").strip().lower()
                if ans == "v":
                    print("\n" + (r["body"] or "") + "\n")
                    continue
                break
            if ans == "1":
                store.update(r["id"], "queued", "одобрено вручную", approved=1)
                print("   В очереди.")
            elif ans == "2":
                store.update(r["id"], "skipped", "пропущено вручную")
            elif ans == "q":
                break
            continue
        from .forms import runner
        if runner.available():
            print(f"\n   Подача через сайт: {runner.best_url(r)}")
            print("   Enter — открыть форму и заполнить автоматически   o — просто открыть ссылку   2 — не подаю   l — позже")
            ans = input("   > ").strip().lower()
            if ans == "q":
                break
            if ans == "l":
                continue
            if ans == "2":
                store.update(r["id"], "skipped", "пропущено вручную")
                continue
            if ans != "o":
                status = runner.fill_job(settings, store, r["id"])
                print("   Статус:", STATUS_RU.get(status, status))
                continue
        link = r["apply_url"] or r["external_url"] or r["source_url"]
        copied = _copy(f"{r['subject']}\n\n{r['body'] or ''}")
        if link:
            webbrowser.open(link)
        if sys.platform == "win32" and settings.cv_path.exists():
            subprocess.Popen(["explorer", "/select,", str(settings.cv_path)])
        print("\n   Открыл объявление в браузере" + (" и скопировал письмо (Ctrl+V в поле «Anschreiben» или «Nachricht»)." if copied else "."))
        print(f"   Резюме для загрузки: {settings.cv_path}")
        if not copied:
            print("\n" + (r["body"] or "") + "\n")
        print("   1 — подал   2 — не подаю   Enter — позже")
        ans = input("   > ").strip().lower()
        if ans == "1":
            store.update(r["id"], "applied_manually", "подано вручную через сайт",
                         sent_at=__import__("datetime").datetime.now().isoformat(timespec="seconds"))
            print("   Отмечено: подано.")
        elif ans == "2":
            store.update(r["id"], "skipped", "пропущено вручную")
        elif ans == "q":
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
