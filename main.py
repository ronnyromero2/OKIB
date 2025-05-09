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

# Startfrage bei neuer Interaktion
@app.get("/start_interaction/{user_id}")
def start_interaction(user_id: str):
    # Letzte 5 Nachrichten des Benutzers abrufen
    recent_interactions = supabase.table("conversation_history") \
        .select("user_input") \
        .eq("user_id", user_id) \
        .order("timestamp", desc=True) \
        .limit(5) \
        .execute()

    # Nachrichten extrahieren
    messages = [msg["user_input"] for msg in recent_interactions.data if msg["user_input"] != "Starte ein Gespräch"]

    # Falls keine Nachrichten vorhanden sind, eine allgemeine Frage stellen
    if not messages:
        return {"frage": "Hey, was beschäftigt dich heute? Gibt es etwas, das du loswerden möchtest?"}

    # GPT-Anfrage vorbereiten
    context = "\n".join(messages)
    prompt = f"""
    Du bist ein persönlicher Mentor und Coach. Basierend auf den letzten Nachrichten des Nutzers, formuliere eine motivierende, offene Frage, die ihn dazu einlädt, weiterzumachen oder ein vorheriges Thema zu vertiefen. Hier sind die letzten Nachrichten:

    {context}

    Beispiel für motivierende Fragen:
    - Hast du eine Lösung für XYZ gefunden?
    - Wie lief die Vorbereitung für ABC?
    - Möchtest du etwas über DEF erzählen?
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )
        frage = response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Fehler bei der GPT-Anfrage: {e}")
        frage = "Was möchtest du heute erreichen oder klären?"

    return {"frage": frage}


# Automatischer Wochen- und Monatsbericht
@app.get("/bericht/automatisch")
def automatischer_bericht():
    heute = datetime.datetime.now()
    wochentag = heute.weekday()  # Montag = 0, Sonntag = 6
    letzter_tag_des_monats = (heute.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1)

    if heute.date() == letzter_tag_des_monats.date():
        bericht = generiere_rueckblick("Monats", 30)
        return {"typ": "Monatsbericht", "inhalt": bericht}
    elif wochentag == 6:
        bericht = generiere_rueckblick("Wochen", 7)
        return {"typ": "Wochenbericht", "inhalt": bericht}
    else:
        return {"typ": None, "inhalt": None}

# Wochen- und Monatsberichte generieren
def generiere_rueckblick(zeitraum: str, tage: int):
    seit = (datetime.datetime.utcnow() - datetime.timedelta(days=tage)).isoformat()
    gespraeche = supabase.table("conversation_history").select("*").gte("timestamp", seit).execute().data
    ziele = supabase.table("goals").select("*").gte("created_at", seit).execute().data

    gespraeche_text = "\n".join([f"User: {g['user_input']} | Berater: {g['ai_response']}" for g in gespraeche])
    ziele_text = "\n".join([f"{z['titel']} ({z['status']})" for z in ziele])

    system = f"""
    Du bist ein persönlicher Assistent. Schreibe einen Rückblick basierend auf den letzten {zeitraum}, einschließlich Gesprächen und Zielen. Sei konkret und motivierend.
    """
    user = f"""
    Hier sind die letzten {zeitraum}:
    
    Gespräche:
    {gespraeche_text}
    
    Ziele:
    {ziele_text}
    
    Bitte gib einen motivierenden Rückblick.
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ]
    )

    bericht = response.choices[0].message.content

    # Bericht speichern
    supabase.table("long_term_memory").insert({
        "thema": f"{zeitraum}srückblick",
        "inhalt": bericht,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }).execute()

    return bericht
    
from pydantic import BaseModel

class RoutineUpdate(BaseModel):
    id: int
    checked: bool

# Routinen abrufen
@app.get("/routines")
def get_routines():
    today = datetime.datetime.now().strftime("%A")

    # Routines abrufen
    routines = supabase.table("routines").select("*").eq("day", today).execute().data

    # Übergebe `checked`-Status für jede Routine
    return {"routines": routines}

# Routinenstatus aktualisieren
@app.post("/routines/update")
def update_routine_status(update: RoutineUpdate):
    try:
        supabase.table("routines").update({"checked": update.checked}).eq("id", update.id).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Routine: {e}")
        return {"status": "error", "message": str(e)}

# Ziele abrufen
@app.get("/goals")
def get_goals():
    try:
        goals = supabase.table("goals").select("*").execute().data
        return {"goals": goals}
    except Exception as e:
        print(f"Fehler beim Abrufen der Ziele: {e}")
        return {"goals": []}

# Interviewfrage abrufen
@app.get("/interview")
def get_interview_question():
    try:
        profile = supabase.table("profile").select("*").order("id", desc=True).limit(1).execute().data

        if profile:
            user_profile = profile[0]

            if not user_profile.get("beruf"):
                return {"frage": "Was machst du beruflich oder was interessiert dich beruflich?"}
            elif not user_profile.get("beziehungsziel"):
                return {"frage": "Hast du ein bestimmtes Ziel in deinen Beziehungen, das du verfolgen möchtest?"}
            elif not user_profile.get("prioritäten"):
                return {"frage": "Was sind aktuell deine wichtigsten Prioritäten?"}
            else:
                return {"frage": "Danke, dass du deine Daten geteilt hast. Lass uns weiterarbeiten!"}

        return {"frage": "Welche Themen beschäftigen dich derzeit?"}

    except Exception as e:
        print(f"Fehler bei der Interviewfrage: {e}")
        return {"frage": "Es gab ein Problem beim Abrufen der Interviewfrage."}

# Chat-Funktion
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
    Du bist ein persönlicher Mentor.
    Beruf: {beruf}
    Beziehungsziel: {beziehungsziel}
    Prioritäten: {prioritäten}
    Routinen heute: {routines_text}
    Langzeitgedächtnis: {memory_text}
    Letzte Gespräche: {history_text}
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system_message},
            {"role": "user", "content": input.message}
        ]
    )

    ai_response = response.choices[0].message.content

    # Speichern in der Datenbank
    supabase.table("conversation_history").insert({
        "user_input": input.message,
        "ai_response": ai_response,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }).execute()

    return {"reply": ai_response}
