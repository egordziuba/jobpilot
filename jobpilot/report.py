"""Отчёт после запуска: HTML-файл и короткая сводка себе на почту."""
from __future__ import annotations

import html
import json
from datetime import datetime

from .store import STATUS_RU

ACTIVE = ("queued", "prepared", "verified", "attempted", "send_unknown", "sent", "response", "interview", "offer", "bounced",
          "submitted", "applied_manually")
TIER_RU = {"close": "Близкая", "adjacent": "Смежная", "low": "Слабая", "": "—"}
REQ_RU = {"ok": "подтверждено", "gap": "пробел", "unk": "неизвестно"}


def _e(x) -> str:
    return html.escape(str(x or ""))


def worth_mailing(store, result) -> bool:
    if result.sent or result.unknown or result.bounced or result.errors:
        return True
    if any("ответ" in n or "вернулось" in n or "подтвердил" in n for n in result.notes):
        return True
    new = set(result.new_ids)
    return any(r["id"] in new for r in store.by_status("queued", "prepared"))


def _apply_link(r) -> str:
    return r["apply_url"] or r["external_url"] or r["source_url"] or ""


def summary_email(settings, store, result) -> tuple[str, str]:
    mode = "РАБОЧИЙ (письма уходят)" if result.mode == "live" else "ПРОБНЫЙ (работодателям ничего не отправлено)"
    new = set(result.new_ids)
    queued = store.by_status("queued")
    prepared_new = [r for r in store.by_status("prepared") if r["id"] in new]
    unknown = store.by_status("send_unknown")
    responses = store.by_status("response")
    lines = [
        f"JobPilot Auto — {datetime.now():%d.%m.%Y %H:%M} · режим: {mode}",
        "",
        f"Объявлений просмотрено: {result.collected}, новых: {result.new}, отсеяно: {result.filtered}",
        f"В очереди на автоотправку: {len(queued)} · ждут твоего решения: {len(prepared_new)} · отправлено сейчас: {result.sent}",
        "",
    ]
    act = []
    for r in unknown:
        act.append(f"• НЕЯСНО, ушло ли письмо: {r['company']} — {r['title']}. Проверь «Отправленные»; повторно не отправляю.")
    for r in responses:
        act.append(f"• ОТВЕТ: {r['company']} — {r['response_note']}")
    for r in prepared_new:
        if r["channel"] == "email":
            act.append(f"• E-mail есть, нужна твоя проверка: {r['company']} — {r['title']} ({r['reason']})")
        else:
            act.append(f"• Только через сайт работодателя: {r['company']} — {r['title']}\n  {_apply_link(r)}")
    if prepared_new:
        act.append("  → Двойной клик todo.bat в папке JobPilot: по очереди покажет каждую. Для форм на сайтах программа сама "
                   "откроет форму, заполнит поля и загрузит файлы — ты проверяешь и подтверждаешь отправку.")
    if act:
        lines += ["НУЖНО ТВОЁ ДЕЙСТВИЕ", *act, ""]
    if queued:
        title = "ПИСЬМА В ОЧЕРЕДИ" + (" (уйдут при send_mode = \"live\")" if result.mode != "live" else "")
        lines += [title, ""]
        for r in queued[:10]:
            lines += [f"=== {r['company']} — {r['title']} → {r['email']}", f"Тема: {r['subject']}", "", r["body"] or "", ""]
    if result.notes:
        lines += ["ЗАМЕТКИ", *[f"• {n}" for n in result.notes], ""]
    if result.errors:
        lines += ["ОШИБКИ", *[f"• {e}" for e in result.errors], ""]
    subj = f"JobPilot: отправлено {result.sent}, в очереди {len(queued)}, ждут тебя {len(prepared_new)}"
    if result.mode != "live":
        subj += " (пробный режим)"
    return subj, "\n".join(lines)


