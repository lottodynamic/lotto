from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import random
import uuid
import base58
import base64
import os
from pathlib import Path
from dotenv import load_dotenv
from nacl.signing import VerifyKey

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

try:
    from solana.rpc.api import Client
    from solders.pubkey import Pubkey
    from solders.keypair import Keypair
    from solders.instruction import Instruction, AccountMeta
    from solders.transaction import Transaction
    from solders.system_program import transfer, TransferParams
    SOLANA_LIB_AVAILABLE = True
except ImportError:
    SOLANA_LIB_AVAILABLE = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OWNER_WALLET_ADDRESS = "7FNsGTquWRGfswGv9UiKMH5MegiVWfKAGUAcLJZdMnVy"
TOKEN_MINT_ADDRESS = "AiwTJAypUDEtVcGfsKTY3qM4QwCxsjd342tNLdpbpump"

OWNER_SECRET_KEY_BASE58 = os.getenv("OWNER_SECRET_KEY_BASE58")

SOLANA_RPC_URL = "https://mainnet.helius-rpc.com/?api-key=6da794d3-48c2-4fc3-b03e-85c24b81388f"
solana_client = Client(SOLANA_RPC_URL) if SOLANA_LIB_AVAILABLE else None

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA") if SOLANA_LIB_AVAILABLE else None
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL") if SOLANA_LIB_AVAILABLE else None

draw_state = {
    "draw_id": 1,
    "status": "ready",
    "pool_amount": "Reward: 10% SOL Pool",
    "required_balance": 100000,
    "reward_percentage": 10.0,
    "max_number": 50,
    "pick_count": 6,
    "match_threshold": 1,
    "max_winners": 1,
    "owner_wallet": OWNER_WALLET_ADDRESS,
    "token_mint": TOKEN_MINT_ADDRESS,
    "participants": [],
    "past_payouts": [],
    "winning_numbers": None,
    "result_info": None,
    "created_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
    "expires_at": (datetime.utcnow() + timedelta(minutes=2)).isoformat(),
    "finished_at": None
}

active_admin_tokens = set()

class SettingsUpdate(BaseModel):
    pick_count: int
    match_threshold: int
    max_winners: int
    required_balance: int = 100000
    reward_percentage: float = 10.0
    hours: int = 0
    minutes: int = 2

class TicketSubmit(BaseModel):
    wallet: str
    numbers: List[int]
    signature: str
    message: str

class VerifyModel(BaseModel):
    wallet: str
    signature: str
    message: str

def get_associated_token_address(wallet_pubkey: Pubkey, mint_pubkey: Pubkey, token_program_id: Pubkey) -> Pubkey:
    derived, _ = Pubkey.find_program_address(
        [bytes(wallet_pubkey), bytes(token_program_id), bytes(mint_pubkey)],
        ASSOCIATED_TOKEN_PROGRAM_ID
    )
    return derived

def check_wallet_token_balance(wallet_str: str, required_amt: int) -> tuple[bool, str]:
    if not SOLANA_LIB_AVAILABLE or not solana_client:
        return True, ""
    try:
        wallet_pubkey = Pubkey.from_string(wallet_str)
        mint_pubkey = Pubkey.from_string(TOKEN_MINT_ADDRESS)
        mint_info = solana_client.get_account_info(mint_pubkey)
        active_token_program_id = mint_info.value.owner if (mint_info.value and mint_info.value.owner) else TOKEN_PROGRAM_ID
        ata = get_associated_token_address(wallet_pubkey, mint_pubkey, active_token_program_id)
        balance_resp = solana_client.get_token_account_balance(ata)
        if not balance_resp.value:
            return False, 'you do not have any "tokens" in your wallet'
        
        actual_balance = int(balance_resp.value.amount) / (10 ** balance_resp.value.decimals)
        if actual_balance <= 0:
            return False, 'you do not have any "tokens" in your wallet'
        if actual_balance < required_amt:
            formatted_req = f"{required_amt:,}".replace(",", ".")
            return False, f"you must have {formatted_req} tokens in your wallet."
        return True, ""
    except Exception as e:
        print("Token balance check error:", e)
        return False, 'you do not have any "tokens" in your wallet'

