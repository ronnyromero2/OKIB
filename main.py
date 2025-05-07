from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
import os
import datetime

load_dotenv()

app = FastAPI()

origins = [
    "*"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

supabase_url = os.getenv("SUPABASE_URL")
supabase_key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(supabase_url, supabase_key)

class Message(BaseModel):
    user_id: str
    content: str

@app.post("/send_message")
async def send_message(message: Message, background_tasks: BackgroundTasks):
    response = generate_response(message.user_id, message.content)
    background_tasks.add_task(store_message, message.user_id, message.content, response)
    return {"response": response}

@app.get("/start_interaction/{user_id}")
async def start_interaction(user_id: str):
    # Abrufen der letzten Interaktionen
    response = get_initial_question(user_id)
    return {"response": response}

def get_initial_question(user_id: str):
    # Letzte Nachrichten abrufen
    recent_interactions = supabase.table("conversation_history").select("content").eq("user_id", user_id).order("timestamp", desc=True).limit(5).execute()
    messages = [msg["content"] for msg in recent_interactions.data]
    # Logik zur Generierung der Startfrage
    # Beispiel: Basierend auf letzter Interaktion eine Frage auswählen
    if messages:
        return f"Ich habe gesehen, dass du zuletzt über {messages[0]} gesprochen hast. Möchtest du mir mehr darüber erzählen?"
    else:
        return "Willkommen zurück! Woran arbeitest du gerade oder worüber möchtest du sprechen?"

def generate_response(user_id: str, content: str):
    # Keine Folgefrage. Nur Verarbeitung der Antwort
    return "Danke, dass du das geteilt hast!"
