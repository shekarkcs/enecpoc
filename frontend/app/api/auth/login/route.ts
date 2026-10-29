import { randomBytes, createHash } from 'node:crypto';
import { session, msal, scopes, redirectUri } from '../../../../session';
export async function GET() {
  const s = await session();
  s.state = randomBytes(32).toString('hex');
  s.verifier = randomBytes(48).toString('base64url');
  const url = await msal().getAuthCodeUrl({ scopes: scopes(), redirectUri: redirectUri(), state: s.state,
    codeChallenge: createHash('sha256').update(s.verifier).digest('base64url'), codeChallengeMethod: 'S256' });
  await s.save();
  return Response.redirect(url);
}
