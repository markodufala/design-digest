// Design Digest feedback: stores ♥ / ✕ votes in KV.
//   GET  /votes                -> {id: {v, at}}            (read by the daily job + the site)
//   POST /vote  {id, v, s}     -> from the site (fetch)
//   GET  /v?id=&v=&s=          -> from email links, answers with a tiny HTML page
//   GET  /taste                -> the taste profile {text, generated_at, edited_at}
//   POST /taste {text, s, by}  -> Claude (daily job) or the person (site) rewrites it
//   GET  /status               -> what the cron saw last: {cron: {at, ok, status, token_expires}}
//   POST /img?key=&s=  (jpeg)  -> the daily job mirrors each pick's image here…
//   GET  /img/<key>.jpg        -> …so the archive survives galleries deleting or hotlink-blocking them
// `s` is an HMAC of the item id (or of "taste"), so only the digest and its email can write.
//
// Cron (wrangler.toml): starts the daily GitHub workflow, because GitHub's own
// schedule sometimes just doesn't fire. `if_missing` makes it a no-op once today's
// digest exists, so this and GitHub's cron can both fire without a second email.

const KEY = "votes";
const TASTE = "taste";
const CRON = "cron";

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
      const res = await record(env, body.id, Number(body.v), body.s);
      return Response.json(res, { status: res.ok ? 200 : 400, headers: cors });
    }

    if (url.pathname === "/v" && req.method === "GET") {
      const v = Number(url.searchParams.get("v"));
      const res = await record(env, url.searchParams.get("id"), v, url.searchParams.get("s"));
      return page(res.ok ? (v === 1 ? "♥ Uložené" : "✕ Toto už neuvidíš") : "Neplatný odkaz", env.SITE_URL);
    }

    if (url.pathname === "/taste" && req.method === "GET") {
      const taste = (await env.VOTES.get(TASTE, "json")) || {};
      return Response.json(taste, { headers: { ...cors, "Cache-Control": "no-store" } });
    }

    if (url.pathname === "/taste" && req.method === "POST") {
      const body = await req.json().catch(() => ({}));
      const text = String(body.text ?? "").trim().slice(0, 2000);
      if (body.s !== (await hmac(env.SIGNING_KEY, TASTE))) {
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

function page(message, siteUrl) {
  const back = siteUrl ? `<a href="${siteUrl}" style="color:#111;text-decoration:none">Späť na Design Digest →</a>` : "";
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Design Digest</title>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500&display=swap" rel="stylesheet">
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#fff;color:#111;
font:15px/1.45 'Inter Tight',system-ui,-apple-system,sans-serif;text-align:center">
<div><p style="font-size:48px;line-height:1;letter-spacing:-.04em;margin:0 0 16px">${message}</p>
<p style="color:#8a8a8a">Zajtrajší výber sa podľa toho prispôsobí.</p>
<p>${back}</p></div></body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}
