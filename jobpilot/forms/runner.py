"""Браузерная часть автозаполнения: открывает форму в установленном Edge/Chrome, заполняет, грузит файлы,
показывает тебе и отправляет только после твоего «Enter» (или по auto_submit, если ты включил).

CAPTCHA, вход в аккаунты и галочки согласия программа не трогает — это делаешь ты в том же окне.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import documents
from .fieldmap import ACCOUNT_ATS, Plan, build_profile, classify, detect_ats, fold, outcome_from, plan_fields

log = logging.getLogger("jobpilot.forms")

# --------------------------------------------------------------------------- JavaScript в странице

COLLECT_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = n => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    let t = '';
    if (el.id) { try { const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]'); if (l) t = txt(l); } catch (e) {} }
    if (!t) { const l = el.closest('label'); if (l) t = txt(l); }
    if (!t && el.getAttribute('aria-labelledby')) t = el.getAttribute('aria-labelledby').split(' ').map(i => txt(document.getElementById(i))).join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t && el.tagName === 'SELECT' && el.options.length && !el.options[0].value) t = txt(el.options[0]);  // «Anrede *», «Wie bist Du … *»
    if (!t) { let p = el.parentElement; for (let i = 0; i < 3 && p && !t; i++, p = p.parentElement) {
      if (p.querySelectorAll('input:not([type=hidden]),select,textarea').length > 1 && el.type !== 'radio') break;
      const lab = p.querySelector('label,legend'); if (lab && !lab.contains(el)) t = txt(lab); } }
    if (el.type === 'radio') {   // вопрос группы (legend / radiogroup) важнее подписи варианта «ja»
      const fs = el.closest('fieldset, [role=radiogroup]');
      const q = fs ? (txt(fs.querySelector('legend')) || fs.getAttribute('aria-label') || '') : '';
      if (q && !t.includes(q)) t = q + ' — ' + t;
    }
    return (t || '').slice(0, 200);
  };
  const ctxs = el => { const out = []; let p = el; for (let i = 0; i < 4 && p.parentElement; i++) { p = p.parentElement; out.push(txt(p).slice(0, 240)); } return out; };
  const forms = [...document.forms];
  const out = [];
  let i = 0;
  document.querySelectorAll('input,select,textarea').forEach(el => {
    const type = (el.type || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    const idx = i++;
    el.setAttribute('data-jp', String(idx));
    const label = labelOf(el);
    const d = { idx, tag: el.tagName.toLowerCase(), type, id: el.id || '', name: el.name || '', label,
      placeholder: el.placeholder || '', autocomplete: el.getAttribute('autocomplete') || '', aria: el.getAttribute('aria-label') || '',
      required: !!(el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label)),
      visible: vis(el), checked: !!el.checked, value: (type === 'checkbox' || type === 'radio') ? '' : (el.value || ''),
      multiple: !!el.multiple, accept: el.accept || '', options: el.tagName === 'SELECT' ? [...el.options].map(o => txt(o)) : [],
      in_form: !!el.form, form: el.form ? forms.indexOf(el.form) : -1, context: '', contexts: [] };
    if (['file', 'checkbox', 'radio', 'select-one'].includes(type)) { d.contexts = ctxs(el); d.context = d.contexts[1] || d.contexts[0] || ''; }
    out.push(d);
  });
  return out;
}
"""

CAPTCHA_JS = r"""
() => {
  const q = s => document.querySelector(s);
  if (q('.frc-captcha, [data-friendly-captcha], iframe[src*="friendlycaptcha"]')) return 'FriendlyCaptcha';
  if (q('.h-captcha, iframe[src*="hcaptcha.com"]')) return 'hCaptcha';
  if (q('.cf-turnstile, iframe[src*="challenges.cloudflare.com"]')) return 'Cloudflare Turnstile';
  const box = q('.g-recaptcha:not([data-size="invisible"])');
  const anchor = [...document.querySelectorAll('iframe[src*="recaptcha"]')].find(f => /anchor/.test(f.src) && !/size=invisible/.test(f.src));
  if (box || anchor) return 'reCAPTCHA';
  return '';
}
"""

