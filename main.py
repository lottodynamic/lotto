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
import requests
from pathlib import Path
from dotenv import load_dotenv
from nacl.signing import VerifyKey

env_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=env_path)

try:
    from solana.rpc.api import Client
    from solders.pubkey import Pubkey
    from solders.keypair import Keypair
    from solders.message import Message
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

draw_state = {
    "draw_id": 1,
    "status": "ready",
    "pool_amount": "Reward: 10% SOL Pool",
    "required_balance": 55000,
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
    required_balance: int = 55000
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

def check_wallet_token_balance(wallet_str: str, required_amt: int) -> tuple[bool, str]:
    if required_amt <= 0:
        return True, ""

    programs = [
        "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",  # Standard SPL Token Program
        "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"    # Token-2022 Program
    ]

    total_balance = 0.0
    success_fetch = False

    for prog_id in programs:
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTokenAccountsByOwner",
                "params": [
                    wallet_str,
                    {"programId": prog_id},
                    {"encoding": "jsonParsed"}
                ]
            }
            headers = {"Content-Type": "application/json"}
            response = requests.post(SOLANA_RPC_URL, json=payload, headers=headers, timeout=10)
            data = response.json()

            if "result" in data and "value" in data["result"]:
                success_fetch = True
                accounts = data["result"]["value"]
                for acc in accounts:
                    try:
                        parsed_info = acc["account"]["data"]["parsed"]["info"]
                        mint = parsed_info.get("mint")
                        if mint == TOKEN_MINT_ADDRESS:
                            token_amount = parsed_info["tokenAmount"]
                            amount_str = token_amount.get("amount", "0")
                            decimals = int(token_amount.get("decimals", 0))
                            calculated_ui_amount = float(amount_str) / (10 ** decimals) if decimals > 0 else float(amount_str)
                            total_balance += calculated_ui_amount
                    except Exception as ex:
                        print("Error parsing token account item:", ex)
        except Exception as e:
            print(f"Token balance check RPC exception for program {prog_id}:", e)

    print(f"\n--- TOKEN BALANCE CHECK ---")
    print(f"Wallet: {wallet_str}")
    print(f"Target Mint: {TOKEN_MINT_ADDRESS}")
    print(f"Total Balance Found: {total_balance} | Required: {required_amt}")
    print(f"---------------------------")

    if not success_fetch and total_balance <= 0:
        return False, 'you do not have any "tokens" in your wallet'

    if total_balance <= 0:
        return False, 'you do not have any "tokens" in your wallet'
    
    if total_balance < required_amt:
        formatted_req = f"{required_amt:,}".replace(",", ".")
        return False, f"you must have {formatted_req} tokens in your wallet."
        
    return True, ""

