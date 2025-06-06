from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
import os
import datetime
import random # Hinzugefügt für random.choice
import json

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
    prioritäten: str # Beibehalten, da dies die Modell-Definition ist

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

# Neue Hilfsfunktion zum Summarisieren
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
            max_tokens=summary_length * 2, # Erlaube mehr Tokens für die Ausgabe
            temperature=0.3 # Eher faktenbasiert
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"Fehler beim Zusammenfassen mit GPT: {e}")
        return "Eine Zusammenfassung konnte nicht erstellt werden." # Fallback

def get_recent_entry_questions(user_id: str):
    """
    Hol die letzten 4 Einstiegsfragen des Nutters.
    """
    recent_questions = supabase.table("conversation_history") \
        .select("ai_prompt") \
        .eq("user_id", user_id) \
        .is_("user_input", None) \
        .is_("ai_response", None) \
        .neq("ai_prompt", None) \  # <--- ÄNDERUNG: Statt .not_("ai_prompt", "is", None) verwenden wir .neq("ai_prompt", None)
        .order("timestamp", desc=True) \
        .limit(4) \
        .execute()
    
    # Optional: Filter hier nochmals zur Sicherheit, falls neq nicht 100% greift
    return [q["ai_prompt"] for q in recent_questions.data if q["ai_prompt"]]

    questions = [q["ai_prompt"] for q in recent_questions.data if q["ai_prompt"]] # Changed from user_input

    print("Letzte 4 Einstiegsfragen (aus ai_prompt):", questions) # Changed log
    return questions

