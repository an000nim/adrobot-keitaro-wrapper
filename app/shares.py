from fastapi import HTTPException


def distribute(offers):
    active = [offer for offer in offers if not offer.get("removed")]
    if not active:
        raise HTTPException(422, "В потоке должен остаться хотя бы один оффер")
    pinned = [offer for offer in active if offer.get("pinned")]
    free = [offer for offer in active if not offer.get("pinned")]
    remaining = 100 - sum(offer["share"] for offer in pinned)
    if remaining < 0 or (not free and remaining != 0):
        raise HTTPException(422, "Закреплённые доли не позволяют получить сумму 100%")
    if free:
        share, remainder = divmod(remaining, len(free))
        for index, offer in enumerate(free):
            offer["share"] = share + (index < remainder)
    for offer in offers:
        if offer.get("removed"):
            offer["share"] = 0
    return offers
