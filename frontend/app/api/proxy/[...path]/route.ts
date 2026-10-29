import { session, tokens } from '../../../../session';

async function proxy(req: Request, ctx: { params: Promise<{path: string[]}> }) {
  const { path } = await ctx.params;
  if (path.some(p => !/^[a-zA-Z0-9_-]+$/.test(p))) return new Response('Invalid path', {status: 400});
  if (req.method !== 'GET' && req.headers.get('origin') !== new URL(req.url).origin)
    return new Response('Forbidden origin', { status: 403 });
  let accessToken: string | undefined;
  if (process.env.AUTH_MODE === 'dev' && process.env.NODE_ENV !== 'production') {
    accessToken = process.env.DEV_API_TOKEN;
  } else {
    const s = await session();
    const token = s.id ? tokens.get(s.id) : undefined;
    if (token && token.expiresAt > Date.now()) accessToken = token.accessToken;
    else if (s.id) tokens.delete(s.id);
  }
  if (!accessToken) return new Response('Sign in required', { status: 401 });
  const target = new URL(path.join('/'), (process.env.API_BASE_URL || 'http://127.0.0.1:8000') + '/');
  target.search = new URL(req.url).search;
  const contentLength = Number(req.headers.get('content-length') || 0);
  if (contentLength > 6 * 1024 * 1024) return new Response('File too large', {status: 413});
  const body = req.method === 'GET' ? undefined : await req.arrayBuffer();
  if (body && body.byteLength > 6 * 1024 * 1024) return new Response('File too large', {status: 413});
  const headers: Record<string,string> = { Authorization: `Bearer ${accessToken}` };
  if (req.headers.get('content-type')) headers['Content-Type'] = req.headers.get('content-type')!;
  try {
    const upstream = await fetch(target, { method: req.method, headers, body, cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(120000) });
    return new Response(await upstream.arrayBuffer(), {status: upstream.status,
      headers: {'Content-Type': upstream.headers.get('content-type') || 'application/json', 'Cache-Control': 'no-store'}});
  } catch { return Response.json({detail: 'API unavailable'}, {status: 502}); }
}
export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
