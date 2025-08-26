# baccarat/schema.py
from pydantic import BaseModel
from typing import List, Optional

class PlaceBetReq(BaseModel):
    room: str
    side: str       # 'player'|'banker'|'tie'
    amount: int

class StateResp(BaseModel):
    room: str
    day_key: str
    round_no: int
    status: str       # 'betting'|'dealing'|'settled'
    seconds_left: int
    totals: dict      # {"player": amount, "banker": amount, "tie": amount}
    bettors: int      # 當前局下單的人數

class HistoryItem(BaseModel):
    round_no: int
    winner: Optional[str]
    pt: Optional[int]
    bt: Optional[int]

class RevealResp(BaseModel):
    show: bool
    winner: Optional[str]
    player_cards: List[str] = []
    banker_cards: List[str] = []
    player_total: Optional[int] = None
    banker_total: Optional[int] = None
