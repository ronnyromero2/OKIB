from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
import os
import datetime

load_dotenv()

# API-Keys
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# CORS aktivieren
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTML-Datei ausliefern
@app.get("/")
def serve_html():
    return FileResponse("berater.html")

# Modelle
class ChatInput(BaseModel):
    message: str

class ProfileData(BaseModel):
    beruf: str
    beziehungsziel: str
    prioritäten: str

class InterviewAntwort(BaseModel):
    antwort: str

class MemoryInput(BaseModel):
    thema: str
    inhalt: str

class Goal(BaseModel):
    titel: str
    status: str = "offen"
    deadline: str = ""

class GoalUpdate(BaseModel):
    id: int
    status: str

# Endpunkte
@app.post("/chat")
def chat(input: ChatInput):
    profile = supabase.table("profile").select("*").order("id", desc=True).limit(1).execute().data
    beruf = profile[0]["beruf"] if profile else ""
    beziehungsziel = profile[0]["beziehungsziel"] if profile else ""
    prioritäten = profile[0]["prioritäten"] if profile else ""

    today = datetime.datetime.now().strftime("%A")
    routines = supabase.table("routines").select("*").eq("day", today).execute().data
    routines_text = "\n".join([f"{r['time']} - {r['task']}" for r in routines]) if routines else "Heute keine speziellen Aufgaben."

    history = supabase.table("conversation_history").select("*").order("timestamp", desc=True).limit(10).execute().data
    history_text = "\n".join([f"User: {h['user_input']} | Berater: {h['ai_response']}" for h in reversed(history)]) if history else ""

    memory = supabase.table("long_term_memory").select("*").order("timestamp").execute().data
    memory_text = "\n".join([f"{m['thema']}: {m['inhalt']}" for m in memory]) if memory else ""

    system_message = f"""
Du bist ein persönlicher Mentor und Berater.
Beruf: {beruf}
Beziehungsziel: {beziehungsziel}
Prioritäten: {prioritäten}
Routinen heute: {routines_text}
Langzeitgedächtnis: {memory_text}
Letzte Gespräche: {history_text}

Antworten bitte kurz, präzise, motivierend. Maximal 4 Sätze.
"""

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": input.message}
        ]
    )

    ai_response = response.choices[0].message.content

    supabase.table("conversation_history").insert({
        "user_input": input.message,
        "ai_response": ai_response,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }).execute()

    return {"reply": ai_response}

@app.post("/profile")
def update_profile(data: ProfileData):
    supabase.table("profile").upsert({
        "id": 1,
        "beruf": data.beruf,
        "beziehungsziel": data.beziehungsziel,
        "prioritäten": data.prioritäten
    }).execute()
    return {"status": "Profil aktualisiert"}

@app.get("/routines")
def get_routines():
    today = datetime.datetime.now().strftime("%A")
    routines = supabase.table("routines").select("*").eq("day", today).execute().data
    if routines:
        text = "\n".join([f"{r['time']} - {r['task']}" for r in routines])
    else:
        text = "Heute stehen keine speziellen Aufgaben an."
    return {"text": text}

@app.get("/interview")
def get_interview_question():
    profile = supabase.table("profile").select("*").order("id", desc=True).limit(1).execute().data
    if not profile:
        return {"frage": "Welchen Beruf übst du aktuell aus oder was ist deine berufliche Leidenschaft?"}

    row = profile[0]
    if not row.get("beruf"):
        return {"frage": "Welchen Beruf übst du aktuell aus oder was ist deine berufliche Leidenschaft?"}
    if not row.get("beziehungsziel"):
        return {"frage": "Was ist dein wichtigstes Ziel in deiner Beziehung?"}
    if not row.get("prioritäten"):
        return {"frage": "Was sind deine aktuell wichtigsten Prioritäten im Leben?"}

    return {"frage": "Alle Basisdaten sind vorhanden. Danke!"}

@app.post("/interview")
def speichere_interview(data: InterviewAntwort):
    profile = supabase.table("profile").select("*").order("id", desc=True).limit(1).execute().data
    if not profile:
        supabase.table("profile").insert({"beruf": data.antwort}).execute()
        return {"status": "Beruf gespeichert"}

    row = profile[0]
    beruf = row.get("beruf", "")
    ziel = row.get("beziehungsziel", "")
    prios = row.get("prioritäten", "")

    if not beruf:
        supabase.table("profile").insert({"beruf": data.antwort, "beziehungsziel": ziel, "prioritäten": prios}).execute()
        return {"status": "Beruf gespeichert"}
    if not ziel:
        supabase.table("profile").insert({"beruf": beruf, "beziehungsziel": data.antwort, "prioritäten": prios}).execute()
        return {"status": "Beziehungsziel gespeichert"}
    if not prios:
        supabase.table("profile").insert({"beruf": beruf, "beziehungsziel": ziel, "prioritäten": data.antwort}).execute()
        return {"status": "Prioritäten gespeichert"}

    return {"status": "Keine Speicherung nötig"}

@app.post("/memory")
def speichere_gedaechtnis(data: MemoryInput):
    supabase.table("long_term_memory").insert({
        "thema": data.thema,
        "inhalt": data.inhalt,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }).execute()
    return {"status": "Thema gespeichert"}

@app.post("/goals")
def ziel_anlegen(goal: Goal):
    supabase.table("goals").insert({
        "titel": goal.titel,
        "status": goal.status,
        "deadline": goal.deadline,
        "created_at": datetime.datetime.utcnow().isoformat()
    }).execute()
    return {"status": "Ziel gespeichert"}

@app.get("/goals")
def alle_ziele():
    goals = supabase.table("goals").select("*").order("created_at", desc=True).execute().data
    return {"goals": goals}

@app.post("/goals/update")
def ziel_aktualisieren(update: GoalUpdate):
    supabase.table("goals").update({"status": update.status}).eq("id", update.id).execute()
    return {"status": f"Ziel {update.id} auf '{update.status}' gesetzt"}
