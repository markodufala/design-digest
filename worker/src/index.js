// Design Digest feedback: stores ♥ / ✕ votes in KV.
//   GET  /votes                -> {id: {v, at}}            (read by the daily job + the site)
//   POST /vote  {id, v, s}     -> from the site (fetch)
//   GET  /v?id=&v=&s=          -> from email links, answers with a tiny HTML page
// `s` is an HMAC of the item id, so only items the digest published can be voted on.

const KEY = "votes";

export default {
  async fetch(req, env) {
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

    return new Response("Design Digest feedback", { headers: cors });
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
  const back = siteUrl ? `<a href="${siteUrl}">Späť na Design Digest →</a>` : "";
  return new Response(
    `<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Design Digest</title>
<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#f4f2ee;color:#141414;
font:16px/1.5 -apple-system,system-ui,sans-serif;text-align:center">
<div><p style="font:400 44px Georgia,serif;margin:0 0 12px">${message}</p>
<p style="color:#6f6b66">Zajtrajší výber sa podľa toho prispôsobí.</p>
<p>${back}</p></div></body>`,
    { headers: { "Content-Type": "text/html; charset=utf-8" } },
  );
}