SIGNALS_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = n => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const inputs = [...document.querySelectorAll('input[type=text],input[type=email],input[type=tel],input:not([type]),textarea')].filter(vis);
  const errSel = '[aria-invalid="true"], .error, .errors, .invalid-feedback, .has-error, .field-error, .form-error, .alert-danger, .alert-error, .is-invalid, [role=alert], [class*="error-message"], [class*="errorMessage"]';
  const errors = [...document.querySelectorAll(errSel)].filter(vis).map(e => {
    if (e.matches('input,select,textarea')) { const l = e.id && document.querySelector('label[for="' + e.id + '"]'); return 'Pflichtfeld: ' + (l ? txt(l) : (e.name || '')); }
    return txt(e).slice(0, 200); }).filter(Boolean);
  // встроенная проверка браузера: форма без novalidate с незаполненным обязательным полем просто не отправляется
  [...document.forms].filter(f => !f.noValidate).forEach(f => [...f.elements].filter(x => x.willValidate && !x.checkValidity() && vis(x)).forEach(x => {
    const l = (x.id && document.querySelector('label[for="' + x.id + '"]')) || x.closest('label');
    errors.push('Pflichtfeld: ' + (l ? txt(l).slice(0, 120) : (x.name || x.id || x.type)));
  }));
  errors.splice(12);
  return { text: txt(document.body).slice(0, 20000), url: location.href, inputs: inputs.length,
           password: [...document.querySelectorAll('input[type=password]')].some(vis), errors };
}
"""

APPLY_BUTTON_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = n => ((n && (n.innerText || n.textContent || n.value)) || '').replace(/\s+/g, ' ').trim();
  const good = /^(jetzt |online |direkt |hier |gleich )?(online[- ])?(bewerben|bewerbung starten|zur bewerbung|zum bewerbungsformular|bewerbungsformular|apply|apply now|apply for this (job|position)|apply here|jetzt online bewerben)\b/i;
  const bad = /initiativ|login|anmelden|registr|speichern|merken|teilen|share|linkedin|xing|indeed/i;
  const c = [...document.querySelectorAll('a, button, input[type=button], input[type=submit], [role=button]')]
    .filter(e => vis(e) && good.test(txt(e)) && !bad.test(txt(e)));
  document.querySelectorAll('[data-jp-apply]').forEach(e => e.removeAttribute('data-jp-apply'));
  if (!c.length) return '';
  c[0].setAttribute('data-jp-apply', '1');
  return txt(c[0]).slice(0, 60);
}
"""

SUBMIT_JS = r"""
(formIndex) => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = n => ((n && (n.innerText || n.textContent || n.value)) || '').replace(/\s+/g, ' ').trim();
  const root = (formIndex >= 0 && document.forms[formIndex]) ? document.forms[formIndex] : document;
  const good = /absenden|abschicken|abschlie(ss|ß)en|einreichen|senden|submit|send application|apply now|^apply$|jetzt bewerben|bewerbung (ab|ein|ver)|^bewerben$/i;
  const bad = /initiativ|automatisch|zur(ü|ue)ck|stellenanzeige|abbrechen|cancel|login|anmelden|registr|hochladen|upload|datei|suche|search|cookie/i;
  const c = [...root.querySelectorAll('button, input[type=submit], [role=button], a.btn, a.button')].filter(e => vis(e) && !bad.test(txt(e)));
  const best = c.find(b => b.type === 'submit' && good.test(txt(b))) || c.find(b => good.test(txt(b))) || c.find(b => b.type === 'submit');
  document.querySelectorAll('[data-jp-submit]').forEach(e => e.removeAttribute('data-jp-submit'));
  if (!best) return '';
  best.setAttribute('data-jp-submit', '1');
  return txt(best).slice(0, 60) || 'submit';
}
"""

