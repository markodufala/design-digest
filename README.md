# Design Digest

Každé ráno vyberie ~21 najlepších dizajnov z viacerých zdrojov, pošle ich mailom
a uloží na stránku s archívom. Beží samo na GitHub Actions.

| Sekcia | Zdroje |
|---|---|
| Web | Awwwards (Site of the Day), Land-book, Minimal Gallery, One Page Love |
| Vizuál & shots | Dribbble (popular, cez Playwright), Godly |
| UI komponenty | 21st.dev (3 rotujúce kategórie denne: hero, shader, background…) |
| Retro | Web Design Museum (staré weby + história webu) |

## Ako to funguje

1. `designdigest/sources.py` — jeden fetcher na zdroj; chyba jedného zdroja nezhodí ostatné.
2. `designdigest/rank.py` — popularita sa normalizuje v rámci zdroja, potom sa
   po sekciách berú najlepšie ešte nevidené veci (striedavo zo zdrojov kvôli pestrosti).
3. `data/days/YYYY-MM-DD.json` + `data/seen.json` — archív, commitovaný späť do repa.
4. `site/` — statická stránka (GitHub Pages), `templates/email.html` — mail.

## Lokálne

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m designdigest --no-email          # scrape + build site/
.venv/bin/python -m designdigest --only godly,21st --no-email
python3 -m http.server 8765 --directory site
```

## Nastavenie na GitHube (raz)

1. **Settings → Pages → Source: GitHub Actions**
2. **Settings → Secrets and variables → Actions → New repository secret**
   - `MAIL_TO` — kam posielať
   - Gmail: `SMTP_USER` (tvoja adresa) + `SMTP_PASS` (App Password z
     myaccount.google.com/apppasswords, nie bežné heslo)
   - alebo Resend: `RESEND_API_KEY` (bez vlastnej domény posiela len na adresu účtu)
3. **Actions → Daily design digest → Run workflow** na prvý test.

Beží denne o 05:00 UTC (7:00 v lete, 6:00 v zime). GitHub cron môže meškať pár minút.

## Pridanie zdroja

Napíš async funkciu v `sources.py`, ktorá vráti `list[Item]`, a pridaj ju do
`SOURCES` a `LABELS`. Sekcia (`web`/`visual`/`ui`/`retro`) určuje, kam padne.
