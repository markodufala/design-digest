// Design Digest feedback: stores ♥ / ✕ votes in KV.
//   GET  /votes                -> {id: {v, at}}            (read by the daily job + the site)
//   POST /vote  {id, v, s, k}  -> from the site (fetch); `k` = owner key, only the owner's taste counts
//   GET  /v?id=&v=&s=          -> from email links, answers with a tiny HTML page
//   GET  /taste                -> the taste profile {text, generated_at, edited_at}
//   POST /taste {text, s, by}  -> Claude (daily job) or the person (site) rewrites it
//   GET  /status               -> what the cron saw last: {cron: {at, ok, status, token_expires}}
//   POST /img?key=&s=  (jpeg)  -> the daily job mirrors each pick's image here…
//   GET  /img/<key>.jpg        -> …so the archive survives galleries deleting or hotlink-blocking them
//   GET  /subscribe            -> {enabled}: the site only shows the form once mail is configured
//   POST /subscribe {email}    -> double opt-in: stores a pending address, mails a confirm link
//   GET  /confirm?e=&t=        -> activates it
//   GET|POST /unsubscribe?e=&s=  -> one click (POST = RFC 8058 List-Unsubscribe-Post)
//   GET  /subscribers?s=       -> active addresses, for the daily job
// `s` is an HMAC of what it authorises (item id, "owner", "img:<key>", "unsub:<id>", "subscribers").
// The owner key arrives on the owner's devices through the email's #k= links.
//
// Cron (wrangler.toml): starts the daily GitHub workflow, because GitHub's own
// schedule sometimes just doesn't fire. `if_missing` makes it a no-op once today's
// digest exists, so this and GitHub's cron can both fire without a second email.

const KEY = "votes";
const TASTE = "taste";
const OWNER = "owner";
const CRON = "cron";
const SUB = "sub:";
const PER_IP_PER_DAY = 5;       // confirmation mails one visitor can trigger
const CONFIRMS_PER_DAY = 20;    // all together — Resend's free tier is 100 mails a day
const RESEND_CONFIRM_AFTER = 24 * 3600 * 1000;

