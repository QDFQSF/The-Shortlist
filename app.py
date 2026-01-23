import streamlit as st
import google.generativeai as genai
import json, urllib.parse, re, requests
import os
import time
import random
from supabase import create_client, Client
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from streamlit.components.v1 import html

# --- FONCTION DE RÉCUPÉRATION SÉCURISÉE ---
def get_secret(key, default=""):
    try:
        return st.secrets[key]
    except:
        return os.environ.get(key, default)

# --- 1. CONFIGURATION ---
AMAZON_PARTNER_ID = "theshorlistap-21"
INSTANT_GAMING_ID = "theshortlistapp"

# Récupération des secrets
SUPABASE_URL = get_secret("SUPABASE_URL")
SUPABASE_KEY = get_secret("SUPABASE_KEY")
TMDB_API_KEY = get_secret("TMDB_API_KEY")
GEMINI_API_KEY = get_secret("GEMINI_API_KEY")

# --- INITIALISATION ROBUSTE (CACHE ANTI-CRASH) ---
@st.cache_resource(ttl=3600)
def init_connections():
    # 1. Supabase
    try:
        client_supa = create_client(SUPABASE_URL, SUPABASE_KEY)
    except:
        client_supa = None
        
    # 2. Gemini
    try:
        genai.configure(api_key=GEMINI_API_KEY)
        config = {
          "temperature": 0.2,
          "top_p": 0.95,
          "top_k": 40,
          "max_output_tokens": 8192,
        }
        client_model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            generation_config=config
        )
    except:
        client_model = None

    return client_supa, client_model

# Chargement unique
supabase, model = init_connections()

# Config Page (Doit être en premier hors du main)
st.set_page_config(page_title="The Shortlist", page_icon="3️⃣", layout="wide")

# Sécurité connexion
if not supabase or not model:
    st.error("Erreur de connexion serveur. Veuillez rafraîchir la page.")
    st.stop()

# --- BANQUE D'ANECDOTES ---
PROMO_FACTS = [
    "💡 ASTUCE : Passer par nos liens Instant Gaming économise en moyenne 15€ par jeu récent.",
    "💡 SAVIEZ-VOUS que l'essai Amazon Prime Video est gratuit 30 jours ? Idéal pour binger une série ce week-end.",
    "💡 INFO : The Shortlist est financé uniquement par vos clics, merci de soutenir le projet !",
    "💡 BON PLAN : Les livres Kindle sont souvent 30% moins chers que la version papier."
]

LOADING_FACTS = {
    "🎮 Jeux Vidéo": [
        "Le code Konami (Haut, Haut, Bas, Bas...) existe car le créateur de Gradius trouvait le jeu trop dur !",
        "Pac-Man a été inspiré par une pizza à laquelle il manquait une part.",
        "La carte de GTA V fait deux fois la taille de l'île de Manhattan réelle.",
        "Mario s'appelait à l'origine 'Jumpman' et était charpentier, pas plombier.",
        "Le jeu 'E.T.' sur Atari a été enterré dans le désert car il était jugé trop mauvais."
    ],
    "🎬 Films": [
        "Le budget marketing du film 'Barbie' était supérieur au budget du film lui-même !",
        "Dans 'Psychose', le sang dans la douche était en fait du sirop de chocolat.",
        "Sean Connery portait une perruque dans tous ses films James Bond.",
        "Le bruit des vélociraptors dans Jurassic Park ? Des tortues en train de s'accoupler.",
        "Tom Cruise a vraiment escaladé le Burj Khalifa pour Mission Impossible."
    ],
    "📺 Séries": [
        "Les acteurs de 'Friends' gagnaient 1 million de dollars par épisode à la fin.",
        "Le Trône de Fer de Game of Thrones contient une épée de Gandalf (Seigneur des Anneaux).",
        "Walter White (Breaking Bad) porte des couleurs de plus en plus sombres à mesure que la série avance.",
        "Homer Simpson a travaillé dans plus de 188 métiers différents.",
        "Netflix a été créé parce que le fondateur en avait marre des pénalités de retard de ses DVD."
    ],
    "📚 Livres": [
        "J.K. Rowling a été rejetée par 12 maisons d'édition avant de publier Harry Potter.",
        "L'odeur des vieux livres a un nom : le 'biblichor'.",
        "Le livre le plus volé dans les bibliothèques publiques est le Guinness des Records.",
        "Agatha Christie a écrit ses meilleurs romans en mangeant des pommes dans son bain.",
        "Il existe un mot pour l'acte de sentir les livres : la 'bibliosmia'."
    ],
    "Autre": [
        "L'IA réfléchit... C'est plus long de trouver une pépite que de générer du texte !",
        "Patience, les meilleures choses ont besoin de temps (comme le bon vin).",
        "Le saviez-vous ? Les loutres se tiennent la main pour ne pas dériver en dormant."
    ]
}