COOKIE_JS = r"""
() => {
  const vis = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
                      return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const txt = n => ((n && (n.innerText || n.textContent)) || '').replace(/\s+/g, ' ').trim();
  const ids = ['#CybotCookiebotDialogBodyButtonDecline', '#onetrust-reject-all-handler', '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowallSelection'];
  for (const s of ids) { const b = document.querySelector(s); if (b && vis(b)) { b.click(); return txt(b) || s; } }
  const roots = [document];
  const uc = document.querySelector('#usercentrics-root, #usercentrics-cmp-ui');
  if (uc && uc.shadowRoot) roots.push(uc.shadowRoot);
  const deny = /^(alle )?ablehnen$|^ablehnen( und schlie(ss|ß)en)?$|nur (technisch )?(notwendige|erforderliche|essenzielle|essentielle)( cookies)?( (akzeptieren|zulassen|erlauben|verwenden))?$|^(reject|decline)( all)?( cookies)?$|necessary only|only (essential|necessary)|^nicht zustimmen$|^weiter ohne (einwilligung|zustimmung)$/i;
  for (const r of roots) {
    const testid = r.querySelector('[data-testid="uc-deny-all-button"]'); if (testid) { testid.click(); return 'deny-all'; }
    const b = [...r.querySelectorAll('button, a, [role=button]')].find(e => vis(e) && deny.test(txt(e)));
    if (b) { b.click(); return txt(b); }
  }
  return '';
}
"""

REFRESH_SELECT_JS = r"""
(idx) => {
  const el = document.querySelector('[data-jp="' + idx + '"]'); if (!el) return;
  el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
  if (window.jQuery) { try { const $el = window.jQuery(el); if ($el.selectpicker) $el.selectpicker('refresh'); $el.trigger('change'); } catch (e) {} }
}
"""

SELECT_BY_TEXT_JS = r"""
([idx, wanted]) => {
  const el = document.querySelector('[data-jp="' + idx + '"]'); if (!el) return false;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const opt = [...el.options].find(o => norm(o.text) === norm(wanted)); if (!opt) return false;
  el.value = opt.value; [...el.options].forEach(o => o.selected = (o === opt));
  el.dispatchEvent(new Event('input', { bubbles: true })); el.dispatchEvent(new Event('change', { bubbles: true }));
  return true;
}
"""

HIGHLIGHT_JS = r"""
() => {
  let n = 0;
  document.querySelectorAll('[data-jp]').forEach(el => {
    const t = (el.type || '').toLowerCase();
    const empty = (t === 'checkbox' || t === 'radio') ? !el.checked : (t === 'file' ? !(el.files && el.files.length) : !el.value);
    const req = el.required || el.getAttribute('aria-required') === 'true';
    if (req && empty && t !== 'file') { el.style.outline = '3px solid #e8590c'; el.style.outlineOffset = '2px'; n++; }
  });
  return n;
}
"""


# --------------------------------------------------------------------------- ввод/вывод в консоли

class ConsoleIO:
    def say(self, text: str) -> None:
        print(text, flush=True)

    def ask(self, prompt: str) -> str:
        try:
            return input(prompt)
        except EOFError:
            return "q"


@dataclass
class FillReport:
    ats: str = ""
    url: str = ""
    filled: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    todo: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    captcha: str = ""
    account: bool = False
    form_found: bool = False
    form_index: int = -1
    frame_url: str = ""

    def lines(self) -> list[str]:
        out = []
        if self.ats:
            out.append(f"   Система подачи: {self.ats}")
        if self.filled:
            out.append("   Заполнил: " + ", ".join(self.filled))
        for f in self.files:
            out.append("   Файл: " + f)
        if self.captcha:
            out.append(f"   ! Капча ({self.captcha}) — пройди её сам в окне браузера, я её не трогаю.")
        for t in self.todo:
            out.append("   ! " + t)
        for t in self.failed:
            out.append("   ? не получилось: " + t)
        for t in self.skipped:
            out.append("   · " + t)
        return out


