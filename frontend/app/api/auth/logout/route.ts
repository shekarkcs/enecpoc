import { session, tokens } from '../../../../session';
export async function POST(req: Request) {
  if (req.headers.get('origin') !== new URL(req.url).origin) return new Response('Forbidden', { status: 403 });
  const s = await session();
  if (s.id) tokens.delete(s.id);
  s.destroy();
  return Response.json({ status: 'signed out' });
}
