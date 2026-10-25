"""Explicit local-owner operation; registration never exposes anonymous legacy groups."""

import argparse
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Account, Group, Membership, SavedTraveller, User
from app.db.session import engine


def claim_legacy(session, email, apply=False):
    account = session.scalar(select(Account).where(Account.email == email.strip().lower()))
    if account is None:
        raise ValueError("Сначала зарегистрируйте этот аккаунт через интерфейс")
    # Serialize concurrent claims, including an empty initial dataset.
    session.execute(select(Account).order_by(Account.id).with_for_update()).all()
    groups = list(
        session.scalars(select(Group).where(Group.owner_account_id.is_(None)).with_for_update())
    )
    users = list(
        session.scalars(select(User).where(User.owner_account_id.is_(None)).with_for_update())
    )
    group_ids, user_ids = {g.id for g in groups}, {u.id for u in users}
    links = session.execute(
        select(Membership, User, Group)
        .join(User, Membership.user_id == User.id)
        .join(Group, Membership.group_id == Group.id)
    ).all()
    for member, user, group in links:
        if (group.id in group_ids or user.id in user_ids) and any(
            owner not in (None, account.id)
            for owner in (group.owner_account_id, user.owner_account_id)
        ):
            raise ValueError(
                "Обнаружены связи с другим владельцем. Нужен выборочный ручной перенос"
            )
    result = {"groups": len(groups), "participants": len(users), "applied": apply}
    if not apply:
        return result
    for group in groups:
        group.owner_account_id = account.id
    latest_by_user = {}
    for member, user, _group in links:
        if user.id not in latest_by_user or member.id > latest_by_user[user.id].id:
            latest_by_user[user.id] = member
    pending_profiles = []
    for user in users:
        user.owner_account_id = account.id
        latest = latest_by_user.get(user.id)
        if latest:
            profile = SavedTraveller(
                owner_account_id=account.id,
                name=user.name,
                interests=latest.interests or {},
                travel_profile=latest.travel_profile or {},
                age_profile=latest.age_profile or {},
            )
            session.add(profile)
            pending_profiles.append((user, profile))
    session.flush()
    for user, profile in pending_profiles:
        user.saved_traveller_id = profile.id
    session.flush()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Attach all unowned local records; without this only preview counts",
    )
    args = parser.parse_args()
    with Session(engine) as session:
        result = claim_legacy(session, args.email, args.apply)
        if args.apply:
            session.commit()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