def execute_real_solana_sol_transfer(winner_wallet_str: str, reward_percentage: float):
    if not SOLANA_LIB_AVAILABLE:
        print("CRITICAL ERROR: Solana libraries are not installed!")
        return None
    if not OWNER_SECRET_KEY_BASE58:
        print("CRITICAL ERROR: Could not read OWNER_SECRET_KEY_BASE58 from .env file!")
        return None
        
    try:
        owner_keypair = Keypair.from_base58_string(OWNER_SECRET_KEY_BASE58)
        winner_pubkey = Pubkey.from_string(winner_wallet_str)
        
        owner_balance_resp = solana_client.get_balance(owner_keypair.pubkey())
        owner_lamports = owner_balance_resp.value
        
        transfer_lamports = int(owner_lamports * (reward_percentage / 100.0))
        if transfer_lamports < 1000:
            print("Transfer lamports amount is too low:", transfer_lamports)
            return None
            
        blockhash_resp = solana_client.get_latest_blockhash()
        recent_blockhash = blockhash_resp.value.blockhash
        
        transfer_ix = transfer(
            TransferParams(
                from_pubkey=owner_keypair.pubkey(),
                to_pubkey=winner_pubkey,
                lamports=transfer_lamports
            )
        )
        
        txn = Transaction.new_signed_with_payer(
            instructions=[transfer_ix],
            payer=owner_keypair.pubkey(),
            signing_keypairs=[owner_keypair],
            recent_blockhash=recent_blockhash
        )
        
        res = solana_client.send_transaction(txn)
        tx_sig = str(res.value) if hasattr(res, 'value') else str(res)
        print("SUCCESSFUL TRANSFER - TX Signature:", tx_sig)
        
        return f"https://solscan.io/tx/{tx_sig}"
    except Exception as e:
        import traceback
        print("DETAILED SOL TRANSFER ERROR:")
        traceback.print_exc()
        return None

def run_automatic_draw():
    winning_numbers = sorted(random.sample(range(1, draw_state["max_number"] + 1), draw_state["pick_count"]))
    draw_state["winning_numbers"] = winning_numbers
    draw_state["status"] = "finished"
    draw_state["finished_at"] = datetime.utcnow().isoformat()
    
    threshold = draw_state["match_threshold"]
    winners = []
    matched_threshold_count = 0
    
    for p in draw_state["participants"]:
        user_nums = p.get("numbers", [])
        matches = len(set(user_nums).intersection(set(winning_numbers)))
        if matches >= threshold:
            matched_threshold_count += 1
            has_token, _ = check_wallet_token_balance(p["wallet"], draw_state["required_balance"])
            if has_token:
                winners.append(p["wallet"])
                if len(winners) >= draw_state["max_winners"]:
                    break
            else:
                print(f"Disqualified: Tokens were transferred out of {p['wallet']} wallet!")

    if len(winners) > 0:
        tx_url = execute_real_solana_sol_transfer(winners[0], draw_state.get("reward_percentage", 10.0))
        if tx_url:
            draw_state["result_info"] = "Winner announced, SOL reward sent!"
            payout_status = "winner"
        else:
            winners = []
            tx_url = None
            draw_state["result_info"] = "Winner determined but SOL transfer failed (Check balance/RPC)."
            payout_status = "no_winner"
    else:
        tx_url = None
        req_bal_formatted = f"{draw_state['required_balance']:,}".replace(",", ".")
        if matched_threshold_count > 0:
            draw_state["result_info"] = f"There were tickets with correct numbers, but the wallet did not have the required {req_bal_formatted} tokens condition."
        else:
            draw_state["result_info"] = "No tickets guessed the correct numbers."
        payout_status = "no_winner"

    payout = {
        "draw_id": draw_state["draw_id"],
        "date": draw_state["created_at"],
        "prize": f"{draw_state['reward_percentage']}% SOL",
        "status": payout_status,
        "winners": winners,
        "winning_numbers": winning_numbers,
        "participants": [p.copy() for p in draw_state["participants"]],
        "tx_url": tx_url
    }
    draw_state["past_payouts"].insert(0, payout)

