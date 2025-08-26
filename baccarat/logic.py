# baccarat/logic.py
import random

SUITS = ["S","H","D","C"]  # ♠ ♥ ♦ ♣ (用字母存)
RANKS = ["A","2","3","4","5","6","7","8","9","10","J","Q","K"]

def fresh_shoe():
    # 單副牌即可；需要多副可乘上去
    deck = [f"{r}{s}" for s in SUITS for r in RANKS]
    random.shuffle(deck)
    return deck

def card_value(rank: str) -> int:
    if rank in ("J","Q","K","10"):
        return 0
    if rank == "A":
        return 1
    return int(rank)

def hand_total(cards: list[str]) -> int:
    total = sum(card_value(c[:-1]) for c in cards)  # c[:-1] 去掉花色字母
    return total % 10

def deal_round() -> dict:
    """
    依百家樂規則發牌 & 補牌。回傳：
    {
      "player_cards": [...],
      "banker_cards": [...],
      "player_total": int,
      "banker_total": int,
      "outcome": "player"|"banker"|"tie"
    }
    """
    deck = fresh_shoe()

    # 起手各兩張（P1 B1 P2 B2）
    p = [deck.pop(0), deck.pop(0)]
    b = [deck.pop(0), deck.pop(0)]
    pt = hand_total(p)
    bt = hand_total(b)

    # Natural 8/9 -> 直接結束
    if pt in (8,9) or bt in (8,9):
        return _result(p, b)

    # Player 第三張規則
    player_third = None
    if pt <= 5:
        player_third = deck.pop(0)
        p.append(player_third)
        pt = hand_total(p)

    # Banker 規則
    if player_third is None:
        # Player 未補牌，Banker<=5 補
        if bt <= 5:
            b.append(deck.pop(0))
            bt = hand_total(b)
    else:
        # Player 有第三張，照賭場表
        v = card_value(player_third[:-1])
        if bt <= 2:
            b.append(deck.pop(0))
        elif bt == 3 and v != 8:
            b.append(deck.pop(0))
        elif bt == 4 and v in (2,3,4,5,6,7):
            b.append(deck.pop(0))
        elif bt == 5 and v in (4,5,6,7):
            b.append(deck.pop(0))
        elif bt == 6 and v in (6,7):
            b.append(deck.pop(0))
        # bt == 7 停；8/9 前面已處理
        bt = hand_total(b)

    return _result(p, b)

def _result(p, b):
    pt = hand_total(p)
    bt = hand_total(b)
    if pt > bt: outcome = "player"
    elif bt > pt: outcome = "banker"
    else: outcome = "tie"
    return {
        "player_cards": p,
        "banker_cards": b,
        "player_total": pt,
        "banker_total": bt,
        "outcome": outcome,
    }