# --- FONCTIONS ---
def get_ai_summary(title, author, mode):
    media_type = "jeu vidéo" if mode == "🎮 Jeux Vidéo" else "ouvrage/média"
    prompt = f"Fais un résumé très court (maximum 3 lignes) en français pour ce {media_type} : '{title}' par '{author}'. Style direct et accrocheur."
    try:
        response = model.generate_content(prompt)
        return response.text
    except:
        return "Résumé indisponible pour le moment."

def save_rejection(email, title, mode):
    if email:
        try:
            supabase.table("user_dislikes").insert({
                "user_email": email, "item_title": title, "category": mode
            }).execute()
        except: pass

def toggle_favorite_db(email, mode, title, current_status):
    new_status = not current_status
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").update({"is_favorite": new_status}).eq("user_email", email).eq("game_title", title).execute()
    else:
        supabase.table("user_media").update({"is_favorite": new_status}).eq("user_email", email).eq("title", title).eq("category", mode).execute()

def load_data(email, mode):
    try:
        if mode == "🎮 Jeux Vidéo":
            res = supabase.table("user_library").select("game_title, game_studio, rating, is_favorite").eq("user_email", email).execute()
            return [{'title': d['game_title'], 'author': d.get('game_studio', ''), 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
        else:
            res = supabase.table("user_media").select("title, author, rating, is_favorite").eq("user_email", email).eq("category", mode).execute()
            return [{'title': d['title'], 'author': d.get('author', ''), 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
    except: return []

def save_item(email, mode, title, author):
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").insert({"user_email": email, "game_title": title, "game_studio": author, "rating": 0}).execute()
    else:
        supabase.table("user_media").insert({"user_email": email, "title": title, "author": author, "category": mode, "rating": 0}).execute()

def update_rating_db(email, mode, title, note):
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").update({"rating": note}).eq("user_email", email).eq("game_title", title).execute()
    else:
        supabase.table("user_media").update({"rating": note}).eq("user_email", email).eq("title", title).eq("category", mode).execute()

def delete_item_db(email, mode, title):
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").delete().eq("user_email", email).eq("game_title", title).execute()
    else:
        supabase.table("user_media").delete().eq("user_email", email).eq("title", title).eq("category", mode).execute()

@lru_cache(maxsize=128)
def fetch_image_turbo(title, mode):
    try:
        t_out = 3
        if mode == "🎮 Jeux Vidéo":
            url = f"https://api.rawg.io/api/games?key=aaa189410c114919ab95e6a90ada62f1&search={urllib.parse.quote(title)}&page_size=1"
            r = requests.get(url, timeout=t_out).json()
            return r['results'][0]['background_image'] if r.get('results') else None
        elif mode in ["🎬 Films", "📺 Séries"]:
            stype = "tv" if mode == "📺 Séries" else "movie"
            url = f"https://api.themoviedb.org/3/search/{stype}?api_key={TMDB_API_KEY}&query={urllib.parse.quote(title)}"
            r = requests.get(url, timeout=t_out).json()
            if r.get('results') and r['results'][0].get('poster_path'):
                return f"https://image.tmdb.org/t/p/w500{r['results'][0]['poster_path']}"
        elif mode == "📚 Livres":
            try:
                search_term = urllib.parse.quote(title)
                apple_url = f"https://itunes.apple.com/search?term={search_term}&media=ebook&entity=ebook&limit=1"
                r = requests.get(apple_url, timeout=2).json()
                if r['resultCount'] > 0:
                    img_url = r['results'][0]['artworkUrl100']
                    return img_url.replace("100x100", "600x600")
            except: pass
            try:
                g_url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(title)}&maxResults=1"
                r = requests.get(g_url, timeout=2).json()
                if "items" in r:
                    img_links = r['items'][0]['volumeInfo'].get('imageLinks', {})
                    return img_links.get('extraLarge', img_links.get('large', img_links.get('thumbnail')))
            except: pass
        elif mode in ["🧧 Animés", "🎋 Mangas"]:
            mtype = "manga" if mode == "🎋 Mangas" else "anime"
            url = f"https://api.jikan.moe/v4/{mtype}?q={urllib.parse.quote(title)}&limit=1"
            r = requests.get(url, timeout=t_out).json()
            if r.get('data'):
                imgs = r['data'][0]['images']['jpg']
                return imgs.get('large_image_url', imgs.get('image_url'))
    except Exception as e:
        print(f"Erreur Image: {e}")
    return "https://placehold.co/400x600/1e293b/ffffff?text=Image+Non+Trouvée"

def get_smart_link(title, author, mode):
    query = f"{title}"
    query_encoded = urllib.parse.quote(query)
    if mode == "🎮 Jeux Vidéo":
        return f"https://www.instant-gaming.com/fr/rechercher/?q={query_encoded}&igr={INSTANT_GAMING_ID}"
    elif mode in ["📚 Livres", "🎋 Mangas", "📚 Livres & Mangas"]:
        if author: query_encoded = urllib.parse.quote(f"{title} {author}")
        return f"https://www.amazon.fr/s?k={query_encoded}&tag={AMAZON_PARTNER_ID}&linkCode=ll2"
    elif mode in ["🎬 Films", "📺 Séries"]:
        return f"https://www.amazon.fr/gp/video/search/ref=atv_nb_sr?phrase={query_encoded}&ie=UTF8&tag={AMAZON_PARTNER_ID}"
    return f"https://www.amazon.fr/s?k={query_encoded}&tag={AMAZON_PARTNER_ID}"

# --- FONCTION PRINCIPALE (MAIN) POUR PROTEGER L'EXECUTION ---
def main():
    # CSS
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap');
    html, body, [data-testid="stAppViewContainer"] { background-color: #0B1120 !important; color: #FFFFFF; font-family: 'Inter', sans-serif; }
    a[href*="streamlit.io"] { display: none !important; }
    [data-testid="stSidebar"] { background-color: #111827 !important; min-width: 310px !important; }
    [data-testid="stSidebar"] h1 { font-size: 34px !important; color: #3B82F6 !important; font-weight: 900 !important; text-transform: uppercase; }
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stSubheader p { font-size: 22px !important; font-weight: 800 !important; color: #FFFFFF !important; margin-top: 20px !important; }
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] label p { font-size: 20px !important; color: #FFFFFF !important; font-weight: 700 !important; }
    .deal-card { background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.4); border-radius: 15px; padding: 15px; margin-bottom: 12px; }
    .deal-title { color: #3B82F6; font-weight: 800; font-size: 16px; }
    .paypal-button { background: linear-gradient(135deg, #0070BA 0%, #1546a0 100%); color: white !important; padding: 15px; border-radius: 15px; text-align: center; font-weight: 800; display: block; text-decoration: none; box-shadow: 0 4px 15px rgba(0, 112, 186, 0.4); }
    [data-testid="stAppViewContainer"]::before { content: "⬅️ MENU"; position: fixed; top: 16px; left: 55px; background-color: #3B82F6; color: white; padding: 4px 8px; border-radius: 6px; font-size: 10px; font-weight: 800; z-index: 9999999; pointer-events: none; box-shadow: 0 2px 5px rgba(0,0,0,0.3); animation: bounce 2s infinite; }
    @media (min-width: 768px) { [data-testid="stAppViewContainer"]::before { content: "⬅️ OUVRIR LE MENU"; top: 25px; left: 90px; padding: 6px 12px; font-size: 12px; } }
    @keyframes bounce { 0%, 100% { transform: translateX(0); } 50% { transform: translateX(5px); } }
    .logo-container { display: flex; align-items: center; justify-content: center; gap: 15px; margin-bottom: 25px; }
    .logo-icon { background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%); width: 50px; height: 50px; border-radius: 14px; display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 28px; color: white; }
    .logo-text { font-size: 28px; font-weight: 900; color: white; }
    button[data-baseweb="tab"] { background-color: transparent !important; border: none !important; border-bottom: 2px solid rgba(255,255,255,0.1) !important; margin-right: 20px !important; padding: 10px 0 !important; }
    button[data-baseweb="tab"] p { font-size: 18px !important; font-weight: 700 !important; color: #94A3B8 !important; }
    button[aria-selected="true"] { border-bottom: 3px solid #3B82F6 !important; }
    button[aria-selected="true"] p { color: #FFFFFF !important; }
    .stButton>button { background: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%) !important; color: #FFFFFF !important; border: none !important; border-radius: 12px !important; height: 50px !important; font-weight: 800 !important; text-shadow: 0 1px 2px rgba(0,0,0,0.3); box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important; }
    footer {display: none !important;} [data-testid="stHeader"] {background: transparent !important;}
    </style>
    """, unsafe_allow_html=True)

    # INITIALISATION ÉTATS
    if 'user_email' not in st.session_state: st.session_state.user_email = None
    if 'seen_items' not in st.session_state: st.session_state.seen_items = []
    if 'current_recos' not in st.session_state: st.session_state.current_recos = None
    if 'last_query' not in st.session_state: st.session_state.last_query = ""

    # SIDEBAR
    with st.sidebar:
        st.markdown('<h1 style="color:#3B82F6; font-size:34px; font-weight:900; margin-bottom:20px;">MENU</h1>', unsafe_allow_html=True)
        app_mode = st.radio("Catégorie", ["🎮 Jeux Vidéo", "🎬 Films", "📺 Séries", "🧧 Animés", "🎋 Mangas", "📚 Livres"], key="final_category_radio")
        st.write("---")
        st.markdown('<p style="color:white; font-size:22px; font-weight:800; margin-bottom:10px;">🎁 Offres du moment</p>', unsafe_allow_html=True)
        if app_mode in ["📚 Livres", "🎋 Mangas"]:
            st.markdown(f'<div class="deal-card"><div style="color:#3B82F6; font-weight:800;">📖 Kindle Unlimited</div><a style="color:white; text-decoration:none; font-size:14px;" href="https://www.amazon.fr/kindle-dbs/hz/signup?tag={AMAZON_PARTNER_ID}" target="_blank">👉 Essai gratuit ici</a></div>', unsafe_allow_html=True)
        elif app_mode in ["🎬 Films", "📺 Séries"]:
            st.markdown(f'<div class="deal-card"><div style="color:#3B82F6; font-weight:800;">🍿 Prime Video</div><a style="color:white; text-decoration:none; font-size:14px;" href="https://www.primevideo.com/?tag={AMAZON_PARTNER_ID}" target="_blank">👉 30 jours gratuits</a></div>', unsafe_allow_html=True)
        st.write("---")
        
        if app_mode == "🎮 Jeux Vidéo":
            selected_platform = st.selectbox("Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"], key="final_plat")
        elif app_mode == "📚 Livres":
            selected_genre = st.selectbox("Style", ["Général", "Dark Romance", "New Romance / New Adult", "Thriller / Policier", "SF/Fantasy", "Horreur / Frisson", "Développement Personnel", "Business / Finance", "Biographie / Vécu", "Classiques / Littérature", "Jeunesse / Young Adult" ], key="final_style")
        
        st.write("---")
        if not st.session_state.user_email:
            email = st.text_input("Votre Email", key="sidebar_mail_input")
            if st.button("Se connecter", key="sidebar_login_btn"):
                st.session_state.user_email = email
                st.rerun()
        else:
            st.write(f"Connecté : **{st.session_state.user_email}**")
            if st.button("Déconnexion", key="sidebar_logout_btn"): st.session_state.user_email = None; st.rerun()
        
        st.write("---")
        st.markdown('<p style="color:white; font-size:22px; font-weight:800;">💙 Soutenir</p>', unsafe_allow_html=True)
        st.markdown(f'<a href="https://www.paypal.me/TheShortlistApp" target="_blank" class="paypal-button" style="background:#0070BA; color:white; padding:12px; border-radius:10px; display:block; text-align:center; text-decoration:none; font-weight:bold;">☕ Offrir un café (PayPal)</a>', unsafe_allow_html=True)
        
        with st.expander("⚖️ Légal"):
            st.caption("The Shortlist est un curateur IA. Partenaire Amazon (bénéfices sur achats affiliés).")

    # LOGIQUE PRINCIPALE
    raw_label = app_mode.split(" ")[1]
    media_label = raw_label.rstrip('s') if raw_label.endswith('s') else raw_label
    tab_search, tab_lib = st.tabs(["🔎 Trouver", "📚 Ma Liste"])

    with tab_search:
        st.markdown("""<div class="logo-container"><div class="logo-icon">3</div><div class="logo-text">THE SHORTLIST</div></div>""", unsafe_allow_html=True)
        
        c_filters = st.columns([1, 2, 1])
        with c_filters[1]:
            selected_platform = "Toutes plateformes"
            selected_genre = "Général"
            if app_mode == "🎮 Jeux Vidéo":
                selected_platform = st.selectbox("🎮 Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"], label_visibility="collapsed")
            elif app_mode == "📚 Livres":
                selected_genre = st.selectbox("📖 Style de lecture", ["Général", "Dark Romance", "New Romance / New Adult", "Thriller / Policier", "Fantasy / Science-Fiction", "Horreur / Frisson", "Développement Personnel", "Business / Finance", "Biographie / Vécu", "Classiques / Littérature", "Jeunesse / Young Adult"], label_visibility="collapsed")
                if selected_genre == "Dark Romance": st.caption("✨ Mode 'Dark Romance' activé.")
        
        query = st.text_input("Recherche", placeholder=f"Ex: Un {media_label.lower()} qui ressemble à...", label_visibility="collapsed", key="main_search_input")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("🔎 TROUVER", use_container_width=True, key="search_btn"):
                st.session_state.last_query = query
                st.session_state.current_recos = None
        with b2:
            if st.button("🎲 SURPRENDS-MOI", use_container_width=True, key="surprise_btn"):
                st.session_state.last_query = f"Une pépite de type {media_label.lower()} méconnue"
                st.session_state.current_recos = None

        with st.expander("ℹ️ Comment utiliser The Shortlist ?"):
            st.markdown("Bienvenue ! Choisissez votre univers à gauche, décrivez votre envie, et laissez l'IA trouver 3 pépites.")

        # --- MOTEUR DE RECHERCHE IA ---
        if st.session_state.last_query and st.session_state.current_recos is None:
            limit_date = (datetime.datetime.now() - datetime.timedelta(days=14)).isoformat()
            lib = load_data(st.session_state.user_email, app_mode) if st.session_state.user_email else []
            historical_dislikes = []
            if st.session_state.user_email:
                try:
                    res_dis = supabase.table("user_dislikes").select("item_title").eq("user_email", st.session_state.user_email).gt("created_at", limit_date).execute()
                    historical_dislikes = [d['item_title'] for d in res_dis.data]
                except: pass
            exclude_list = list(set(st.session_state.seen_items + historical_dislikes))
            
            # PROMPT
            media_clean = app_mode.split(" ")[1]
            if "Jeux" in app_mode: media_clean = "Jeux Vidéo"
            if "Séries" in app_mode: role_def, author_label = "Expert en SÉRIES TV.", "le créateur"
            elif "Films" in app_mode: role_def, author_label = "Expert en CINÉMA.", "le réalisateur"
            elif "Jeux" in app_mode: role_def, author_label = "Expert en GAMING.", "le studio"
            else: role_def, author_label = f"Expert en {media_clean}.", "l'auteur"

            prompt = f"""
            RÔLE : {role_def}
            MISSION : L'utilisateur cherche "{st.session_state.last_query}".
            CONTEXTE : Catégorie {app_mode.upper()} | Style {selected_genre}.
            
            🧠 PROTOCOLE D'ANALYSE :
            1. Connais-tu "{st.session_state.last_query}" ?
               - OUI -> 3 œuvres SIMILAIRES.
               - NON -> IGNORE le titre, propose 3 pépites du genre "{selected_genre}".
            
            ⛔ EXCLUSIONS :
            1. ANTI-PERROQUET : Ne propose JAMAIS "{st.session_state.last_query}".
            2. ANTI-FRANCHISE : Pas de suites/spin-offs.
            3. CATÉGORIE STRICTE : Uniquement {app_mode}.
            
            INSTRUCTIONS :
            1. CIBLE : {selected_genre}.
            2. RÉALISME : Œuvres existantes en France.
            3. JSON : Champ "auteur" = {author_label}.
            
            FORMAT JSON :
            [ {{ "titre": "Titre exact", "auteur": "Nom ({author_label})", "badge": "Badge court", "desc": "Argumentaire court." }} ]
            """
            
            # ANIMATION
            loader_placeholder = st.empty()
            executor = ThreadPoolExecutor()
            future = executor.submit(model.generate_content, prompt)
            
            current_facts = LOADING_FACTS.get(app_mode, LOADING_FACTS["Autre"])
            fact_index = 0
            loading_gif = "https://media.giphy.com/media/xT9IgzoKnwFNmISR8I/giphy.gif"
            
            while not future.done():
                if fact_index % 4 == 0 and fact_index > 0:
                    fact, prefix, color = PROMO_FACTS[fact_index % len(PROMO_FACTS)], "💸 BON PLAN PARTENAIRE", "#10B981"
                else:
                    fact = current_facts[fact_index % len(current_facts)] if len(current_facts) > 0 else "Recherche en cours..."
                    prefix, color = "⚡ ANALYSE EN COURS...", "#3B82F6"

                html_content = f"""
                <div style="background-color: #111827; border: 2px solid {color}; border-radius: 15px; padding: 30px; text-align: center; margin-top: 20px; box-shadow: 0 0 30px rgba(59, 130, 246, 0.2);">
                    <h3 style="color: {color}; font-weight: 900; margin-bottom: 25px; letter-spacing: 1px;">{prefix}</h3>
                    <img src="{loading_gif}" style="width: 250px; border-radius: 8px; margin-bottom: 25px; opacity: 0.9;">
                    <div style="min-height: 90px; display: flex; align-items: center; justify-content: center; background: rgba(255,255,255,0.05); border-radius: 10px; padding: 15px;">
                        <p style="color: white; font-size: 17px; font-style: italic; font-weight: 500; line-height: 1.4;">" {fact} "</p>
                    </div>
                    <div style="margin-top: 15px;">
                         <div style="width: 100%; height: 4px; background: #374151; border-radius: 2px; overflow: hidden;">
                            <div style="width: 50%; height: 100%; background: {color}; animation: loading-bar 2s infinite ease-in-out;"></div>
                        </div>
                        <p style="color: #6B7280; font-size: 11px; margin-top: 8px; text-transform: uppercase; font-weight: bold;">Recherche dans la base de données...</p>
                    </div>
                </div>
                <style>@keyframes loading-bar {{ 0% {{ transform: translateX(-100%); }} 50% {{ transform: translateX(100%); }} 100% {{ transform: translateX(-100%); }} }}</style>
                """
                with loader_placeholder.container(): st.markdown(html_content, unsafe_allow_html=True)
                time.sleep(3.5)
                fact_index += 1
            
            try:
                response = future.result()
                executor.shutdown(wait=False)
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                if json_match:
                    recos = json.loads(json_match.group())
                    loader_placeholder.markdown("<p style='text-align:center; color:#3B82F6;'>✅ Analyse terminée. Récupération des visuels...</p>", unsafe_allow_html=True)
                    with ThreadPoolExecutor(max_workers=3) as img_executor:
                        titles = [r['titre'] for r in recos]
                        image_results = list(img_executor.map(lambda t: fetch_image_turbo(t, app_mode), titles))
                    for i, r in enumerate(recos): r['img'] = image_results[i]
                    st.session_state.current_recos = recos
                    loader_placeholder.empty()
                    st.rerun()
                else:
                    loader_placeholder.error("Erreur de format IA. Réessaie !")
                    time.sleep(2)
                    st.session_state.current_recos = None
                    st.rerun()
            except Exception as e:
                loader_placeholder.error(f"Erreur technique : {e}")

        # AFFICHAGE RÉSULTATS
        if st.session_state.current_recos:
            st.write("---")
            cols = st.columns(3)
            st.markdown("""<div style="background: rgba(59, 130, 246, 0.1); border-radius: 12px; padding: 20px; text-align: center; margin-top: 30px; border: 1px dashed rgba(59, 130, 246, 0.3);"><p style="color: white; font-weight: 600; margin-bottom: 10px;">❤️ Cette recommandation vous a plu ?</p><p style="color: #9CA3AF; font-size: 14px; margin-bottom: 15px;">The Shortlist est gratuit et sans pub intrusive. Si vous avez découvert une pépite grâce à nous, le meilleur moyen de soutenir le projet est d'utiliser nos liens partenaires ou d'offrir un café !</p><div style="display: flex; justify-content: center; gap: 15px; flex-wrap: wrap;"><a href="https://www.paypal.me/TheShortlistApp" target="_blank" style="text-decoration: none; background: #0070BA; color: white; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 14px;">☕ Offrir un Café</a><a href="https://www.instant-gaming.com/?igr=theshortlistapp" target="_blank" style="text-decoration: none; background: #FF5400; color: white; padding: 8px 16px; border-radius: 20px; font-weight: bold; font-size: 14px;">🎮 Jeux -70% (Instant Gaming)</a></div></div>""", unsafe_allow_html=True)
            
            for i, item in enumerate(st.session_state.current_recos):
                with cols[i]:
                    auteur_item = item.get('auteur', '')
                    affiliate_link = get_smart_link(item['titre'], auteur_item, app_mode)
                    img_url = item['img'] if item['img'] else "https://placehold.co/400x600"
                    if app_mode == "🎮 Jeux Vidéo": btn_text, btn_color = "🎮 VOIR PRIX (INSTANT GAMING)", "#FF5400"
                    elif app_mode in ["🎬 Films", "📺 Séries"]: btn_text, btn_color = "🍿 VOIR SUR PRIME VIDEO", "#00A8E1"
                    else: btn_text, btn_color = "🛒 VOIR SUR AMAZON", "#FF9900"
                    
                    st.markdown(f"""
                        <div class="game-card" style="position: relative; background: rgba(255,255,255,0.05); padding: 20px; border-radius: 15px; border: 1px solid rgba(255,255,255,0.1);">
                            <div style="position: absolute; top: 10px; right: 10px; background: #3B82F6; color: white; padding: 4px 12px; border-radius: 20px; font-size: 0.7rem; font-weight: 900; z-index: 10;">{item.get('badge', '⭐ Sélection')}</div>
                            <img src="{img_url}" style="width:100%; height:250px; object-fit:cover; border-radius:15px;">
                            <div style="font-weight:800; margin-top:15px; font-size:1.1rem; color:white;">{item['titre']}</div>
                            <div style="color:#3B82F6; font-size:0.8rem; font-weight:700;">{item.get('auteur', '')}</div>
                            <div style="color:rgba(255,255,255,0.6); font-size:0.85rem; margin-top:10px; height: 60px; overflow: hidden;">{item['desc']}</div>
                            <a href="{affiliate_link}" target="_blank" style="display: block; text-align: center; background: {btn_color}; color: white; text-decoration: none; padding: 12px; border-radius: 12px; margin-top: 15px; font-weight: 800; font-size: 0.9rem; box-shadow: 0 4px 6px rgba(0,0,0,0.2);">{btn_text}</a>
                        </div>
                    """, unsafe_allow_html=True)
                    
                    with st.expander("📖 Synopsis & Détails"):
                        st.write(f"Découvrez l'univers de **{item['titre']}**. Un choix incontournable pour les amateurs du genre.")
                        st.markdown(f"[🔍 En savoir plus](https://www.google.com/search?q={urllib.parse.quote(f'{item['titre']} {auteur_item} synopsis français')})")
                    
                    if st.button(f"❌ Pas pour moi", key=f"rej_{i}", use_container_width=True):
                        save_rejection(st.session_state.user_email, item['titre'], app_mode)
                        st.session_state.seen_items.append(item['titre'])
                        with st.spinner("Recherche d'une autre pépite..."):
                            exclude_updated = ", ".join(st.session_state.seen_items)
                            replace_prompt = f"RÔLE : Curateur expert en {app_mode} ({selected_genre}). MISSION : Propose 1 SEULE nouvelle pépite différente de : {exclude_updated}. RÈGLES : Français uniquement. FORMAT JSON : {{\"titre\": \"...\", \"auteur\": \"...\", \"desc\": \"...\"}}"
                            try:
                                resp = model.generate_content(replace_prompt)
                                match = re.search(r'\{.*\}', resp.text, re.DOTALL)
                                if match:
                                    new_data = json.loads(match.group())
                                    new_data['img'] = fetch_image_turbo(new_data['titre'], app_mode)
                                    st.session_state.current_recos[i] = new_data
                                    st.rerun()
                            except: st.toast("⚠️ Petit hoquet de l'IA, réessayez !")
                    
                    if st.button(f"✅ J'y ai joué/vu", key=f"p_{i}", use_container_width=True):
                        if st.session_state.user_email: save_item(st.session_state.user_email, app_mode, item['titre'], item.get('auteur', ''))
                        st.session_state.seen_items.append(item['titre'])
                        st.session_state.current_recos = None
                        st.rerun()
            
            st.write("---")
            _, c_reload, _ = st.columns([1, 2, 1])
            with c_reload:
                if st.button("🔄 Proposer 3 autres options", use_container_width=True):
                    for item in st.session_state.current_recos: st.session_state.seen_items.append(item['titre'])
                    st.session_state.current_recos = None
                    st.rerun()

    with tab_lib:
        if not st.session_state.user_email: st.info("Connectez-vous pour voir votre collection personnelle.")
        else:
            full_data = load_data(st.session_state.user_email, app_mode)
            st.markdown('<p style="font-size:26px; font-weight:900; color:#3B82F6;">📊 MES STATS</p>', unsafe_allow_html=True)
            total_items, fav_count = len(full_data), len([g for g in full_data if g.get('fav')])
            avg_rating = sum([g['rating'] for g in full_data]) / total_items if total_items > 0 else 0
            c1, c2, c3 = st.columns(3)
            with c1: st.metric("Titres", total_items)
            with c2: st.metric("Coups de cœur", fav_count)
            with c3: st.metric("Note moyenne", f"{avg_rating:.1f}/5")
            
            st.write("---")
            st.markdown('<p style="font-size:26px; font-weight:900; color:#FF3366; margin-bottom:20px;">❤️ MES COUPS DE CŒUR</p>', unsafe_allow_html=True)
            absolute_favs = [g for g in full_data if g.get('fav')]
            if absolute_favs:
                fav_cols = st.columns(5)
                for idx, g in enumerate(absolute_favs[:5]):
                    with fav_cols[idx]:
                        img_fav = fetch_image_turbo(g['title'], app_mode)
                        st.markdown(f"<div style='text-align:center; margin-bottom:20px;'><img src='{img_fav}' style='width:100%; height:140px; object-fit:cover; border-radius:10px; border:2px solid #FF3366;'><div style='font-weight:800; font-size:0.8rem; margin-top:5px; color:white; height:35px; overflow:hidden;'>{g['title']}</div></div>", unsafe_allow_html=True)
            else: st.caption("Aucun coup de cœur.")
            
            st.write("---")
            st.markdown('<p style="font-size:26px; font-weight:900; color:#3B82F6; margin-bottom:20px;">📚 MA COLLECTION</p>', unsafe_allow_html=True)
            search_lib = st.text_input("🔍 Rechercher un titre sauvegardé...", key="lib_search_input")
            filtered_data = [d for d in full_data if search_lib.lower() in d['title'].lower()]
            if not filtered_data: st.info("Votre bibliothèque est vide ou aucun titre ne correspond.")
            else:
                lib_cols = st.columns(3)
                for idx, g in enumerate(filtered_data):
                    with lib_cols[idx % 3]:
                        img_lib = fetch_image_turbo(g['title'], app_mode)
                        st.markdown(f"<div style='background:rgba(255,255,255,0.05); padding:15px; border-radius:15px; border:1px solid rgba(255,255,255,0.1); margin-bottom:10px;'><img src='{img_lib}' style='width:100%; height:180px; object-fit:cover; border-radius:10px;'><div style='font-weight:800; margin-top:10px; color:white;'>{g['title']}</div><div style='color:#3B82F6; font-size:0.8rem; font-weight:700;'>{g.get('author', 'Auteur inconnu')}</div></div>", unsafe_allow_html=True)
                        if st.button("📝 Résumé IA", key=f"sum_{idx}_{g['title']}", use_container_width=True):
                            with st.spinner("Analyse de l'IA..."): st.info(get_ai_summary(g['title'], g.get('author', ''), app_mode))
                        c_btn1, c_btn2, c_btn3 = st.columns([1, 2, 1])
                        with c_btn1:
                            if st.button("❤️" if g.get('fav') else "🤍", key=f"lib_fav_{idx}_{g['title']}"):
                                toggle_favorite_db(st.session_state.user_email, app_mode, g['title'], g.get('fav', False))
                                st.rerun()
                        with c_btn2:
                            new_note = st.select_slider("Note", options=[0,1,2,3,4,5], value=g['rating'], key=f"lib_r_{idx}_{g['title']}", label_visibility="collapsed")
                            if new_note != g['rating']:
                                update_rating_db(st.session_state.user_email, app_mode, g['title'], new_note)
                                st.rerun()
                        with c_btn3:
                            if st.button("🗑️", key=f"lib_del_{idx}_{g['title']}"):
                                delete_item_db(st.session_state.user_email, app_mode, g['title'])
                                st.rerun()

# --- POINT D'ENTRÉE SÉCURISÉ (AIRBAG) ---
if __name__ == "__main__":
    import datetime
    try:
        main()
    except Exception as e:
        # C'est ici que l'erreur 500 est capturée
        # Si ça plante (à cause de la perte de session mobile), on recharge
        print(f"Erreur attrapée : {e}")
        st.session_state.clear()
        st.rerun()
