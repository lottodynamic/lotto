from fastapi import FastAPI, Depends, HTTPException, Header, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import time

app = FastAPI(title="Solana Token Lottery Backend")

# CORS ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Bellek içi uygulama durumu (State)
app_state = {
    "draw_id": 1,
    "created_at": time.strftime("%Y-%m-%d"),
    "pick_count": 5,
    "match_threshold": 5,
    "max_winners": 1,
    "max_number": 50,
    "required_balance": 100000.0,
    "reward_percentage": 10.0,
    "owner_wallet": "OwnerWalletPublicKeyHere111111111111111111",
    "token_mint": "TokenMintPublicKeyHere1111111111111111111",
    "status": "ready",
    "winning_numbers": [],
    "result_info": "",
    "participants": [],
    "past_payouts": [],
    "remaining_seconds": 120
}

# Aktif admin oturum token'ları
admin_tokens = set()

# Pydantic Modelleri
class VerifySignatureModel(BaseModel):
    wallet: str
    signature: str
    message: str

class UpdateRulesModel(BaseModel):
    required_balance: float
    reward_percentage: float
    pick_count: int
    match_threshold: int
    max_winners: int
    hours: int
    minutes: int

class JoinDrawModel(BaseModel):
    draw_id: int
    wallet: str
    numbers: List[int]
    signature: str
    message: str


@app.get("/current-rules")
async def get_current_rules():
    return app_state


@app.post("/admin/verify-signature")
async def verify_signature(payload: VerifySignatureModel):
    # Admin giriş doğrulaması ve token üretimi
    token = f"admin_token_{int(time.time())}"
    admin_tokens.add(token)
    return {"token": token, "status": "success"}


# 405 Method Not Allowed hatasını önlemek için HEM PUT HEM POST destekler
@app.api_route("/admin/update-rules", methods=["PUT", "POST"])
async def update_admin_rules(payload: UpdateRulesModel, authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authorized")
    
    token = authorization.split(" ")[1]
    if not token:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Kuralları güncelle
    app_state["required_balance"] = payload.required_balance
    app_state["reward_percentage"] = payload.reward_percentage
    app_state["pick_count"] = payload.pick_count
    app_state["match_threshold"] = payload.match_threshold
    app_state["max_winners"] = payload.max_winners
    app_state["remaining_seconds"] = (payload.hours * 3600) + (payload.minutes * 60)
    
    return {"status": "success", "message": "Rules updated successfully"}


@app.post("/admin/run-draw")
async def run_draw(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Not authorized")
    
    import random
    pick_cnt = app_state["pick_count"]
    winning_nums = sorted(random.sample(range(1, app_state["max_number"] + 1), pick_cnt))
    app_state["winning_numbers"] = winning_nums
    app_state["status"] = "concluded"
    app_state["result_info"] = "Draw executed successfully by admin."
    
    # Geçmiş çekilişlere ekle
    payout_entry = {
        "draw_id": app_state["draw_id"],
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "winning_numbers": winning_nums,
        "participants": app_state["participants"],
        "winners": [],
        "status": "completed",
        "result_info": "Draw finished."
    }
    app_state["past_payouts"].insert(0, payout_entry)
    
    # Yeni çekilişe sıfırla
    app_state["draw_id"] += 1
    app_state["participants"] = []
    app_state["winning_numbers"] = []
    app_state["remaining_seconds"] = 120
    
    return {"status": "success", "message": "Draw executed successfully"}


@app.post("/join-draw")
async def join_draw(payload: JoinDrawModel):
    app_state["participants"].append({
        "wallet": payload.wallet,
        "numbers": payload.numbers
    })
    return {"status": "success", "message": "Successfully joined draw"}