# --------------------------------------------------------------------------- сессия браузера

def available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _short(exc: Exception) -> str:
    return str(exc).strip().split("\n")[0][:140]


class FormSession:
    def __init__(self, settings, headless: bool = False, channel: str | None = None, profile_dir: Path | None = None):
        from playwright.sync_api import sync_playwright
        self.settings = settings
        fm = settings.cfg.get("forms", {})
        self._pw = sync_playwright().start()
        self.submitting = False
        profile = profile_dir or (settings.data_dir / "browser-profile")
        profile.mkdir(parents=True, exist_ok=True)
        wanted = channel if channel is not None else fm.get("browser", "msedge")
        chain = [wanted] + [c for c in ("msedge", "chrome", "chromium") if c != wanted]
        last = None
        self.ctx = None
        for ch in chain:
            try:
                kwargs = {"headless": headless, "viewport": None if not headless else {"width": 1280, "height": 1600},
                          "accept_downloads": False, "locale": "de-DE"}
                if ch and ch != "chromium":
                    kwargs["channel"] = ch
                if not headless:
                    kwargs["args"] = ["--start-maximized"]
                self.ctx = self._pw.chromium.launch_persistent_context(str(profile), **kwargs)
                self.channel = ch
                break
            except Exception as exc:
                last = exc
        if self.ctx is None:
            self._pw.stop()
            raise RuntimeError(f"не удалось запустить браузер (Edge/Chrome): {_short(last)}")
        self.ctx.on("page", self._on_page)
        for p in self.ctx.pages:
            self._on_page(p)

    def _on_page(self, page) -> None:
        page.on("dialog", self._on_dialog)

    def _on_dialog(self, dialog) -> None:
        log.info("диалог страницы: %s", dialog.message)
        try:
            dialog.accept() if self.submitting else dialog.dismiss()
        except Exception:
            pass

    def page(self):
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        return pages[-1] if pages else self.ctx.new_page()

    def open(self, url: str):
        page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        self._settle(page)
        return page

    def close(self) -> None:
        try:
            self.ctx.close()
        finally:
            self._pw.stop()

    # ---------------------------------------------------------------- шаги
    @staticmethod
    def _settle(page, ms: int = 1500) -> None:
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(ms)

    def dismiss_cookies(self, page) -> str:
        for frame in page.frames:
            try:
                res = frame.evaluate(COOKIE_JS)
                if res:
                    page.wait_for_timeout(800)
                    return res
            except Exception:
                continue
        return ""

    def scan(self, page) -> list[tuple[object, list[dict], int]]:
        """Кадры с формой: (frame, поля, число распознанных полей)."""
        found = []
        for frame in page.frames:
            try:
                descs = frame.evaluate(COLLECT_JS)
            except Exception:
                continue
            known = [d for d in descs if classify(d) not in ("skip", "unknown", "checkbox_other") and (d.get("visible") or d.get("type") == "file")]
            if len(known) >= 2:
                found.append((frame, descs, len(known)))
        found.sort(key=lambda x: -x[2])
        return found

    def has_visible_form(self, page) -> bool:
        """Есть ли на странице видимые поля формы (а не только скрытая форма за кнопкой «Jetzt bewerben»)."""
        for _frame, descs, _n in self.scan(page):
            if any(d.get("visible") and d.get("type") != "file" and classify(d) not in ("skip", "unknown", "checkbox_other")
                   for d in descs):
                return True
        return False

    def advance(self, page):
        """Нажимает «Jetzt bewerben», если на странице объявление, а не форма. Возвращает актуальную страницу."""
        for frame in page.frames:
            try:
                label = frame.evaluate(APPLY_BUTTON_JS)
            except Exception:
                continue
            if not label:
                continue
            before = len(self.ctx.pages)
            try:
                frame.locator("[data-jp-apply]").first.click(timeout=5000)
            except Exception:
                continue
            page.wait_for_timeout(1500)
            if len(self.ctx.pages) > before:
                page = self.ctx.pages[-1]
            self._settle(page)
            return page, label
        return page, ""

    def fill(self, page, plan_ctx: dict, with_files: bool = True) -> FillReport:
        rep = FillReport(url=page.url)
        try:
            html = page.content()
        except Exception:
            html = ""
        rep.ats = detect_ats(page.url, html)
        try:
            sig = page.main_frame.evaluate(SIGNALS_JS)
        except Exception:
            sig = {"password": False}
        frames = self.scan(page)
        if not frames:
            rep.account = rep.ats in ACCOUNT_ATS or bool(sig.get("password"))
            return rep
        rep.form_found = True
        profile, job, docs, letter_text = plan_ctx["profile"], plan_ctx["job"], plan_ctx["docs"], plan_ctx["letter_text"]
        for n, (frame, descs, _) in enumerate(frames[:2]):
            plan = plan_fields(descs, profile, job, letter_text, docs if (n == 0 and with_files) else {})
            if not (n == 0 and with_files):
                plan.actions = [a for a in plan.actions if a.kind != "files"]
                plan.todo = [t for t in plan.todo if "резюме" not in t]
            if n == 0:
                forms = [d.get("form", -1) for d in descs if classify(d) not in ("skip", "unknown")]
                rep.form_index = max(set(forms), key=forms.count) if forms else -1
                rep.frame_url = frame.url
            self._apply(frame, plan, rep)
            rep.todo += plan.todo
            rep.skipped += plan.skipped
        main = frames[0][0]
        if rep.files:
            main.wait_for_timeout(2500)
            self._doc_types(main, docs, rep)
        for frame, _, _ in frames[:2]:
            try:
                rep.captcha = rep.captcha or frame.evaluate(CAPTCHA_JS)
                frame.evaluate(HIGHLIGHT_JS)
            except Exception:
                pass
        try:
            rep.captcha = rep.captcha or page.main_frame.evaluate(CAPTCHA_JS)
        except Exception:
            pass
        return rep

    def _apply(self, frame, plan: Plan, rep: FillReport) -> None:
        for a in plan.actions:
            loc = frame.locator(f'[data-jp="{a.idx}"]').first
            try:
                if a.kind == "fill":
                    loc.fill(str(a.value), timeout=4000)
                    shown = str(a.value) if a.key != "message" else "текст письма"
                    rep.filled.append(f"{a.label} = {shown[:40]}")
                elif a.kind == "select":
                    try:
                        loc.select_option(label=str(a.value), timeout=3000, force=True)
                    except Exception:
                        if not frame.evaluate(SELECT_BY_TEXT_JS, [a.idx, str(a.value)]):
                            raise
                    frame.evaluate(REFRESH_SELECT_JS, a.idx)
                    rep.filled.append(f"{a.label} = {a.value}")
                elif a.kind == "check":
                    loc.check(timeout=3000, force=True)
                    rep.filled.append(f"{a.label[:50]} = ✓")
                elif a.kind == "files":
                    loc.set_input_files(list(a.value), timeout=10000)
                    rep.files += [f"{Path(x).name} → «{a.label[:40]}»" for x in a.value]
            except Exception as exc:
                rep.failed.append(f"«{a.label[:50]}»: {_short(exc)}")

    def _doc_types(self, frame, docs: dict, rep: FillReport) -> None:
        """После загрузки: если у каждого файла есть выбор типа (concludis и др.) — выставить по имени файла."""
        try:
            descs = frame.evaluate(COLLECT_JS)
        except Exception:
            return
        for d in descs:
            if d.get("tag") != "select":
                continue
            opts = [o for o in d.get("options") or []]
            if not any(re.search(r"lebenslauf|^cv$|resume", fold(o)) for o in opts):
                continue
            hay = fold(" ".join([d.get("label", "")] + list(d.get("contexts") or [])[:2]))
            m = re.search(r"([\w\-\.\[\]]+\.(pdf|docx?|odt))", hay)
            if not m:
                continue
            fname = m.group(1)
            if re.search(r"lebenslauf|\bcv\b|resume", fname):
                want = [r"^lebenslauf$", r"lebenslauf", r"^cv$", r"resume"]
            elif re.search(r"anschreiben|cover", fname):
                want = [r"^anschreiben$", r"anschreiben", r"cover"]
            elif re.search(r"zeugnis|certificate", fname):
                want = [r"^zeugnis$", r"^arbeitszeugnis$", r"zeugnis", r"certificate"]
            else:
                continue
            choice = None
            for rx in want:
                choice = next((o for o in opts if re.search(rx, fold(o))), None)
                if choice:
                    break
            if not choice:
                continue
            current = frame.evaluate("(i) => { const e = document.querySelector('[data-jp=\"' + i + '\"]'); return e && e.selectedIndex >= 0 ? e.options[e.selectedIndex].text.trim() : ''; }", d["idx"])
            if current == choice:
                continue
            ok = False
            try:  # как человек: открыть список рядом с файлом и выбрать пункт
                wrap = frame.locator(f'[data-jp="{d["idx"]}"]').locator("xpath=..")
                wrap.click(timeout=2000)
                wrap.get_by_text(choice, exact=True).last.click(timeout=2000)
                ok = True
            except Exception:
                pass
            if not ok:
                try:
                    frame.locator(f'[data-jp="{d["idx"]}"]').select_option(label=choice, timeout=2000, force=True)
                    frame.evaluate(REFRESH_SELECT_JS, d["idx"])
                    ok = True
                except Exception as exc:
                    rep.failed.append(f"тип файла {fname}: {_short(exc)}")
            if ok:
                rep.filled.append(f"тип файла {fname[:30]} = {choice}")

    def submit(self, page, form_index: int) -> str:
        for frame in page.frames:
            try:
                label = frame.evaluate(SUBMIT_JS, form_index if frame == page.main_frame else -1)
            except Exception:
                continue
            if label:
                self.submitting = True
                frame.locator("[data-jp-submit]").first.click(timeout=8000)
                return label
        return ""

    def outcome(self, page, wait_s: int = 25) -> tuple[str, list[str]]:
        last_errors: list[str] = []
        invalid_hits = 0
        for i in range(wait_s):
            page.wait_for_timeout(1000)
            page = self.page()
            texts, errors, inputs, url = [], [], 0, page.url
            for frame in page.frames:
                try:
                    s = frame.evaluate(SIGNALS_JS)
                except Exception:
                    continue
                texts.append(s["text"])
                errors += s["errors"]
                inputs += s["inputs"]
            res = outcome_from(" ".join(texts), url, inputs >= 2, errors)
            if res == "success":
                return "success", []
            if res == "invalid":
                invalid_hits += 1
                last_errors = errors
                if invalid_hits >= 3:
                    return "invalid", errors
        return ("invalid", last_errors) if last_errors and invalid_hits else ("unknown", [])


