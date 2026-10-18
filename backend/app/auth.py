"""Sign-in: verified Google accounts or anonymous guests, both backed by an
opaque bearer-token session (see models.User / models.AuthSession). Google ID
tokens are verified server-side (signature, issuer, audience) via the
google-auth library — the frontend must never be trusted to self-report who
it is, since anyone can hand-craft a JWT-shaped payload client-side."""
import logging
import secrets
from datetime import datetime
from typing import Optional

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

from .config import settings
from .db import SessionLocal
from .models import AuthSession, User

logger = logging.getLogger(__name__)

_google_request = google_requests.Request()


class InvalidGoogleCredential(Exception):
    pass


def verify_google_credential(credential: str) -> dict:
    if not settings.google_client_id:
        raise InvalidGoogleCredential("GOOGLE_CLIENT_ID is not configured on the server")
    try:
        payload = google_id_token.verify_oauth2_token(credential, _google_request, settings.google_client_id)
    except Exception as exc:
        raise InvalidGoogleCredential(str(exc)) from exc
    if payload.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        raise InvalidGoogleCredential("unexpected issuer")
    return payload


def _issue_session(db, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token=token, user_id=user.id))
    db.commit()
    return token


def login_with_google(credential: str) -> tuple[User, str]:
    payload = verify_google_credential(credential)
    sub = payload["sub"]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.google_sub == sub).first()
        if user is None:
            user = User(
                kind="google",
                google_sub=sub,
                email=payload.get("email"),
                name=payload.get("name"),
                picture=payload.get("picture"),
            )
            db.add(user)
        else:
            user.email = payload.get("email") or user.email
            user.name = payload.get("name") or user.name
            user.picture = payload.get("picture") or user.picture
            user.last_seen_at = datetime.utcnow()
        db.commit()
        db.refresh(user)
        token = _issue_session(db, user)
        # _issue_session's own commit expires every object in this session,
        # including `user` — refresh once more before detaching it, or
        # accessing its attributes after db.close() raises DetachedInstanceError.
        db.refresh(user)
        db.expunge(user)
        return user, token
    finally:
        db.close()


def login_as_guest() -> tuple[User, str]:
    db = SessionLocal()
    try:
        user = User(kind="guest")
        db.add(user)
        db.commit()
        db.refresh(user)
        token = _issue_session(db, user)
        db.refresh(user)
        db.expunge(user)
        return user, token
    finally:
        db.close()


def get_user_from_token(token: str) -> Optional[User]:
    if not token:
        return None
    db = SessionLocal()
    try:
        session = db.get(AuthSession, token)
        if session is None:
            return None
        user = session.user
        db.expunge(user)
        return user
    finally:
        db.close()


def logout(token: str) -> None:
    db = SessionLocal()
    try:
        session = db.get(AuthSession, token)
        if session is not None:
            db.delete(session)
            db.commit()
    finally:
        db.close()