export default {
  async fetch(req, env, ctx) {
    const url = new URL(req.url);
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (req.method === "OPTIONS") return new Response(null, { headers: cors });

    if (url.pathname === "/votes" && req.method === "GET") {
      const votes = (await env.VOTES.get(KEY, "json")) || {};
      return Response.json(votes, { headers: { ...cors, "Cache-Control": "no-store" } });
    }

    if (url.pathname === "/vote" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      // the page is public; without this anyone could steer the owner's digest
      if (body.k !== (await hmac(env.SIGNING_KEY, OWNER))) {
        return Response.json({ ok: false, error: "not the owner" }, { status: 403, headers: cors });
      }
      const res = await record(env, body.id, Number(body.v), body.s);
      return Response.json(res, { status: res.ok ? 200 : 400, headers: cors });
    }

    if (url.pathname === "/v" && req.method === "GET") {
      const v = Number(url.searchParams.get("v"));
      const res = await record(env, url.searchParams.get("id"), v, url.searchParams.get("s"));
      return page(res.ok ? (v === 1 ? "♥ Uložené" : "✕ Toto už neuvidíš") : "Neplatný odkaz", env.SITE_URL,
        "Zajtrajší výber sa podľa toho prispôsobí.");
    }

    if (url.pathname === "/taste" && req.method === "GET") {
      const taste = (await env.VOTES.get(TASTE, "json")) || {};
      return Response.json(taste, { headers: { ...cors, "Cache-Control": "no-store" } });
    }

    if (url.pathname === "/taste" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const text = String(body.text ?? "").trim().slice(0, 2000);
      if (body.s !== (await hmac(env.SIGNING_KEY, OWNER))) {
        return Response.json({ ok: false, error: "bad signature" }, { status: 403, headers: cors });
      }
      const taste = (await env.VOTES.get(TASTE, "json")) || {};
      const now = new Date().toISOString();
      Object.assign(taste, { text }, body.by === "claude" ? { generated_at: now } : { edited_at: now });
      await env.VOTES.put(TASTE, JSON.stringify(taste));
      return Response.json({ ok: true, ...taste }, { headers: cors });
    }

    if (url.pathname === "/img" && req.method === "POST") {
      const key = url.searchParams.get("key") || "";
      if (!/^[0-9a-f]{8,40}$/.test(key) || url.searchParams.get("s") !== (await hmac(env.SIGNING_KEY, `img:${key}`))) {
        return Response.json({ ok: false, error: "bad signature" }, { status: 403 });
      }
      const body = await req.arrayBuffer();
      if (!body.byteLength || body.byteLength > 2_000_000) return Response.json({ ok: false, error: "bad size" }, { status: 400 });
      await env.VOTES.put(`img:${key}`, body, { metadata: { type: req.headers.get("Content-Type") || "image/jpeg" } });
      return Response.json({ ok: true });
    }

    if (url.pathname.startsWith("/img/") && req.method === "GET") {
      const cache = caches.default;   // images never change, so KV is read once per edge location
      const hit = await cache.match(req);
      if (hit) return hit;
      const key = url.pathname.slice(5).replace(/\.\w+$/, "");
      const { value, metadata } = await env.VOTES.getWithMetadata(`img:${key}`, "arrayBuffer");
      if (!value) return new Response("not found", { status: 404 });
      const res = new Response(value, {
        headers: {
          "Content-Type": metadata?.type || "image/jpeg",
          "Cache-Control": "public, max-age=31536000, immutable",
          "Access-Control-Allow-Origin": "*",
        },
      });
      ctx.waitUntil(cache.put(req, res.clone()));
      return res;
    }

    if (url.pathname === "/subscribe" && req.method === "GET") {
      return Response.json({ enabled: mailEnabled(env) }, { headers: cors });
    }

    if (url.pathname === "/subscribe" && req.method === "POST") {
      const { status = 200, ...res } = await subscribe(req, env, url.origin).catch(e => {
        console.error(e);
        return { ok: false, status: 502, error: "Potvrdzovací mail sa nepodarilo poslať, skús to neskôr." };
      });
      return Response.json(res, { status, headers: cors });
    }

    if (url.pathname === "/confirm" && req.method === "GET") {
      const id = url.searchParams.get("e") || "";
      const { metadata: m } = await env.VOTES.getWithMetadata(SUB + id);
      if (!m || m.token !== url.searchParams.get("t")) return page("Neplatný odkaz", env.SITE_URL, "Skús sa prihlásiť znova.");
      if (m.status !== "active") {
        await env.VOTES.put(SUB + id, "1", { metadata: { email: m.email, status: "active", at: new Date().toISOString() } });
      }
      return page("Odber potvrdený", env.SITE_URL, "Prvý digest príde zajtra ráno.");
    }

    if (url.pathname === "/unsubscribe" && (req.method === "GET" || req.method === "POST")) {
      const id = url.searchParams.get("e") || "";
      const ok = url.searchParams.get("s") === (await hmac(env.SIGNING_KEY, `unsub:${id}`));
      if (ok) await env.VOTES.delete(SUB + id);
      if (req.method === "POST") return new Response(ok ? "ok" : "bad link", { status: ok ? 200 : 400 });
      return page(ok ? "Odhlásené" : "Neplatný odkaz", env.SITE_URL,
        ok ? "Tvoju adresu sme zmazali, už ti nič nepošleme." : "Skús odkaz z najnovšieho mailu.");
    }

    if (url.pathname === "/subscribers" && req.method === "GET") {
      if (url.searchParams.get("s") !== (await hmac(env.SIGNING_KEY, "subscribers"))) {
        return Response.json({ ok: false, error: "bad signature" }, { status: 403 });
      }
      const out = [];
      let cursor;
      do {
        const batch = await env.VOTES.list({ prefix: SUB, cursor });
        for (const k of batch.keys) {
          if (k.metadata?.status === "active") out.push({ id: k.name.slice(SUB.length), email: k.metadata.email });
        }
        cursor = batch.list_complete ? null : batch.cursor;
      } while (cursor);
      return Response.json(out, { headers: { "Cache-Control": "no-store" } });
    }

    if (url.pathname === "/status" && req.method === "GET") {
      const cron = (await env.VOTES.get(CRON, "json")) || null;
      return Response.json({ cron }, { headers: { ...cors, "Cache-Control": "no-store" } });
    }

    return new Response("Design Digest feedback", { headers: cors });
  },

  async scheduled(_event, env) {
    const gh = (path, init = {}) => fetch(`https://api.github.com${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${env.GITHUB_TOKEN}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "design-digest-cron",
      },
    });
    const r = await gh(`/repos/${env.GITHUB_REPO}/actions/workflows/daily.yml/dispatches`, {
      method: "POST",
      body: JSON.stringify({ ref: "main", inputs: { if_missing: true } }),
    });
    // GitHub tells us when the PAT dies; the daily job warns in the email a week before
    const expHeader = "github-authentication-token-expiration";
    const expires = r.headers.get(expHeader) || (await gh("/rate_limit")).headers.get(expHeader);
    const error = r.ok ? null : `${r.status} ${(await r.text()).slice(0, 200)}`;
    await env.VOTES.put(CRON, JSON.stringify({
      at: new Date().toISOString(), ok: r.ok, status: r.status, token_expires: expires, error,
    }));
    if (error) throw new Error(`dispatch failed: ${error}`);
  },
};

function mailEnabled(env) {
  return Boolean((env.RESEND_API_KEY && env.MAIL_FROM) || env.MAIL_DRY_RUN);
}

async function subscribe(req, env, origin) {
  if (!mailEnabled(env)) return { ok: false, status: 503, error: "Odber zatiaľ nie je spustený." };
  const body = await req.json().catch(() => ({}));
  if (body.website) return { ok: true };   // honeypot — bots fill every field
  const email = String(body.email || "").trim().toLowerCase();
  if (email.length > 254 || !/^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(email)) {
    return { ok: false, status: 400, error: "Toto nevyzerá ako e-mailová adresa." };
  }
  const id = (await sha256(email)).slice(0, 24);
  const { metadata: m } = await env.VOTES.getWithMetadata(SUB + id);
  // same answer whether or not the address is already on the list — nobody can probe it
  const done = { ok: true, message: "Skontroluj si mail a potvrď odber." };
  if (m?.status === "active") return done;
  if (m?.status === "pending" && Date.now() - Date.parse(m.at) < RESEND_CONFIRM_AFTER) return done;

  const day = new Date().toISOString().slice(0, 10);
  const ipKey = `rl:${req.headers.get("CF-Connecting-IP") || "?"}:${day}`, allKey = `rl:all:${day}`;
  const [ipCount, allCount] = await Promise.all([ipKey, allKey].map(async k => Number(await env.VOTES.get(k)) || 0));
  if (ipCount >= PER_IP_PER_DAY || allCount >= CONFIRMS_PER_DAY) {
    return { ok: false, status: 429, error: "Dnes už toho bolo veľa, skús to prosím zajtra." };
  }
  await Promise.all([
    env.VOTES.put(ipKey, String(ipCount + 1), { expirationTtl: 2 * 86400 }),
    env.VOTES.put(allKey, String(allCount + 1), { expirationTtl: 2 * 86400 }),
  ]);

  const token = [...crypto.getRandomValues(new Uint8Array(16))].map(b => b.toString(16).padStart(2, "0")).join("");
  await env.VOTES.put(SUB + id, "1", { metadata: { email, status: "pending", token, at: new Date().toISOString() } });
  const link = `${origin}/confirm?e=${id}&t=${token}`;
  await sendMail(env, email, "Potvrď odber Design Digestu", confirmHtml(link),
    `Klikni na odkaz a potvrď odber Design Digestu:\n${link}\n\nAk si sa neprihlasoval(a), tento mail ignoruj.`);
  return done;
}

async function sendMail(env, to, subject, html, text) {
  if (env.MAIL_DRY_RUN) {   // local testing: keep the mail instead of sending it
    await env.VOTES.put("debug:lastmail", JSON.stringify({ to, subject, html, text }));
    return;
  }
  const r = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ from: env.MAIL_FROM, to: [to], subject, html, text }),
  });
  if (!r.ok) throw new Error(`resend ${r.status}: ${(await r.text()).slice(0, 200)}`);
}

function confirmHtml(link) {
  const font = "font-family:Inter Tight,-apple-system,Segoe UI,Helvetica,Arial,sans-serif";
  return `<!doctype html><meta charset="utf-8"><body style="margin:0;background:#fff">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 16px">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" style="width:100%;max-width:480px">
<tr><td style="${font};font-size:14px;font-weight:500;color:#111;padding-bottom:40px">Design Digest</td></tr>
<tr><td style="${font};font-size:36px;line-height:1.05;letter-spacing:-1.2px;color:#111;padding-bottom:16px">Ešte jeden klik</td></tr>
<tr><td style="${font};font-size:15px;line-height:1.5;color:#8a8a8a;padding-bottom:28px">
Potvrď, že chceš každé ráno dostávať výber toho najlepšieho z webového a vizuálneho dizajnu.</td></tr>
<tr><td><a href="${link}" style="${font};display:inline-block;background:#111;color:#fff;text-decoration:none;font-size:15px;padding:12px 22px;border-radius:999px">Potvrdiť odber</a></td></tr>
<tr><td style="${font};font-size:13px;line-height:1.5;color:#8a8a8a;padding-top:32px">
Ak si sa neprihlasoval(a), tento mail ignoruj — bez potvrdenia ti nič nepríde.</td></tr>
</table></td></tr></table></body>`;
}

async function sha256(text) {
  const d = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(d)].map(b => b.toString(16).padStart(2, "0")).join("");
}

async function record(env, id, v, sig) {
  if (!id || ![1, -1, 0].includes(v)) return { ok: false, error: "bad vote" };
  if (sig !== (await hmac(env.SIGNING_KEY, id))) return { ok: false, error: "bad signature" };
  const votes = (await env.VOTES.get(KEY, "json")) || {};
  if (v === 0) delete votes[id];
  else votes[id] = { v, at: new Date().toISOString() };
  await env.VOTES.put(KEY, JSON.stringify(votes));
  return { ok: true };
}

async function hmac(secret, msg) {
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(msg));
  return [...new Uint8Array(mac)].map((b) => b.toString(16).padStart(2, "0")).join("").slice(0, 16);
}

function page(message, siteUrl, note) {
  const back = siteUrl ? `<a href="${siteUrl}" style="color:#111;text-decoration:none">Späť na Design Digest →</a>` : "";
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Design Digest</title>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500&display=swap" rel="stylesheet">
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#fff;color:#111;
font:15px/1.45 'Inter Tight',system-ui,-apple-system,sans-serif;text-align:center">
<div><p style="font-size:48px;line-height:1;letter-spacing:-.04em;margin:0 0 16px">${message}</p>
<p style="color:#8a8a8a">${note}</p>
<p>${back}</p></div></body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}
