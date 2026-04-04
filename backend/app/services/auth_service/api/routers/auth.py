import bcrypt
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from app.services.auth_service.models import UserAuth

from ..dependencies import get_db
from ..schemas import RegisterRequest, RegisterResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
def register(body: RegisterRequest, session: Session = Depends(get_db)) -> RegisterResponse:
    if session.exec(select(UserAuth).where(UserAuth.username == body.username)).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Username already registered")
    if session.exec(select(UserAuth).where(UserAuth.email == str(body.email))).first():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")

    password_hash = bcrypt.hashpw(body.password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    user = UserAuth(username=body.username, email=str(body.email), password_hash=password_hash)
    session.add(user)
    session.commit()
    session.refresh(user)
    assert user.user_auth_id is not None
    return RegisterResponse(
        user_auth_id=user.user_auth_id,
        username=user.username,
        email=user.email,
        created_at=user.created_at,
    )
