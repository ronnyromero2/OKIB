import datetime
import random # Hinzugefügt, falls nicht schon da (war in start_interaction)
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
import os

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

# Modelle (Deine Pydantic Models)
class ChatInput(BaseModel):
    message: str

class ProfileData(BaseModel):
    beruf: str
    beziehungsziel: str
    prioritaeten: str

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

class RoutineUpdate(BaseModel): # Sicherstellen, dass diese Klasse existiert
    id: int
    checked: bool

# Neue Hilfsfunktion zum Summarisieren (musste wieder hinzugefügt werden)
def summarize_text_with_gpt(text_to_summarize: str, summary_length: int = 200, prompt_context: str = "wichtige Punkte und Muster"):
    """
    Fasst einen langen Text mit GPT zusammen, um Token zu sparen.
    Verwendet gpt-3.5-turbo für Kosten- und Geschwindigkeitseffizienz bei der Summarisierung.
    """
    if not text_to_summarize.strip():
        return "" # Nichts zusammenfassen, wenn der Text leer ist

    summary_prompt = f"""
    Fasse den folgenden Text prägnant zusammen und konzentriere dich auf die {prompt_context}.
    Beschränke die Zusammenfassung auf maximal {summary_length} Wörter.

    Text:
    {text_to_summarize}
    """
    try:
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Du bist ein hilfreicher Assistent, der lange Texte zusammenfassen kann."},
                {"role": "user", "content": summary_prompt}
            ],
            max_tokens=summary_length * 2,
            temperature=0.3
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Fehler beim Zusammenfassen mit GPT: {e}")
        return "Eine Zusammenfassung konnte nicht erstellt werden."


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
@app.get("/start_interaction/{user_id}") # user_id im Pfad, da get_recent_entry_questions sie braucht
async def start_interaction(user_id: str): # user_id als Parameter, um aus dem Pfad zu nehmen
    # Letzte 30 Nachrichten abrufen
    recent_interactions = supabase.table("conversation_history") \
        .select("user_input") \
        .eq("user_id", user_id) \
        .order("timestamp", desc=True) \
        .limit(30) \
        .execute()

    messages = [msg["user_input"] for msg in recent_interactions.data if msg["user_input"] != "Starte ein Gespräch"]

    print("Letzte 30 Nachrichten:", messages)

    if not messages:
        return {"frage": "Was möchtest du heute angehen? Gibt es ein neues Thema, über das du sprechen möchtest?"}

    recent_entry_questions = get_recent_entry_questions(user_id) # user_id hierhin übergeben

    recent_topics = messages[:5]
    remaining_messages = messages[5:]

    # Routinen überprüfen (OHNE user_id Filter, da keine user_id-Spalte in der routines-Tabelle)
    today = datetime.datetime.now().strftime("%A")
    unfulfilled_routines = supabase.table("routines") \
        .select("*") \
        .eq("day", today) \
        .eq("checked", False) \
        .execute().data

    routine_texts = [r["task"] for r in unfulfilled_routines if r.get("missed_count", 0) >= 3]
    routine_context = ", ".join(routine_texts)

    print("Wiederholt unerfüllte Routinen:", routine_context)

    simulate_universe = random.random() < 0.05 # Wahrscheinlichkeit für Simulation/Universum-Perspektive

    if simulate_universe:
        prompt = f"""
        Du bist hypothetisch die Simulation oder das Universum und möchtest dem Nutzer heute einen konkreten Hinweis geben. 
        Tue so, als ob du tatsächlich Kontakt zum Universum oder zur Simulation hättest und etwas Wichtiges über seinen heutigen Tag weißt. 
        Vermeide die letzten vier Einstiegsfragen:

        {", ".join(recent_entry_questions)}

        Sei sehr konkret und weise auf eine bestimmte Aktion, Einstellung oder ein Ereignis hin. Bleibe dabei einfühlsam und motivierend.
        """
    else:
        filtered_messages = [msg for msg in remaining_messages if msg not in recent_entry_questions]

        if not filtered_messages:
            # Liste für den Fall, dass keine geeigneten Themen gefunden werden
            filtered_messages = ["Langfristige Ziele", "Bestehende Routinen", "Neue Routinen", "Selbstreflexion", "Freizeitgestaltung",
                                 "Umgang mit Herausforderungen", "Lernprozesse", "Beziehungen pflegen",
                                 "Umgang mit Energie und Erholung", "Persönliche Werte", "Zukunftsvisionen",
                                 "Umgang mit Ängsten oder Sorgen", "Erfolge feiern"]

        selected_topic = random.choice(filtered_messages)

        prompt = f"""
        Du bist eine offene Freundin, die ein Gespräch mit mich starten will. Formuliere eine **motivierende, sehr konkrete und personalisierte** Frage basierend auf einem Thema,
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

    print("GPT Prompt:", prompt)

    try:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt + "\n\nBitte antworte in maximal 3 kurzen Zeilen."}],
            max_tokens=120,
            temperature=0.7
        )

        frage = response.choices[0].message.content.strip()

        if not frage:
            frage = "Was möchtest du heute erreichen oder klären?"

        # user_id HINZUGEFÜGT, da conversation_history eine user_id-Spalte hat
        supabase.table("conversation_history").insert({
            "user_input": f"Einstiegsfrage: {frage}",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "user_id": user_id # user_id hier übergeben
        }).execute()

        return {"frage": frage}

    except Exception as e:
        print(f"Fehler bei der GPT-Anfrage: {e}")
        return {"frage": "Es gab ein Problem beim Generieren der Einstiegsfrage. Was möchtest du heute besprechen?"}


# Chat-Funktion (TOKEN-OPTIMIERTE UND VERBESSERTE VERSION)
@app.post("/chat")
async def chat(input: ChatInput):
    try:
        user_id = 1 # Feste user_id

        profile_data = supabase.table("profile").select("*").eq("id", user_id).execute().data
        profile = profile_data[0] if profile_data else {}

        beruf = profile.get("beruf", "nicht angegeben")
        beziehungsziel = profile.get("beziehungsziel", "nicht angegeben")
        prioritaeten = profile.get("prioritaeten", "nicht angegeben") # Korrekter Schlüssel

        # Routinen laden (OHNE user_id Filter)
        today = datetime.datetime.now().date()
        routines_data = supabase.table("routines").select("*").eq("day", today.strftime("%A")).execute().data

        routines_for_prompt = []
        unfulfilled_routines_today = [] # Liste für unerledigte Routinen heute
        for r in routines_data:
            db_last_checked_date = None
            if r['last_checked_date']:
                try:
                    db_last_checked_date = datetime.date.fromisoformat(r['last_checked_date'])
                except ValueError:
                    db_last_checked_date = r['last_checked_date'].date() if isinstance(r['last_checked_date'], datetime.datetime) else None

            current_checked_status = r['checked'] and (db_last_checked_date == today)
            routines_for_prompt.append(f"{r['task']} (Erledigt: {'Ja' if current_checked_status else 'Nein'})")
            
            if not current_checked_status: # Wenn die Routine für heute nicht erledigt ist
                unfulfilled_routines_today.append(r['task'])

        routines_text = "\n".join(routines_for_prompt) if routines_for_prompt else "Keine spezifischen Routinen für heute."
        unfulfilled_routines_text = ", ".join(unfulfilled_routines_today) if unfulfilled_routines_today else "Alle Routinen sind erledigt oder es gibt keine."


        # Konversationshistorie laden (mit user_id Filter) - NUR die letzten 10 Nachrichten
        history = supabase.table("conversation_history").select("user_input, ai_response").eq("user_id", user_id).order("timestamp", desc=True).limit(10).execute().data
        history_text = "\n".join([f"User: {h['user_input']} | Berater: {h['ai_response']}" for h in reversed(history)]) if history else "Bisher keine frühere Konversationshistorie."


        # Langzeitgedächtnis laden (OHNE user_id Filter) - Die letzten 5 wichtigen Erkenntnisse
        # Wichtig: Diese Einträge sollten bereits prägnante Zusammenfassungen sein (durch generiere_rueckblick erstellt)
        memory = supabase.table("long_term_memory").select("thema, inhalt").order("timestamp", desc=True).limit(5).execute().data
        memory_text = "\n".join([f"Thema: {m['thema']}\nInhalt: {m['inhalt']}" for m in memory]) if memory else "Keine spezifischen Langzeit-Erkenntnisse gespeichert."


        system_message = f"""
        Du bist ein persönlicher, **anspruchsvoller und konstruktiver Mentor und Therapeut**. Dein Ziel ist es, dem Nutzer **realistisch, prägnant und umsetzbar** zu helfen.
        
        **Es ist von höchster Priorität, dass du dich an folgende Anweisungen hältst:**
        1.  **Aktueller Fokus:** Beziehe dich primär auf das aktuelle Gespräch und die letzten 10 Nachrichten der Konversationshistorie.
        2.  **Langzeitgedächtnis nutzen:** Greife relevante ältere Themen, Ziele und wichtige Erkenntnisse (z.B. der Halbmarathon, Beziehung zur Frau) aus dem bereitgestellten "Langzeitgedächtnis" aktiv auf, wenn sie zur aktuellen Unterhaltung passen. **Erinnere den Nutzer an seinen Fortschritt oder ausstehende Punkte zu diesen Langzeitthemen.**
        3.  **Veraltete/Irrelevante Infos ignorieren:** Informationen, die offensichtlich veraltet oder nicht mehr relevant für den aktuellen Kontext sind (z.B. ein alter Standort wie Bogotá), sind zu **ignorieren**, es sei denn, der Nutzer spricht sie ausdrücklich an oder fordert dich dazu auf. Antworte **niemals** mit veralteten Informationen auf eine Frage nach dem aktuellen Zustand.
        4.  **Unerledigte Routinen ansprechen:** Wenn es für den heutigen Tag unerledigte Routinen gibt (siehe "Unerledigte Routinen heute"), **sprich den Nutzer DIREKT darauf an**. Frage nach den Gründen, eventuellen Hindernissen oder schlage konkrete Schritte vor, wie sie heute noch erledigt werden können.

        Nutze die folgenden Informationen, um dem Nutzer **direkt auf den Punkt kommende, handlungsorientierte Ratschläge und Verbesserungsvorschläge** zu geben:

        Nutzerprofil:
        Beruf: {beruf}
        Beziehungsziel: {beziehungsziel}
        Prioritaeten: {prioritaeten}
        
        Deine heutigen Routinen:
        {routines_text}
        
        **Unerledigte Routinen heute:** {unfulfilled_routines_text}

        **Wichtige Erkenntnisse aus dem Langzeitgedächtnis (ältere Themen/Ziele):**
        {memory_text}

        **Jüngste Konversationshistorie (letzte 10 Nachrichten):**
        {history_text}

        **Analysiere die aktuelle Nachricht des Nutzers IMMER im Kontext ALLER verfügbaren Informationen.**
        **Erkenne dabei auch mögliche Inkonsistenzen oder mangelnden Fortschritt.**
        **Gebe KEIN allgemeines Lob oder oberflächliche Bestätigungen.**
        **Fokussiere dich darauf, WO der Nutzer WIRKLICH ansetzen kann, um voranzukommen.**
        **Stelle konkrete Fragen, schlage spezifische Aktionen vor oder weise auf notwendige Reflexionen hin.**

        Antworte **maximal 4 Sätze**. Deine Antworten sollen **knapp, direkt, motivierend und auf konkrete nächste Schritte** ausgerichtet sein.
        """

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": input.message}
            ],
            max_tokens=100, # Max tokens für die Antwort, nicht den Prompt
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()

        supabase.table("conversation_history").insert({
            "user_id": user_id,
            "user_input": input.message,
            "ai_response": reply,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute()

        return {"reply": reply}

    except Exception as e:
        print(f"Fehler in der Chat-Funktion: {e}")
        return {"reply": "Entschuldige, es gab ein Problem beim Verarbeiten deiner Anfrage. Bitte versuche es später noch einmal."}

# Automatischer Wochen- und Monatsbericht
@app.get("/bericht/automatisch")
def automatischer_bericht():
    heute = datetime.datetime.now()
    wochentag = heute.weekday() # Montag = 0, Sonntag = 6
    # Korrigierte Logik für den letzten Tag des Monats
    letzter_tag_des_monats = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)

    if heute.date() == letzter_tag_des_monats.date():
        bericht = generiere_rueckblick("Monats", 30)
        return {"typ": "Monatsbericht", "inhalt": bericht}
    elif wochentag == 6: # Sonntag
        bericht = generiere_rueckblick("Wochen", 7)
        return {"typ": "Wochenbericht", "inhalt": bericht}
    else:
        return {"typ": None, "inhalt": None}

# Wochen- und Monatsberichte generieren (VERBESSERT FÜR LANGZEITGEDÄCHTNIS)
def generiere_rueckblick(zeitraum: str, tage: int):
    user_id = 1 # Feste User ID für Berichte, da conversation_history user_id hat

    seit = (datetime.datetime.utcnow() - datetime.timedelta(days=tage)).isoformat()

    # Rufe die gesamte Konversationshistorie für den Zeitraum ab (MIT user_id Filter)
    all_gespraeche = supabase.table("conversation_history").select("user_input, ai_response, timestamp").gte("timestamp", seit).eq("user_id", user_id).order("timestamp", asc=True).execute().data
    
    # Ziele abrufen (OHNE user_id Filter, da keine user_id-Spalte in der goals-Tabelle)
    all_ziele = supabase.table("goals").select("titel, status, created_at").gte("created_at", seit).order("created_at", asc=True).execute().data

    # Trenne jüngste Gespräche (z.B. die letzten 10) vom Rest für detaillierte Darstellung
    recent_gespraeche = all_gespraeche[-10:] if len(all_gespraeche) > 10 else all_gespraeche[:]
    older_gespraeche = all_gespraeche[:-10] if len(all_gespraeche) > 10 else []

    recent_gespraeche_text = "\n".join([f"User: {g['user_input']} | Berater: {g['ai_response']}" for g in recent_gespraeche])

    summarized_older_history = ""
    if older_gespraeche:
        older_history_raw_text = "\n".join([f"User: {g['user_input']} | Berater: {g['ai_response']}" for g in older_gespraeche])
        # WICHTIG: Hier die summarize_text_with_gpt Funktion nutzen
        summarized_older_history = summarize_text_with_gpt(older_history_raw_text, summary_length=150, prompt_context="die wichtigsten Trends, Herausforderungen und Entscheidungen")

    gespraeche_text_for_prompt = ""
    if recent_gespraeche_text:
        gespraeche_text_for_prompt += f"Jüngste Gespräche (letzte {len(recent_gespraeche)}):\n{recent_gespraeche_text}\n"
    if summarized_older_history:
        gespraeche_text_for_prompt += f"\nZusammenfassung älterer Gespräche im {zeitraum}:\n{summarized_older_history}\n"
    if not gespraeche_text_for_prompt:
        gespraeche_text_for_prompt = "Es gab keine relevanten Gespräche in diesem Zeitraum."

    ziele_text = "\n".join([f"{z['titel']} ({z['status']})" for z in all_ziele[-20:]]) # Limit auf die letzten 20 Ziele

    system = f"""
    Du bist ein persönlicher Assistent. Schreibe einen detaillierten und motivierenden Rückblick basierend auf den letzten {zeitraum}, einschließlich der wichtigsten Punkte aus Gesprächen und dem Status von Zielen.
    Analysiere Trends, erkenne Fortschritte oder Herausforderungen und gebe konkrete, umsetzbare Vorschläge für die Zukunft.
    Berücksichtige sowohl die jüngsten detaillierten Gespräche als auch die zusammengefassten älteren Kontexte.
    """
    user = f"""
    Hier sind die Informationen für den {zeitraum}-Rückblick:

    Gespräche:
    {gespraeche_text_for_prompt}

    Ziele (Status):
    {ziele_text}

    Bitte gib einen motivierenden und tiefgehenden Rückblick, der wirklich analysiert, was passiert ist und konkrete, umsetzbare nächste Schritte vorschlägt.
    Antworte in einem zusammenhängenden Textabschnitt, maximal 500 Wörter.
    """

    response = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        max_tokens=500,
        temperature=0.7
    )

    bericht = response.choices[0].message.content

    # Bericht speichern (OHNE user_id, da long_term_memory keine user_id-Spalte haben soll)
    supabase.table("long_term_memory").insert({
        "thema": f"{zeitraum}srückblick",
        "inhalt": bericht,
        "timestamp": datetime.datetime.utcnow().isoformat()
    }).execute()

    return bericht
    
# Routinen abrufen (wie zuletzt besprochen: OHNE user_id)
@app.get("/routines")
def get_routines():
    today = datetime.datetime.now().date() 

    routines = supabase.table("routines").select("*").eq("day", today.strftime("%A")).execute().data 

    for r in routines:
        db_last_checked_date = None
        if r['last_checked_date']:
            try:
                db_last_checked_date = datetime.date.fromisoformat(r['last_checked_date'])
            except ValueError:
                db_last_checked_date = r['last_checked_date'].date() if isinstance(r['last_checked_date'], datetime.datetime) else None

        if r['checked'] and (db_last_checked_date is None or db_last_checked_date != today):
            r['checked'] = False

    return {"routines": routines}

# Routinenstatus aktualisieren (wie zuletzt besprochen: OHNE user_id)
@app.post("/routines/update")
def update_routine_status(update: RoutineUpdate):
    try:
        checked_status = update.checked
        current_date = datetime.datetime.now().date().isoformat() if checked_status else None

        supabase.table("routines").update({
            "checked": checked_status,
            "last_checked_date": current_date 
        }).eq("id", update.id).execute()

        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Routine: {e}")
        return {"status": "error", "message": str(e)}

# Ziele abrufen (wie zuletzt besprochen: OHNE user_id)
@app.get("/goals")
def get_goals():
    try:
        goals = supabase.table("goals").select("*").execute().data 
        return {"goals": goals}
    except Exception as e:
        print(f"Fehler beim Abrufen der Ziele: {e}")
        return {"goals": []}

# Ziel erstellen (Hinzugefügt, da es in deinem Code fehlte)
@app.post("/goals") # DECORATOR HINZUGEFÜGT
def create_goal(goal: Goal): # user_id Parameter entfernt, da keine user_id-Spalte in goals-Tabelle
    try:
        goal_data = goal.model_dump()
        supabase.table("goals").insert(goal_data).execute()
        return {"status": "success", "message": "Ziel erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern des Ziels: {e}")
        return {"status": "error", "message": str(e)}

# Zielstatus aktualisieren (Hinzugefügt, da es in deinem Code fehlte)
@app.post("/goals/update") # DECORATOR HINZUGEFÜGT
def update_goal_status(update: GoalUpdate): # user_id Parameter entfernt
    try:
        supabase.table("goals").update({"status": update.status}).eq("id", update.id).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren des Ziels: {e}")
        return {"status": "error", "message": str(e)}

# Interviewfrage abrufen (user_id im Pfad, da profile id hat)
@app.get("/interview/{user_id}") # PFAD ANGEPASST, um user_id zu übergeben
async def get_interview_question(user_id: str): # user_id als Parameter
    try:
        profile = supabase.table("profile").select("*").eq("id", user_id).execute().data # Filter nach user_id

        if not profile:
            return {"frage": "Welche Themen beschäftigen dich derzeit?"}

        user_profile = profile[0]

        if not user_profile.get("beruf"):
            return {"frage": "Was machst du beruflich oder was interessiert dich beruflich?"}
        elif not user_profile.get("beziehungsziel"):
            return {"frage": "Hast du ein bestimmtes Ziel in deinen Beziehungen, das du verfolgen möchtest?"}
        elif not user_profile.get("prioritaeten"):
            return {"frage": "Was sind aktuell deine wichtigsten Prioritäten?"}

        recent_questions = supabase.table("conversation_history") \
            .select("user_input") \
            .eq("user_id", user_id) \
            .ilike("user_input", "Interviewfrage:%") \
            .order("timestamp", desc=True) \
            .limit(5) \
            .execute().data

        covered_topics = [q["user_input"].replace("Interviewfrage: ", "") for q in recent_questions]

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

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
            )

            frage = response.choices[0].message.content.strip()

            if not frage:
                frage = "Welche Ziele möchtest du in den nächsten Monaten erreichen? GPT hat keine sinnvolle Frage geliefert"

            return {"frage": frage}

        except Exception as e:
            print(f"Fehler bei der dynamischen Interviewfrage: {e}")
            return {"frage": "Es gab ein Problem beim Generieren der nächsten Interviewfrage."}

    except Exception as e:
        print(f"Fehler bei der Interviewfrage: {e}")
        return {"frage": "Es gab ein Problem beim Abrufen der Interviewfrage."}


# Memory-Endpoint (Hinzugefügt, da es in deinem Code fehlte)
@app.post("/memory")
def create_memory(memory_input: MemoryInput):
    try:
        supabase.table("long_term_memory").insert({
            "thema": memory_input.thema,
            "inhalt": memory_input.inhalt,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute() # OHNE user_id, da long_term_memory keine user_id-Spalte haben soll
        return {"status": "success", "message": "Erinnerung erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern der Erinnerung: {e}")
        return {"status": "error", "message": str(e)}

# Profil-Endpoint (Hinzugefügt, da es in deinem Code fehlte)
@app.post("/profile")
def create_profile(profile_data: ProfileData, user_id: str = "1"): # user_id als Parameter, um es in die DB zu schreiben
    try:
        existing_profile = supabase.table("profile").select("id").eq("id", user_id).execute().data
        
        profile_dict = profile_data.model_dump()
        profile_dict["id"] = user_id # id ist der Primärschlüssel für das Profil

        if existing_profile:
            supabase.table("profile").update(profile_dict).eq("id", user_id).execute()
            return {"status": "success", "message": "Profil erfolgreich aktualisiert."}
        else:
            supabase.table("profile").insert(profile_dict).execute()
            return {"status": "success", "message": "Profil erfolgreich erstellt."}
    except Exception as e:
        print(f"Fehler beim Speichern des Profils: {e}")
        return {"status": "error", "message": str(e)}

# Manuelle Berichts-Endpunkte (Wieder hinzugefügt, da in deinem Code fehlend)
@app.get("/bericht/woche")
def generiere_wochenbericht_manuell():
    bericht_inhalt = generiere_rueckblick("Wochen", 7)
    return {"typ": "Wochenbericht", "inhalt": bericht_inhalt}

@app.get("/bericht/monat")
def generiere_monatsbericht_manuell():
    bericht_inhalt = generiere_rueckblick("Monats", 30)
    return {"typ": "Monatsbericht", "inhalt": bericht_inhalt}