CSS = """
:root{--bg:#f4f6f9;--card:#fff;--ink:#18202c;--muted:#5d6878;--line:#dde2ea;--accent:#2347a8;--ok:#1d7546;--okb:#e2f3e9;--unk:#7d5b00;--unkb:#faf0d4;--gap:#a3312b;--gapb:#f9e1df}
@media (prefers-color-scheme:dark){:root{--bg:#0f141c;--card:#171e2a;--ink:#e4e8f0;--muted:#98a2b3;--line:#2a3445;--accent:#93acff;--ok:#72d39e;--okb:#16301f;--unk:#e7c360;--unkb:#33290f;--gap:#ff8e85;--gapb:#3a1b19}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:24px max(16px,4vw)}
h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:26px 0 10px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}
.wrap{max-width:1100px;margin:auto}.kpis{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:120px}.kpi b{display:block;font-size:22px;font-variant-numeric:tabular-nums}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 14px;margin-bottom:10px}
.co{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600}.ti{font-weight:600;font-size:15px}
.meta{color:var(--muted);font-size:13px}.pill{display:inline-block;font-size:12px;border:1px solid var(--line);border-radius:5px;padding:1px 7px;margin-right:4px}
.st{font-size:11px;border-radius:4px;padding:1px 6px;margin-right:6px}.ok{background:var(--okb);color:var(--ok)}.unk{background:var(--unkb);color:var(--unk)}.gap{background:var(--gapb);color:var(--gap)}
ul.r{list-style:none;padding:0;margin:8px 0}ul.r li{margin:2px 0;font-size:13px}pre{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:6px;padding:10px;font:13px/1.45 Consolas,monospace}
a{color:var(--accent)}code{background:var(--bg);padding:1px 5px;border-radius:4px}table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}
.err{border-color:var(--gap)}
"""


def _card(r, show_letter: bool = True, action: str = "") -> str:
    reqs = json.loads(r["reqs"] or "[]")
    req_html = "".join(f'<li><span class="st {x["s"]}">{REQ_RU.get(x["s"], x["s"])}</span>{_e(x["t"])}</li>' for x in reqs)
    link = _apply_link(r)
    channel = f"e-mail: {_e(r['email'])}" if r["channel"] == "email" else ("внешний портал" if r["channel"] == "external" else "портал работодателя")
    parts = [
        f'<div class="card"><div class="co">{_e(r["company"])}</div><div class="ti">{_e(r["title"])}</div>',
        f'<div class="meta"><span class="pill">{TIER_RU.get(r["tier"] or "", r["tier"])}</span><span class="pill">балл {r["score"]}</span>'
        f'<span class="pill">{_e(STATUS_RU.get(r["status"], r["status"]))}</span>{_e(r["location"])} · {channel}'
        + (f' · срок {_e(r["deadline"])}' if r["deadline"] else "") + "</div>",
        f'<div class="meta">{_e(r["reason"])}</div>' if r["reason"] else "",
        f'<ul class="r">{req_html}</ul>' if req_html else "",
        f'<div><a href="{_e(link)}" target="_blank" rel="noopener">Объявление / подача</a></div>' if link else "",
        f'<div class="meta">{action}</div>' if action else "",
    ]
    if show_letter and r["body"]:
        parts.append(f'<details><summary>Письмо: {_e(r["subject"])}</summary><pre>{_e(r["body"])}</pre></details>')
    if r["response_note"]:
        parts.append(f'<div class="meta"><b>Почта:</b> {_e(r["response_note"])}</div>')
    parts.append("</div>")
    return "".join(parts)


