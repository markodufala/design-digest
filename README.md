# Design Digest

Každé ráno vyberie ~21 najlepších dizajnov z viacerých zdrojov, pošle ich mailom
a uloží na stránku s archívom. Beží samo na GitHub Actions.

| Sekcia | Zdroje |
|---|---|
| Web | Awwwards (Site of the Day), Land-book, Minimal Gallery, One Page Love |
| Vizuál & shots | Dribbble (popular, cez Playwright), Godly |
| UI komponenty | 21st.dev (3 rotujúce kategórie denne: hero, shader, background…) |
| Typo | Fonts In Use (písmo v reálnych projektoch, s menami fontov) |
| Retro | Web Design Museum (staré weby + história webu) |

## Ako to funguje

1. `designdigest/sources.py` — jeden fetcher na zdroj; chyba jedného zdroja nezhodí ostatné.
2. `designdigest/rank.py` — popularita sa normalizuje v rámci zdroja, potom sa
   po sekciách berú najlepšie ešte nevidené veci (striedavo zo zdrojov kvôli pestrosti).
3. `designdigest/analyze.py` — rozbor vybraných vecí: otvorí živý web (Playwright), vytiahne
   fonty, paletu a stack (GSAP, Three.js, Webflow…), potom Claude napíše „čo si z toho zobrať“.
4. Profil vkusu — Claude ho raz týždenne prepíše z ♥ / ✕ (`curate.write_profile`), uložený je
   vo workeri (`/taste`) a dá sa upraviť na stránke, ak ju otvoríš cez odkaz v maile (`#k=…`).
   Kurátor ho berie ako hlavný vstup.
5. `designdigest/alerts.py` — výstrahy navrchu mailu: GitHub token workera expiruje do 7 dní
   (worker si pri crone ukladá hlavičku `github-authentication-token-expiration`, `/status`),
   cron zlyhal, kurátor alebo rozbor nefungoval.
6. `designdigest/mirror.py` — obrázky vybraných vecí orezané na 4:3 sa ukladajú do workera
   (KV, `/img/<key>.jpg`), aby archív a staré maily neostali prázdne, keď galéria obrázok zmaže.
   Zlyhané zdroje sa skúšajú druhýkrát; rovnaký web z viacerých galérií prejde len raz
   (`Item.dedupe_keys`: adresa webu / názov, ukladajú sa aj do `seen.json`).
7. `data/days/YYYY-MM-DD.json` + `data/seen.json` — archív, commitovaný späť do repa.
8. `site/` — statická stránka (GitHub Pages), `templates/email.html` — mail (drž ho v súlade s `page.html`).

## Lokálne

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python -m designdigest --no-email          # scrape + build site/
.venv/bin/python -m designdigest --only godly,21st --no-email --no-rozbor
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

## Odber noviniek (zatiaľ vypnutý)

Hotové, ale spí, kým nie je overená doména — z `onboarding@resend.dev` smie Resend
posielať len majiteľovi účtu. Výber riadi len tvoj vkus; odberatelia dostanú rovnaký
digest bez hlasovania, výstrah a kľúča, s odhlásením jedným klikom (`List-Unsubscribe`).

- Prihlásenie s potvrdením mailom (double opt-in), adresy v KV workera (`sub:<hash>`),
  ochrana: honeypot, 5 potvrdení na IP denne, 20 spolu (Resend zadarmo = 100 mailov/deň).
- Hlasovať ♥ / ✕ a upravovať vkus môže len zariadenie s kľúčom z tvojho mailu (`#k=`).

Zapnutie:
1. Resend → Domains → pridaj doménu a nastav DNS záznamy, ktoré ukáže.
2. GitHub secret `MAIL_FROM` = `Design Digest <digest@tvoja-domena.sk>`
3. V `worker/`: `npx wrangler secret put RESEND_API_KEY`, `npx wrangler secret put MAIL_FROM`
   (rovnaké hodnoty), potom `npx wrangler deploy`. Formulár sa na stránke ukáže sám.

## Pridanie zdroja

Napíš async funkciu v `sources.py`, ktorá vráti `list[Item]`, a pridaj ju do
`SOURCES` a `LABELS`. Sekcia (`web`/`visual`/`ui`/`retro`) určuje, kam padne.
