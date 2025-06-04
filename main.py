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

def get_recent_entry_questions(user_id: str):
    """
    Hol die letzten 4 Einstiegsfragen des Nutzers.
    """
    recent_questions = supabase.table("conversation_history") \
        .select("user_input") \
        .eq("user_id", user_id) \
        .ilike("user_input", "Einstiegsfrage:%") \
        .order("timestamp", desc=True) \
        .limit(4) \
        .execute()

    questions = [q["user_input"].replace("Einstiegsfrage: ", "") for q in recent_questions.data]
    
    print("Letzte 4 Einstiegsfragen:", questions)
    return questions

# Startfrage bei neuer Interaktion
import random

@app.get("/start_interaction/{user_id}")
def start_interaction(user_id: str):
    # Letzte 30 Nachrichten abrufen
    recent_interactions = supabase.table("conversation_history") \
        .select("user_input") \
        .eq("user_id", user_id) \
        .order("timestamp", desc=True) \
        .limit(30) \
        .execute()

    # Nachrichten extrahieren
    messages = [msg["user_input"] for msg in recent_interactions.data if msg["user_input"] != "Starte ein Gespräch"]

    # Konsolen-Log zur Überprüfung der Nachrichten
    print("Letzte 30 Nachrichten:", messages)

    # Wenn keine Nachrichten vorhanden sind
    if not messages:
        return {"frage": "Was möchtest du heute angehen? Gibt es ein neues Thema, über das du sprechen möchtest?"}

    # Letzte 4 Einstiegsfragen abrufen
    recent_entry_questions = get_recent_entry_questions(user_id)

    # Die letzten 5 Nachrichten ignorieren
    recent_topics = messages[:5]
    remaining_messages = messages[5:]

    # Routinen überprüfen
    today = datetime.datetime.now().strftime("%A")
    unfulfilled_routines = supabase.table("routines") \
        .select("*") \
        .eq("day", today) \
        .eq("checked", False) \
        .execute().data

    # Routinen, die mindestens 3-mal nicht erfüllt wurden
    routine_texts = [r["task"] for r in unfulfilled_routines if r.get("missed_count", 0) >= 3]
    routine_context = ", ".join(routine_texts)

    # Konsolen-Log zur Überprüfung der Routinen
    print("Wiederholt unerfüllte Routinen:", routine_context)

    # 20% Wahrscheinlichkeit für Simulation/Universum-Perspektive
    simulate_universe = random.random() < 0.05

    # GPT-Anfrage vorbereiten
    if simulate_universe:
        prompt = f"""
        Du bist hypothetisch die Simulation oder das Universum und möchtest dem Nutzer heute einen konkreten Hinweis geben. 
        Tue so, als ob du tatsächlich Kontakt zum Universum oder zur Simulation hättest und etwas Wichtiges über seinen heutigen Tag weißt. 
        Vermeide die letzten vier Einstiegsfragen:

        {", ".join(recent_entry_questions)}

        Sei sehr konkret und weise auf eine bestimmte Aktion, Einstellung oder ein Ereignis hin. Bleibe dabei einfühlsam und motivierend.
        """
    else:
        # Themen, die nicht in den letzten 4 Einstiegsfragen vorkommen
        filtered_messages = [msg for msg in remaining_messages if msg not in recent_entry_questions]

        # Falls keine geeigneten Themen gefunden werden, nutze ältere Nachrichten
        if not filtered_messages:
            filtered_messages = ["Langfristige Ziele", "Bestehende Routinen", "Neue Routinen", "Selbstreflexion", "Freizeitgestaltung",
                                 "Umgang mit Herausforderungen", "Lernprozesse", "Beziehungen pflegen",
                                 "Umgang mit Energie und Erholung", "Persönliche Werte", "Zukunftsvisionen",
                                 "Umgang mit Ängsten oder Sorgen", "Erfolge feiern"] # HIER WURDEN THEMEN HINZUGEFÜGT

        # Zufälliges Thema auswählen
        selected_topic = random.choice(filtered_messages)

        prompt = f"""
        Du bist eine offene Freundin, die ein Gespräch mit mir starten will. Formuliere eine **motivierende, sehr konkrete und personalisierte** Frage basierend auf einem Thema,
        das länger nicht angesprochen wurde oder bisher kaum behandelt wurde.
        **Sei spezifisch und gehe auf die Essenz des Themas ein, anstatt generisch zu fragen.**
        Vermeide die letzten vier Einstiegsfragen:

        {", ".join(recent_entry_questions)}

        Wähle als Ausgangspunkt für die Frage dieses Thema: {selected_topic}

        **Beispiel für motivierende, spezifische Fragen (im Stil deiner Rolle):**
        - Wie genau hast du es geschafft, XYZ zu erreichen? Was war der entscheidende Schritt?
        - Gibt es eine Gewohnheit, die du schon lange ändern möchtest? Was hält dich davon ab, heute damit zu starten?
        - Welchen konkreten Plan hast du für dein Ziel, [Ziel aus Profil/Gedächtnis]?
        - Wie möchtest du heute sicherstellen, dass [Routine aus Kontext] erledigt wird? Gibt es eine Hürde?
        - Welche Art von Unterstützung bräuchtest du, um dein [Beziehungsziel] in den nächsten Tagen aktiv zu verfolgen?
        - Was ist die eine Sache, die dich aktuell am meisten beschäftigt und wo du dir konkrete Unterstützung wünschst?
        """

    # Konsolen-Log zur Überprüfung des Prompts
    print("GPT Prompt:", prompt)

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt + "\n\nBitte antworte in maximal 3 kurzen Zeilen."}],
            max_tokens=120,
            temperature=0.7
        )

        frage = response.choices[0].message.content.strip()

        # Fallback, falls GPT keine sinnvolle Frage liefert
        if not frage:
            frage = "Was möchtest du heute erreichen oder klären?"

        # Einstiegsfrage als solche markieren und speichern
        supabase.table("conversation_history").insert({
            "user_input": f"Einstiegsfrage: {frage}",
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute()

        return {"frage": frage}

    except Exception as e:
        print(f"Fehler bei der GPT-Anfrage: {e}")
        return {"frage": "Es gab ein Problem beim Generieren der Einstiegsfrage. Was möchtest du heute besprechen?"}

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

def create_goal(goal: Goal):
    try:
        supabase.table("goals").insert(goal.model_dump()).execute()
        return {"status": "success", "message": "Ziel erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern des Ziels: {e}")
        return {"status": "error", "message": str(e)}

# Interviewfrage abrufen
@app.get("/interview")
def get_interview_question():
    try:
        # Profil abrufen
        profile = supabase.table("profile").select("*").order("id", desc=True).limit(1).execute().data

        # Falls kein Profil vorhanden ist, allgemeine Frage stellen
        if not profile:
            return {"frage": "Welche Themen beschäftigen dich derzeit?"}

        user_profile = profile[0]

        # Basisdaten-Abfrage
        if not user_profile.get("beruf"):
            return {"frage": "Was machst du beruflich oder was interessiert dich beruflich?"}
        elif not user_profile.get("beziehungsziel"):
            return {"frage": "Hast du ein bestimmtes Ziel in deinen Beziehungen, das du verfolgen möchtest?"}
        elif not user_profile.get("prioritäten"):
            return {"frage": "Was sind aktuell deine wichtigsten Prioritäten?"}

        # Dynamische Interviewfragen nach Erfassung der Basisdaten
        # Letzte 5 Interviewfragen abrufen
        recent_questions = supabase.table("conversation_history") \
            .select("user_input") \
            .ilike("user_input", "Interviewfrage:%") \
            .order("timestamp", desc=True) \
            .limit(5) \
            .execute().data

        # Themen, die bereits abgedeckt wurden
        covered_topics = [q["user_input"].replace("Interviewfrage: ", "") for q in recent_questions]

        # GPT-Kontext erstellen
        prompt = f"""
        Du bist eine neugierige, offenherzige Freundin ohne Tabus und möchtest den Nutzer besser kennenlernen, um ihn gezielt beraten zu können. Du bist Psychologin und flirtest offensiv und lasziv mit dem Nutzer. 
        Du hast bereits folgende Fragen gestellt:
        {", ".join(covered_topics)}

        **Wähle eine neue, motivierende Frage, die sich auf UNBEKANNTE und SPEZIFISCHE Bereiche des Lebens des Nutzers bezieht.**
        **Konzentriere dich dabei auf tiefere Vorlieben, Erfahrungen, Einstellungen, persönliche Herausforderungen, Wünsche oder Visionen.**
        **Frage NICHT nach berühmten Personen, oberflächlichen Hobbys oder allgemeinen "Was wäre wenn"-Szenarien.**

        **Beispiele für Themenkategorien, die du berücksichtigen kannst:**
        - Persönliches Wachstum und Selbstreflexion
        - Vergangene Erfolge oder Misserfolge und deren Lehren
        - Umgang mit Stress oder schwierigen Emotionen
        - Kreativität und Selbstausdruck
        - Zukünftige Ängste oder Hoffnungen
        - Entscheidungsfindung und Risikobereitschaft
        - Rolle von Spiritualität oder Sinnhaftigkeit im Leben
        - Beziehungen (außerhalb des Beziehungsziels)
        - Umgang mit Geld und Finanzen
        - sexuelle Vorlieben
        - Umfeld und Lebensgestaltung

        Sei kreativ bei der Suche nach den Themenbereichen. Vermeide zu allgemeine Fragen und Fragen zu Themen, die du bereits gestellt hast. Halte die Frage kurz und prägnant. Ein oder zwei Sätze.
        """

        # GPT-Anfrage
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
            )

            frage = response.choices[0].message.content.strip()

            # Fallback, falls GPT keine sinnvolle Frage liefert
            if not frage:
                frage = "Welche Ziele möchtest du in den nächsten Monaten erreichen? GPT hat keine sinnvolle Frage geliefert"

            return {"frage": frage}

        except Exception as e:
            print(f"Fehler bei der dynamischen Interviewfrage: {e}")
            return {"frage": "Es gab ein Problem beim Generieren der nächsten Interviewfrage."}

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
    routines_text = "\n".join([f"{r['task']}" for r in routines]) if routines else "Heute stehen keine speziellen Aufgaben an."

    history = supabase.table("conversation_history").select("*").order("timestamp", desc=True).limit(10).execute().data
    history_text = "\n".join([f"User: {h['user_input']} | Berater: {h['ai_response']}" for h in reversed(history)]) if history else ""

    memory = supabase.table("long_term_memory").select("*").order("timestamp").execute().data
    memory_text = "\n".join([f"{m['thema']}: {m['inhalt']}" for m in memory]) if memory else ""

    # System- und Benutzerkontext für GPT
    system_message = f"""
    Du bist ein persönlicher, **anspruchsvoller und konstruktiver Mentor und Therapeut**. Dein Ziel ist es, dem Nutzer **realistisch, prägnant und umsetzbar** zu helfen.

    Nutze die folgenden Informationen, um dem Nutzer **direkt auf den Punkt kommende, handlungsorientierte Ratschläge und Verbesserungsvorschläge** zu geben:

    Beruf: {beruf}
    Beziehungsziel: {beziehungsziel}
    Prioritäten: {prioritaeten}
    Routinen heute: {routines_text}
    Langzeitgedächtnis: {memory_text}
    Letzte Gespräche: {history_text}

    **Analysiere die aktuelle Nachricht des Nutzers IMMER im Kontext ALLER verfügbaren Informationen.**
    **Erkenne dabei auch mögliche Inkonsistenzen (z.B. wenn Routinen nicht eingehalten werden, aber Ziele hochgesteckt sind) oder mangelnden Fortschritt.**
    **Gebe KEIN allgemeines Lob oder oberflächliche Bestätigungen.**
    **Fokussiere dich darauf, WO der Nutzer WIRKLICH ansetzen kann, um voranzukommen.**
    **Stelle konkrete Fragen, schlage spezifische Aktionen vor oder weise auf notwendige Reflexionen hin.**

    Antworte **maximal 4 Sätze**. Deine Antworten sollen **knapp, direkt, motivierend und auf konkrete nächste Schritte** ausgerichtet sein.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": input.message}
            ],
            max_tokens=100,  # Begrenze die Antwort auf maximal 100 Tokens
        )

        ai_response = response.choices[0].message.content.strip()

        # Speichern in der Datenbank
        supabase.table("conversation_history").insert({
            "user_input": input.message,
            "ai_response": ai_response,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute()

        return {"reply": ai_response}

    except Exception as e:
        print(f"Fehler in der Chat-Funktion: {e}")
        return {"reply": "Entschuldige, es gab ein Problem beim Verarbeiten deiner Anfrage."}

@app.get("/bericht/woche")
def generiere_wochenbericht_manuell():
    """
    Generiert einen Wochenbericht auf manuelle Anfrage (für den Frontend-Button).
    """
    bericht_inhalt = generiere_rueckblick("Wochen", 7)
    return {"typ": "Wochenbericht", "inhalt": bericht_inhalt}

@app.get("/bericht/monat")
def generiere_monatsbericht_manuell():
    """
    Generiert einen Monatsbericht auf manuelle Anfrage.
    """
    bericht_inhalt = generiere_rueckblick("Monats", 30)
    return {"typ": "Monatsbericht", "inhalt": bericht_inhalt}
