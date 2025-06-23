from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import HTTPException
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
from typing import Optional, Dict, Any, List
import os
import datetime
import random
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
            "timestamp": datetime.datetime.utcnow().isoformat() + 'Z'
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

class RoutineUpdate(BaseModel):
    id: str
    checked: bool
    user_id: str

class ProfileData(BaseModel):
    # Fügen Sie hier die Attribute hinzu, die Sie im Benutzerprofil speichern möchten
    # und die von der Funktion extrahiert werden (z.B. durch InterviewAntwort)
    hobby: Optional[str] = None
    beruf: Optional[str] = None
    interessen: Optional[str] = None
    
# Funktion zur Extraktion und Speicherung von erweiterten Profildetails im EAV-Modell
async def extrahiere_und_speichere_profil_details(user_id: str, user_input: str, ai_response: str, ai_prompt: str):
    print(f"Starte dynamische Profil-Extraktion und Speicherung für User {user_id}...")
    print(f"Analysiere aktuelle Nachricht: '{user_input}'")
    
    # Bestehendes dynamisches Profil aus der 'profile'-Tabelle abrufen
    # Die 'profile'-Tabelle ist jetzt unsere EAV-Tabelle
    current_dynamic_profile_response = supabase.table("profile") \
        .select("attribute_name, attribute_value") \
        .eq("user_id", user_id) \
        .execute().data
    
    existing_dynamic_profile: Dict[str, str] = {
        item["attribute_name"]: item["attribute_value"] 
        for item in current_dynamic_profile_response
    }
    
    system_prompt = f"""
    Du bist ein spezialisierter Assistent, der wichtige persönliche Informationen und Vorlieben des Benutzers aus Gesprächen extrahiert.
    Deine Aufgabe ist es, ein detailliertes Langzeitprofil des Benutzers aufzubauen und kontinuierlich zu erweitern.
    
    WICHTIG: Du sollst AKTIV nach NEUEN Informationen suchen und NEUE Kategorien erstellen!
    AUSGABEFORMAT - SEHR WICHTIG:
    - Verwende IMMER reine Strings als Werte, NIEMALS Arrays
    - Beispiel: "Hobbys": "Laufen, Kochen, Lesen" (NICHT ["Laufen", "Kochen"])
    - Alle Werte als Text formatieren
    
    REGELN:
    1. Analysiere JEDEN Gesprächsteil nach neuen, relevanten Informationen
    2. Erstelle NEUE Kategorien für jede neue Information (z.B. "Lieblingsspeise", "Musik", "Reisepläne_August")
    3. Behalte bestehende Kategorien bei, wenn sie weiterhin relevant sind
    4. Aktualisiere bestehende Werte bei neuen/anderen Informationen
    5. Ignoriere nur wirklich temporäre Dinge (wie "heute bin ich müde")
    
    NEUE KATEGORIEN-BEISPIELE:
    - Lieblingsspeise, Kochvorlieben, Musik, Hobbys, Reisepläne_spezifisch
    - Arbeitsbereiche, Sprachen, Filme/Serien, Sport, Wohnsituation
    - Haustiere, Freunde, Familie, Gesundheit, Finanzen, etc.
    
    Du erhältst eine aktuelle Gesprächshistorie und bereits bekannte Profilinformationen.
    Gib das VOLLSTÄNDIGE, ERWEITERTE JSON-Objekt zurück - mit allen alten UND neuen Kategorien.
    
    Antworte NUR mit dem JSON-Objekt.
    """

    user_prompt = f"""
    Kontext der aktuellen Unterhaltung:
    AI-Einstiegsfrage: "{ai_prompt}"
    User-Eingabe: "{user_input}"
    AI-Antwort: "{ai_response}"
    
    Bereits bekanntes Profil: {json.dumps(existing_dynamic_profile, ensure_ascii=False)}
    
    Extrahiere neue persönliche Informationen aus dem User-Input und gib das vollständige, aktualisierte Profil als JSON-Objekt zurück.
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o", # GPT-4o ist gut für JSON-Extraktion
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}, # Explizit JSON-Format anfordern
            temperature=0.3
        )
        
        extracted_data_str = response.choices[0].message.content
        new_extracted_profile: Dict[str, str] = json.loads(extracted_data_str)

        print(f"Extrahierte Profildaten von GPT für User {user_id}: {new_extracted_profile}")

        # Temporäre Kategorien ausschließen
        EXCLUDED_CATEGORIES = ['Aktuelles_Datum']

        # Vergleichen und Aktualisieren der Daten in der 'profile' Tabelle (EAV-Modell)
        for key, value in new_extracted_profile.items():
            # Temporäre Infos überspringen
            if key in EXCLUDED_CATEGORIES:
                print(f"Überspringe temporäre Kategorie: {key}")
                continue
                
            # Prüfen ob Eintrag schon existiert
            existing = supabase.table("profile").select("*").eq("user_id", user_id).eq("attribute_name", key).execute().data
            
            if existing:
                # Update bestehenden Eintrag
                supabase.table("profile").update({"attribute_value": value, "last_updated": datetime.datetime.utcnow().isoformat() + 'Z'}).eq("user_id", user_id).eq("attribute_name", key).execute()
            else:
                # Neuen Eintrag hinzufügen
                supabase.table("profile").upsert({"user_id": user_id, "attribute_name": key, "attribute_value": value, "last_updated": datetime.datetime.utcnow().isoformat() + 'Z'}).execute()

        print(f"Dynamisches Profil für User {user_id} erfolgreich aktualisiert.")

    except json.JSONDecodeError as e:
        print(f"FEHLER beim Parsen der JSON-Antwort von GPT in extrahiere_und_speichere_profil_details: {e}")
        print(f"GPT-Antwort (Roh): {extracted_data_str}")
    except Exception as e:
        print(f"FEHLER bei der Profil-Extraktion oder Speicherung in extrahiere_und_speichere_profil_details: {e}")

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

#Abrufen der letzten 8 unbeantworteten Einstiegsfragen
async def get_recent_entry_questions(user_id: str):
    recent_prompts = supabase.table("conversation_history") \
        .select("ai_prompt") \
        .eq("user_id", user_id) \
        .eq("user_input", "") \
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
        .select("user_input, ai_response, ai_prompt") \
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
                "timestamp": datetime.datetime.utcnow().isoformat() + 'Z'
            }).execute()
        except Exception as e:
            print(f"Fehler beim Speichern der initialen Einstiegsfrage als AI-Prompt: {e}")
            return {"frage": frage_text}
        
        return {"frage": frage_text}

    else:
        frage = ""
        
        # Letzte 8 Einstiegsfragen abrufen
        recent_ai_prompts_to_avoid_raw = await get_recent_entry_questions(user_id)
        recent_ai_prompts_to_avoid = [
            str(p) for p in recent_ai_prompts_to_avoid_raw
            if p is not None and str(p).strip() != ""
        ]

        user_profile_data_raw = supabase.table("profile") \
            .select("attribute_name, attribute_value") \
            .eq("user_id", user_id) \
            .execute().data
        
        user_profile_context = ""
        if user_profile_data_raw:
            user_profile_context = "\nAktuelles Benutzerprofil:\n" + "\n".join([
                f"- {item['attribute_name']}: {item['attribute_value']}"
                for item in user_profile_data_raw
            ])
        else:
            user_profile_context = "\nBisher keine Profilinformationen erfasst."
        
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
            .select("titel", "status") \
            .eq("user_id", user_id) \
            .limit(5) \
            .execute().data            
 
        goals_context = ""
        if user_goals:
            goals_context = "\nAktuelle Ziele:\n" + "\n".join([
                f"- {str(g.get('titel', ''))} (Status: {str(g.get('status', ''))})" 
                for g in user_goals
            ])
        else:
            goals_context = "\nBisher keine Ziele erfasst."

        # Laden aller Routinen aus der 'routines'-Tabelle für eine Gesamtübersicht
        all_user_routines = supabase.table("routines") \
            .select("task, day, checked, missed_count") \
            .eq("user_id", user_id) \
            .limit(10) \
            .execute().data
            
        routines_overview_context = ""
        if all_user_routines:
            routines_overview_context = "\nÜbersicht aller Routinen:\n" + "\n".join([
                f"- {str(r.get('task', ''))} ({str(r.get('day', ''))}, Erledigt: {'Ja' if r.get('checked', False) else 'Nein'}, Verpasst: {str(r.get('missed_count', 0))})" 
                for r in all_user_routines
            ])
        else:
            routines_overview_context = "\nBisher keine Routinen erfasst."
            
        # Routinen überprüfen
        today = datetime.datetime.now().strftime("%A")
        unfulfilled_routines = supabase.table("routines") \
            .select("task, missed_count") \
            .eq("day", today) \
            .eq("checked", False) \
            .eq("user_id", user_id) \
            .execute().data

        # Routinen, die mindestens 3-mal nicht erfüllt wurden
        five_weeks_ago = (datetime.datetime.now() - datetime.timedelta(weeks=5)).strftime("%Y-%m-%d")
        routine_texts = []
        for r in unfulfilled_routines:
            missed_dates = r.get("missed_dates") or []
            recent_missed = [d for d in missed_dates if d >= five_weeks_ago]
            if len(recent_missed) >= 3 and r.get("task") is not None:
                routine_texts.append(str(r.get("task", '')))
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
                
            context_for_gpt += user_profile_context
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
            Vermeide zusammengesetzte Fragen oder Fragen, die mit 'und' verbunden sind.
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
            - Umfeld und Lebensgestaltung
            Frage aber auch nach anderen Themen.
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
@app.post("/chat/{user_id}")
async def chat(user_id: str, chat_input: ChatInput):
    user_message = chat_input.message
    try:
        # Konversationshistorie der letzten 5 Nachrichten abrufen
        try:
            history_response = supabase.table("conversation_history") \
                .select("user_input, ai_response, ai_prompt") \
                .eq("user_id", user_id) \
                .order("timestamp", desc=True) \
                .limit(5) \
                .execute()
            gespraechs_historie = history_response.data
            gespraechs_historie.reverse() # Älteste zuerst
        except Exception as e:
            print(f"Fehler beim Abrufen der Konversationshistorie: {e}")
            gespraechs_historie = [] # Setze Historie auf leer im Fehlerfall

        # Laden des Wochenberichts
        wochenbericht_text = "Kein Wochenbericht verfügbar."
        try:
            latest_weekly_report = supabase.table("long_term_memory") \
                .select("inhalt") \
                .eq("user_id", user_id) \
                .eq("thema", "Wochenrückblick") \
                .order("timestamp", desc=True) \
                .limit(1) \
                .execute()
            if latest_weekly_report.data:
                wochenbericht_text = latest_weekly_report.data[0]['inhalt']
        except Exception as e:
            print(f"Fehler beim Abrufen des Wochenberichts: {e}")

        # Laden des Monatsberichts
        monatsbericht_text = "Kein Monatsbericht verfügbar."
        try:
            latest_monthly_report = supabase.table("long_term_memory") \
                .select("inhalt") \
                .eq("user_id", user_id) \
                .eq("thema", "Monatsrückblick") \
                .order("timestamp", desc=True) \
                .limit(1) \
                .execute()
            if latest_monthly_report.data:
                monatsbericht_text = latest_monthly_report.data[0]['inhalt']
        except Exception as e:
            print(f"Fehler beim Abrufen des Monatsberichts: {e}")

        # Aktuelle Ziele abrufen
        ziele_text = "Keine offenen Ziele."
        try:
            open_goals_response = supabase.table("goals") \
                .select("titel", "status", "deadline") \
                .eq("user_id", user_id) \
                .eq("status", "offen") \
                .execute()
            open_goals = open_goals_response.data
            if open_goals:
                ziele_text = "Aktuelle offene Ziele:\n" + "\n".join([f"- {g['titel']} (Deadline: {g['deadline']})" for g in open_goals])
        except Exception as e:
            print(f"Fehler beim Abrufen der Ziele: {e}")

        # Laden der dynamischen Profildaten aus der 'profile'-Tabelle (EAV-Modell)
        profile_text_for_prompt = "Keine spezifischen Profilinformationen erfasst." # Standardwert
        try:
            profile_attributes_data = supabase.table("profile") \
                .select("attribute_name, attribute_value") \
                .eq("user_id", user_id) \
                .execute().data
            
            if profile_attributes_data:
                user_profile_details = {item["attribute_name"]: item["attribute_value"] for item in profile_attributes_data}
                profile_text_for_prompt = "Aktuelles Benutzerprofil:\n" + "\n".join([f"- {name}: {value}" for name, value in user_profile_details.items()])
        except Exception as e:
            print(f"Fehler beim Laden des Profils: {e}")

        # Routinen laden
        routines_text = "Keine Routinen definiert." # Standardwert
        try:
            today = datetime.datetime.now().strftime("%A")
            routines_response = supabase.table("routines").select("task, checked, day, missed_count").eq("user_id", user_id).execute()
            routines = routines_response.data
            if routines:
                routines_text = "Aktuelle Routinen:\n" + "\n".join([f"- {r['task']} (Tag: {r['day']}, Erledigt: {'Ja' if r['checked'] else 'Nein'}, Verpasst: {str(r['missed_count'])})" for r in routines])
        except Exception as e:
            print(f"Fehler beim Abrufen der Routinen: {e}")
        
        # Langzeitgedächtnis laden
        memory_text = "Keine spezifischen Langzeit-Erkenntnisse gespeichert." # Standardwert
        try:
            memory = supabase.table("long_term_memory").select("thema", "inhalt").order("timestamp", desc=True).limit(10).execute().data
            if memory:
                memory_text = "\n".join([f"{m['thema']}: {m['inhalt']}" for m in memory])
        except Exception as e:
            print(f"Fehler beim Abrufen des Langzeitgedächtnisses: {e}")

        # Konversationshistorie für den System-Prompt formatieren
        history_messages = []
        for h in reversed(gespraechs_historie):
            if h.get('user_input'):
                history_messages.append(f"User: {h['user_input']}")
            if h.get('ai_response'):
                history_messages.append(f"Berater: {h['ai_response']}")
            if h.get('ai_prompt'):
                history_messages.append(f"Interviewfrage: {h['ai_prompt']}")

        history_text = "\n".join(history_messages) if history_messages else "Bisher keine frühere Konversationshistorie."
        
        # Systemnachricht zusammenstellen
        system_message = f"""
        Du bist ein persönlicher, anspruchsvoller und konstruktiver Mentor und Therapeut. Dein Ziel ist es, dem Nutzer realistisch, prägnant und umsetzbar zu helfen.

        WICHTIGE INFORMATIONEN:
        - Heutiges Datum: {datetime.datetime.now().strftime('%d. %B %Y')}
        - Aktueller Wochentag: {datetime.datetime.now().strftime('%A')}

        Nutze folgende Informationen für direkt handlungsorientierte Ratschläge:

        Nutzerprofil:
        {profile_text_for_prompt}
        
        Deine heutigen Routinen:
        {routines_text}

        Langzeitgedächtnis / Wichtige Erkenntnisse:
        {memory_text}

        Aktueller Wochenbericht:
        {wochenbericht_text}
        
        Aktueller Monatsbericht:
        {monatsbericht_text}
        
        Aktuelle Ziele:
        {ziele_text}

        Konversationshistorie (letzte 5 Nachrichten):
        {history_text}

        Analysiere die aktuelle Nachricht im Kontext ALLER Infos. Erkenne Inkonsistenzen oder mangelnden Fortschritt.
        Kein allgemeines Lob. Fokussiere dich auf konkrete Ansatzpunkte.
        Stelle konkrete Fragen, schlage Aktionen vor oder weise auf Reflexionen hin.
        Achte auf realistische Vorschläge, da der Nutzer schon feste Routinen, eine Arbeit und Frau hat.

        Antworte maximal 3 Sätze. Deine Antworten sollen knapp, direkt, motivierend und auf konkrete nächste Schritte ausgerichtet sein.
        """

        # Chat-Interaktion mit OpenAI
        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": user_message}
            ],
            max_tokens=500,
            temperature=0.7
        )
        ai_response_content = completion.choices[0].message.content.strip()

        # Nachricht in Historie speichern
        await _save_conversation_entry(user_id, user_message, ai_response_content, "")
        
        # Die letzte ai_prompt aus der Historie holen
        last_ai_prompt = ""
        if gespraechs_historie:
            last_entry = gespraechs_historie[-1]
            last_ai_prompt = last_entry.get('ai_prompt', '')
                
        await extrahiere_und_speichere_profil_details(user_id, user_message, ai_response_content, last_ai_prompt)

        return {"response": ai_response_content}

    except Exception as e:
        print(f"Fehler in der Chat-Funktion: {e}")
        raise HTTPException(status_code=500, detail="Entschuldige, es gab ein Problem beim Verarbeiten deiner Anfrage. Bitte versuche es später noch einmal.")
        
# Automatischer Wochen- und Monatsbericht
@app.get("/bericht/automatisch")
async def automatischer_bericht():
    # Annahme einer festen User ID für Berichte, wie in generiere_rueckblick
    user_id = "1" 

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
            # ▚▚▚ ANPASSUNG: user_id an generiere_rueckblick übergeben ▚▚▚
            bericht_result = await generiere_rueckblick("Monats", 30, user_id) 
            bericht_inhalt = bericht_result # generiere_rueckblick gibt jetzt direkten String zurück
        else:
            print(f"{bericht_typ} für User {user_id} wurde heute bereits generiert. Überspringe Generierung.")

    # Wochenbericht prüfen und ggf. generieren
    elif wochentag_utc == 6: # Sonntag (im deutschen Kontext, basierend auf UTC)
        bericht_typ = "Wochenrückblick"
        today_start_utc = heute_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start_utc = today_start_utc + datetime.timedelta(days=1)
        print(f"Wochenbericht-Check - Abfragebereich: {today_start_utc.isoformat()} bis {tomorrow_start_utc.isoformat()}")

        existing_report_response = supabase.table("long_term_memory") \
            .select("id", "thema", "timestamp") \
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
            # ▚▚▚ ANPASSUNG: user_id an generiere_rueckblick übergeben ▚▚▚
            bericht_inhalt = await generiere_rueckblick("Wochen", 7, user_id)
            # generiere_rueckblick speichert den Bericht bereits, daher hier keine weitere Speicherung
        # NEU: Nachricht, wenn Bericht bereits existiert
        else:
            print(f"{bericht_typ} für User {user_id} wurde heute bereits generiert. Überspringe Generierung.")
    print(f"--- Ende 'automatischer_bericht' ---\n")
    return {"typ": bericht_typ, "inhalt": bericht_inhalt}

# Wochen- und Monatsberichte generieren (mit Summarisierung)
async def generiere_rueckblick(zeitraum: str, tage: int, user_id: str):
    seit = (datetime.datetime.utcnow() - datetime.timedelta(days=tage)).isoformat() + 'Z'

    # Rufe die gesamte Konversationshistorie für den Zeitraum ab
    all_gespraeche = supabase.table("conversation_history").select("user_input, ai_response, ai_prompt, timestamp").gte("timestamp", seit).eq("user_id", user_id).order("timestamp", desc=False).execute().data
    all_ziele = supabase.table("goals").select("titel", "status", "created_at").gte("created_at", seit).eq("user_id", user_id).order("created_at", desc=False).execute().data

    profil_data = supabase.table("profile") \
        .select("attribute_name, attribute_value") \
        .eq("user_id", user_id) \
        .execute().data
    
    profil_text = ""
    if profil_data:
        profil_text = "\n".join([f"- {item['attribute_name']}: {item['attribute_value']}" for item in profil_data])
    else:
        profil_text = "Keine Profildaten vorhanden."

    # Alle Gespräche des Zeitraums für den Prompt nutzen
    gespraeche_text_for_prompt = ""
    if all_gespraeche:
        formatted_gespraeche = []
        for g in all_gespraeche:
            if g.get('user_input'):
                formatted_gespraeche.append(f"User: {g['user_input']}")
            if g.get('ai_response'):
                formatted_gespraeche.append(f"Berater: {g['ai_response']}")
            if g.get('ai_prompt'):
                formatted_gespraeche.append(f"Interviewfrage: {g['ai_prompt']}")
        gespraeche_text_for_prompt += "\n".join(formatted_gespraeche)
    else:
        gespraeche_text_for_prompt = "Es gab keine relevanten Gespräche in diesem Zeitraum."

    # Ziele können oft kompakter sein. Wenn sie aber auch zu lang werden, hier auch summarisieren.
    ziele_text = "\n".join([f"{z['titel']} ({z['status']})" for z in all_ziele[-20:]]) # max. die letzten 20 Ziele

    all_routines_res = supabase.table("routines") \
        .select("task, checked, day, missed_count") \
        .eq("user_id", user_id) \
        .execute().data
    
    routinen_text = ""
    if all_routines_res:
        routinen_text = "\n".join([f"- {r['task']} (Tag: {r['day']}, Heute erledigt: {'Ja' if r['checked'] else 'Nein'}, Verpasst: {r['missed_count']})" for r in all_routines_res])
    else:
        routinen_text = "Keine Routinen vorhanden."
        
    latest_report_res = supabase.table("long_term_memory") \
        .select("inhalt") \
        .eq("user_id", user_id) \
        .eq("thema", f"{zeitraum}rückblick") \
        .order("timestamp", desc=True) \
        .limit(1) \
        .execute().data
    
    previous_report_content = latest_report_res[0]['inhalt'] if latest_report_res else "Kein früherer Bericht dieses Typs vorhanden."

    system = f"""
    Du bist ein persönlicher Beobachter und Coach. Liste die im letzten {zeitraum} besprochenen Themen und den Status von Zielen und Routinen rückblickend knapp auf.
    Analysiere Trends, erkenne Fortschritte oder Herausforderungen und gebe konkrete, umsetzbare Vorschläge für die Zukunft.
    Berücksichtige alle Gespräche im jeweiligen Zeitraum (Woche oder Monat).
    """
    user = f"""
    Hier sind die Informationen für den {zeitraum}-Rückblick:

    Gespräche:
    {gespraeche_text_for_prompt}

    Ziele (Status):
    {ziele_text}
    
    Benutzerprofil-Details:
    {profil_text}
    
    Bitte gib einen motivierenden und tiefgehenden Rückblick, der wirklich analysiert, was passiert ist und konkrete, umsetzbare nächste Schritte vorschlägt.
    ```
    """

    response = client.chat.completions.create(
        model="gpt-4", # Für den Hauptbericht bleiben wir bei GPT-4
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        max_tokens=300, # Begrenze die Ausgabe des Berichts
        temperature=0.7
    )

    bericht = response.choices[0].message.content

    # Bericht speichern
    supabase.table("long_term_memory").insert({
        "thema": f"{zeitraum}rückblick",
        "inhalt": bericht,
        "timestamp": datetime.datetime.utcnow().isoformat() + 'Z',
        "user_id": user_id # user_id auch hier speichern!
    }).execute()

    return bericht
    
# Endpunkt zum Abrufen des neuesten gespeicherten Berichts
@app.get("/bericht/abrufen/{report_type_name}")
def get_stored_report(report_type_name: str, user_id: int = 1): # user_id kann als Standard 1 haben
    try:
        # Hier wird der "thema"-String genau so gesucht, wie er gespeichert wird
        # (z.B. "Wochenrückblick" oder "Monatsrückblick", ohne 's')
        
        # Sicherstellen, dass der übergebene Typ einem bekannten Thema entspricht
        if report_type_name not in ["Wochenrückblick", "Monatsrückblick"]:
            raise HTTPException(status_code=400, detail="Ungültiger Berichtstyp angefragt.")

        report_data = supabase.table("long_term_memory") \
            .select("inhalt") \
            .eq("user_id", user_id) \
            .eq("thema", report_type_name) \
            .order("timestamp", desc=True) \
            .limit(1) \
            .execute().data
        
        if report_data:
            print(f"Bericht '{report_type_name}' für User {user_id} gefunden.")
            return {"inhalt": report_data[0]["inhalt"]}
        else:
            print(f"Kein Bericht '{report_type_name}' für User {user_id} gefunden.")
            return {"inhalt": f"Kein {report_type_name} verfügbar. Er wird {report_type_name.lower().replace('rückblick', '')}s generiert."}
    except HTTPException as http_exc:
        raise http_exc # HTTPExceptions weiterleiten
    except Exception as e:
        print(f"FEHLER beim Abrufen des Berichts '{report_type_name}' für User {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Fehler beim Abrufen des Berichts.")

# Routinen abrufen
@app.get("/routines/{user_id}")
def get_routines(user_id: str):
    today = datetime.datetime.now().strftime("%A")
    current_date = datetime.datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%A")
    yesterday_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        # 🆕 SCHRITT 1: Alle Routinen für heute abrufen (mit last_checked_date)
        today_routines = supabase.table("routines").select("id, task, time, day, checked, missed_count, last_checked_date, missed_dates").eq("day", today).eq("user_id", user_id).execute().data
        yesterday_routines = supabase.table("routines").select("id, task, time, day, checked, missed_count, last_checked_date, missed_dates").eq("day", yesterday).eq("user_id", user_id).execute().data
        all_routines = []
        
        # 🆕 SCHRITT 2: Reset-Logik für jeden Routine-Eintrag
        for routine in today_routines:
            routine_copy = routine.copy()
            routine_copy['date'] = current_date
            routine_copy['display_date'] = 'heute'
            routine_id = routine['id']
            last_checked = routine.get('last_checked_date')
            is_checked = routine.get('checked', False)
            
            # 🎯 RESET-BEDINGUNG: Wenn last_checked_date nicht heute ist (oder NULL)
            if last_checked != current_date:
                print(f"Routine {routine_id} ({routine['task']}) - Reset erforderlich. Letzter Check: {last_checked}, Heute: {current_date}")
                
                # Wenn Routine nicht gecheckt wurde -> missed_count erhöhen
                # Reset ohne missed_dates zu ändern (48h Kulanz)
                supabase.table("routines").update({
                    "checked": False,
                    "last_checked_date": current_date
                }).eq("id", routine_id).execute()
                
                # Lokale Daten aktualisieren
                routine['checked'] = False
                
                routine_copy['checked'] = False
                routine_copy['last_checked_date'] = current_date    
                routine['last_checked_date'] = current_date
            routine_copy['checked'] = routine.get('checked', False)
            routine_copy['last_checked_date'] = routine.get('last_checked_date')
            all_routines.append(routine_copy)
        
        # Verarbeite GESTERN-Routinen (nur unerledigte)
        for routine in yesterday_routines:
            last_checked = routine.get('last_checked_date')
            is_checked = routine.get('checked', False)
            
            # Nur anzeigen wenn NICHT gecheckt wurde gestern
            if last_checked != yesterday_date or not is_checked:
                routine_copy = routine.copy()
                routine_copy['date'] = yesterday_date
                routine_copy['display_date'] = 'gestern'
                routine_copy['checked'] = False  # Immer unchecked anzeigen
                all_routines.append(routine_copy)
                
                # Prüfe ob diese Routine vorgestern auch schon nicht gecheckt war
                day_before_yesterday = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
                
                # Nur zu missed_dates hinzufügen wenn 48h vorbei sind (vorgestern nicht gecheckt)
                if last_checked and last_checked <= day_before_yesterday:
                    missed_dates = routine.get('missed_dates') or []
                    if yesterday_date not in missed_dates:
                        missed_dates.append(yesterday_date)
                        print(f"Routine {routine['id']} - 48h Kulanz abgelaufen, zu missed_dates hinzugefügt")
                        supabase.table("routines").update({
                            "missed_dates": missed_dates
                        }).eq("id", routine['id']).execute()
        
        # Sortiere: heute zuerst, dann gestern
        all_routines.sort(key=lambda x: x['date'], reverse=True)
                
        print(f"Routinen für {today} (User {user_id}) erfolgreich abgerufen und resettet.")
        return {"routines": all_routines}
        
    except Exception as e:
        print(f"Fehler beim Abrufen/Reset der Routinen: {e}")
        # Fallback ohne last_checked_date
        all_routines = supabase.table("routines").select("id, task, time, day, checked, missed_count, missed_dates").eq("day", today).eq("user_id", user_id).execute().data
        return {"routines": all_routines}
        
@app.post("/routines/update")
def update_routine_status(update: RoutineUpdate):
   try:
       current_date = datetime.datetime.now().strftime("%Y-%m-%d")
       yesterday_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

       # Hole aktuelle Routine-Daten um zu bestimmen für welches Datum das Update gilt
       routine_data = supabase.table("routines").select("day").eq("id", update.id).eq("user_id", update.user_id).execute().data

       if not routine_data:
           return {"status": "error", "message": "Routine nicht gefunden"}

       routine = routine_data[0]
       routine_day = routine['day']

       # Bestimme ob es sich um eine Heute- oder Gestern-Routine handelt
       today_weekday = datetime.datetime.now().strftime("%A")
       yesterday_weekday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%A")

       if routine_day == today_weekday:
           target_date = current_date
       elif routine_day == yesterday_weekday:
           target_date = yesterday_date
       else:
           return {"status": "error", "message": "Routine gehört weder zu heute noch zu gestern"}
       
       # Update checked-Status UND last_checked_date
       supabase.table("routines").update({
           "checked": update.checked,
           "last_checked_date": target_date
       }).eq("id", update.id).eq("user_id", update.user_id).execute()
       
       print(f"Routine {update.id} für {target_date} auf checked={update.checked} gesetzt")
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

@app.post("/goals/{user_id}")
def create_goal(goal: Goal, user_id: str):
    try:
        goal_data = goal.model_dump()
        goal_data["user_id"] = user_id 
        supabase.table("goals").insert(goal_data).execute()
        return {"status": "success", "message": "Ziel erfolgreich gespeichert."}
    except Exception as e:
        print(f"Fehler beim Speichern des Ziels: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/goals/update/{user_id}")
def update_goal_status(update: GoalUpdate, user_id: str):
    try:
        supabase.table("goals").update({"status": update.status}).eq("id", update.id).eq("user_id", user_id).execute()
        return {"status": "success"}
    except Exception as e:
        print(f"Fehler beim Aktualisieren des Ziels: {e}")
        return {"status": "error", "message": str(e)}

# Memory-Endpoint
@app.post("/memory/{user_id}")
def create_memory(memory_input: MemoryInput, user_id: str):
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
@app.post("/profile/{user_id}")
def create_profile(profile_data: ProfileData, user_id: str):
    try:
        
        for attribute, value in profile_data.model_dump(exclude_unset=True).items():
            if value is None:
                continue # Überspringe Attribute, die nicht gesetzt sind oder None sind

            # Prüfen, ob das Attribut für diesen Benutzer bereits existiert
            existing_entry = supabase.table("profile") \
                .select("id") \
                .eq("user_id", user_id) \
                .eq("attribute_name", attribute) \
                .execute().data
            
            if existing_entry:
                # Aktualisiere den Wert des bestehenden Attributs
                supabase.table("profile") \
                    .update({"attribute_value": value}) \
                    .eq("id", existing_entry[0]["id"]) \
                    .execute()
                print(f"Profil-Attribut '{attribute}' für User '{user_id}' aktualisiert.")
            else:
                # Füge neues Attribut hinzu
                supabase.table("profile") \
                    .insert({"user_id": user_id, "attribute_name": attribute, "attribute_value": value}) \
                    .execute()
                print(f"Profil-Attribut '{attribute}' für User '{user_id}' erstellt.")
        return {"status": "success", "message": "Profil erfolgreich verarbeitet."}
    except Exception as e:
        print(f"Fehler beim Speichern des Profils: {e}")
        return {"status": "error", "message": str(e)}
