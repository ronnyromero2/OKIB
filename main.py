from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi import HTTPException
from openai import OpenAI
from dotenv import load_dotenv
from supabase import create_client, Client
from typing import Optional, Dict, Any, List, Union, Literal
import os
import datetime
import random
import json
import re

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

if not OPENAI_API_KEY or not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("Fehlende Umgebungsvariablen – bitte .env prüfen (OPENAI_API_KEY, SUPABASE_URL, SUPABASE_SERVICE_KEY)")

client = OpenAI(api_key=OPENAI_API_KEY)
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
    allow_origins=["https://okib.onrender.com"],
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
    
class MemoryInput(BaseModel):
    thema: str
    inhalt: str

class Goal(BaseModel):
    titel: str
    status: str = "offen"
    deadline: Optional[str] = None

class GoalUpdate(BaseModel):
    id: int
    status: str

class RoutineUpdate(BaseModel):
    id: Union[int, str]
    checked: bool
    user_id: Union[int, str]
    
class ProfileData(BaseModel):
    hobby: Optional[str] = None
    beruf: Optional[str] = None
    interessen: Optional[str] = None

class TodoInput(BaseModel):
    title: str
    description: str = ""
    priority: Literal["low", "medium", "high"] = "medium"
    due_date: Optional[str] = None  # YYYY-MM-DD
    category: str = "allgemein"
    is_recurring: bool = False
    recurrence_type: Optional[str] = None  # "monthly_first", "monthly_15th", "every_2_months", "every_3_months", "yearly"
    recurrence_day: Optional[int] = None  # Tag im Monat (1-31)

class TodoUpdate(BaseModel):
    id: Union[int, str]
    completed: bool
    user_id: Union[int, str]

class TodoStatusUpdate(BaseModel):
    id: Union[int, str]
    status: str  # "open", "in_progress", "completed", "archived"
    
class TodoEdit(BaseModel):
    id: Union[int, str]
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    due_date: Optional[str] = None
    category: Optional[str] = None
    
# Funktion zur Extraktion und Speicherung von erweiterten Profildetails im EAV-Modell
async def extrahiere_und_speichere_profil_details(user_id: str, user_input: str, ai_response: str, ai_prompt: str):
    
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
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            response_format={"type": "json_object"}, # Explizit JSON-Format anfordern
            temperature=0.3
        )
        
        extracted_data_str = response.choices[0].message.content
        new_extracted_profile: Dict[str, str] = json.loads(extracted_data_str)


        # Temporäre Kategorien ausschließen
        EXCLUDED_CATEGORIES = ['Aktuelles_Datum']
        now = datetime.datetime.utcnow().isoformat() + 'Z'

        records = [
            {"user_id": user_id, "attribute_name": key, "attribute_value": value, "last_updated": now}
            for key, value in new_extracted_profile.items()
            if key not in EXCLUDED_CATEGORIES
        ]

        if records:
            supabase.table("profile").upsert(records).execute()


    except json.JSONDecodeError as e:
        print(f"FEHLER beim Parsen der JSON-Antwort von GPT in extrahiere_und_speichere_profil_details: {e}")
        print(f"GPT-Antwort (Roh): {extracted_data_str}")
    except Exception as e:
        print(f"FEHLER bei der Profil-Extraktion oder Speicherung in extrahiere_und_speichere_profil_details: {e}")

# Zusammenfassung um Token zu sparen (mit gpt-4o-mini)
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
            model="gpt-4o-mini",
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
    return questions

def calculate_next_due_date(recurrence_type: str, recurrence_day: int = None, last_completed: str = None):
    """Berechnet das nächste Fälligkeitsdatum für wiederkehrende To-Dos"""
    today = datetime.datetime.now()
    
    if recurrence_type == "monthly_first":
        # Jeden ersten des Monats
        next_month = today.replace(day=1)
        if today.day > 1:
            if next_month.month == 12:
                next_month = next_month.replace(year=next_month.year + 1, month=1)
            else:
                next_month = next_month.replace(month=next_month.month + 1)
        return next_month.strftime("%Y-%m-%d")
    
    elif recurrence_type == "monthly_15th":
        # Jeden 15. des Monats
        next_date = today.replace(day=15)
        if today.day >= 15:  # Wenn heute schon nach dem 15. ist
            if next_date.month == 12:
                next_date = next_date.replace(year=next_date.year + 1, month=1)
            else:
                next_date = next_date.replace(month=next_date.month + 1)
        return next_date.strftime("%Y-%m-%d")
    
    elif recurrence_type == "monthly_custom" and recurrence_day:
        # Bestimmter Tag im Monat
        try:
            next_date = today.replace(day=recurrence_day)
            if today.day >= recurrence_day:
                if next_date.month == 12:
                    next_date = next_date.replace(year=next_date.year + 1, month=1)
                else:
                    next_date = next_date.replace(month=next_date.month + 1)
            return next_date.strftime("%Y-%m-%d")
        except ValueError:
            # Tag existiert nicht in diesem Monat (z.B. 31. Februar)
            return None
    
    elif recurrence_type == "every_2_months":
        # Alle 2 Monate
        next_date = today
        if next_date.month <= 10:
            next_date = next_date.replace(month=next_date.month + 2)
        else:
            next_date = next_date.replace(year=next_date.year + 1, month=next_date.month - 10)
        return next_date.strftime("%Y-%m-%d")
    
    elif recurrence_type == "every_3_months":
        # Alle 3 Monate (quartalsweise)
        next_date = today
        if next_date.month <= 9:
            next_date = next_date.replace(month=next_date.month + 3)
        else:
            next_date = next_date.replace(year=next_date.year + 1, month=next_date.month - 9)
        return next_date.strftime("%Y-%m-%d")
    
    elif recurrence_type == "yearly":
        # Jährlich
        next_date = today.replace(year=today.year + 1)
        return next_date.strftime("%Y-%m-%d")
    
    return None

