import { cookies } from 'next/headers';
import { getIronSession } from 'iron-session';
import { ConfidentialClientApplication } from '@azure/msal-node';

type Session = { id?: string; state?: string; verifier?: string };
type Token = { accessToken: string; expiresAt: number };
// Single-instance POC store. Replace with shared encrypted Redis before scaling.
const globalTokens = globalThis as typeof globalThis & { reconTokens?: Map<string, Token> };
export const tokens = globalTokens.reconTokens ??= new Map<string, Token>();
export async function session() {
  const password = process.env.SESSION_PASSWORD;
  if (!password || password.length < 32) throw new Error('SESSION_PASSWORD must have at least 32 characters');
  return getIronSession<Session>(await cookies(), { password, cookieName: 'ccrecon',
    ttl: 3600, cookieOptions: { secure: process.env.NODE_ENV === 'production', httpOnly: true, sameSite: 'lax', path: '/' } });
}
export function msal() {
  return new ConfidentialClientApplication({ auth: {
    clientId: process.env.ENTRA_WEB_CLIENT_ID!,
    authority: `https://login.microsoftonline.com/${process.env.ENTRA_TENANT_ID!}`,
    clientSecret: process.env.ENTRA_WEB_CLIENT_SECRET!
  }});
}
export const scopes = () => [process.env.ENTRA_API_SCOPE!];
export const redirectUri = () => process.env.ENTRA_REDIRECT_URI!;