@app.get("/current-rules")
def get_current_rules():
    now = datetime.utcnow()
    
    if draw_state["status"] == "ready":
        try:
            expires_dt = datetime.fromisoformat(draw_state["expires_at"])
            if now >= expires_dt:
                run_automatic_draw()
        except Exception:
            pass
            
    elif draw_state["status"] == "finished":
        finished_dt = draw_state.get("finished_at")
        if finished_dt:
            try:
                if (now - datetime.fromisoformat(finished_dt)).total_seconds() > 7:
                    draw_state["draw_id"] += 1
                    draw_state["status"] = "ready"
                    draw_state["winning_numbers"] = None
                    draw_state["result_info"] = None
                    draw_state["participants"] = []
                    draw_state["created_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
                    draw_state["expires_at"] = (now + timedelta(minutes=2)).isoformat()
                    draw_state["finished_at"] = None
            except Exception:
                pass
                
    remaining_seconds = 0
    if draw_state["status"] == "ready":
        try:
            expires_dt = datetime.fromisoformat(draw_state["expires_at"])
            remaining_seconds = max(0, int((expires_dt - datetime.utcnow()).total_seconds()))
        except Exception:
            remaining_seconds = 120

    response_data = draw_state.copy()
    response_data["remaining_seconds"] = remaining_seconds
    return response_data

@app.post("/admin/verify-signature")
def verify_signature(data: VerifyModel):
    try:
        try:
            sig_bytes = base58.b58decode(data.signature)
        except Exception:
            sig_bytes = base64.b64decode(data.signature)

        try:
            pub_key_bytes = base58.b58decode(data.wallet)
        except Exception:
            pub_key_bytes = base64.b64decode(data.wallet)

        msg_bytes = data.message.encode('utf-8')

        verify_key = VerifyKey(pub_key_bytes)
        verify_key.verify(msg_bytes, sig_bytes)
    except Exception as e:
        print("Verify Error:", e)
        raise HTTPException(status_code=400, detail="Signature could not be verified!")

    if data.wallet != OWNER_WALLET_ADDRESS:
        raise HTTPException(status_code=403, detail="Only the Owner wallet can access the admin panel!")

    token = str(uuid.uuid4())
    active_admin_tokens.add(token)
    return {"token": token, "message": "Login successful!"}

@app.post("/admin/update-settings/{draw_id}")
def update_settings(draw_id: int, settings: SettingsUpdate, x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token not in active_admin_tokens:
        raise HTTPException(status_code=401, detail="Unauthorized access or session expired!")
    
    if draw_state["draw_id"] != draw_id:
        draw_state["draw_id"] = draw_id

    draw_state["pick_count"] = settings.pick_count
    draw_state["match_threshold"] = settings.match_threshold
    draw_state["max_winners"] = settings.max_winners
    draw_state["required_balance"] = settings.required_balance
    draw_state["reward_percentage"] = settings.reward_percentage
    draw_state["pool_amount"] = f"Reward: {settings.reward_percentage}% SOL Pool"
    draw_state["status"] = "ready"
    draw_state["winning_numbers"] = None
    draw_state["result_info"] = None
    draw_state["participants"] = []
    draw_state["created_at"] = datetime.now().strftime("%d.%m.%Y %H:%M")
    draw_state["finished_at"] = None
    
    type_minutes = settings.hours * 60 + settings.minutes
    if type_minutes < 1:
        type_minutes = 1
    draw_state["expires_at"] = (datetime.utcnow() + timedelta(minutes=type_minutes)).isoformat()
    
    return {"message": "Rules updated and new draw opened", "draw_state": draw_state}

@app.post("/admin/run-draw/{draw_id}")
def run_draw(draw_id: int, x_admin_token: Optional[str] = Header(None)):
    if not x_admin_token or x_admin_token not in active_admin_tokens:
        raise HTTPException(status_code=401, detail="Unauthorized access or session expired!")
    
    if draw_state["draw_id"] != draw_id:
        raise HTTPException(status_code=404, detail="Draw not found")

    run_automatic_draw()
    return {"message": "Draw completed", "winning_numbers": draw_state["winning_numbers"]}

@app.post("/submit-ticket")
def submit_ticket(ticket: TicketSubmit, request: Request):
    if draw_state["status"] != "ready":
        raise HTTPException(status_code=400, detail="Draw has ended, waiting for the new draw.")
    if not ticket.wallet or len(ticket.wallet) < 32:
        raise HTTPException(status_code=400, detail="Invalid wallet address")
    
    try:
        try:
            sig_bytes = base58.b58decode(ticket.signature)
        except Exception:
            sig_bytes = base64.b64decode(ticket.signature)

        try:
            pub_key_bytes = base58.b58decode(ticket.wallet)
        except Exception:
            pub_key_bytes = base64.b64decode(ticket.wallet)

        msg_bytes = ticket.message.encode('utf-8')
        verify_key = VerifyKey(pub_key_bytes)
        verify_key.verify(msg_bytes, sig_bytes)
    except Exception as e:
        print("Ticket Signature Verify Error:", e)
        raise HTTPException(status_code=400, detail="Wallet signature for the ticket could not be verified!")

    if len(ticket.numbers) != draw_state["pick_count"]:
        raise HTTPException(status_code=400, detail=f"You must select exactly {draw_state['pick_count']} numbers.")
    
    for n in ticket.numbers:
        if not (1 <= n <= draw_state["max_number"]):
            raise HTTPException(status_code=400, detail="Numbers must be between 1 and 50.")

    has_token, err_msg = check_wallet_token_balance(ticket.wallet, draw_state["required_balance"])
    if not has_token:
        raise HTTPException(status_code=400, detail=err_msg)

    client_ip = request.client.host if request.client else "unknown"

    existing = next((p for p in draw_state["participants"] if p["wallet"] == ticket.wallet), None)
    if existing:
        existing["numbers"] = ticket.numbers
        existing["ip"] = client_ip
    else:
        draw_state["participants"].append({"wallet": ticket.wallet, "numbers": ticket.numbers, "ip": client_ip})
        
    return {"message": "Ticket successfully signed and saved!"}

app.mount("/", StaticFiles(directory=".", html=True), name="static")