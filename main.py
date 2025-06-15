from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
from typing import Optional
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

async def _save_conversation_entry(user_id: str, user_input: Optional[str], ai_response: Optional[str], ai_prompt: Optional[str]):
    """Speichert einen neuen Eintrag in der Konversationshistorie."""
    try:
        supabase.table("conversation_history").insert({
            "user_id": user_id,
            "user_input": user_input,
            "ai_response": ai_response,
            "ai_prompt": ai_prompt,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }).execute()
    except Exception as e:
        print(f"Fehler beim Speichern der Konversationshistorie: {e}")

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

# Zusammenfassung um Token zu sparen (mit gpt-3.5-turbo)
def summarize_text_with_gpt(text_to_summarize: str, summary_length: int = 200, prompt_context: str = "wichtige Punkte und Muster"):
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

#Abrufen der letzten 8 unbeantworteten Einstiegs- und Interviewfragen
def get_recent_entry_questions(user_id: str):
    recent_prompts = supabase.table("conversation_history") \
        .select("ai_prompt") \
        .eq("user_id", user_id) \
        .neq("ai_prompt", None) \
        .order("timestamp", desc=True) \
        .limit(8) \
        .execute()
    
    questions = [q["ai_prompt"] for q in recent_prompts.data if q["ai_prompt"]]
    print("Letzte 8 Einstiegsfragen (aus ai_prompt):", questions)
    return questions