# --------------------------------------------------------------------------- сценарий для одной вакансии

DESC_URL = re.compile(r"https?://[^\s\)\]>\"']+")


def best_url(row) -> str:
    for k in ("apply_url", "external_url"):
        if row[k]:
            return row[k]
    urls = DESC_URL.findall(row["description"] or "")
    urls = [u.rstrip(".,;#") for u in urls if "arbeitsagentur.de" not in u]
    ranked = sorted(urls, key=lambda u: (not re.search(r"bewerb|apply|karriere|career|jobs|stellen", u.lower()), len(u)))
    specific = [u for u in ranked if re.search(r"\d{4,}|/job/|/jobs/.+|stellenangebot|stellenboerse/.+", u.lower())]
    if specific:
        return specific[0]
    return row["source_url"] or (ranked[0] if ranked else "")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _save_form_info(store, job_id: str, rep: FillReport) -> None:
    row = store.get(job_id)
    try:
        extra = json.loads(row["extra"] or "{}")
    except Exception:
        extra = {}
    extra["form"] = {"at": _now(), "ats": rep.ats, "url": rep.frame_url or rep.url, "todo": rep.todo[:10], "captcha": rep.captcha}
    store.update(job_id, None, extra=json.dumps(extra, ensure_ascii=False))


MENU = "   [Enter] отправить (нажму кнопку сам) · f заполнить снова · s я отправил сам · n не подаю · q позже\n   > "