def write_html(settings, store, result) -> str:
    out = settings.data_dir / "reports"
    out.mkdir(exist_ok=True)
    rows = {s: store.by_status(s) for s in ACTIVE}
    mode = "рабочий — письма уходят" if result.mode == "live" else "пробный — работодателям ничего не отправляется"
    k = [("Просмотрено", result.collected), ("Новых", result.new), ("Отсеяно", result.filtered),
         ("В очереди", len(rows["queued"])), ("Ждут тебя", len(rows["prepared"])),
         ("Подано всего", len(rows["sent"]) + len(rows["response"]) + len(rows["submitted"]) + len(rows["applied_manually"])
          + len(rows["interview"]) + len(rows["offer"])),
         ("Ответы", len(rows["response"]))]
    body = [f"<div class='wrap'><h1>JobPilot Auto</h1><div class='meta'>{datetime.now():%d.%m.%Y %H:%M} · режим: {mode}</div>",
            "<div class='kpis'>" + "".join(f"<div class='kpi'><b>{v}</b>{_e(n)}</div>" for n, v in k) + "</div>"]
    if result.errors:
        body.append("<div class='card err'><b>Ошибки</b><ul>" + "".join(f"<li>{_e(e)}</li>" for e in result.errors) + "</ul></div>")
    if rows["send_unknown"] or rows["response"] or rows["bounced"]:
        body.append("<h2>Срочно</h2>")
        for r in rows["send_unknown"]:
            body.append(_card(r, False, "Проверь «Отправленные» в Gmail. Повторно письмо не отправляется."))
        for r in rows["response"]:
            body.append(_card(r, False, f"Отметить: <code>jp mark {_e(r['id'])} interview</code> или <code>rejected</code>"))
        for r in rows["bounced"]:
            body.append(_card(r, False, "Письмо не доставлено — подай через сайт компании."))
    if rows["queued"]:
        body.append("<h2>Очередь автоотправки</h2>")
        for r in rows["queued"]:
            body.append(_card(r, True, "" if result.mode == "live" else "Уйдёт после переключения send_mode на \"live\"."))
    if rows["prepared"]:
        body.append("<h2>Ждут твоего решения</h2>")
        for r in rows["prepared"]:
            act = (f"Письмо готово, но есть вопрос (см. выше). Отправить: двойной клик <b>todo.bat</b> → «1», или <code>jp approve {_e(r['id'])}</code>"
                   if r["channel"] == "email" else
                   "Подача через форму на сайте работодателя. Двойной клик <b>todo.bat</b> → Enter: программа откроет форму, "
                   f"заполнит поля и загрузит Anschreiben и резюме, ты проверяешь и подтверждаешь отправку. Или <code>jp fill {_e(r['id'])}</code>.")
            body.append(_card(r, True, act))
    others = rows["verified"]
    if others:
        body.append(f"<h2>Другие варианты — по желанию ({len(others)})</h2><details><summary>Смежные вакансии без действий: показать</summary><table>")
        for r in others[:60]:
            link = _apply_link(r)
            body.append(f"<tr><td>{r['score']}</td><td>{_e(r['company'])}</td><td>{_e(r['title'])}</td><td>{_e(r['reason'])}</td>"
                        f"<td>{('<a href=' + chr(34) + _e(link) + chr(34) + ' target=_blank>ссылка</a>') if link else ''}</td></tr>")
        body.append("</table></details>")
    sent = sorted(rows["sent"] + rows["submitted"] + rows["applied_manually"] + rows["interview"] + rows["offer"],
                  key=lambda r: r["sent_at"] or "", reverse=True)
    if sent:
        body.append("<h2>Подано</h2><table><tr><th>Дата</th><th>Компания</th><th>Вакансия</th><th>Канал</th><th>Статус</th></tr>")
        for r in sent:
            body.append(f"<tr><td>{_e((r['sent_at'] or '')[:16])}</td><td>{_e(r['company'])}</td><td>{_e(r['title'])}</td><td>{_e(r['email'] or 'форма на сайте')}</td>"
                        f"<td>{_e(STATUS_RU.get(r['status']))}{(' · ' + _e(r['response_note'])) if r['response_note'] else ''}</td></tr>")
        body.append("</table>")
    if result.filtered_reasons:
        body.append("<h2>Отсеяно в этом запуске</h2><table>" + "".join(
            f"<tr><td>{_e(k)}</td><td>{v}</td></tr>" for k, v in sorted(result.filtered_reasons.items(), key=lambda x: -x[1])) + "</table>")
    body.append("</div>")
    doc = f"<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>JobPilot Auto</title><style>{CSS}</style></head><body>{''.join(body)}</body></html>"
    latest = out / "latest.html"
    latest.write_text(doc, encoding="utf-8")
    (out / f"{datetime.now():%Y-%m-%d_%H%M}.html").write_text(doc, encoding="utf-8")
    return str(latest)