# Einstiegsfrage bei neuer Interaktion
@app.get("/start_interaction/{user_id}")
async def start_interaction(user_id: str):
    # Letzte 30 Nachrichten abrufen
    recent_interactions_data = supabase.table("conversation_history") \
        .select("user_input, ai_response") \
        .eq("user_id", user_id) \
        .order("timestamp", desc=True) \
        .limit(30) \
        .execute().data
    
    messages = [] # <--- Initialisierung der messages Liste
    # Nachrichten extrahieren
    for msg_entry in recent_interactions_data:
        user_msg = msg_entry.get("user_input")
        ai_msg = msg_entry.get("ai_response")
        ai_prompt_msg = msg_entry.get("ai_prompt")

        # Nur hinzufügen, wenn BEIDES (User-Input UND AI-Response) vorhanden und nicht leer ist
        if (user_msg is not None and user_msg.strip() != "" and user_msg != "Starte ein Gespräch") and \
           (ai_msg is not None and ai_msg.strip() != ""):
            messages.append(f"User: {user_msg}")
            messages.append(f"AI: {ai_msg}")

        # Hinzufügen von ai_prompts zur Historie (damit sie bei Vermeidung berücksichtigt werden)
        if ai_prompt_msg is not None and ai_prompt_msg.strip() != "": # <--- DIESER BLOCK IST NEU
            messages.append(f"Berater (Frage): {ai_prompt_msg}")

    # Konsolen-Log zur Überprüfung der Nachrichten
    print("Letzte 30 Nachrichten:", messages)

    # Wenn keine Nachrichten vorhanden sind (erste Interaktion)
    if not messages:
        frage_text = "Was möchtest du heute angehen? Gibt es ein neues Thema, über das du sprechen möchtest?"
        
        # Speichern als ai_prompt
        try:
            supabase.table("conversation_history").insert({
                "user_id": user_id,
                "user_input": "",
                "ai_response": "",
                "ai_prompt": frage_text,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }).execute()
        except Exception as e:
            print(f"Fehler beim Speichern der initialen Einstiegsfrage als AI-Prompt: {e}")
            return {"frage": frage_text}
        
        return {"frage": frage_text}

    else:
        frage = ""
        
        # Letzte 8 Einstiegsfragen abrufen
        recent_ai_prompts_to_avoid_raw = get_recent_entry_questions(user_id)
        recent_ai_prompts_to_avoid = [
            str(p) for p in recent_ai_prompts_to_avoid_raw
            if p is not None and str(p).strip() != ""
        ]
        
        # Letzte 10 Monatsrückblicke und letzte 4 Wochenrückblicke
        monthly_reports = supabase.table("long_term_memory") \
            .select("thema", "inhalt") \
            .eq("user_id", user_id) \
            .eq("thema", "Monatsrückblick") \
            .order("timestamp", desc=True) \
            .limit(10) \
            .execute().data
        
        weekly_reports = supabase.table("long_term_memory") \
            .select("thema", "inhalt") \
            .eq("user_id", user_id) \
            .eq("thema", "Wochenrückblick") \
            .order("timestamp", desc=True) \
            .limit(4) \
            .execute().data
        
        # Kombiniere die Berichte für den Kontext
        all_recent_reports = monthly_reports + weekly_reports
        
        reports_context = "\n".join([
            f"{str(r.get('thema', ''))}: {str(r.get('inhalt', ''))}" 
            for r in all_recent_reports
        ])

        if not reports_context.strip():
            reports_context = "Bisher keine Berichte verfügbar."
            
        # Laden der Ziele aus der 'goals'-Tabelle
        user_goals = supabase.table("goals") \
            .select("goal_description", "status") \
            .eq("user_id", user_id) \
            .limit(5) \
            .execute().data            
 
        goals_context = ""
        if user_goals:
            goals_context = "\nAktuelle Ziele:\n" + "\n".join([
                f"- {str(g.get('goal_description', ''))} (Status: {str(g.get('status', ''))})" 
                for g in user_goals
            ])
        else:
            goals_context = "\nBisher keine Ziele erfasst."

        # Laden aller Routinen aus der 'routines'-Tabelle für eine Gesamtübersicht
        all_user_routines = supabase.table("routines") \
            .select("task", "day", "checked", "missed_count") \
            .eq("user_id", user_id) \
            .limit(10) \
            .execute().data
            
        routines_overview_context = ""
        if all_user_routines:
            routines_overview_context = "\nÜbersicht aller Routinen:\n" + "\n".join([
                f"- {str(r.get('task', ''))} ({str(r.get('day', ''))}, Verpasst: {str(r.get('missed_count', 0))})" 
                for r in all_user_routines
            ])
        else:
            routines_overview_context = "\nBisher keine Routinen erfasst."
            
        # Routinen überprüfen
        today = datetime.datetime.now().strftime("%A")
        unfulfilled_routines = supabase.table("routines") \
            .select("task", "missed_count") \
            .eq("day", today) \
            .eq("checked", False) \
            .eq("user_id", user_id) \
            .execute().data

        # Routinen, die mindestens 3-mal nicht erfüllt wurden
        routine_texts = [
            str(r.get("task", '')) for r in unfulfilled_routines 
            if r.get("missed_count", 0) >= 3 and r.get("task") is not None
        ]
        routine_context_today = ", ".join(routine_texts)

        print("Wiederholt unerfüllte Routinen:", routine_context_today)

        # 5% Wahrscheinlichkeit für Simulation/Universum-Perspektive
        simulate_universe = random.random() < 0.05

        # GPT-Anfrage vorbereiten
        if simulate_universe:
            prompt = f"""
            Du bist hypothetisch die Simulation oder das Universum und möchtest dem Nutzer heute einen konkreten Hinweis geben. 
            Tue so, als ob du tatsächlich Kontakt zum Universum oder zur Simulation hättest und etwas Wichtiges über seinen heutigen Tag weißt. 
            Vermeide die letzten acht Einstiegsfragen:

            {", ".join(recent_ai_prompts_to_avoid)}

            Sei sehr konkret und weise auf eine bestimmte Aktion, Einstellung oder ein Ereignis hin. Bleibe dabei einfühlsam und motivierend.
            """
        else:
            context_for_gpt = "\nUser-Historie (letzte 30 Nachrichten):\n" + "\n".join(messages)
            if recent_ai_prompts_to_avoid:
                context_for_gpt += "\nKürzlich gestellte Fragen des Beraters:\n" + ", ".join(recent_ai_prompts_to_avoid)
                
            context_for_gpt += "\nAktuelle Berichte:\n" + reports_context
            context_for_gpt += goals_context            
            context_for_gpt += routines_overview_context    
            context_for_gpt += routine_context_today    

            fallback_topics = ["Langfristige Ziele", "Bestehende Routinen", "Neue Routinen", "Selbstreflexion", "Freizeitgestaltung",
                               "Umgang mit Herausforderungen", "Lernprozesse", "Beziehungen pflegen",
                               "Umgang mit Energie und Erholung", "Persönliche Werte", "Zukunftsvisionen",
                               "Umgang mit Ängsten oder Sorgen", "Erfolge feiern"]
            topic_suggestions = ", ".join(fallback_topics)

            prompt = f"""
            Du bist eine offene Freundin, die ein Gespräch mit mir starten will. Formuliere EINE einzige, konkrete, lockere und personalisierte Frage, entweder basierend auf dem bereitgestellten Kontext (Historie, Berichte, Ziele, Routinen) oder zu einem ganz neuen Thema, das noch nie besprochen wurde.
            Frage z.B. zu Meinungen und Interessen aus allen möglichen Bereichen, je präziser desto besser. Du willst mehr über den Benutzer erfahren, aber ihn nicht durch zu komplizierte Fragen überfordern.
            **Vermeide zusammengesetzte Fragen oder Fragen, die mit 'und' verbunden sind.**
            **Halte die Frage sehr kurz, idealerweise in einem Satz.**
            Vermeide die letzten vier Einstiegsfragen:

            {", ".join(recent_ai_prompts_to_avoid)}
            
            **Beispiel für motivierende, spezifische Fragen (im Stil deiner Rolle):**
            - Aus welchem Themenbereich soll das nächste Buch sein, das Du liest?
            - Was macht Dir morgens schlechte Laune?
            - Wo machst Du gerne Urlaub?
            - Was würdest Du mit einem Lottogewinn machen?

            Mögliche Themenbereiche:
 
            {"; ".join(topic_suggestions)}
    
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

            Nutze den oben bereitgestellten Kontext (Historie, Berichte, Ziele, Routinen) für die Personalisierung der Frage.
            """
            
        print("GPT Prompt:", prompt)

        try:
            response = client.chat.completions.create(
                model="gpt-4",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=60,
                temperature=0.7
            )

            frage = response.choices[0].message.content.strip()

            if not frage.strip():
                frage = "Was möchtest du heute erreichen oder klären?"

            try:
                supabase.table("conversation_history").insert({
                    "user_id": user_id,
                    "user_input": "",
                    "ai_response": "",
                    "ai_prompt": frage,
                    "timestamp": datetime.datetime.utcnow().isoformat()
                }).execute()
            except Exception as e:
                print(f"Fehler beim Speichern der dynamischen Interviewfrage als AI-Prompt: {e}")

            return {"frage": frage}

        except Exception as e:
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
        if input.message and input.message.strip():
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
                print(f"Extrahierter JSON-String aus Nachricht: {extracted_json_str}")

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
                print(f"Allgemeiner Fehler bei der Profilaktualisierung aus Nachricht: {e}")

        # Routinen laden
        today = datetime.datetime.now().strftime("%A")
        routines = supabase.table("routines").select("*").eq("day", today).eq("user_id", user_id).execute().data
        routines_text = "\n".join([f"{r['task']} (Erledigt: {'Ja' if r['checked'] else 'Nein'})" for r in routines]) if routines else "Keine spezifischen Routinen für heute."

        # Konversationshistorie laden
        history_raw = supabase.table("conversation_history").select("user_input, ai_response, ai_prompt").order("timestamp", desc=True).limit(5).execute().data

        # Konversationshistorie für den System-Prompt formatieren
        history_messages = []
        for h in reversed(history_raw):
            if h.get('user_input'):
                history_messages.append(f"User: {h['user_input']}")
            if h.get('ai_response'):
                history_messages.append(f"Berater: {h['ai_response']}")
            if h.get('ai_prompt'):
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
        Achte auf realistische Vorschläge, da der Nutzer schon feste Routinen, eine Arbeit und Frau hat.

        Antworte maximal 3 Sätze. Deine Antworten sollen knapp, direkt, motivierend und auf konkrete nächste Schritte ausgerichtet sein.
        """

        # Chat-Interaktion mit OpenAI
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": input.message}
            ],
            max_tokens=60,
            temperature=0.7
        )
        reply = response.choices[0].message.content.strip()

        # Nachricht in Historie speichern
        await _save_conversation_entry(user_id, input.message, reply, None)
        return {"reply": reply}

    except Exception as e:
        print(f"Fehler in der Chat-Funktion: {e}")
        return {"reply": "Entschuldige, es gab ein Problem beim Verarbeiten deiner Anfrage. Bitte versuche es später noch einmal."}
        
# Automatischer Wochen- und Monatsbericht
@app.get("/bericht/automatisch")
def automatischer_bericht():
    # Annahme einer festen User ID für Berichte, wie in generiere_rueckblick
    user_id = 1 

    # Alle Zeitberechnungen basieren jetzt konsistent auf UTC
    heute_utc = datetime.datetime.utcnow() 
    wochentag_utc = heute_utc.weekday() # Montag = 0, Sonntag = 6 (UTC-basiert)
    
    # Debug-Ausgaben für den Start
    print(f"\n--- Start 'automatischer_bericht' ---")
    print(f"Aktuelle Server-UTC-Zeit: {heute_utc.isoformat()}")
    print(f"Aktueller Wochentag (0=Mo, 6=So) in UTC: {wochentag_utc}")

    bericht_typ = None
    bericht_inhalt = None

    # Monatsbericht prüfen und ggf. generieren
    # Prüfe, ob es der letzte Tag des Monats ist (konsistent in UTC)
    if heute_utc.day == (heute_utc.replace(day=1) + datetime.timedelta(days=32)).replace(day=1).day - 1:
        bericht_typ = "Monatsrückblick"
        # Start des aktuellen Monats (UTC)
        start_of_month_utc = heute_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Ende des aktuellen Monats (UTC) - 1 Mikrosekunde vor dem nächsten Monat
        end_of_month_utc = (start_of_month_utc.replace(month=start_of_month_utc.month % 12 + 1, day=1) - datetime.timedelta(microseconds=1))
        print(f"Monatsbericht-Check - Abfragebereich: {start_of_month_utc.isoformat()} bis {end_of_month_utc.isoformat()}")
  
        existing_report_response = supabase.table("long_term_memory") \
            .select("id, thema, timestamp") \
            .eq("user_id", user_id) \
            .eq("thema", bericht_typ) \
            .gte("timestamp", start_of_month_utc.isoformat() + 'Z') \
            .lt("timestamp", end_of_month_utc.isoformat() + 'Z') \
            .execute()
        
        # ! WICHTIG: `.data` auf das response-Objekt zugreifen, um die Liste der gefundenen Einträge zu erhalten
        existing_report_data = existing_report_response.data
        
        print(f"Monatsbericht-Check - Supabase Roh-Response: {existing_report_response}")
        print(f"Monatsbericht-Check - Gefundene Berichte: {existing_report_data}")
        
        # NEU: Bedingte Generierung nur, wenn kein existierender Bericht gefunden wurde
        if not existing_report_data: # Prüfung auf leere Liste ist korrekt
            print(f"Generiere neuen {bericht_typ} für User {user_id}...")
            bericht_inhalt = generiere_rueckblick("Monats", 30)
            # generiere_rueckblick speichert den Bericht bereits, daher hier keine weitere Speicherung
        else:
            print(f"{bericht_typ} für User {user_id} wurde heute bereits generiert. Überspringe Generierung.")

    # Wochenbericht prüfen und ggf. generieren
    elif wochentag_utc == 6: # Sonntag (im deutschen Kontext, basierend auf UTC)
        bericht_typ = "Wochenrückblick"
        today_start_utc = heute_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start_utc = today_start_utc + datetime.timedelta(days=1)
        print(f"Wochenbericht-Check - Abfragebereich: {today_start_utc.isoformat()} bis {tomorrow_start_utc.isoformat()}")

        existing_report_response = supabase.table("long_term_memory") \
            .select("id, thema, timestamp") \
            .eq("user_id", user_id) \
            .eq("thema", bericht_typ) \
            .gte("timestamp", today_start_utc.isoformat() + 'Z') \
            .lt("timestamp", tomorrow_start_utc.isoformat() + 'Z') \
            .execute()
        
        # ! WICHTIG: `.data` auf das response-Objekt zugreifen, um die Liste der gefundenen Einträge zu erhalten
        existing_report_data = existing_report_response.data
        
        print(f"Wochenbericht-Check - Supabase Roh-Response: {existing_report_response}")
        print(f"Wochenbericht-Check - Gefundene Berichte: {existing_report_data}")

        # NEU: Bedingte Generierung nur, wenn kein existierender Bericht gefunden wurde
        if not existing_report_data: # Prüfung auf leere Liste ist korrekt
            print(f"Generiere neuen {bericht_typ} für User {user_id}...")
            bericht_inhalt = generiere_rueckblick("Wochen", 7)
            # generiere_rueckblick speichert den Bericht bereits, daher hier keine weitere Speicherung
        # NEU: Nachricht, wenn Bericht bereits existiert
        else:
            print(f"{bericht_typ} für User {user_id} wurde heute bereits generiert. Überspringe Generierung.")
    print(f"--- Ende 'automatischer_bericht' ---\n")
    return {"typ": bericht_typ, "inhalt": bericht_inhalt}

# Wochen- und Monatsberichte generieren (mit Summarisierung)
def generiere_rueckblick(zeitraum: str, tage: int):
    user_id = 1 # Annahme einer festen User ID für Berichte
    seit = (datetime.datetime.utcnow() - datetime.timedelta(days=tage)).isoformat() + 'Z'

    # Rufe die gesamte Konversationshistorie für den Zeitraum ab
    all_gespraeche = supabase.table("conversation_history").select("user_input, ai_response, timestamp").gte("timestamp", seit).eq("user_id", user_id).order("timestamp", desc=False).execute().data
    all_ziele = supabase.table("goals").select("titel, status, created_at").gte("created_at", seit).eq("user_id", user_id).order("created_at", desc=False).execute().data


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
        "timestamp": datetime.datetime.utcnow().isoformat() + 'Z',
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