def create_recurring_todo_instance(original_todo, user_id: str):
    """Erstellt eine neue Instanz eines wiederkehrenden To-Dos"""
    next_due = calculate_next_due_date(
        original_todo['recurrence_type'], 
        original_todo.get('recurrence_day')
    )
    
    if next_due:
        new_todo = {
            "user_id": user_id,
            "title": original_todo['title'],
            "description": original_todo['description'],
            "priority": original_todo['priority'],
            "due_date": next_due,
            "category": original_todo['category'],
            "status": "open",
            "completed": False,
            "is_recurring": True,
            "recurrence_type": original_todo['recurrence_type'],
            "recurrence_day": original_todo.get('recurrence_day'),
            "parent_todo_id": original_todo['id'],
            "created_at": datetime.datetime.utcnow().isoformat() + 'Z'
        }
        
        try:
            result = supabase.table("todos").insert(new_todo).execute()
            return result.data[0] if result.data else None
        except Exception as e:
            print(f"Fehler beim Erstellen der wiederkehrenden To-Do Instanz: {e}")
            return None
    
    return None

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
        if ai_prompt_msg is not None and ai_prompt_msg.strip() != "":
            messages.append(f"Berater (Frage): {ai_prompt_msg}")

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


# Modus-Auswahl per Zufall
        roll = random.random()

        if roll < 0.05:
            mode = "universum"
        elif roll < 0.20:
            mode = "insight"
        elif roll < 0.30:
            mode = "rueckblick"
        elif roll < 0.40:
            mode = "ziel_check"
        elif roll < 0.50:
            mode = "routine_reflexion"
        elif roll < 0.57:
            mode = "provokation"
        else:
            mode = "normal"


        # Kontext für GPT aufbauen
        context_for_gpt = "\nUser-Historie (letzte 30 Nachrichten):\n" + "\n".join(messages)
        if recent_ai_prompts_to_avoid:
            context_for_gpt += "\nKürzlich gestellte Fragen des Beraters:\n" + ", ".join(recent_ai_prompts_to_avoid)
        context_for_gpt += user_profile_context
        context_for_gpt += "\nAktuelle Berichte:\n" + reports_context
        context_for_gpt += goals_context
        context_for_gpt += routines_overview_context
        context_for_gpt += routine_context_today

        if mode == "universum":
            prompt = f"""
            Du bist hypothetisch die Simulation oder das Universum und möchtest dem Nutzer heute einen konkreten Hinweis geben. 
            Tue so, als ob du tatsächlich Kontakt zum Universum oder zur Simulation hättest und etwas Wichtiges über seinen heutigen Tag weißt. 
            Vermeide die letzten acht Einstiegsfragen:
            {", ".join(recent_ai_prompts_to_avoid)}
            Sei sehr konkret und weise auf eine bestimmte Aktion, Einstellung oder ein Ereignis hin. Bleibe dabei einfühlsam und motivierend.
            """

        elif mode == "insight":
            insights = supabase.table("long_term_memory") \
                .select("thema, inhalt") \
                .eq("user_id", user_id) \
                .not_.in_("thema", ["Wochenrückblick", "Monatsrückblick", "Jahresrückblick"]) \
                .order("timestamp", desc=True) \
                .limit(20) \
                .execute().data

            if insights:
                insight = random.choice(insights)
                prompt = f"""
                Du bist ein persönlicher Mentor. Der Nutzer hat folgende Erkenntnis einmal festgehalten:
                Thema: "{insight['thema']}"
                Inhalt: "{insight['inhalt']}"
                
                Greife diese Erkenntnis heute auf. Frag nach, wie es damit steht, ob sie sich bestätigt hat oder ob sich etwas verändert hat.
                Maximal 1-2 Sätze. Nicht wiederholen was in den letzten Fragen schon stand:
                {", ".join(recent_ai_prompts_to_avoid)}
                """
            else:
                mode = "normal"  # Fallback wenn keine Insights vorhanden

        elif mode == "rueckblick":
            # Lade die letzten 8 Wochenberichte, 20 Monatsberichte und alle Jahresberichte
            wochen_berichte = supabase.table("long_term_memory") \
                .select("thema, inhalt, timestamp") \
                .eq("user_id", user_id) \
                .eq("thema", "Wochenrückblick") \
                .order("timestamp", desc=True) \
                .limit(8) \
                .execute().data

            monats_berichte = supabase.table("long_term_memory") \
                .select("thema, inhalt, timestamp") \
                .eq("user_id", user_id) \
                .eq("thema", "Monatsrückblick") \
                .order("timestamp", desc=True) \
                .limit(20) \
                .execute().data

            jahres_berichte = supabase.table("long_term_memory") \
                .select("thema, inhalt, timestamp") \
                .eq("user_id", user_id) \
                .eq("thema", "Jahresrückblick") \
                .order("timestamp", desc=True) \
                .execute().data

            alle_rueckblicke = wochen_berichte + monats_berichte + jahres_berichte

            if alle_rueckblicke:
                gewählter_bericht = random.choice(alle_rueckblicke)
                prompt = f"""
                Du bist ein persönlicher Mentor. Hier ist ein früherer Rückblick des Nutzers:
                Typ: "{gewählter_bericht['thema']}"
                Inhalt: "{gewählter_bericht['inhalt']}"
                
                Greife ein konkretes Thema, Muster oder eine Herausforderung aus diesem Rückblick auf und stelle eine kurze, 
                direkte Anschlussfrage — hat sich etwas verändert, was ist daraus geworden?
                Maximal 1 Satz. Nicht wiederholen was schon in den letzten Fragen stand:
                {", ".join(recent_ai_prompts_to_avoid)}
                """
            else:
                mode = "normal"  # Fallback wenn keine Berichte vorhanden

        elif mode == "ziel_check":
            prompt = f"""
            Du bist ein persönlicher Mentor. Der Nutzer hat folgende offene Ziele:
            {goals_context}
            
            Wähle EIN konkretes Ziel aus und frage direkt nach dem aktuellen Stand — kurz und präzise, maximal 1 Satz.
            Nicht wiederholen was schon in den letzten Fragen stand:
            {", ".join(recent_ai_prompts_to_avoid)}
            """

        elif mode == "routine_reflexion":
            prompt = f"""
            Du bist ein persönlicher Mentor. Hier ist eine Übersicht der Routinen des Nutzers:
            {routines_overview_context}
            
            Greife eine Routine auf — entweder eine die oft verpasst wird, oder eine die gut läuft — und frage nach dem Warum.
            Maximal 1 Satz. Nicht wiederholen was schon in den letzten Fragen stand:
            {", ".join(recent_ai_prompts_to_avoid)}
            """

        elif mode == "provokation":
            prompt = f"""
            Du bist ein direkter, provokanter Mentor. Stelle dem Nutzer eine unbequeme, herausfordernde These oder Frage 
            basierend auf dem was du über ihn weißt. Ziel ist produktive Selbstreflexion, nicht Beleidigung.
            Maximal 1 Satz. Nicht wiederholen was schon in den letzten Fragen stand:
            {", ".join(recent_ai_prompts_to_avoid)}
            
            Was du über den Nutzer weißt:
            {user_profile_context}
            {goals_context}
            {routines_overview_context}
            """

        else:  # normal
            prompt = f"""
            Du bist ein kreativer, neugieriger Gesprächspartner. Deine Aufgabe: Stelle EINE einzige, kurze Frage.
            
            REGELN:
            1. Schaue zuerst auf das Benutzerprofil und die Historie — was weißt du bereits? Frag nach etwas, das du noch NICHT weißt.
            2. Diese Fragen wurden bereits gestellt — stelle sie NIEMALS nochmal oder ähnlich:
            {chr(10).join(f"- {q}" for q in recent_ai_prompts_to_avoid)}
            3. Verbiete dir selbst folgende Themen komplett: Lieblingsessen, Lieblingsmusik, Lieblingsfilm, Lieblingsbuch, Urlaubsziele, Lottogewinn.
            4. Sei konkret und persönlich, nicht allgemein. Nicht "Wie gehst du mit Stress um?" sondern z.B. "Was machst du als erstes, wenn ein Arbeitstag richtig schiefläuft?"
            5. Frag nach echten Erlebnissen, konkreten Momenten oder ehrlichen Meinungen — nicht nach Präferenzen oder Lieblingsthemen.
            6. Die Frage soll maximal 1 Satz lang sein.
            
            Benutzerprofil (was du bereits weißt):
            {user_profile_context}
            
            Bisherige Gesprächsthemen:
            {context_for_gpt}
            """

        # Fallback-Prompt falls mode auf "normal" zurückgefallen ist
        if mode == "normal" and 'prompt' not in locals():
            prompt = f"""
            Du bist ein kreativer, neugieriger Gesprächspartner. Stelle EINE kurze, persönliche Frage.
            Vermeide: {", ".join(recent_ai_prompts_to_avoid)}
            Benutzerprofil: {user_profile_context}
            """


        try:
            response = client.chat.completions.create(
                model="gpt-4o",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150,
                temperature=0.9
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
                print(f"Fehler beim Speichern der Einstiegsfrage: {e}")

            return {"frage": frage}

        except Exception as e:
            print(f"Fehler bei der GPT-Anfrage: {e}")
            return {"frage": "Es gab ein Problem beim Generieren der Einstiegsfrage. Was möchtest du heute besprechen?"}
            
# Chat-Funktion
@app.post("/chat/{user_id}")
async def chat(user_id: str, chat_input: ChatInput):
    user_message = chat_input.message
    # 🔥 TASK DETECTION: Prüfe auf To-Do/Routine Erstellung
    if extract_routine_intent(user_message):
        try:
            task, frequency = await create_routine_from_chat(user_id, user_message)
            frequency_text = {
                'daily': 'täglich', 
                'weekly': 'wöchentlich',
                'monthly': 'monatlich',
                'biweekly': 'alle zwei Wochen',
                'triweekly': 'alle drei Wochen',
                'fourweekly': 'alle vier Wochen'
            }.get(frequency, frequency)
            
            response = f"✅ Ich habe eine neue Routine '{task}' erstellt, Wiederholung {frequency_text}."
            
            return {"response": response, "created_routine": True}
        except Exception as e:
            print(f"Fehler beim Erstellen der Routine: {e}")
            return {"response": "❌ Fehler beim Erstellen der Routine. Bitte versuche es erneut.", "created_routine": False}
    elif extract_todo_intent(user_message):
        try:
            title, priority, due_date = await create_todo_from_chat(user_id, user_message)
            priority_text = {'high': 'Hoch', 'medium': 'Medium', 'low': 'Niedrig'}.get(priority, 'Medium')
            
            response = f"✅ Ich habe ein neues To-Do '{title}' erstellt mit Relevanz {priority_text}"
            if due_date:
                due_formatted = datetime.datetime.fromisoformat(due_date).strftime('%d.%m.%Y')
                response += f", fällig am {due_formatted}"
            response += "."
            
            return {"response": response, "created_todo": True}
        except Exception as e:
            print(f"Fehler beim Erstellen des To-Dos: {e}")
            return {"response": "❌ Fehler beim Erstellen des To-Dos. Bitte versuche es erneut.", "created_todo": False}
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
       
        # To-Dos laden
        todos_text = "Keine To-Dos definiert."
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            open_todos = supabase.table("todos").select("title, priority, due_date, category, status").eq("user_id", user_id).in_("status", ["open", "in_progress"]).limit(10).execute().data
            overdue_todos = supabase.table("todos").select("title, priority, due_date, category").eq("user_id", user_id).lt("due_date", today).neq("status", "completed").neq("status", "archived").execute().data
            
            if open_todos or overdue_todos:
                todos_parts = []
                if open_todos:
                    todos_parts.append("Offene To-Dos:\n" + "\n".join([f"- {t['title']} (Priorität: {t['priority']}, Fällig: {t.get('due_date', 'Kein Datum')}, Kategorie: {t['category']})" for t in open_todos]))
                if overdue_todos:
                    todos_parts.append("Überfällige To-Dos:\n" + "\n".join([f"- {t['title']} (Fällig seit: {t['due_date']}, Priorität: {t['priority']})" for t in overdue_todos]))
                todos_text = "\n\n".join(todos_parts)
        except Exception as e:
            print(f"Fehler beim Abrufen der To-Dos: {e}")
        
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
                history_messages.append(f"Einstiegsfrage: {h['ai_prompt']}")

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

        Deine aktuellen To-Dos:
        {todos_text}

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
        Wenn der Nutzer überrascht über Deine Nachricht scheint, frage direkt nach, ob Du etwas bestimmtes falsch einschätzt und korrigiere Deine Infos, falls der Nutzer auf Fehler hinweist.
        Kein allgemeines Lob. Fokussiere dich auf konkrete Ansatzpunkte.
        Stelle konkrete Fragen oder weise auf Reflexionen hin. Mache NUR in etwa 5% der Fälle einen konkreten Vorschlag für nächste Schritte. In 15% der Fälle erzähle einen sarkastischen Witz im Zusammenhang mit der Antwort und lache Dich kaputt. In den anderen 85% der Fälle: akzeptiere die Antwort, hake nach oder gib eine kurze Einschätzung — ohne Empfehlungen.
        Achte auf realistische Vorschläge, da der Nutzer schon feste Routinen, eine Arbeit und Frau hat.

        Antworte maximal 3 Sätze. Deine Antworten sollen knapp, direkt, motivierend oder kritisch sein.
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

        return {"response": ai_response_content, "created_todo": False, "created_routine": False}

    except Exception as e:
        print(f"Fehler in der Chat-Funktion: {e}")
        raise HTTPException(status_code=500, detail="Entschuldige, es gab ein Problem beim Verarbeiten deiner Anfrage. Bitte versuche es später noch einmal.")

def extract_routine_intent(message: str) -> bool:
    patterns = [
        # Täglich
        r'(?:täglich|jeden tag|daily)',
        
        # Wöchentlich
        r'(?:wöchentlich|jede woche|weekly)',
        r'(?:jeden|jede)\s+(?:montag|dienstag|mittwoch|donnerstag|freitag|samstag|sonntag)',
        
        # Mehrwöchig
        r'(?:alle)\s+(?:zwei|drei|vier)\s+(?:wochen|tage)',
        
        # Monatlich
        r'(?:monatlich|jeden monat|monthly)',
        r'(?:jeden)\s+(?:\d+\.)\s+(?:des monats|tag)',
        r'(?:jeden ersten|jeden letzten)\s+(?:tag des monats)',
        r'(?:am ersten)',
        
        # Mehrmonatig
        r'(?:alle)\s+(?:\d+)\s+(?:monate|wochen)',
        
        # Jährlich
        r'(?:jährlich|jedes jahr|yearly|annually)',
        r'(?:jeden)\s+(?:\d+\.)\s+(?:januar|februar|märz|april|mai|juni|juli|august|september|oktober|november|dezember)',
        
        # Allgemein
        r'(?:routine|gewohnheit|habit)',
        r'(?:regelmäßig|wiederkehrend)',
    ]
    return any(re.search(pattern, message.lower()) for pattern in patterns)

def extract_todo_intent(message: str) -> bool:
    # Wenn Zeitintervalle → Routine, nicht To-Do
    if any(re.search(p, message.lower()) for p in [
        r'(?:alle|jeden|täglich|wöchentlich|monatlich)',
        r'(?:zwei|drei|vier)\s+(?:wochen|tage|monate)'
    ]):
        return False
    
    patterns = [
        r'(?:erstelle|neue|neues|mach|add).*?(?:to-?do|aufgabe|task)',
        r'(?:ich muss|sollte|möchte|will).*?(?:kaufen|erledigen)',  # Zeitangaben entfernt
        r'(?:unbedingt).*?(?:muss|sollte)',
    ]
    return any(re.search(pattern, message.lower()) for pattern in patterns)

def extract_title_from_message(message: str) -> str:
    """Extrahiert Aufgaben-Titel"""
    clean = re.sub(r'^(?:ich muss|sollte|möchte|will|erstelle|neue|neues|mach|add|erinnere mich)?\s*(?:to-?do|aufgabe|task|daran)?\s*:?\s*', '', message, flags=re.IGNORECASE)
    clean = re.sub(r'\s+(?:heute|morgen|täglich|wöchentlich|jeden|jede).*$', '', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\b(?:wichtig|urgent|dringend|dass|das|ich|mich|an)\b', '', clean, flags=re.IGNORECASE)
    return clean.strip() or "Neue Aufgabe"

def extract_priority(message: str) -> str:
    """Extrahiert Priorität"""
    if any(word in message.lower() for word in ['wichtig', 'urgent', 'dringend', 'sofort', 'unbedingt']):
        return 'high'
    elif any(word in message.lower() for word in ['später', 'niedrig', 'unwichtig']):
        return 'low'
    return 'medium'

def extract_due_date(message: str) -> str:
    """Extrahiert Fälligkeitsdatum"""
    today = datetime.datetime.now().date()
    if 'heute' in message.lower():
        return today.isoformat()
    elif 'morgen' in message.lower():
        return (today + datetime.timedelta(days=1)).isoformat()
    elif 'übermorgen' in message.lower():
        return (today + datetime.timedelta(days=2)).isoformat()
    return None

async def create_todo_from_chat(user_id: str, message: str):
    """Erstellt To-Do aus Chat-Message"""
    title = extract_title_from_message(message)
    priority = extract_priority(message)
    due_date = extract_due_date(message)
    
    todo_data = {
        "user_id": user_id,  # ← Direkt hier
        "title": title,
        "description": "",  # ← Hinzufügen
        "priority": priority,
        "status": "open",
        "category": "chat_erstellt",
        "due_date": due_date,
        "completed": False,  # ← Hinzufügen
        "completed_at": None,  # ← Hinzufügen
        "is_recurring": False,  # ← Hinzufügen
        "recurrence_type": None,  # ← Hinzufügen
        "recurrence_details": None,  # ← Hinzufügen
        "parent_todo_id": None  # ← Hinzufügen
    }
    
    result = supabase.table("todos").insert(todo_data).execute()
    
    return title, priority, due_date

async def create_routine_from_chat(user_id: str, message: str):
    """Erstellt Routine aus Chat-Message"""
    task = extract_title_from_message(message)
    
    # Standard day setzen
    day = "monday"  # Default
    frequency = "daily"
    
    # Monatliche Routinen ZUERST prüfen (höhere Priorität)
    if re.search(r'(?:immer\s+)?(?:am|jeden)\s+ersten', message.lower()):
        frequency = "monthly"
        day = "1"
    elif re.search(r'(?:am|jeden)\s+letzten', message.lower()):
        frequency = "monthly"
        day = "last"  # Spezieller Marker für letzten Tag
    elif re.search(r'(?:am|jeden)\s+(\d+)\.?\s*(?:des monats|tag)?', message.lower()):
        match = re.search(r'(?:am|jeden)\s+(\d+)', message.lower())
        if match:
            frequency = "monthly"
            day = match.group(1)
    elif any(word in message.lower() for word in ['monatlich', 'jeden monat', 'monthly']):
        frequency = "monthly"
        day = "1"
    # Wöchentliche Routinen
    elif re.search(r'montag', message.lower()): 
        frequency = "weekly"
        day = "monday"
    elif re.search(r'dienstag', message.lower()): 
        frequency = "weekly"
        day = "tuesday"
    elif re.search(r'mittwoch', message.lower()): 
        frequency = "weekly"
        day = "wednesday"
    elif re.search(r'donnerstag', message.lower()): 
        frequency = "weekly"
        day = "thursday"
    elif re.search(r'freitag', message.lower()): 
        frequency = "weekly"
        day = "friday"
    elif re.search(r'samstag', message.lower()): 
        frequency = "weekly"
        day = "saturday"
    elif re.search(r'sonntag', message.lower()): 
        frequency = "weekly"
        day = "sunday"
    elif any(word in message.lower() for word in ['wöchentlich', 'jede woche', 'weekly']):
        frequency = "weekly"
    elif re.search(r'alle\s+(?:zwei|2)\s+wochen', message.lower()):
        frequency = "biweekly"
        day = str(datetime.datetime.now().day)  # Heute als Starttag
    elif re.search(r'alle\s+(?:drei|3)\s+wochen', message.lower()):
        frequency = "triweekly"
        day = str(datetime.datetime.now().day)
    elif re.search(r'alle\s+(?:vier|4)\s+wochen', message.lower()):
        frequency = "fourweekly"
        day = str(datetime.datetime.now().day)
    
    # Berechne next_due_date basierend auf frequency
    next_due = None
    if frequency == "monthly":
        next_due = calculate_next_due_date(f"monthly_custom", int(day) if day.isdigit() else None)
    elif frequency in ["biweekly", "triweekly", "fourweekly"]:
        weeks = {"biweekly": 2, "triweekly": 3, "fourweekly": 4}[frequency]
        next_due = (datetime.datetime.now() + datetime.timedelta(weeks=weeks)).strftime("%Y-%m-%d")
    
    routine_data = {
        "task": task,
        "checked": False,
        "day": day,  # Bei monthly: Tag des Monats, bei weekly: Wochentag
        "time": None,
        "last_checked_date": None,
        "user_id": user_id,
        "missed_count": 0,
        "missed_dates": [],
        "frequency": frequency,  # NEU
        "recurrence_day": int(day) if frequency == "monthly" and day.isdigit() else None,  # NEU
        "next_due_date": next_due,  # NEU
        "recurrence_details": {  # NEU
            "type": frequency,
            "day_of_month": day if frequency == "monthly" else None,
            "day_of_week": day if frequency == "weekly" else None,
            "interval_weeks": {"biweekly": 2, "triweekly": 3, "fourweekly": 4}.get(frequency)
        }
    }
    
    result = supabase.table("routines").insert(routine_data).execute()
    
    return task, frequency
    
# Automatischer Wochen- und Monatsbericht
@app.get("/bericht/automatisch")
async def automatischer_bericht():
    # Annahme einer festen User ID für Berichte, wie in generiere_rueckblick
    user_id = "1" 

    # Alle Zeitberechnungen basieren jetzt konsistent auf UTC
    heute_utc = datetime.datetime.utcnow() 
    wochentag_utc = heute_utc.weekday() # Montag = 0, Sonntag = 6 (UTC-basiert)
    
    # Debug-Ausgaben für den Start

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
  
        existing_report_response = supabase.table("long_term_memory") \
            .select("id, thema, timestamp") \
            .eq("user_id", user_id) \
            .eq("thema", bericht_typ) \
            .gte("timestamp", start_of_month_utc.isoformat() + 'Z') \
            .lt("timestamp", end_of_month_utc.isoformat() + 'Z') \
            .execute()
        
        # ! WICHTIG: `.data` auf das response-Objekt zugreifen, um die Liste der gefundenen Einträge zu erhalten
        existing_report_data = existing_report_response.data
        
        # NEU: Bedingte Generierung nur, wenn kein existierender Bericht gefunden wurde
        if not existing_report_data:
            bericht_result = await generiere_rueckblick("Monats", 30, user_id)
            bericht_inhalt = bericht_result

    # Wochenbericht prüfen und ggf. generieren
    elif wochentag_utc == 6: # Sonntag (im deutschen Kontext, basierend auf UTC)
        bericht_typ = "Wochenrückblick"
        today_start_utc = heute_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start_utc = today_start_utc + datetime.timedelta(days=1)

        existing_report_response = supabase.table("long_term_memory") \
            .select("id", "thema", "timestamp") \
            .eq("user_id", user_id) \
            .eq("thema", bericht_typ) \
            .gte("timestamp", today_start_utc.isoformat() + 'Z') \
            .lt("timestamp", tomorrow_start_utc.isoformat() + 'Z') \
            .execute()
        
        # ! WICHTIG: `.data` auf das response-Objekt zugreifen, um die Liste der gefundenen Einträge zu erhalten
        existing_report_data = existing_report_response.data
        

        # NEU: Bedingte Generierung nur, wenn kein existierender Bericht gefunden wurde
        if not existing_report_data:
            bericht_inhalt = await generiere_rueckblick("Wochen", 7, user_id)
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
                formatted_gespraeche.append(f"Einstiegsfrage: {g['ai_prompt']}")
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
            return {"inhalt": report_data[0]["inhalt"]}
        else:
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
    today_day_of_month = datetime.datetime.now().day

    try:
        # 🆕 SCHRITT 1: Alle Routinen für heute abrufen (mit last_checked_date)

        # Hole ALLE Routinen des Users (inkl. frequency)
        all_user_routines = supabase.table("routines").select("id, task, time, day, checked, missed_count, last_checked_date, missed_dates, frequency, recurrence_day").eq("user_id", user_id).execute().data
        
        # Filtere für heute relevante Routinen
        today_routines = []
        yesterday_routines = []
        
        for routine in all_user_routines:
            frequency = routine.get('frequency', 'daily')
            
            # Tägliche Routinen - immer für heute
            if frequency == 'daily':
                today_routines.append(routine)
            
            # Wöchentliche Routinen - nur am richtigen Wochentag
            elif frequency == 'weekly' and routine.get('day') == today:
                today_routines.append(routine)
            
            # Monatliche Routinen - nur am richtigen Tag des Monats
            elif frequency == 'monthly':
                routine_day = routine.get('recurrence_day') or int(routine.get('day', 1))
                if routine_day == today_day_of_month:
                    today_routines.append(routine)
                # Letzter Tag des Monats
                elif routine.get('day') == 'last':
                    last_day = (datetime.datetime.now().replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
                    if datetime.datetime.now().day == last_day.day:
                        today_routines.append(routine)
            
            # Gestern-Routinen analog
            if frequency == 'weekly' and routine.get('day') == yesterday:
                yesterday_routines.append(routine)
            # NEU: Monatliche Routinen von gestern
            elif frequency == 'monthly':
                yesterday_day_of_month = (datetime.datetime.now() - datetime.timedelta(days=1)).day
                routine_day = routine.get('recurrence_day') or int(routine.get('day', 1))
                if routine_day == yesterday_day_of_month:
                    yesterday_routines.append(routine)
                # Letzter Tag des vorherigen Monats
                elif routine.get('day') == 'last':
                    yesterday_date_obj = datetime.datetime.now() - datetime.timedelta(days=1)
                    last_day_prev = (yesterday_date_obj.replace(day=1) + datetime.timedelta(days=32)).replace(day=1) - datetime.timedelta(days=1)
                    if yesterday_date_obj.day == last_day_prev.day:
                        yesterday_routines.append(routine)

        all_routines = []
        
        # 🆕 SCHRITT 2: Reset-Logik für jeden Routine-Eintrag
        for routine in today_routines:
            routine_copy = routine.copy()
            routine_copy['date'] = current_date
            routine_copy['display_date'] = 'heute'
            routine_id = routine['id']
            last_checked = routine.get('last_checked_date')
            is_checked = routine.get('checked', False)

            # DEBUG-AUSGABEN HIER HINZUFÜGEN:
            
            # 🎯 RESET-BEDINGUNG: Wenn last_checked_date nicht heute ist (oder NULL)
            if last_checked != current_date and last_checked is not None:
                if last_checked < current_date:  # Nur wenn letzter Check VOR heute war
                    
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
                        supabase.table("routines").update({
                            "missed_dates": missed_dates
                        }).eq("id", routine['id']).execute()
        
        # Sortiere: heute zuerst, dann gestern
        all_routines.sort(key=lambda x: x['date'], reverse=True)
                
        return {"routines": all_routines}
        
    except Exception as e:
        print(f"Fehler beim Abrufen/Reset der Routinen: {e}")
        # Fallback ohne last_checked_date
        all_routines = supabase.table("routines").select("id, task, time, day, checked, missed_count, missed_dates").eq("day", today).eq("user_id", user_id).execute().data
        return {"routines": all_routines}
        
@app.post("/routines/update")
def update_routine_status(update: RoutineUpdate):
    # Konvertiere zu Strings für Supabase
    routine_id = str(update.id)
    user_id = str(update.user_id)
        
    try:
        current_date = datetime.datetime.now().strftime("%Y-%m-%d")
        yesterday_date = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")

        # Hole aktuelle Routine-Daten um zu bestimmen für welches Datum das Update gilt     
        routine_data = supabase.table("routines").select("day, frequency, recurrence_day").eq("id", routine_id).eq("user_id", user_id).execute().data

        if not routine_data:
            return {"status": "error", "message": "Routine nicht gefunden"}

        routine = routine_data[0]
        frequency = routine.get('frequency', 'daily')
        
        # Bestimme target_date basierend auf frequency
        if frequency == 'daily':
            target_date = current_date  # Tägliche Routinen = heute
        elif frequency == 'weekly':
            # Für wöchentliche Routinen: bisherige Logik
            routine_day = routine['day']
            today_weekday = datetime.datetime.now().strftime("%A")
            yesterday_weekday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%A")
            
            if routine_day == today_weekday:
                target_date = current_date
            elif routine_day == yesterday_weekday:
                target_date = yesterday_date
            else:
                return {"status": "error", "message": "Wöchentliche Routine gehört weder zu heute noch zu gestern"}
        elif frequency == 'monthly':
            target_date = current_date  # Monatliche Routinen = heute
        else:
            target_date = current_date  # Fallback
              
        # Update checked-Status UND last_checked_date
        result = supabase.table("routines").update({
            "checked": update.checked,
            "last_checked_date": target_date
        }).eq("id", routine_id).eq("user_id", user_id).execute()
        
        return {"status": "success"}
        
    except Exception as e:
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
            else:
                # Füge neues Attribut hinzu
                supabase.table("profile") \
                    .insert({"user_id": user_id, "attribute_name": attribute, "attribute_value": value}) \
                    .execute()
        return {"status": "success", "message": "Profil erfolgreich verarbeitet."}
    except Exception as e:
        print(f"Fehler beim Speichern des Profils: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/todos/{user_id}")
def get_todos(user_id: str, status: str = None, category: str = None):
    """Alle To-Dos eines Users abrufen mit optionalen Filtern"""
    try:
        query = supabase.table("todos").select("*").eq("user_id", user_id)
        
        if status:
            query = query.eq("status", status)
        if category:
            query = query.eq("category", category)
            
        todos = query.order("created_at", desc=True).execute().data
        
        # Gruppiere nach Status für bessere Übersicht
        grouped_todos = {
            "open": [],
            "in_progress": [],
            "completed": [],
            "overdue": []
        }
        
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        for todo in todos:
            todo_status = todo.get('status', 'open')
            due_date = todo.get('due_date')
            
            # Prüfe auf überfällige To-Dos
            if due_date and due_date < today and todo_status not in ['completed', 'archived']:
                grouped_todos["overdue"].append(todo)
            else:
                if todo_status in grouped_todos:
                    grouped_todos[todo_status].append(todo)
                else:
                    grouped_todos["open"].append(todo)
        
        return {"todos": grouped_todos, "total": len(todos)}
        
    except Exception as e:
        print(f"Fehler beim Abrufen der To-Dos: {e}")
        return {"todos": {"open": [], "in_progress": [], "completed": [], "overdue": []}, "total": 0}

@app.post("/todos/{user_id}")
def create_todo(todo_input: TodoInput, user_id: str):
    """Neues To-Do erstellen"""
    try:
        todo_data = todo_input.model_dump()
        todo_data["user_id"] = user_id
        todo_data["status"] = "open"
        todo_data["completed"] = False
        todo_data["created_at"] = datetime.datetime.utcnow().isoformat() + 'Z'
        
        # Für wiederkehrende To-Dos: Wenn kein due_date gesetzt, berechne das erste
        if todo_input.is_recurring and todo_input.recurrence_type and not todo_input.due_date:
            next_due = calculate_next_due_date(todo_input.recurrence_type, todo_input.recurrence_day)
            todo_data["due_date"] = next_due
        
        result = supabase.table("todos").insert(todo_data).execute()
        return {"status": "success", "message": "To-Do erfolgreich erstellt", "todo": result.data[0] if result.data else None}
        
    except Exception as e:
        print(f"Fehler beim Erstellen des To-Dos: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/todos/update/{user_id}")
def update_todo_completion(update: TodoUpdate, user_id: str):
    """To-Do als erledigt/unerledigt markieren"""
    todo_id = str(update.id)
    user_id = str(update.user_id)
    
    try:
        # Hole To-Do Daten um zu prüfen ob es wiederkehrend ist
        todo_data = supabase.table("todos").select("*").eq("id", todo_id).eq("user_id", user_id).execute().data
        
        if not todo_data:
            return {"status": "error", "message": "To-Do nicht gefunden"}
        
        todo = todo_data[0]
        
        # Update des aktuellen To-Dos
        update_data = {
            "completed": update.completed,
            "status": "completed" if update.completed else "open",
            "completed_at": datetime.datetime.utcnow().isoformat() + 'Z' if update.completed else None
        }
        
        supabase.table("todos").update(update_data).eq("id", todo_id).eq("user_id", user_id).execute()
        
        # Wenn To-Do als erledigt markiert und wiederkehrend ist, erstelle neue Instanz
        if update.completed and todo.get('is_recurring') and todo.get('recurrence_type'):
            create_recurring_todo_instance(todo, user_id)
        
        return {"status": "success"}
        
    except Exception as e:
        print(f"Fehler beim Aktualisieren des To-Dos: {e}")
        return {"status": "error", "message": str(e)}

@app.post("/todos/status/{user_id}")
def update_todo_status(update: TodoStatusUpdate, user_id: str):
    """To-Do Status ändern (open, in_progress, completed, archived)"""
    todo_id = str(update.id)
    
    try:
        update_data = {"status": update.status}
        
        if update.status == "completed":
            update_data["completed"] = True
            update_data["completed_at"] = datetime.datetime.utcnow().isoformat() + 'Z'
        elif update.status in ["open", "in_progress"]:
            update_data["completed"] = False
            update_data["completed_at"] = None
        
        supabase.table("todos").update(update_data).eq("id", todo_id).eq("user_id", user_id).execute()
        return {"status": "success"}
        
    except Exception as e:
        print(f"Fehler beim Aktualisieren des To-Do Status: {e}")
        return {"status": "error", "message": str(e)}

@app.put("/todos/{todo_id}/{user_id}")
def edit_todo(todo_id: str, user_id: str, todo_edit: TodoEdit):
    """To-Do bearbeiten"""
    try:
        update_data = {k: v for k, v in todo_edit.model_dump(exclude_unset=True).items() if v is not None and k != 'id'}
        
        if update_data:
            update_data["updated_at"] = datetime.datetime.utcnow().isoformat() + 'Z'
            supabase.table("todos").update(update_data).eq("id", todo_id).eq("user_id", user_id).execute()
        
        return {"status": "success", "message": "To-Do erfolgreich aktualisiert"}
        
    except Exception as e:
        print(f"Fehler beim Bearbeiten des To-Dos: {e}")
        return {"status": "error", "message": str(e)}

@app.delete("/todos/{todo_id}/{user_id}")
def delete_todo(todo_id: str, user_id: str):
    """To-Do löschen"""
    try:
        supabase.table("todos").delete().eq("id", todo_id).eq("user_id", user_id).execute()
        return {"status": "success", "message": "To-Do erfolgreich gelöscht"}
        
    except Exception as e:
        print(f"Fehler beim Löschen des To-Dos: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/todos/categories/{user_id}")
def get_todo_categories(user_id: str):
    """Alle verwendeten Kategorien eines Users abrufen"""
    try:
        categories = supabase.table("todos").select("category").eq("user_id", user_id).execute().data
        unique_categories = list(set([cat['category'] for cat in categories if cat['category']]))
        return {"categories": unique_categories}
        
    except Exception as e:
        print(f"Fehler beim Abrufen der Kategorien: {e}")
        return {"categories": ["allgemein"]}

@app.get("/todos/stats/{user_id}")
def get_todo_stats(user_id: str):
    """To-Do Statistiken für Dashboard"""
    try:
        all_todos = supabase.table("todos").select("status, priority, due_date, completed").eq("user_id", user_id).execute().data
        
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        stats = {
            "total": len(all_todos),
            "open": len([t for t in all_todos if t.get('status') == 'open']),
            "in_progress": len([t for t in all_todos if t.get('status') == 'in_progress']),
            "completed": len([t for t in all_todos if t.get('status') == 'completed']),
            "overdue": len([t for t in all_todos if t.get('due_date') and t.get('due_date') < today and t.get('status') not in ['completed', 'archived']]),
            "due_today": len([t for t in all_todos if t.get('due_date') == today and t.get('status') not in ['completed', 'archived']]),
            "high_priority": len([t for t in all_todos if t.get('priority') == 'high' and t.get('status') not in ['completed', 'archived']])
        }
        
        return {"stats": stats}
        
    except Exception as e:
        print(f"Fehler beim Abrufen der To-Do Statistiken: {e}")
        return {"stats": {"total": 0, "open": 0, "in_progress": 0, "completed": 0, "overdue": 0, "due_today": 0, "high_priority": 0}}

# 4. Automatisches Archivieren alter To-Dos:

@app.post("/todos/cleanup/{user_id}")
def cleanup_completed_todos(user_id: str, days_old: int = 30):
    """Archiviert abgeschlossene To-Dos die älter als X Tage sind"""
    try:
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days_old)).strftime("%Y-%m-%d")
        
        # Markiere alte erledigte To-Dos als archiviert
        result = supabase.table("todos").update({
            "status": "archived"
        }).eq("user_id", user_id).eq("status", "completed").lt("completed_at", cutoff_date).execute()
        
        archived_count = len(result.data) if result.data else 0
        return {"status": "success", "archived_count": archived_count, "message": f"{archived_count} To-Dos archiviert"}
        
    except Exception as e:
        print(f"Fehler beim Archivieren der To-Dos: {e}")
        return {"status": "error", "message": str(e)}

@app.get("/todos/completed/{user_id}")
def get_completed_todos(user_id: str, limit: int = 20):
    """Zeigt die letzten erledigten To-Dos zur Übersicht"""
    try:
        completed_todos = supabase.table("todos").select("title, completed_at, category, priority").eq("user_id", user_id).eq("status", "completed").order("completed_at", desc=True).limit(limit).execute().data
        return {"completed_todos": completed_todos}
    except Exception as e:
        print(f"Fehler beim Abrufen erledigter To-Dos: {e}")
        return {"completed_todos": []}

@app.delete("/todos/completed/{user_id}")
def delete_old_completed_todos(user_id: str, days_old: int = 90):
    """Löscht sehr alte erledigte To-Dos permanent (z.B. nach 3 Monaten)"""
    try:
        cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=days_old)).isoformat() + 'Z'
        
        # Lösche nur normale (nicht-wiederkehrende) To-Dos die sehr alt sind
        result = supabase.table("todos").delete().eq("user_id", user_id).eq("status", "completed").eq("is_recurring", False).lt("completed_at", cutoff_date).execute()
        
        deleted_count = len(result.data) if result.data else 0
        return {"status": "success", "deleted_count": deleted_count, "message": f"{deleted_count} alte To-Dos permanent gelöscht"}
        
    except Exception as e:
        print(f"Fehler beim Löschen alter To-Dos: {e}")
        return {"status": "error", "message": str(e)}

