import os
from dataclasses import dataclass
from functools import lru_cache

import jwt
from fastapi import Depends, Header, HTTPException
from . import config


@dataclass
class User:
    id: str
    roles: set[str]


@lru_cache
def jwks_client():
    tenant = os.environ['ENTRA_TENANT_ID']
    return jwt.PyJWKClient(f'https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys')


def current_user(authorization: str = Header(default='')):
    if config.AUTH_MODE == 'dev':
        # Explicit opt-in only; never enabled by default or in container image.
        users = {'dev-preparer': User('preparer', {'Preparer'}),
                 'dev-approver': User('approver', {'Approver'}),
                 'dev-approver2': User('approver2', {'Approver'}),
                 'dev-admin': User('admin', {'Admin'}),
                 'dev-admin2': User('admin2', {'Admin'}),
                 'dev-auditor': User('auditor', {'Auditor'})}
        user = users.get(authorization.removeprefix('Bearer '))
        if user:
            return user
        raise HTTPException(401, 'Use an explicit local development token')
    try:
        token = authorization.removeprefix('Bearer ')
        key = jwks_client().get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=['RS256'],
                            audience=os.environ['ENTRA_API_AUDIENCE'],
                            issuer=f"https://login.microsoftonline.com/{os.environ['ENTRA_TENANT_ID']}/v2.0",
                            options={'require': ['exp', 'iat', 'iss', 'aud', 'oid']})
        return User(claims['oid'], set(claims.get('roles', [])))
    except Exception:
        raise HTTPException(401, 'Invalid or unavailable authentication') from None


def require(*roles):
    def check(user: User = Depends(current_user)):
        if not user.roles.intersection(roles):
            raise HTTPException(403, 'Role does not permit this operation')
        return user
    return check