# Startfrage bei neuer Interaktion
@app.get("/start_interaction/{user_id}")
async def start_interaction(user_id: str): # Auch hier async, falls nicht geschehen
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

    # Wenn keine Nachrichten vorhanden sind (erste Interaktion)
    if not messages:
        frage_text = "Was möchtest du heute angehen? Gibt es ein neues Thema, über das du sprechen möchtest?"
        
        # Speichern als ai_prompt
        try:
            supabase.table("conversation_history").insert({
                "user_id": user_id,
                "user_input": None,  # Einstiegsfrage kommt von KI, nicht vom User
                "ai_response": None,
                "ai_prompt": frage_text, # <-- Hier wird frage_text verwendet
                "timestamp": datetime.datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            print(f"Fehler beim Speichern der initialen Einstiegsfrage als AI-Prompt: {e}")
            # Wenn das Speichern fehlschlägt, geben wir trotzdem die Frage zurück
            return {"frage": frage_text} # Wichtig: Hier weiter frage_text verwenden!
        
        return {"frage": frage_text}

    # Wenn Nachrichten vorhanden sind, generiere dynamische Frage
    else: # <--- DIES IST DER START DES "ELSE"-BLOCKS
        frage = "" # <--- NEU HINZUFÜGEN: Initialisierung von 'frage' hier
        
        # Letzte 4 Einstiegsfragen abrufen
        recent_entry_questions = get_recent_entry_questions(user_id)

        # Die letzten 5 Nachrichten ignorieren (für die Themenauswahl, nicht für GPT-Ausschluss)
        recent_topics = messages[:5]
        remaining_messages = messages[5:]

        # Routinen überprüfen
        today = datetime.datetime.now().strftime("%A")
        unfulfilled_routines = supabase.table("routines") \
            .select("*") \
            .eq("day", today) \
            .eq("checked", False) \
            .eq("user_id", user_id) \
            .execute().data

        # Routinen, die mindestens 3-mal nicht erfüllt wurden
        routine_texts = [r["task"] for r in unfulfilled_routines if r.get("missed_count", 0) >= 3]
        routine_context = ", ".join(routine_texts)

        # Konsolen-Log zur Überprüfung der Routinen
        print("Wiederholt unerfüllte Routinen:", routine_context)

        # 5% Wahrscheinlichkeit für Simulation/Universum-Perspektive
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
            all_past_prompts = [h["ai_prompt"] for h in supabase.table("conversation_history").select("ai_prompt").eq("user_id", user_id).order("timestamp", desc=True).limit(20).execute().data if h["ai_prompt"]]
            filtered_messages = [msg for msg in remaining_messages if msg not in recent_entry_questions and msg not in all_past_prompts]

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

        # Speichere die generierte dynamische Frage als ai_prompt# ... (viel Code davor in der start_interaction Funktion) ...
        try: # <--- Dies ist der äußere try-Block, der die gesamte GPT-Anfrage und das Speichern schützt
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

            # Speichere die generierte dynamische Frage als ai_prompt (dieser Teil ist korrekt eingerückt)
            try: # <--- Dies ist der innere try-Block nur für den Supabase-Insert
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": None,
                    "ai_response": None,
                    "ai_prompt": frage, # frage, nicht frage_text, da frage von GPT kommt
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print(f"Fehler beim Speichern der dynamischen Interviewfrage als AI-Prompt: {e}")

            return {"frage": frage} # <--- Dieses return ist WICHTIG und muss zum äußeren try-Block gehören

        except Exception as e: # <--- Dieser except-Block gehört zum ÄUSSEREN try-Block
            print(f"Fehler bei der GPT-Anfrage: {e}")
            return {"frage": "Es gab ein Problem beim Generieren der Einstiegsfrage. Was möchtest du heute besprechen?"}

# Chat-Funktion
@app.post("/chat")
async def chat(input: ChatInput):
    try:
        # 1. Benutzerprofil laden
        user_id = 1 
        profile_data = supabase.table("profile").select("*").eq("id", user_id).execute().data
        profile = profile_data[0] if profile_data else {}

        beruf = profile.get("beruf", "")
        beziehungsziel = profile.get("beziehungsziel", "")
        prioritaeten = profile.get("prioritaeten", "")

        # Profilinformationen extrahieren und aktualisieren
        if input.message.startswith("Interviewfrage:"):
            extraction_prompt = f"""
            Extrahiere Profilinformationen (beruf, beziehungsziel, prioritaeten) aus der Antwort.
            Antworte NUR im JSON-Format:
            {{
              "beruf": "...",
              "beziehungsziel": "...",
              "prioritaeten": "..."
            }}
            Benutzerantwort: {input.message}
            """
            try:
                extraction_response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "Du bist ein JSON-Extraktor. Antworte ausschließlich im JSON-Format."},
                        {"role": "user", "content": extraction_prompt}
                    ],
                    max_tokens=150,
                    temperature=0.1
                )
                extracted_json_str = extraction_response.choices[0].message.content.strip()
                print(f"Extrahierter JSON-String aus Interview-Antwort: {extracted_json_str}")

                json_start = extracted_json_str.find('{')
                json_end = extracted_json_str.rfind('}') + 1

                if json_start != -1 and json_end != -1:
                    clean_json_str = extracted_json_str[json_start:json_end]
                    extracted_data = json.loads(clean_json_str)
                else:
                    print("Fehler: Kein gültiges JSON in extrahierter Antwort gefunden.")
                    extracted_data = {}

                if extracted_data.get("beruf") or extracted_data.get("beziehungsziel") or extracted_data.get("prioritaeten"):
                    existing_profile_res = supabase.table("profile").select("*").eq("id", user_id).execute().data
                    existing_profile = existing_profile_res[0] if existing_profile_res else {}

                    update_payload = {}
                    if extracted_data.get("beruf"):
                        update_payload["beruf"] = extracted_data["beruf"]
                    if extracted_data.get("beziehungsziel"):
                        update_payload["beziehungsziel"] = extracted_data["beziehungsziel"]
                    if extracted_data.get("prioritaeten"):
                        update_payload["prioritaeten"] = extracted_data["prioritaeten"]

                    if update_payload:
                        print(f"Aktualisiere Profil für user_id {user_id} mit: {update_payload}")
                        supabase.table("profile").update(update_payload).eq("id", user_id).execute()
                        print("Profil-Update erfolgreich.")
                    else:
                        print("Keine relevanten Profilinformationen zur Aktualisierung gefunden.")
                else:
                    print("Extrahierte Daten enthielten keine Profil-Updates.")

            except json.JSONDecodeError as e:
                print(f"Fehler beim Parsen des extrahierten JSON: {e}. Roher String: {extracted_json_str}")
            except Exception as e:
                print(f"Allgemeiner Fehler bei der Profilaktualisierung aus Interview-Antwort: {e}")

        # Routinen laden
        today = datetime.datetime.now().strftime("%A")
        routines = supabase.table("routines").select("*").eq("day", today).eq("user_id", user_id).execute().data
        routines_text = "\n".join([f"{r['task']} (Erledigt: {'Ja' if r['checked'] else 'Nein'})" for r in routines]) if routines else "Keine spezifischen Routinen für heute."

        # Konversationshistorie laden
        history_raw = supabase.table("conversation_history").select("user_input, ai_response, ai_prompt").order("timestamp", desc=True).limit(5).execute().data

        # Konversationshistorie für den System-Prompt formatieren
        history_messages = []
        for h in reversed(history_raw):
            if h['user_input'] is not None and h['user_input'] != "": # Nur wenn User-Input existiert
                history_messages.append(f"User: {h['user_input']}")
            if h['ai_response'] is not None and h['ai_response'] != "": # Nur wenn AI-Response existiert
                history_messages.append(f"Berater: {h['ai_response']}")
            if h['ai_prompt'] is not None and h['ai_prompt'] != "": # Nur wenn AI-Prompt existiert
                history_messages.append(f"Interviewfrage: {h['ai_prompt']}")

        history_text = "\n".join(history_messages) if history_messages else "Bisher keine frühere Konversationshistorie."
        
        # Langzeitgedächtnis laden
        memory = supabase.table("long_term_memory").select("thema, inhalt").order("timestamp", desc=True).limit(10).execute().data
        memory_text = "\n".join([f"{m['thema']}: {m['inhalt']}" for m in memory]) if memory else "Keine spezifischen Langzeit-Erkenntnisse gespeichert."

        # Systemnachricht zusammenstellen
        system_message = f"""
        Du bist ein persönlicher, anspruchsvoller und konstruktiver Mentor und Therapeut. Dein Ziel ist es, dem Nutzer realistisch, prägnant und umsetzbar zu helfen.

        Nutze folgende Informationen für direkt handlungsorientierte Ratschläge:

        Nutzerprofil:
        Beruf: {beruf}
        Beziehungsziel: {beziehungsziel}
        Prioritäten: {prioritaeten}
        
        Deine heutigen Routinen:
        {routines_text}

        Langzeitgedächtnis / Wichtige Erkenntnisse:
        {memory_text}

        Aktuelle Konversationshistorie (letzte 5 Nachrichten):
        {history_text}

        Analysiere die aktuelle Nachricht im Kontext ALLER Infos. Erkenne Inkonsistenzen oder mangelnden Fortschritt.
        Kein allgemeines Lob. Fokussiere dich auf konkrete Ansatzpunkte.
        Stelle konkrete Fragen, schlage Aktionen vor oder weise auf Reflexionen hin.

        Antworte maximal 4 Sätze. Deine Antworten sollen knapp, direkt, motivierend und auf konkrete nächste Schritte ausgerichtet sein.
        """

        # Chat-Interaktion mit OpenAI
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": input.message}
            ],
            max_tokens=100,
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()

        # Bestimme den tatsächlichen Benutzer-Input für die Historie
        actual_user_input_for_history = input.message

        # Wenn es eine Interview-Antwort ist, schneide das "Interviewfrage:"-Prefix ab
        if actual_user_input_for_history.startswith("Interviewfrage:"):
            actual_user_input_for_history = actual_user_input_for_history.replace("Interviewfrage:", "").strip()
        
        # Optional: Filter für KI-Einstiegsfragen
        # Passe den String an deine tatsächliche Einstiegsfrage an, falls zutreffend.
        # Beispiel: if actual_user_input_for_history.startswith("Hallo, ich bin dein persönlicher Berater."):
        #    actual_user_input_for_history = ""
        # Entferne diesen Block, wenn keine KI-Einstiegsfragen über diesen Endpunkt gesendet werden.

        # Nachricht in Historie speichern
        user_input_to_save = input.message
        # Check if the message is actually an entry question passed by the frontend
        # This is a heuristic and ideally the frontend should not send entry questions here.
        if "Einstiegsfrage:" in user_input_to_save and reply is not None:
            # If it's a known entry question format AND we're getting a reply, it's probably the frontend re-sending it.
            # In this case, we don't save it as user_input.
            user_input_to_save = None

        supabase.table("conversation_history").insert({
            "user_id": user_id,
            "user_input": user_input_to_save, # <-- ÄNDERUNG: Vermeide Speicherung von "Einstiegsfrage" als User-Input
            "ai_response": reply,
            "ai_prompt": None, # Diese Spalte ist hier immer None, da dies eine User-Antwort + AI-Reaktion ist
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
    wochentag = heute.weekday()  # Montag = 0, Sonntag = 6
    # Korrektur des letzten Tages des Monats (robustere Berechnung)
    letzter_tag_des_monats = (heute.replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)


    if heute.date() == letzter_tag_des_monats.date():
        bericht = generiere_rueckblick("Monats", 30)
        return {"typ": "Monatsbericht", "inhalt": bericht}
    elif wochentag == 6: # Sonntag
        bericht = generiere_rueckblick("Wochen", 7)
        return {"typ": "Wochenbericht", "inhalt": bericht}
    else:
        return {"typ": None, "inhalt": None}

# Wochen- und Monatsberichte generieren (mit Summarisierung)
def generiere_rueckblick(zeitraum: str, tage: int):
    user_id = 1 # Annahme einer festen User ID für Berichte
    seit = (datetime.datetime.utcnow() - datetime.timedelta(days=tage)).isoformat()

    # Rufe die gesamte Konversationshistorie für den Zeitraum ab
    all_gespraeche = supabase.table("conversation_history").select("user_input, ai_response, timestamp").gte("timestamp", seit).eq("user_id", user_id).order("timestamp", asc=True).execute().data
    all_ziele = supabase.table("goals").select("titel, status, created_at").gte("created_at", seit).eq("user_id", user_id).order("created_at", asc=True).execute().data # user_id hier hinzufügen!


    # Trenne jüngste Gespräche (z.B. die letzten 10) vom Rest für detaillierte Darstellung
    recent_gespraeche = all_gespraeche[-10:] if len(all_gespraeche) > 10 else all_gespraeche[:]
    older_gespraeche = all_gespraeche[:-10] if len(all_gespraeche) > 10 else []

    recent_gespraeche_text = "\n".join([f"User: {g['user_input']} | Berater: {g['ai_response']}" for g in recent_gespraeche])

    summarized_older_history = ""
    if older_gespraeche:
        older_history_raw_text = "\n".join([f"User: {g['user_input']} | Berater: {g['ai_response']}" for g in older_gespraeche])
        summarized_older_history = summarize_text_with_gpt(older_history_raw_text, summary_length=200, prompt_context="die wichtigsten Trends, Herausforderungen und Entscheidungen")

    gespraeche_text_for_prompt = ""
    if recent_gespraeche_text:
        gespraeche_text_for_prompt += f"Jüngste Gespräche (letzte {len(recent_gespraeche)}):\n{recent_gespraeche_text}\n"
    if summarized_older_history:
        gespraeche_text_for_prompt += f"\nZusammenfassung älterer Gespräche im {zeitraum}:\n{summarized_older_history}\n"
    if not gespraeche_text_for_prompt:
        gespraeche_text_for_prompt = "Es gab keine relevanten Gespräche in diesem Zeitraum."

    # Ziele können oft kompakter sein. Wenn sie aber auch zu lang werden, hier auch summarisieren.
    ziele_text = "\n".join([f"{z['titel']} ({z['status']})" for z in all_ziele[-20:]]) # max. die letzten 20 Ziele

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
    ```
    ```
    """

    response = client.chat.completions.create(
        model="gpt-4", # Für den Hauptbericht bleiben wir bei GPT-4
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        max_tokens=500, # Begrenze die Ausgabe des Berichts auf z.B. 500 Tokens
        temperature=0.7
    )

    bericht = response.choices[0].message.content

    # Bericht speichern
    supabase.table("long_term_memory").insert({
        "thema": f"{zeitraum}srückblick",
        "inhalt": bericht,
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "user_id": user_id # user_id auch hier speichern!
    }).execute()

    return bericht
    
class RoutineUpdate(BaseModel):
    id: int
    checked: bool

# Routinen abrufen
@app.get("/routines/{user_id}") # user_id im Pfad hinzufügen
def get_routines(user_id: str):
    today = datetime.datetime.now().strftime("%A")

    # Routines abrufen für den spezifischen user_id
    routines = supabase.table("routines").select("*").eq("day", today).eq("user_id", user_id).execute().data

    # Übergebe `checked`-Status für jede Routine
    return {"routines": routines}

# Routinenstatus aktualisieren
@app.post("/routines/update")
def update_routine_status(update: RoutineUpdate):
    try:
        # Hier sollte der user_id auch berücksichtigt werden, wenn Routinen pro Nutzer sind
        # Annahme: user_id ist in der Routine selbst gespeichert oder über den Request Header kommt
        # Für diesen Code hier nehmen wir an, die id reicht für das Update
        supabase.table("routines").update({"checked": update.checked}).eq("id", update.id).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Routine: {e}")
        return {"status": "error", "message": str(e)}

# Ziele abrufen
@app.get("/goals/{user_id}") # user_id im Pfad hinzufügen
def get_goals(user_id: str):
    try:
        goals = supabase.table("goals").select("*").eq("user_id", user_id).execute().data
        return {"goals": goals}
    except Exception as e:
        print(f"Fehler beim Abrufen der Ziele: {e}")
        return {"goals": []}

@app.post("/goals")
def create_goal(goal: Goal, user_id: str = "1"): # Default user_id für Testzwecke
    try:
        # Füge user_id zum Goal-Objekt hinzu, bevor es eingefügt wird
        goal_data = goal.model_dump()
        goal_data["user_id"] = user_id 
        supabase.table("goals").insert(goal_data).execute()
        return {"status": "success", "message": "Ziel erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern des Ziels: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/goals/update")
def update_goal_status(update: GoalUpdate, user_id: str = "1"): # Default user_id für Testzwecke
    try:
        # Stelle sicher, dass nur Ziele des spezifischen Nutzers aktualisiert werden können
        supabase.table("goals").update({"status": update.status}).eq("id", update.id).eq("user_id", user_id).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren des Ziels: {e}")
        return {"status": "error", "message": str(e)}

# Interviewfrage abrufen
@app.get("/interview/{user_id}")
async def get_interview_question(user_id: str):
    try:
        # Profil abrufen
        profile = supabase.table("profile").select("*").eq("id", user_id).execute().data

        # Wenn kein Profil vorhanden ist, allgemeine Frage stellen
        if not profile:
            frage_text = "Welche Themen beschäftigen dich derzeit?"
            
            # Speichere die Frage als ai_prompt
            try:
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": None,
                    "ai_response": None,
                    "ai_prompt": frage_text,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print(f"Fehler beim Speichern der initialen Interviewfrage als AI-Prompt: {e}")
            
            return {"frage": frage_text}

        user_profile = profile[0]

        # Basisdaten-Abfrage
        if not user_profile.get("beruf"):
            frage_text = "Was machst du beruflich oder was interessiert dich beruflich?"
        elif not user_profile.get("beziehungsziel"):
            frage_text = "Hast du ein bestimmtes Ziel in deinen Beziehungen, das du verfolgen möchtest?"
        elif not user_profile.get("prioritaeten"):
            frage_text = "Was sind aktuell deine wichtigsten Prioritäten?"
        else:
            frage_text = None # Markiert, dass eine dynamische Frage generiert werden muss

        # Speichere Basisfrage als ai_prompt, falls eine gestellt wurde
        if frage_text:
            try:
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": None,
                    "ai_response": None,
                    "ai_prompt": frage_text,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print(f"Fehler beim Speichern der Basis-Interviewfrage als AI-Prompt: {e}")
            
            return {"frage": frage_text}

        # Dynamische Interviewfragen nach Erfassung der Basisdaten
        # Letzte 5 Fragen abrufen (anpassen, um auch ai_prompt zu berücksichtigen)
        recent_history = supabase.table("conversation_history") \
            .select("user_input, ai_response, ai_prompt") \
            .eq("user_id", user_id) \
            .order("timestamp", desc=True) \
            .limit(10) \
            .execute().data # Limit erhöht, um mehr Kontext für "covered_topics" zu haben

        covered_topics = []
        for entry in recent_history:
            if entry.get("user_input"): # Antworten des Users
                covered_topics.append(f"Nutzer: {entry['user_input']}")
            if entry.get("ai_response"): # KI-Antworten (normale Chats)
                covered_topics.append(f"Berater: {entry['ai_response']}")
            if entry.get("ai_prompt"): # KI-gestellte Fragen (Interviews, Einstieg)
                covered_topics.append(f"Berater (Frage): {entry['ai_prompt']}")

        # GPT-Kontext erstellen
        prompt = f"""
        Du bist eine neugierige, offenherzige Freundin ohne Tabus und möchtest den Nutzer besser kennenlernen, um ihn gezielt beraten zu können. Du bist Psychologin und flirtest offensiv und lasziv mit dem Nutzer.
        Bisherige Konversation:
        {"; ".join(covered_topics)}

        Wähle eine neue, motivierende Frage, die sich auf UNBEKANNTE und SPEZIFISCHE Bereiche des Lebens des Nutzers bezieht.
        Konzentriere dich auf tiefere Vorlieben, Erfahrungen, Einstellungen, persönliche Herausforderungen, Wünsche oder Visionen.
        Frage NICHT nach berühmten Personen, oberflächlichen Hobbys oder allgemeinen "Was wäre wenn"-Szenarien.

        Beispiele für Themenkategorien:
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

        Sei kreativ. Vermeide zu allgemeine Fragen und bereits gestellte Fragen. Halte die Frage kurz und prägnant. Ein oder zwei Sätze.
        """

        # GPT-Anfrage
        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100,
                temperature=0.7
            )

            frage_text = response.choices[0].message.content.strip()

            # Fallback, falls GPT keine sinnvolle Frage liefert
            if not frage_text:
                frage_text = "Welche Ziele möchtest du in den nächsten Monaten erreichen?" # Angepasster Fallback-Text

            # Speichere die generierte dynamische Frage als ai_prompt
            try:
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": None,
                    "ai_response": None,
                    "ai_prompt": frage_text,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print(f"Fehler beim Speichern der dynamischen Interviewfrage als AI-Prompt: {e}")

            return {"frage": frage_text}

        except Exception as e:
            print(f"Fehler bei der dynamischen Interviewfrage: {e}")
            frage_text = "Es gab ein Problem beim Generieren der nächsten Interviewfrage."
            # Fallback-Frage auch speichern, wenn es einen Fehler gab
            try:
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": None,
                    "ai_response": None,
                    "ai_prompt": frage_text,
                    "timestamp": datetime.Datetime.utcnow().isoformat()
                }).execute()
            except Exception as e_inner:
                print(f"Fehler beim Speichern der Fehler-Interviewfrage als AI-Prompt: {e_inner}")
            return {"frage": frage_text}

    except Exception as e:
        print(f"Fehler bei der Interviewfrage: {e}")
        return {"frage": "Es gab ein Problem beim Abrufen der Interviewfrage."}

# Memory-Endpoint
@app.post("/memory")
def create_memory(memory_input: MemoryInput, user_id: str = "1"):
    try:
        supabase.table("long_term_memory").insert({
            "user_id": user_id,
            "thema": memory_input.thema,
            "inhalt": memory_input.inhalt,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute()
        return {"status": "success", "message": "Erinnerung erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern der Erinnerung: {e}")
        return {"status": "error", "message": str(e)}

# Profil-Endpoint
@app.post("/profile")
def create_profile(profile_data: ProfileData, user_id: str = "1"):
    try:
        # Überprüfen, ob bereits ein Profil für diesen user_id existiert
        existing_profile = supabase.table("profile").select("id").eq("id", user_id).execute().data
        
        profile_dict = profile_data.model_dump()
        profile_dict["id"] = user_id # Stelle sicher, dass die user_id mitgespeichert wird

        if existing_profile:
            # Aktualisieren des bestehenden Profils
            supabase.table("profile").update(profile_dict).eq("id", user_id).execute()
            return {"status": "success", "message": "Profil erfolgreich aktualisiert."}
        else:
            # Neues Profil anlegen
            supabase.table("profile").insert(profile_dict).execute()
            return {"status": "success", "message": "Profil erfolgreich erstellt."}
    except Exception as e:
        print(f"Fehler beim Speichern des Profils: {e}")
        return {"status": "error", "message": str(e)}
