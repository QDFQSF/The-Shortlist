import streamlit as st
import google.generativeai as genai
import json, urllib.parse, re, requests
from supabase import create_client, Client
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# --- 1. CONFIGURATION ---
AMAZON_PARTNER_ID = "theshorlistap-21"
INSTANT_GAMING_ID = "theshortlistapp"
SUPABASE_URL = "https://enkgnmxqvnpvqceueayg.supabase.co"
SUPABASE_KEY = "sb_secret_mNz02Qr2x9SnGMqIPtxRaw_GUK0f9Hd"
TMDB_API_KEY = "53f9c070d843a78f4f16579e57bdda32" 

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# --- CONFIGURATION SÉCURISÉE ---
# On essaie de lire les secrets (pour le Web), sinon on prend la valeur locale (pour ton PC)
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except:
    api_key = "" # Uniquement pour tes tests locaux

genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name="gemini-3-flash-preview") # Version stable et rapide

st.set_page_config(page_title="The Shortlist", page_icon="📑", layout="wide")

# INITIALISATION DES ÉTATS
if 'user_email' not in st.session_state: st.session_state.user_email = None
if 'seen_items' not in st.session_state: st.session_state.seen_items = []
if 'current_recos' not in st.session_state: st.session_state.current_recos = None
if 'last_query' not in st.session_state: st.session_state.last_query = ""

# --- 2. FONCTIONS DE BASE DE DONNÉES (CORRIGÉES) ---