def execute_real_solana_sol_transfers_to_winners(winners: List[str], reward_percentage: float):
    if not SOLANA_LIB_AVAILABLE:
        print("CRITICAL ERROR: Solana libraries are not installed!")
        return None
    if not OWNER_SECRET_KEY_BASE58:
        print("CRITICAL ERROR: Could not read OWNER_SECRET_KEY_BASE58 from .env file!")
        return None
        
    try:
        owner_keypair = Keypair.from_base58_string(OWNER_SECRET_KEY_BASE58)
        print(f"Owner Wallet Pubkey: {owner_keypair.pubkey()}")
        
        owner_balance_resp = solana_client.get_balance(owner_keypair.pubkey())
        owner_lamports = owner_balance_resp.value
        print(f"Owner SOL Balance (Lamports): {owner_lamports}")
        
        total_reward_lamports = int(owner_lamports * (reward_percentage / 100.0))
        
        fee_buffer = 15000 * len(winners)
        if owner_lamports <= fee_buffer + 1000:
            print("ERROR: Owner balance is too low to cover transfers and network fees.")
            return None
            
        max_available = owner_lamports - fee_buffer
        if total_reward_lamports > max_available:
            total_reward_lamports = max_available
            
        if total_reward_lamports <= 0:
            print("ERROR: Total reward lamports calculated is 0 or negative.")
            return None
            
        share_per_winner = total_reward_lamports // len(winners)
        if share_per_winner < 1000:
            print("ERROR: Share per winner amount is too low:", share_per_winner)
            return None
            
        print("Fetching latest blockhash for transfer...")
        blockhash_resp = solana_client.get_latest_blockhash()
        recent_blockhash = blockhash_resp.value.blockhash
        print(f"Blockhash retrieved: {recent_blockhash}")
        
        tx_signatures = []
        for winner_wallet_str in winners:
            try:
                winner_pubkey = Pubkey.from_string(winner_wallet_str)
                transfer_ix = transfer(
                    TransferParams(
                        from_pubkey=owner_keypair.pubkey(),
                        to_pubkey=winner_pubkey,
                        lamports=share_per_winner
                    )
                )
                
                # Ham mesaj ve imzalama işlemi (Kütüphane çakışmalarını tamamen önler)
                msg = Message.new_with_blockhash(
                    instructions=[transfer_ix],
                    payer=owner_keypair.pubkey(),
                    blockhash=recent_blockhash
                )
                txn = Transaction.new_unsigned(msg)
                txn.sign([owner_keypair], recent_blockhash)
                
                # Doğrudan RPC üzerinden Ham İşlem Gönderimi (Raw JSON-RPC sendTransaction)
                raw_tx_bytes = bytes(txn)
                b64_encoded_tx = base64.b64encode(raw_tx_bytes).decode('utf-8')
                
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "sendTransaction",
                    "params": [
                        b64_encoded_tx,
                        {"encoding": "base64"}
                    ]
                }
                headers = {"Content-Type": "application/json"}
                response = requests.post(SOLANA_RPC_URL, json=payload, headers=headers, timeout=15)
                res_data = response.json()
                
                if "result" in res_data:
                    tx_sig = res_data["result"]
                    tx_signatures.append(f"https://solscan.io/tx/{tx_sig}")
                    print(f"SUCCESSFUL TRANSFER to {winner_wallet_str} - TX Signature:", tx_sig)
                else:
                    print(f"ERROR in RPC sendTransaction response for {winner_wallet_str}:", res_data)
                    
            except Exception as inner_ex:
                print(f"ERROR executing transfer for specific winner {winner_wallet_str}: {inner_ex}")
                import traceback
                traceback.print_exc()
        
        if not tx_signatures:
            return None
            
        return ", ".join(tx_signatures)
    except Exception as e:
        import traceback
        print("DETAILED SOL TRANSFER MAIN EXCEPTION:")
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
    
    print(f"\n--- DRAW #{draw_state['draw_id']} STARTED ---")
    print(f"Winning Numbers: {winning_numbers}")
    print(f"Match Threshold Required: >= {threshold}")

    for p in draw_state["participants"]:
        user_nums = p.get("numbers", [])
        user_nums_int = [int(n) for n in user_nums]
        winning_nums_int = [int(n) for n in winning_numbers]
        
        matches = len(set(user_nums_int).intersection(set(winning_nums_int)))
        print(f"Wallet: {p['wallet']} | User Numbers: {user_nums_int} | Matches Found: {matches}")

        if matches >= threshold:
            matched_threshold_count += 1
            has_token, err_msg = check_wallet_token_balance(p["wallet"], draw_state["required_balance"])
            if has_token:
                winners.append(p["wallet"])
                print(f"-> WINNER ADDED TO LIST: {p['wallet']}")
            else:
                print(f"-> DISQUALIFIED: Matched numbers but failed token requirement! ({err_msg})")

    tx_url = None
    payout_status = "no_winner"
    
    if len(winners) > 0:
        print(f"Total winners qualified: {len(winners)}. Initiating SOL reward transfer...")
        tx_url = execute_real_solana_sol_transfers_to_winners(winners, draw_state.get("reward_percentage", 10.0))
        if tx_url:
            draw_state["result_info"] = f"Draw completed! {len(winners)} winner(s) shared the reward pool and SOL was sent!"
            payout_status = "winner"
        else:
            draw_state["result_info"] = f"Winners successfully found ({len(winners)}), but SOL transfer failed. Check terminal logs!"
            payout_status = "transfer_error"
    else:
        req_bal_formatted = f"{draw_state['required_balance']:,}".replace(",", ".")
        if matched_threshold_count > 0:
            draw_state["result_info"] = f"There were tickets with correct numbers, but the wallets did not have the required {req_bal_formatted} tokens condition at draw time."
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
    print(f"--- DRAW #{draw_state['draw_id']} FINISHED. Winners: {winners} | TX URL: {tx_url} ---\n")

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
    
    if settings.pick_count < 1 or settings.pick_count > 10:
        raise HTTPException(status_code=400, detail="Pick count must be between 1 and 10.")
        
    if settings.match_threshold < 1 or settings.match_threshold > settings.pick_count:
        raise HTTPException(status_code=400, detail="Match threshold cannot be greater than pick count.")

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

    if not ticket.numbers or len(ticket.numbers) != draw_state["pick_count"]:
        raise HTTPException(status_code=400, detail=f"You must select exactly {draw_state['pick_count']} numbers.")
    
    for n in ticket.numbers:
        if not (1 <= n <= draw_state["max_number"]):
            raise HTTPException(status_code=400, detail="Numbers must be between 1 and 50.")

    existing = next((p for p in draw_state["participants"] if p["wallet"] == ticket.wallet), None)
    if existing:
        raise HTTPException(status_code=400, detail="This wallet has already participated in this draw.")

    has_token, err_msg = check_wallet_token_balance(ticket.wallet, draw_state["required_balance"])
    if not has_token:
        raise HTTPException(status_code=400, detail=err_msg)

    client_ip = request.client.host if request.client else "unknown"

    draw_state["participants"].append({"wallet": ticket.wallet, "numbers": ticket.numbers, "ip": client_ip})
        
    return {"message": "Ticket successfully signed and saved!"}

app.mount("/", StaticFiles(directory=".", html=True), name="static")
