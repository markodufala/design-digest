"""Things that will quietly break the digest — shown at the top of the email and page."""
from datetime import date

WARN_DAYS = 7
RENEW = ("Nový token: github.com/settings/personal-access-tokens (repo design-digest, Actions: read & write), "
         "potom v priečinku worker/: wrangler secret put GITHUB_TOKEN")


SUBSCRIBER_WARN = 90  # Resend's free tier sends 100 mails a day


def collect(today: date, status: dict, curator: dict, analysis: dict, images: dict | None = None,
            subscribers: int = 0) -> list[str]:
    out = []
    cron = status.get("cron") or {}
    if exp := cron.get("token_expires"):
        try:
            left = (date.fromisoformat(exp[:10]) - today).days
        except ValueError:
            left = None
        if left is not None and left < 0:
            out.append(f"GitHub token pre Cloudflare cron vypršal {exp[:10]} — digest teraz visí len na "
                       f"nespoľahlivom GitHub cron-e. {RENEW}")
        elif left is not None and left <= WARN_DAYS:
            out.append(f"GitHub token pre Cloudflare cron vyprší o {left} d ({exp[:10]}). {RENEW}")
    if cron.get("ok") is False:
        out.append(f"Cloudflare cron nevedel spustiť workflow ({cron.get('status')}). {RENEW}")
    elif cron.get("at") and (today - date.fromisoformat(cron["at"][:10])).days > 2:
        out.append(f"Cloudflare cron sa naposledy ozval {cron['at'][:10]} — skontroluj worker (wrangler tail).")
    if (err := curator.get("error")) and err != "disabled":
        out.append(f"Claude kurátor nefungoval, výber je len podľa popularity: {err[:140]}. "
                   "Ak ide o token: claude setup-token → GitHub secret CLAUDE_CODE_OAUTH_TOKEN.")
    if err := analysis.get("error"):
        out.append(f"Rozbor od Clauda zlyhal: {err[:140]}")
    images = images or {}
    if images.get("failed", 0) > images.get("mirrored", 0):
        out.append(f"Obrázky sa neuložili do workera ({images['failed']}× chyba), mail ich berie priamo z galérií. "
                   "Je worker nasadený v poslednej verzii? V priečinku worker/: npx wrangler deploy")
    if subscribers >= SUBSCRIBER_WARN:
        out.append(f"Máš {subscribers} odberateľov — Resend zadarmo pošle len 100 mailov denne (vrátane tvojho), "
                   "nad limit sa ďalší nedostanú. Zváž platený plán alebo týždenný výber.")
    return out
