import { randomBytes } from 'node:crypto';
import { session, msal, scopes, redirectUri, tokens } from '../../../../session';
export async function GET(req: Request) {
  const s = await session();
  const url = new URL(req.url);
  if (!s.state || url.searchParams.get('state') !== s.state || !s.verifier || !url.searchParams.get('code'))
    return new Response('Invalid sign-in state', { status: 400 });
  const verifier = s.verifier;
  delete s.state; delete s.verifier;
  await s.save();
  const token = await msal().acquireTokenByCode({ scopes: scopes(), redirectUri: redirectUri(),
    code: url.searchParams.get('code')!, codeVerifier: verifier });
  if (!token?.accessToken || !token.expiresOn) return new Response('Sign-in failed', { status: 401 });
  if (s.id) tokens.delete(s.id);
  s.id = randomBytes(32).toString('hex');
  tokens.set(s.id, { accessToken: token.accessToken, expiresAt: token.expiresOn.getTime() });
  await s.save();
  return Response.redirect(new URL('/', redirectUri()));
}