def fill_job(settings, store, job_id: str, url: str | None = None, io=None, headless: bool = False,
             session_factory=None) -> str:
    """Полный сценарий для одной вакансии. Возвращает итоговый статус."""
    io = io or ConsoleIO()
    row = store.get(job_id)
    if not row:
        io.say("Вакансия не найдена.")
        return ""
    if row["status"] in ("submitted", "applied_manually", "sent", "attempted", "send_unknown"):
        io.say(f"По этой вакансии уже есть заявка или попытка ({row['status']}). Повторно не подаю.")
        return row["status"]
    cooldown = int(settings.run.get("company_cooldown_days", 30))
    hist = store.in_history(row["company"], row["title"], row["email"] or "")
    recent = store.company_recent(row["company_key"], cooldown, exclude_id=job_id)
    if hist or recent:
        why = hist or f"в {row['company']} уже подавался за последние {cooldown} дней"
        io.say(f"   Внимание: {why}.")
        if io.ask("   Всё равно продолжить? y/n > ").strip().lower() != "y":
            return row["status"]

    docs = documents.prepare(settings, row)
    if not docs.get("cv"):
        io.say(f"Не найден файл резюме: {settings.cv_path}")
        return row["status"]
    try:
        extra = json.loads(row["extra"] or "{}")
    except Exception:
        extra = {}
    ctx = {"profile": build_profile(settings), "docs": docs, "letter_text": documents.letter_text_plain(row["body"] or ""),
           "job": {"source": row["source"], "via": extra.get("via", ""), "company": row["company"], "title": row["title"]}}
    target = url or best_url(row)
    io.say(f"   Открываю: {target}")
    io.say(f"   Файлы: {', '.join(Path(p).name for p in [docs.get('letter'), docs.get('cv'), *docs.get('certificates', [])] if p)}")
    fm = settings.cfg.get("forms", {})
    make = session_factory or (lambda: FormSession(settings, headless=headless))
    try:
        session = make()
    except Exception as exc:
        io.say(f"   Браузер не запустился: {_short(exc)}")
        return row["status"]
    status = row["status"]
    try:
        page = session.open(target)
        session.dismiss_cookies(page)
        if not session.has_visible_form(page):
            page, label = session.advance(page)
            if label:
                io.say(f"   Нажал «{label}».")
                session.dismiss_cookies(page)
        rep = session.fill(page, ctx)
        auto_once = bool(fm.get("auto_submit", False))
        uploaded_at = {rep.frame_url or rep.url} if rep.files else set()
        show = True
        while True:
            if not show:
                pass
            elif rep.account:
                io.say("   Здесь нужен вход или регистрация. Войди сам в окне браузера, дойди до формы и нажми f.")
            elif not rep.form_found:
                io.say("   Формы на странице не вижу. Открой форму сам (кнопка «Bewerben») и нажми f.")
            else:
                _save_form_info(store, job_id, rep)
                for line in rep.lines():
                    io.say(line)
                io.say("   Проверь форму в окне браузера." + (" Оранжевым выделено то, что осталось тебе." if rep.todo else ""))
            show = False
            clean = rep.form_found and not rep.todo and not rep.captcha and not rep.failed
            if auto_once and clean:
                ans = ""
                io.say("   auto_submit включён и всё заполнено — отправляю.")
            else:
                ans = io.ask(MENU).strip().lower()
            auto_once = False
            if ans == "f":
                page = session.page()
                session.dismiss_cookies(page)
                first = session.fill(page, ctx, with_files=False)
                again = (first.frame_url or first.url) in uploaded_at
                rep = first if again else session.fill(page, ctx, with_files=True)
                if rep.files:
                    uploaded_at.add(rep.frame_url or rep.url)
                if again:
                    rep.skipped.append("файлы на этой странице уже загружены — повторно не гружу")
                show = True
                continue
            if ans == "n":
                store.update(job_id, "skipped", "пропущено при заполнении формы")
                return "skipped"
            if ans == "q":
                return status
            if ans == "s":
                page = session.page()
                res, _ = session.outcome(page, wait_s=3)
                if res == "success":
                    store.update(job_id, "submitted", "форма: отправил ты, сайт подтвердил", sent_at=_now())
                    io.say("   Отмечено: подано, сайт подтвердил.")
                    return "submitted"
                store.update(job_id, "applied_manually", "форма: отправил ты", sent_at=_now())
                io.say("   Отмечено: подано вручную.")
                return "applied_manually"
            if ans != "":
                continue
            page = session.page()
            store.update(job_id, "attempted", "форма: нажимаю кнопку отправки", sent_at=_now())
            label = ""
            try:
                label = session.submit(page, rep.form_index)
            except Exception as exc:
                io.say(f"   Кнопку нажать не вышло: {_short(exc)}")
            if not label:
                store.update(job_id, "prepared", "форма: кнопка отправки не найдена — ничего не отправлено", sent_at=None)
                io.say("   Не нашёл кнопку отправки. Нажми её сам и выбери s.")
                continue
            io.say(f"   Нажал «{label}», жду ответ сайта…")
            res, errors = session.outcome(session.page())
            if res == "success":
                store.update(job_id, "submitted", f"форма: сайт подтвердил («{label}»)")
                io.say("   Готово: сайт подтвердил отправку.")
                return "submitted"
            if res == "invalid":
                store.update(job_id, "prepared", "форма не принята: " + "; ".join(errors[:3]), sent_at=None)
                io.say("   Сайт не принял форму:")
                for e in errors[:6]:
                    io.say("     – " + e)
                io.say("   Исправь в окне и снова нажми Enter.")
                session.submitting = False
                continue
            ans2 = io.ask("   Не вижу подтверждения. Посмотри в окно: заявка ушла? y — да, n — нет, ? — не понятно > ").strip().lower()
            if ans2 == "y":
                store.update(job_id, "submitted", "форма: подтверждение увидел ты")
                return "submitted"
            if ans2 == "n":
                store.update(job_id, "prepared", "форма: не отправилась (проверено тобой)", sent_at=None)
                session.submitting = False
                continue
            store.update(job_id, "send_unknown", "форма: результат неясен — повторно не подаю, проверь почту")
            return "send_unknown"
    except Exception as exc:
        msg = _short(exc)
        if "closed" in msg.lower() or "Target" in msg:
            io.say("   Окно браузера закрыто.")
            cur = store.get(job_id)["status"]
            if cur == "attempted":
                store.update(job_id, "send_unknown", "форма: окно закрыто после нажатия — проверь почту")
                return "send_unknown"
            ans = io.ask("   Ты успел отправить заявку? y/n > ").strip().lower()
            if ans == "y":
                store.update(job_id, "applied_manually", "форма: отправил ты", sent_at=_now())
                return "applied_manually"
            return cur
        log.exception("ошибка автозаполнения")
        io.say(f"   Ошибка: {msg}")
        cur = store.get(job_id)["status"]
        if cur == "attempted":
            store.update(job_id, "send_unknown", f"форма: ошибка после нажатия ({msg[:80]}) — повторно не подаю")
            return "send_unknown"
        return cur
    finally:
        try:
            session.close()
        except Exception:
            pass