def load_data(email, mode):
    """Charge les données en incluant le statut favori [cite: 2026-01-06]"""
    try:
        if mode == "🎮 Jeux Vidéo":
            res = supabase.table("user_library").select("game_title, rating, is_favorite").eq("user_email", email).execute()
            return [{'title': d['game_title'], 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
        else:
            res = supabase.table("user_media").select("title, rating, is_favorite").eq("user_email", email).eq("category", mode).execute()
            return [{'title': d['title'], 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
    except: return []

def toggle_favorite_db(email, mode, title, current_status):
    """Bascule le statut favori (All-time) [cite: 2026-01-06]"""
    new_status = not current_status
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").update({"is_favorite": new_status}).eq("user_email", email).eq("game_title", title).execute()
    else:
        supabase.table("user_media").update({"is_favorite": new_status}).eq("user_email", email).eq("title", title).eq("category", mode).execute()

def save_item(email, mode, title):
    """Enregistre le titre proprement selon le mode [cite: 2026-01-06]"""
    if mode == "🎮 Jeux Vidéo":
        # Pour les jeux, on n'envoie PAS de catégorie
        supabase.table("user_library").insert({
            "user_email": email, 
            "game_title": title, 
            "rating": 0
        }).execute()
    else:
        # Pour le reste, on précise la catégorie (Film, Livre, etc.) [cite: 2026-01-06]
        supabase.table("user_media").insert({
            "user_email": email, 
            "title": title, 
            "category": mode, 
            "rating": 0
        }).execute()

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

# --- 3. RÉCUPÉRATION DES IMAGES (HD & PRO) ---

@lru_cache(maxsize=128)
def fetch_image_hd(title, mode):
    """Récupère des images haute définition via des APIs spécialisées"""
    try:
        if mode == "🎮 Jeux Vidéo":
            url = f"https://api.rawg.io/api/games?key=aaa189410c114919ab95e6a90ada62f1&search={urllib.parse.quote(title)}&page_size=1"
            r = requests.get(url, timeout=5).json()
            return r['results'][0]['background_image'] if r.get('results') else None

        elif mode in ["🎬 Films", "📺 Séries"]:
            stype = "tv" if mode == "📺 Séries" else "movie"
            url = f"https://api.themoviedb.org/3/search/{stype}?api_key={TMDB_API_KEY}&query={urllib.parse.quote(title)}"
            r = requests.get(url).json()
            if r.get('results') and r['results'][0].get('poster_path'):
                return f"https://image.tmdb.org/t/p/w500{r['results'][0]['poster_path']}"

        elif mode in ["🧧 Animés", "🎋 Mangas"]:
            # Jikan API pour des affiches d'animés HD
            mtype = "manga" if mode == "🎋 Mangas" else "anime"
            url = f"https://api.jikan.moe/v4/{mtype}?q={urllib.parse.quote(title)}&limit=1"
            r = requests.get(url).json()
            if r.get('data'):
                return r['data'][0]['images']['jpg']['large_image_url']

        # --- LIVRES (GOOGLE BOOKS AVEC HD HACK) ---
        elif mode == "📚 Livres":
            # On tente Google Books avec forçage de résolution
            url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(title)}&maxResults=1"
            r = requests.get(url, timeout=5).json()
            if r.get('items'):
                volume_info = r['items'][0]['volumeInfo']
                img_links = volume_info.get('imageLinks', {})
                # On cherche la plus grande taille disponible
                img = img_links.get('extraLarge') or img_links.get('large') or img_links.get('medium') or img_links.get('thumbnail')
                
                if img:
                    # HD HACK : On force le HTTPS, on enlève les bords 'curl' et on booste le zoom
                    img = img.replace("http://", "https://")
                    img = img.replace("&edge=curl", "") 
                    img = img.replace("zoom=1", "zoom=3") 
                    return img
            
            # FALLBACK : Open Library HD
            url = f"https://openlibrary.org/search.json?title={urllib.parse.quote(title)}&limit=1"
            r = requests.get(url).json()
            if r.get('docs') and r['docs'][0].get('cover_i'):
                return f"https://covers.openlibrary.org/b/id/{r['docs'][0]['cover_i']}-L.jpg"
    except: pass
    return None

def get_all_images_parallel(titles, mode):
    with ThreadPoolExecutor() as executor:
        return list(executor.map(lambda t: fetch_image_hd(t, mode), titles))

def get_smart_link(title, mode):
    """Lien d'affiliation Instant Gaming corrigé avec le slash obligatoire"""
    # quote_plus transforme les espaces en '+' pour le moteur de recherche
    query = urllib.parse.quote_plus(title)
    
    # 🎮 JEUX VIDÉO : Note bien le "/" AVANT le "?" c'est lui qui évite le 404
    if mode == "🎮 Jeux Vidéo":
        return f"https://www.instant-gaming.com/fr/recherche/?q={query}&igr=theshortlistapp"
    
    # Reste du code Amazon (qui est déjà bon selon tes tests)
    query_amazon = urllib.parse.quote(title)
    if mode in ["📚 Livres", "🎋 Mangas", "🎬 Films", "📺 Séries"]:
        return f"https://www.amazon.fr/s?k={query_amazon}&tag=theshorlistap-21"
    
    return f"https://www.google.com/search?q={query_amazon}"

# --- 4. DESIGN (STYLE PREMIUM & HAUTE VISIBILITÉ) ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap');
    
    html, body, [data-testid="stAppViewContainer"] {
        background-color: #0B1120 !important; color: #FFFFFF; font-family: 'Inter', sans-serif;
    }

    /* --- SIDEBAR (MENU) : TEXTES BLANCS --- */
    [data-testid="stSidebar"] { background-color: #111827 !important; min-width: 310px !important; }
    [data-testid="stSidebar"] h1 { font-size: 34px !important; color: #3B82F6 !important; font-weight: 900 !important; text-transform: uppercase; }
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stSubheader p {
        font-size: 22px !important; font-weight: 800 !important; color: #FFFFFF !important; margin-top: 20px !important;
    }
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] label p {
        font-size: 20px !important; color: #FFFFFF !important; font-weight: 700 !important;
    }

    /* --- CARTES D'OFFRES & PAYPAL --- */
    .deal-card {
        background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.4);
        border-radius: 15px; padding: 15px; margin-bottom: 12px;
    }
    .deal-title { color: #3B82F6; font-weight: 800; font-size: 16px; }
    .deal-link { color: #FFFFFF !important; text-decoration: none; font-weight: 600; }
    
    .paypal-button {
        background: linear-gradient(135deg, #0070BA 0%, #1546a0 100%);
        color: white !important; padding: 15px; border-radius: 15px;
        text-align: center; font-weight: 800; display: block; text-decoration: none;
        box-shadow: 0 4px 15px rgba(0, 112, 186, 0.4);
    }

    /* --- LOGO --- */
    .logo-container { display: flex; align-items: center; justify-content: center; gap: 15px; margin-bottom: 25px; }
    .logo-icon {
        background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
        width: 50px; height: 50px; border-radius: 14px;
        display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 28px; color: white;
    }
    .logo-text { font-size: 28px; font-weight: 900; color: white; }

    /* --- ONGLETS (TABS) : PLUS DE CADRES MOCHES --- */
    button[data-baseweb="tab"] {
        background-color: transparent !important; border: none !important;
        border-bottom: 2px solid rgba(255,255,255,0.1) !important;
        margin-right: 20px !important; padding: 10px 0 !important;
    }
    button[data-baseweb="tab"] p {
        font-size: 18px !important; font-weight: 700 !important; color: #94A3B8 !important;
    }
    button[aria-selected="true"] {
        border-bottom: 3px solid #3B82F6 !important;
    }
    button[aria-selected="true"] p {
        color: #FFFFFF !important;
    }
    /* --- BOUTONS : COULEUR ET VISIBILITÉ --- */
    .stButton>button {
        background: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%) !important;
        color: #FFFFFF !important; border: none !important;
        border-radius: 12px !important; height: 50px !important;
        font-weight: 800 !important; text-shadow: 0 1px 2px rgba(0,0,0,0.3);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important;
    }

    footer {display: none !important;} [data-testid="stHeader"] {background: transparent !important;}
    </style>
""", unsafe_allow_html=True)

# --- 5. SIDEBAR (UN SEUL BLOC SANS DOUBLONS) ---
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
        selected_genre = st.selectbox("Style", ["Général", "Dark Romance", "Thriller", "SF/Fantasy"], key="final_style")

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

# --- 6. TABS & LOGO ---
raw_label = app_mode.split(" ")[1]
media_label = raw_label.rstrip('s') if raw_label.endswith('s') else raw_label
tab_search, tab_lib = st.tabs(["🔎 Trouver", "📚 Ma Liste"])

with tab_search:
    # RE-INSERTION DU LOGO ICI
    st.markdown("""
    <div class="logo-container">
        <div class="logo-icon">3</div>
        <div class="logo-text">THE SHORTLIST</div>
    </div>
    """, unsafe_allow_html=True)

    # --- NOUVEAU : FILTRES ACCESSIBLES AU CENTRE ---
    c_filters = st.columns([1, 2, 1])
    with c_filters[1]:
        # Initialisation par défaut
        selected_platform = "Toutes plateformes"
        selected_genre = "Général"
        
        # Filtre dynamique selon le mode choisi [cite: 2026-01-04]
        if app_mode == "🎮 Jeux Vidéo":
            selected_platform = st.selectbox("🎮 Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"], label_visibility="collapsed")
        elif app_mode == "📚 Livres":
            selected_genre = st.selectbox("📖 Style de lecture", [
                "Général", "Dark Romance", "Thriller / Policier", 
                "Fantasy / Science-Fiction", "Développement Personnel", "Classiques"
            ], label_visibility="collapsed")
            # Petit rappel visuel du genre choisi pour ta femme !
            if selected_genre == "Dark Romance":
                st.caption("✨ Mode 'Dark Romance' activé pour des pépites passionnelles.")

    # --- BARRE DE RECHERCHE DYNAMIQUE ---
    query = st.text_input(
        "Recherche", 
        placeholder=f"Ex: Un {media_label.lower()} qui ressemble à...", 
        label_visibility="collapsed", 
        key="main_search_input"
    )
    
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔎 TROUVER", use_container_width=True, key="search_btn"):
            st.session_state.last_query = query
            st.session_state.current_recos = None
    with b2:
        if st.button("🎲 SURPRENDS-MOI", use_container_width=True, key="surprise_btn"):
            st.session_state.last_query = f"Une pépite de type {media_label.lower()} méconnue"
            st.session_state.current_recos = None

# --- NOTE EXPLICATIVE ---
    with st.expander("ℹ️ Comment utiliser The Shortlist ?"):
        st.markdown("""
        ### Bienvenue dans votre moteur de recommandation d'élite ! 🎯
        
        Contrairement aux autres plateformes, **The Shortlist** ne vous propose que **3 options ultra-ciblées** pour vous éviter la fatigue du choix.
        
        **1. Choisissez votre univers** : Utilisez le menu à gauche pour basculer entre Jeux, Films, Livres, etc.
        
        **2. Soyez précis** : Dans la barre de recherche, décrivez une ambiance ou un style précis (ex: *"Un livre de Dark Romance sombre"* ou *"Un jeu d'aventure comme Zelda"* ou *"Ecrivez simplement votre titre préféré et l'ia vous fera une recommendation qui y ressemble"*).
        
        **3. L'IA apprend de vous** : 
        * Cliquez sur **"J'y ai joué/vu"** pour ajouter un titre à votre bibliothèque.
        * **Notez vos favoris (4 ou 5 étoiles)** : L'IA analysera automatiquement ces titres pour affiner ses prochaines suggestions selon vos goûts réels.
        
        **4. Mode "Surprends-moi"** : En panne d'inspiration ? Laissez l'IA dénicher une pépite méconnue pour vous.
        """)

    # --- LOGIQUE IA (Section 6) ---
    if st.session_state.last_query and st.session_state.current_recos is None:
        lib = load_data(st.session_state.user_email, app_mode) if st.session_state.user_email else []
        favs = [g['title'] for g in lib if g['rating'] >= 4]
        exclude = ", ".join(st.session_state.seen_items)
        
        # PROMPT ULTRA-RESTRICTIF POUR ÉVITER LE HORS-SUJET
        prompt = f"""
        RÔLE : Tu es un bibliothécaire et curateur d'élite spécialisé en {app_mode}.
        RECHERCHE ACTUELLE : "{st.session_state.last_query}"
        FAVORIS DE L'UTILISATEUR : {favs}
        DÉJÀ VUS/LUS (À EXCLURE) : {exclude}
        STYLE CIBLÉ : {selected_genre}

        RÈGLES D'OR ABSOLUES :
        1. SOUS-GENRE STRICT : Si la recherche ou les favoris indiquent un genre précis (ex: Dark Romance, Soulslike, Seinen), tu as INTERDICTION de proposer un autre genre. Un fan de Dark Romance ne veut pas de livres de mathématiques ou de fantaisie classique.
        2. PAS DE DOUBLONS DE FRANCHISE : Ne propose JAMAIS deux titres de la même licence ou du même univers. (Ex: Si tu proposes un Naruto, les deux autres doivent être des mangas TOTALEMENT DIFFÉRENTS).
        3. PAS DE SEQUELS : Ne propose pas le "Tome 2" ou un "Spin-off" d'un titre déjà connu ou présent dans la liste.
        4. NOUVEAUTÉ : Priorise des pépites avec une ambiance identique mais d'auteurs/studios différents.
        5. PLATEFORME : {selected_platform}.
        6. EXCLUSIVITÉ : Propose 3 titres qui partagent la MÊME VIBE psychologique et thématique.
        DIRECTIVES CRUCIALES :
        7. AMBIGUÏTÉ DE GENRE : Si l'utilisateur cherche un thème comme "Mafia", "Boss", ou "Enemies to lovers" dans la catégorie Livres, privilégie TOUJOURS la FICTION (notamment la Dark Romance si le genre est sélectionné) plutôt que les documentaires historiques.
        8. ANALYSE DE LA VIBE : Ne te contente pas des mots-clés. Si l'utilisateur cherche "Russian Mafia", il veut l'ambiance sombre, la tension et les codes de ce genre littéraire précis.
        9. QUALITÉ LITTÉRAIRE : Propose des titres récents ou très populaires dans cette niche spécifique.
        10. FORMAT : Réponds uniquement en JSON avec "titre" et "desc".

        RÉPONDS UNIQUEMENT AU FORMAT JSON SUIVANT :
        [
          {{
            "titre": "Nom exact",
            "desc": "Pourquoi ce titre est le choix parfait pour un fan du genre précis demandé."
          }}
        ]
        """
        
        with st.spinner('L\'IA filtre les pépites pour vous...'):
            try:
                # On force Gemini à ne pas sortir du cadre JSON
                response = model.generate_content(prompt)
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                
                if json_match:
                    recos = json.loads(json_match.group())
                    # Booster de vitesse : Chargement parallèle des images
                    imgs = get_all_images_parallel([r['titre'] for r in recos], app_mode)
                    for i, r in enumerate(recos): 
                        r['img'] = imgs[i]
                    st.session_state.current_recos = recos
                else:
                    st.error("Erreur de formatage de l'IA. Réessayez.")
            except Exception as e:
                st.error(f"Erreur IA : {e}")

    # --- 6. AFFICHAGE DES RÉSULTATS (Section 6) ---
if st.session_state.current_recos:
    st.write("---")
    cols = st.columns(3)

    # On récupère le contexte actuel pour le passer au remplacement
    current_context = selected_platform if app_mode == "🎮 Jeux Vidéo" else selected_genre
    
    for i, item in enumerate(st.session_state.current_recos):
        with cols[i]:
            # 1. Génération des liens [cite: 2026-01-04]
            affiliate_link = get_smart_link(item['titre'], app_mode)
            share_text = f"Regarde ce que The Shortlist m'a déniché : {item['titre']} ! {affiliate_link}"
            whatsapp_url = f"https://wa.me/?text={urllib.parse.quote(share_text)}"
            img_url = item['img'] if item['img'] else "https://placehold.co/400x600"
            
            # 2. Affichage de la Carte [cite: 2026-01-06]
            st.markdown(f"""
                <div class="game-card">
                    <div>
                        <img src="{img_url}" style="width:100%; height:250px; object-fit:cover; border-radius:15px;">
                        <div style="font-weight:800; margin-top:15px; font-size:1.1rem;">{item['titre']}</div>
                        <div style="color:rgba(255,255,255,0.6); font-size:0.85rem; margin-top:10px;">{item['desc']}</div>
                    </div>
                    <a href="{affiliate_link}" target="_blank" class="price-action">🛒 Voir le prix</a>
                </div>
            """, unsafe_allow_html=True)
            
            # --- NOUVEAU : SYNOPSIS DÉROULANT ---
            with st.expander("📖 Synopsis & Détails"):
                # On peut ici afficher un texte récupéré de l'API ou demander à l'IA d'en générer un court
                st.write(f"Découvrez l'univers de **{item['titre']}**. Un choix incontournable pour les amateurs du genre.")
                # Lien "En savoir plus" dynamique
                more_info_url = f"https://www.google.com/search?q={urllib.parse.quote(item['titre'] + ' synopsis')}"
                st.markdown(f"[🔍 En savoir plus]({more_info_url})")

            # 4. LE BOUTON DE REJET (FIXÉ)
            if st.button(f"❌ Pas pour moi", key=f"rej_{i}", use_container_width=True):
                st.session_state.seen_items.append(item['titre'])
                
                with st.spinner("Remplacement..."):
                    exclude_updated = ", ".join(st.session_state.seen_items)
                    # Prompt de remplacement ultra-contextuel
                    replace_prompt = f"""
                    RÔLE : Curateur expert en {app_mode}.
                    CONTEXTE : {current_context} (TRÈS IMPORTANT : respecter strictement ce genre/style).
                    RECHERCHE ORIGINALE : "{st.session_state.last_query}"
                    EXCLURE : {exclude_updated}
                    MISSION : Propose 1 SEULE nouvelle pépite.
                    FORMAT JSON : [{{"titre": "...", "desc": "...", "synopsis": "..."}}]
                    """
                    
                    try:
                        resp = model.generate_content(replace_prompt)
                        match = re.search(r'\[.*\]', resp.text, re.DOTALL)
                        if match:
                            new_data = json.loads(match.group())[0]
                            # On récupère l'image en HD
                            new_data['img'] = fetch_image_hd(new_data['titre'], app_mode)
                            
                            # MISE À JOUR CHIRURGICALE DE LA LISTE
                            st.session_state.current_recos[i] = new_data
                            st.rerun()
                    except Exception as e:
                        st.toast("⚠️ L'IA a eu un petit hoquet, réessayez !")

            # 4. Bouton WhatsApp
            st.markdown(f"""
                <a href="{whatsapp_url}" target="_blank" style="text-decoration:none;">
                    <button style="width:100%; background-color:#25D366 !important; color:black; border:none; border-radius:9999px; padding:10px; margin-top:10px; cursor:pointer; font-weight:bold;">
                        📲 Partager
                    </button>
                </a>
            """, unsafe_allow_html=True)

            # 5. Bouton Bibliothèque
            if st.button(f"✅ J'y ai joué/vu", key=f"p_{i}", use_container_width=True):
                if st.session_state.user_email:
                    save_item(st.session_state.user_email, app_mode, item['titre'])
                st.session_state.seen_items.append(item['titre'])
                st.session_state.current_recos = None
                st.rerun()

    # --- BOUTON GLOBAL TOUT EN BAS (Sorti de la boucle) ---
    st.write("---")
    _, c_reload, _ = st.columns([1, 2, 1])
    with c_reload:
        if st.button("🔄 Proposer 3 autres options", use_container_width=True):
            for item in st.session_state.current_recos:
                st.session_state.seen_items.append(item['titre'])
            st.session_state.current_recos = None
            st.rerun()


# --- TAB BIBLIOTHÈQUE (Section 7) ---
with tab_lib:
    if not st.session_state.user_email:
        st.info("Connecte-toi pour voir ta collection.")
    else:
        full_data = load_data(st.session_state.user_email, app_mode)
        
        # --- 1. MES FAVORIS ABSOLUS (TOP 5 ALL-TIME) ---
        st.subheader(f"❤️ Mes Favoris Absolus")
        # On filtre uniquement ceux qui ont is_favorite = True
        absolute_favs = [g for g in full_data if g.get('fav')]
        
        if absolute_favs:
            f_cols = st.columns(5)
            for idx, g in enumerate(absolute_favs[:5]): # Limite aux 5 premiers
                with f_cols[idx]:
                    st.markdown(f"""
                        <div style="text-align:center; padding:10px; background:rgba(255,51,102,0.1); border:1px solid #FF3366; border-radius:12px; margin-bottom:10px;">
                            <div style="font-size:1.2rem;">❤️</div>
                            <strong style="font-size:0.9rem;">{g['title']}</strong>
                        </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("Clique sur le ❤️ à côté d'un titre pour l'épingler ici comme favori absolu !")

        st.write("---")

        # --- 2. TOP 10 PAR NOTE ---
        st.subheader(f"🏆 Mon Top 10 par Note")
        top_items = sorted([g for g in full_data if g['rating'] > 0], key=lambda x: x['rating'], reverse=True)[:10]
        if top_items:
            t_cols = st.columns(5)
            for idx, g in enumerate(top_items):
                with t_cols[idx % 5]:
                    st.markdown(f"""<div class="top-badge"><div style="color:#3B82F6; font-weight:800;">#{idx+1}</div><strong>{g['title']}</strong><br>⭐ {g['rating']}/5</div>""", unsafe_allow_html=True)
        
        st.write("---")
        
        # --- 3. LISTE COMPLÈTE AVEC OPTION FAVORIS ---
        search = st.text_input("🔍 Rechercher dans ma liste...", key="lib_search")
        for g in [d for d in full_data if search.lower() in d['title'].lower()]:
            # On ajoute une colonne pour le bouton Coeur
            c1, c2, c3, c4 = st.columns([3.5, 0.5, 1, 0.5])
            
            c1.markdown(f"**{g['title']}**")
            
            # Bouton Favori (❤️ si oui, 🤍 si non)
            heart_icon = "❤️" if g.get('fav') else "🤍"
            if c2.button(heart_icon, key=f"fav_{g['title']}", help="Mettre en favori absolu"):
                toggle_favorite_db(st.session_state.user_email, app_mode, g['title'], g.get('fav', False))
                st.rerun()
                
            with c3:
                new_n = st.selectbox("Note", [0,1,2,3,4,5], index=g['rating'], key=f"r_{g['title']}", label_visibility="collapsed")
                if new_n != g['rating']:
                    update_rating_db(st.session_state.user_email, app_mode, g['title'], new_n)
                    st.rerun()
            with c4:
                if st.button("🗑️", key=f"del_{g['title']}", use_container_width=True):
                    delete_item_db(st.session_state.user_email, app_mode, g['title'])
                    st.rerun()












import streamlit as st
import google.generativeai as genai
import json, urllib.parse, re, requests
from supabase import create_client, Client
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# --- 1. CONFIGURATION ---
AMAZON_PARTNER_ID = "theshorlistap-21"
INSTANT_GAMING_ID = "theshortlistapp"
SUPABASE_URL = "https://enkgnmxqvnpvqceueayg.supabase.co"
SUPABASE_KEY = "sb_secret_mNz02Qr2x9SnGMqIPtxRaw_GUK0f9Hd"
TMDB_API_KEY = "53f9c070d843a78f4f16579e57bdda32" 

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# --- CONFIGURATION SÉCURISÉE ---
# On essaie de lire les secrets (pour le Web), sinon on prend la valeur locale (pour ton PC)
try:
    api_key = st.secrets["GEMINI_API_KEY"]
except:
    api_key = "" # Uniquement pour tes tests locaux

genai.configure(api_key=api_key)
model = genai.GenerativeModel(model_name="gemini-3-flash-preview") # Version stable et rapide

st.set_page_config(page_title="The Shortlist", page_icon="📑", layout="wide")

# INITIALISATION DES ÉTATS
if 'user_email' not in st.session_state: st.session_state.user_email = None
if 'seen_items' not in st.session_state: st.session_state.seen_items = []
if 'current_recos' not in st.session_state: st.session_state.current_recos = None
if 'last_query' not in st.session_state: st.session_state.last_query = ""

# --- 2. FONCTIONS DE BASE DE DONNÉES (CORRIGÉES) ---

def load_data(email, mode):
    """Charge les données en incluant le statut favori [cite: 2026-01-06]"""
    try:
        if mode == "🎮 Jeux Vidéo":
            res = supabase.table("user_library").select("game_title, rating, is_favorite").eq("user_email", email).execute()
            return [{'title': d['game_title'], 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
        else:
            res = supabase.table("user_media").select("title, rating, is_favorite").eq("user_email", email).eq("category", mode).execute()
            return [{'title': d['title'], 'rating': d['rating'], 'fav': d.get('is_favorite', False)} for d in res.data]
    except: return []

def toggle_favorite_db(email, mode, title, current_status):
    """Bascule le statut favori (All-time) [cite: 2026-01-06]"""
    new_status = not current_status
    if mode == "🎮 Jeux Vidéo":
        supabase.table("user_library").update({"is_favorite": new_status}).eq("user_email", email).eq("game_title", title).execute()
    else:
        supabase.table("user_media").update({"is_favorite": new_status}).eq("user_email", email).eq("title", title).eq("category", mode).execute()

def save_item(email, mode, title):
    """Enregistre le titre proprement selon le mode [cite: 2026-01-06]"""
    if mode == "🎮 Jeux Vidéo":
        # Pour les jeux, on n'envoie PAS de catégorie
        supabase.table("user_library").insert({
            "user_email": email, 
            "game_title": title, 
            "rating": 0
        }).execute()
    else:
        # Pour le reste, on précise la catégorie (Film, Livre, etc.) [cite: 2026-01-06]
        supabase.table("user_media").insert({
            "user_email": email, 
            "title": title, 
            "category": mode, 
            "rating": 0
        }).execute()

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

# --- 3. RÉCUPÉRATION DES IMAGES (HD & PRO) ---

@lru_cache(maxsize=128)
def fetch_image_hd(title, mode):
    """Récupère des images haute définition via des APIs spécialisées"""
    try:
        if mode == "🎮 Jeux Vidéo":
            url = f"https://api.rawg.io/api/games?key=aaa189410c114919ab95e6a90ada62f1&search={urllib.parse.quote(title)}&page_size=1"
            r = requests.get(url, timeout=5).json()
            return r['results'][0]['background_image'] if r.get('results') else None

        elif mode in ["🎬 Films", "📺 Séries"]:
            stype = "tv" if mode == "📺 Séries" else "movie"
            url = f"https://api.themoviedb.org/3/search/{stype}?api_key={TMDB_API_KEY}&query={urllib.parse.quote(title)}"
            r = requests.get(url).json()
            if r.get('results') and r['results'][0].get('poster_path'):
                return f"https://image.tmdb.org/t/p/w500{r['results'][0]['poster_path']}"

        elif mode in ["🧧 Animés", "🎋 Mangas"]:
            # Jikan API pour des affiches d'animés HD
            mtype = "manga" if mode == "🎋 Mangas" else "anime"
            url = f"https://api.jikan.moe/v4/{mtype}?q={urllib.parse.quote(title)}&limit=1"
            r = requests.get(url).json()
            if r.get('data'):
                return r['data'][0]['images']['jpg']['large_image_url']

        # --- LIVRES (GOOGLE BOOKS AVEC HD HACK) ---
        elif mode == "📚 Livres":
            # On tente Google Books avec forçage de résolution
            url = f"https://www.googleapis.com/books/v1/volumes?q={urllib.parse.quote(title)}&maxResults=1"
            r = requests.get(url, timeout=5).json()
            if r.get('items'):
                volume_info = r['items'][0]['volumeInfo']
                img_links = volume_info.get('imageLinks', {})
                # On cherche la plus grande taille disponible
                img = img_links.get('extraLarge') or img_links.get('large') or img_links.get('medium') or img_links.get('thumbnail')
                
                if img:
                    # HD HACK : On force le HTTPS, on enlève les bords 'curl' et on booste le zoom
                    img = img.replace("http://", "https://")
                    img = img.replace("&edge=curl", "") 
                    img = img.replace("zoom=1", "zoom=3") 
                    return img
            
            # FALLBACK : Open Library HD
            url = f"https://openlibrary.org/search.json?title={urllib.parse.quote(title)}&limit=1"
            r = requests.get(url).json()
            if r.get('docs') and r['docs'][0].get('cover_i'):
                return f"https://covers.openlibrary.org/b/id/{r['docs'][0]['cover_i']}-L.jpg"
    except: pass
    return None

def get_all_images_parallel(titles, mode):
    with ThreadPoolExecutor() as executor:
        return list(executor.map(lambda t: fetch_image_hd(t, mode), titles))

def get_smart_link(title, mode):
    """Lien d'affiliation Instant Gaming corrigé avec le slash obligatoire"""
    # quote_plus transforme les espaces en '+' pour le moteur de recherche
    query = urllib.parse.quote_plus(title)
    
    # 🎮 JEUX VIDÉO : Note bien le "/" AVANT le "?" c'est lui qui évite le 404
    if mode == "🎮 Jeux Vidéo":
        return f"https://www.instant-gaming.com/fr/recherche/?q={query}&igr=theshortlistapp"
    
    # Reste du code Amazon (qui est déjà bon selon tes tests)
    query_amazon = urllib.parse.quote(title)
    if mode in ["📚 Livres", "🎋 Mangas", "🎬 Films", "📺 Séries"]:
        return f"https://www.amazon.fr/s?k={query_amazon}&tag=theshorlistap-21"
    
    return f"https://www.google.com/search?q={query_amazon}"

# --- 4. DESIGN (STYLE PREMIUM & HAUTE VISIBILITÉ) ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800;900&display=swap');
    
    html, body, [data-testid="stAppViewContainer"] {
        background-color: #0B1120 !important; color: #FFFFFF; font-family: 'Inter', sans-serif;
    }

    /* --- SIDEBAR (MENU) : TEXTES BLANCS --- */
    [data-testid="stSidebar"] { background-color: #111827 !important; min-width: 310px !important; }
    [data-testid="stSidebar"] h1 { font-size: 34px !important; color: #3B82F6 !important; font-weight: 900 !important; text-transform: uppercase; }
    [data-testid="stSidebar"] label, [data-testid="stSidebar"] .stSubheader p {
        font-size: 22px !important; font-weight: 800 !important; color: #FFFFFF !important; margin-top: 20px !important;
    }
    [data-testid="stSidebar"] .stRadio div[role="radiogroup"] label p {
        font-size: 20px !important; color: #FFFFFF !important; font-weight: 700 !important;
    }

    /* --- CARTES D'OFFRES & PAYPAL --- */
    .deal-card {
        background: rgba(59, 130, 246, 0.1); border: 1px solid rgba(59, 130, 246, 0.4);
        border-radius: 15px; padding: 15px; margin-bottom: 12px;
    }
    .deal-title { color: #3B82F6; font-weight: 800; font-size: 16px; }
    .deal-link { color: #FFFFFF !important; text-decoration: none; font-weight: 600; }
    
    .paypal-button {
        background: linear-gradient(135deg, #0070BA 0%, #1546a0 100%);
        color: white !important; padding: 15px; border-radius: 15px;
        text-align: center; font-weight: 800; display: block; text-decoration: none;
        box-shadow: 0 4px 15px rgba(0, 112, 186, 0.4);
    }

    /* --- LOGO --- */
    .logo-container { display: flex; align-items: center; justify-content: center; gap: 15px; margin-bottom: 25px; }
    .logo-icon {
        background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
        width: 50px; height: 50px; border-radius: 14px;
        display: flex; align-items: center; justify-content: center; font-weight: 900; font-size: 28px; color: white;
    }
    .logo-text { font-size: 28px; font-weight: 900; color: white; }

    /* --- ONGLETS (TABS) : PLUS DE CADRES MOCHES --- */
    button[data-baseweb="tab"] {
        background-color: transparent !important; border: none !important;
        border-bottom: 2px solid rgba(255,255,255,0.1) !important;
        margin-right: 20px !important; padding: 10px 0 !important;
    }
    button[data-baseweb="tab"] p {
        font-size: 18px !important; font-weight: 700 !important; color: #94A3B8 !important;
    }
    button[aria-selected="true"] {
        border-bottom: 3px solid #3B82F6 !important;
    }
    button[aria-selected="true"] p {
        color: #FFFFFF !important;
    }
    /* --- BOUTONS : COULEUR ET VISIBILITÉ --- */
    .stButton>button {
        background: linear-gradient(135deg, #3B82F6 0%, #1D4ED8 100%) !important;
        color: #FFFFFF !important; border: none !important;
        border-radius: 12px !important; height: 50px !important;
        font-weight: 800 !important; text-shadow: 0 1px 2px rgba(0,0,0,0.3);
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3) !important;
    }

    footer {display: none !important;} [data-testid="stHeader"] {background: transparent !important;}
    </style>
""", unsafe_allow_html=True)

# --- 5. SIDEBAR (MENU CONSOLIDÉ) ---
with st.sidebar:
    st.title("Menu")
    app_mode = st.radio("Catégorie", ["🎮 Jeux Vidéo", "🎬 Films", "📺 Séries", "🧧 Animés", "🎋 Mangas", "📚 Livres"], key="menu_category")
    
    st.write("---")
    st.markdown('<p style="color:white; font-size:22px; font-weight:800; margin-top:20px;">🎁 Offres du moment</p>', unsafe_allow_html=True)
    if app_mode in ["📚 Livres", "🎋 Mangas"]:
        st.markdown(f'<div class="deal-card"><div class="deal-title">📖 Kindle Unlimited</div><a class="deal-link" href="https://www.amazon.fr/kindle-dbs/hz/signup?tag={AMAZON_PARTNER_ID}" target="_blank">👉 Essai gratuit</a></div>', unsafe_allow_html=True)
        st.markdown(f'<div class="deal-card"><div class="deal-title">🎧 Audible</div><a class="deal-link" href="https://www.amazon.fr/hz/audible/mlp?tag={AMAZON_PARTNER_ID}" target="_blank">👉 1er Livre Audio offert</a></div>', unsafe_allow_html=True)
    elif app_mode in ["🎬 Films", "📺 Séries"]:
        st.markdown(f'<div class="deal-card"><div class="deal-title">🍿 Prime Video</div><a class="deal-link" href="https://www.primevideo.com/?tag={AMAZON_PARTNER_ID}" target="_blank">👉 30 jours gratuits</a></div>', unsafe_allow_html=True)

    st.write("---")
    if app_mode == "🎮 Jeux Vidéo":
        selected_platform = st.selectbox("Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"], key="filter_plat")
    elif app_mode == "📚 Livres":
        selected_genre = st.selectbox("Style", ["Général", "Dark Romance", "Thriller", "SF/Fantasy", "Classiques"], key="filter_genre")

    st.write("---")
    if not st.session_state.user_email:
        email_input = st.text_input("Votre Email", key="sidebar_email")
        if st.button("Connexion", key="sidebar_conn"):
            st.session_state.user_email = email_input
            st.rerun()
    else:
        st.write(f"Connecté : **{st.session_state.user_email}**")
        if st.button("Déconnexion", key="sidebar_deco"):
            st.session_state.user_email = None
            st.rerun()

    st.write("---")
    st.markdown('<p style="color:white; font-size:22px; font-weight:800; margin-top:20px;">💙 Soutenir</p>', unsafe_allow_html=True)
    st.markdown(f'<a href="https://www.paypal.me/TheShortlistApp" target="_blank" class="paypal-button">Faire un don (PayPal)</a>', unsafe_allow_html=True)
    
    with st.expander("⚖️ Légal"):
        st.caption("The Shortlist est un curateur IA. Partenaire Amazon (bénéfices sur achats affiliés).")

# --- 6. TABS & LOGO ---
raw_label = app_mode.split(" ")[1]
media_label = raw_label.rstrip('s') if raw_label.endswith('s') else raw_label
tab_search, tab_lib = st.tabs(["🔎 Trouver", "📚 Ma Liste"])

with tab_search:
    # RE-INSERTION DU LOGO ICI
    st.markdown("""
    <div class="logo-container">
        <div class="logo-icon">3</div>
        <div class="logo-text">THE SHORTLIST</div>
    </div>
    """, unsafe_allow_html=True)

    # --- NOUVEAU : FILTRES ACCESSIBLES AU CENTRE ---
    c_filters = st.columns([1, 2, 1])
    with c_filters[1]:
        # Initialisation par défaut
        selected_platform = "Toutes plateformes"
        selected_genre = "Général"
        
        # Filtre dynamique selon le mode choisi [cite: 2026-01-04]
        if app_mode == "🎮 Jeux Vidéo":
            selected_platform = st.selectbox("🎮 Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"], label_visibility="collapsed")
        elif app_mode == "📚 Livres":
            selected_genre = st.selectbox("📖 Style de lecture", [
                "Général", "Dark Romance", "Thriller / Policier", 
                "Fantasy / Science-Fiction", "Développement Personnel", "Classiques"
            ], label_visibility="collapsed")
            # Petit rappel visuel du genre choisi pour ta femme !
            if selected_genre == "Dark Romance":
                st.caption("✨ Mode 'Dark Romance' activé pour des pépites passionnelles.")

    # --- BARRE DE RECHERCHE DYNAMIQUE ---
    query = st.text_input(
        "Recherche", 
        placeholder=f"Ex: Un {media_label.lower()} qui ressemble à...", 
        label_visibility="collapsed", 
        key="main_search_input"
    )
    
    b1, b2 = st.columns(2)
    with b1:
        if st.button("🔎 TROUVER", use_container_width=True, key="search_btn"):
            st.session_state.last_query = query
            st.session_state.current_recos = None
    with b2:
        if st.button("🎲 SURPRENDS-MOI", use_container_width=True, key="surprise_btn"):
            st.session_state.last_query = f"Une pépite de type {media_label.lower()} méconnue"
            st.session_state.current_recos = None

# --- NOTE EXPLICATIVE ---
    with st.expander("ℹ️ Comment utiliser The Shortlist ?"):
        st.markdown("""
        ### Bienvenue dans votre moteur de recommandation d'élite ! 🎯
        
        Contrairement aux autres plateformes, **The Shortlist** ne vous propose que **3 options ultra-ciblées** pour vous éviter la fatigue du choix.
        
        **1. Choisissez votre univers** : Utilisez le menu à gauche pour basculer entre Jeux, Films, Livres, etc.
        
        **2. Soyez précis** : Dans la barre de recherche, décrivez une ambiance ou un style précis (ex: *"Un livre de Dark Romance sombre"* ou *"Un jeu d'aventure comme Zelda"* ou *"Ecrivez simplement votre titre préféré et l'ia vous fera une recommendation qui y ressemble"*).
        
        **3. L'IA apprend de vous** : 
        * Cliquez sur **"J'y ai joué/vu"** pour ajouter un titre à votre bibliothèque.
        * **Notez vos favoris (4 ou 5 étoiles)** : L'IA analysera automatiquement ces titres pour affiner ses prochaines suggestions selon vos goûts réels.
        
        **4. Mode "Surprends-moi"** : En panne d'inspiration ? Laissez l'IA dénicher une pépite méconnue pour vous.
        """)

    # --- LOGIQUE IA (Section 6) ---
    if st.session_state.last_query and st.session_state.current_recos is None:
        lib = load_data(st.session_state.user_email, app_mode) if st.session_state.user_email else []
        favs = [g['title'] for g in lib if g['rating'] >= 4]
        exclude = ", ".join(st.session_state.seen_items)
        
        # PROMPT ULTRA-RESTRICTIF POUR ÉVITER LE HORS-SUJET
        prompt = f"""
        RÔLE : Tu es un bibliothécaire et curateur d'élite spécialisé en {app_mode}.
        RECHERCHE ACTUELLE : "{st.session_state.last_query}"
        FAVORIS DE L'UTILISATEUR : {favs}
        DÉJÀ VUS/LUS (À EXCLURE) : {exclude}
        STYLE CIBLÉ : {selected_genre}

        RÈGLES D'OR ABSOLUES :
        1. SOUS-GENRE STRICT : Si la recherche ou les favoris indiquent un genre précis (ex: Dark Romance, Soulslike, Seinen), tu as INTERDICTION de proposer un autre genre. Un fan de Dark Romance ne veut pas de livres de mathématiques ou de fantaisie classique.
        2. PAS DE DOUBLONS DE FRANCHISE : Ne propose JAMAIS deux titres de la même licence ou du même univers. (Ex: Si tu proposes un Naruto, les deux autres doivent être des mangas TOTALEMENT DIFFÉRENTS).
        3. PAS DE SEQUELS : Ne propose pas le "Tome 2" ou un "Spin-off" d'un titre déjà connu ou présent dans la liste.
        4. NOUVEAUTÉ : Priorise des pépites avec une ambiance identique mais d'auteurs/studios différents.
        5. PLATEFORME : {selected_platform}.
        6. EXCLUSIVITÉ : Propose 3 titres qui partagent la MÊME VIBE psychologique et thématique.
        DIRECTIVES CRUCIALES :
        7. AMBIGUÏTÉ DE GENRE : Si l'utilisateur cherche un thème comme "Mafia", "Boss", ou "Enemies to lovers" dans la catégorie Livres, privilégie TOUJOURS la FICTION (notamment la Dark Romance si le genre est sélectionné) plutôt que les documentaires historiques.
        8. ANALYSE DE LA VIBE : Ne te contente pas des mots-clés. Si l'utilisateur cherche "Russian Mafia", il veut l'ambiance sombre, la tension et les codes de ce genre littéraire précis.
        9. QUALITÉ LITTÉRAIRE : Propose des titres récents ou très populaires dans cette niche spécifique.
        10. FORMAT : Réponds uniquement en JSON avec "titre" et "desc".

        RÉPONDS UNIQUEMENT AU FORMAT JSON SUIVANT :
        [
          {{
            "titre": "Nom exact",
            "desc": "Pourquoi ce titre est le choix parfait pour un fan du genre précis demandé."
          }}
        ]
        """
        
        with st.spinner('L\'IA filtre les pépites pour vous...'):
            try:
                # On force Gemini à ne pas sortir du cadre JSON
                response = model.generate_content(prompt)
                json_match = re.search(r'\[.*\]', response.text, re.DOTALL)
                
                if json_match:
                    recos = json.loads(json_match.group())
                    # Booster de vitesse : Chargement parallèle des images
                    imgs = get_all_images_parallel([r['titre'] for r in recos], app_mode)
                    for i, r in enumerate(recos): 
                        r['img'] = imgs[i]
                    st.session_state.current_recos = recos
                else:
                    st.error("Erreur de formatage de l'IA. Réessayez.")
            except Exception as e:
                st.error(f"Erreur IA : {e}")

    # --- 6. AFFICHAGE DES RÉSULTATS (Section 6) ---
if st.session_state.current_recos:
    st.write("---")
    cols = st.columns(3)

    # On récupère le contexte actuel pour le passer au remplacement
    current_context = selected_platform if app_mode == "🎮 Jeux Vidéo" else selected_genre
    
    for i, item in enumerate(st.session_state.current_recos):
        with cols[i]:
            # 1. Génération des liens [cite: 2026-01-04]
            affiliate_link = get_smart_link(item['titre'], app_mode)
            share_text = f"Regarde ce que The Shortlist m'a déniché : {item['titre']} ! {affiliate_link}"
            whatsapp_url = f"https://wa.me/?text={urllib.parse.quote(share_text)}"
            img_url = item['img'] if item['img'] else "https://placehold.co/400x600"
            
            # 2. Affichage de la Carte [cite: 2026-01-06]
            st.markdown(f"""
                <div class="game-card">
                    <div>
                        <img src="{img_url}" style="width:100%; height:250px; object-fit:cover; border-radius:15px;">
                        <div style="font-weight:800; margin-top:15px; font-size:1.1rem;">{item['titre']}</div>
                        <div style="color:rgba(255,255,255,0.6); font-size:0.85rem; margin-top:10px;">{item['desc']}</div>
                    </div>
                    <a href="{affiliate_link}" target="_blank" class="price-action">🛒 Voir le prix</a>
                </div>
            """, unsafe_allow_html=True)
            
            # --- NOUVEAU : SYNOPSIS DÉROULANT ---
            with st.expander("📖 Synopsis & Détails"):
                # On peut ici afficher un texte récupéré de l'API ou demander à l'IA d'en générer un court
                st.write(f"Découvrez l'univers de **{item['titre']}**. Un choix incontournable pour les amateurs du genre.")
                # Lien "En savoir plus" dynamique
                more_info_url = f"https://www.google.com/search?q={urllib.parse.quote(item['titre'] + ' synopsis')}"
                st.markdown(f"[🔍 En savoir plus]({more_info_url})")

            # 4. LE BOUTON DE REJET (FIXÉ)
            if st.button(f"❌ Pas pour moi", key=f"rej_{i}", use_container_width=True):
                st.session_state.seen_items.append(item['titre'])
                
                with st.spinner("Remplacement..."):
                    exclude_updated = ", ".join(st.session_state.seen_items)
                    # Prompt de remplacement ultra-contextuel
                    replace_prompt = f"""
                    RÔLE : Curateur expert en {app_mode}.
                    CONTEXTE : {current_context} (TRÈS IMPORTANT : respecter strictement ce genre/style).
                    RECHERCHE ORIGINALE : "{st.session_state.last_query}"
                    EXCLURE : {exclude_updated}
                    MISSION : Propose 1 SEULE nouvelle pépite.
                    FORMAT JSON : [{{"titre": "...", "desc": "...", "synopsis": "..."}}]
                    """
                    
                    try:
                        resp = model.generate_content(replace_prompt)
                        match = re.search(r'\[.*\]', resp.text, re.DOTALL)
                        if match:
                            new_data = json.loads(match.group())[0]
                            # On récupère l'image en HD
                            new_data['img'] = fetch_image_hd(new_data['titre'], app_mode)
                            
                            # MISE À JOUR CHIRURGICALE DE LA LISTE
                            st.session_state.current_recos[i] = new_data
                            st.rerun()
                    except Exception as e:
                        st.toast("⚠️ L'IA a eu un petit hoquet, réessayez !")

            # 4. Bouton WhatsApp
            st.markdown(f"""
                <a href="{whatsapp_url}" target="_blank" style="text-decoration:none;">
                    <button style="width:100%; background-color:#25D366 !important; color:black; border:none; border-radius:9999px; padding:10px; margin-top:10px; cursor:pointer; font-weight:bold;">
                        📲 Partager
                    </button>
                </a>
            """, unsafe_allow_html=True)

            # 5. Bouton Bibliothèque
            if st.button(f"✅ J'y ai joué/vu", key=f"p_{i}", use_container_width=True):
                if st.session_state.user_email:
                    save_item(st.session_state.user_email, app_mode, item['titre'])
                st.session_state.seen_items.append(item['titre'])
                st.session_state.current_recos = None
                st.rerun()

    # --- BOUTON GLOBAL TOUT EN BAS (Sorti de la boucle) ---
    st.write("---")
    _, c_reload, _ = st.columns([1, 2, 1])
    with c_reload:
        if st.button("🔄 Proposer 3 autres options", use_container_width=True):
            for item in st.session_state.current_recos:
                st.session_state.seen_items.append(item['titre'])
            st.session_state.current_recos = None
            st.rerun()


# --- TAB BIBLIOTHÈQUE (Section 7) ---
with tab_lib:
    if not st.session_state.user_email:
        st.info("Connecte-toi pour voir ta collection.")
    else:
        full_data = load_data(st.session_state.user_email, app_mode)
        
        # --- 1. MES FAVORIS ABSOLUS (TOP 5 ALL-TIME) ---
        st.subheader(f"❤️ Mes Favoris Absolus")
        # On filtre uniquement ceux qui ont is_favorite = True
        absolute_favs = [g for g in full_data if g.get('fav')]
        
        if absolute_favs:
            f_cols = st.columns(5)
            for idx, g in enumerate(absolute_favs[:5]): # Limite aux 5 premiers
                with f_cols[idx]:
                    st.markdown(f"""
                        <div style="text-align:center; padding:10px; background:rgba(255,51,102,0.1); border:1px solid #FF3366; border-radius:12px; margin-bottom:10px;">
                            <div style="font-size:1.2rem;">❤️</div>
                            <strong style="font-size:0.9rem;">{g['title']}</strong>
                        </div>
                    """, unsafe_allow_html=True)
        else:
            st.info("Clique sur le ❤️ à côté d'un titre pour l'épingler ici comme favori absolu !")

        st.write("---")

        # --- 2. TOP 10 PAR NOTE ---
        st.subheader(f"🏆 Mon Top 10 par Note")
        top_items = sorted([g for g in full_data if g['rating'] > 0], key=lambda x: x['rating'], reverse=True)[:10]
        if top_items:
            t_cols = st.columns(5)
            for idx, g in enumerate(top_items):
                with t_cols[idx % 5]:
                    st.markdown(f"""<div class="top-badge"><div style="color:#3B82F6; font-weight:800;">#{idx+1}</div><strong>{g['title']}</strong><br>⭐ {g['rating']}/5</div>""", unsafe_allow_html=True)
        
        st.write("---")
        
        # --- 3. LISTE COMPLÈTE AVEC OPTION FAVORIS ---
        search = st.text_input("🔍 Rechercher dans ma liste...", key="lib_search")
        for g in [d for d in full_data if search.lower() in d['title'].lower()]:
            # On ajoute une colonne pour le bouton Coeur
            c1, c2, c3, c4 = st.columns([3.5, 0.5, 1, 0.5])
            
            c1.markdown(f"**{g['title']}**")
            
            # Bouton Favori (❤️ si oui, 🤍 si non)
            heart_icon = "❤️" if g.get('fav') else "🤍"
            if c2.button(heart_icon, key=f"fav_{g['title']}", help="Mettre en favori absolu"):
                toggle_favorite_db(st.session_state.user_email, app_mode, g['title'], g.get('fav', False))
                st.rerun()
                
            with c3:
                new_n = st.selectbox("Note", [0,1,2,3,4,5], index=g['rating'], key=f"r_{g['title']}", label_visibility="collapsed")
                if new_n != g['rating']:
                    update_rating_db(st.session_state.user_email, app_mode, g['title'], new_n)
                    st.rerun()
            with c4:
                if st.button("🗑️", key=f"del_{g['title']}", use_container_width=True):
                    delete_item_db(st.session_state.user_email, app_mode, g['title'])
                    st.rerun()













