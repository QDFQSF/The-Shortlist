import streamlit as st
import google.generativeai as genai
import json, urllib.parse, re, requests
from supabase import create_client, Client
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

# --- 1. CONFIGURATION ---
AMAZON_PARTNER_ID = "theshorlistap-21"
SUPABASE_URL = "https://enkgnmxqvnpvqceueayg.supabase.co"
SUPABASE_KEY = "sb_secret_mNz02Qr2x9SnGMqIPtxRaw_GUK0f9Hd"
TMDB_API_KEY = "53f9c070d843a78f4f16579e57bdda32" 

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# --- CONFIGURATION SÉCURISÉE ---
# On essaie de lire les secrets (pour le Web), sinon on prend la valeur locale (pour ton PC)
try:
    api_key = st.secrets["AIzaSyDin9pcCwmmYNiiPlxRzGcS7URCfCg9qLE"]
except:
    api_key = "AIzaSyDin9pcCwmmYNiiPlxRzGcS7URCfCg9qLE" # Uniquement pour tes tests locaux

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
    """Génère un lien d'affiliation Amazon pour les produits physiques [cite: 2026-01-04]"""
    query = urllib.parse.quote(title)
    tag = "theshorlistap-21"
    if mode in ["📚 Livres", "🎋 Mangas", "🎮 Jeux Vidéo", "🎬 Films"]:
        # Lien de recherche Amazon France avec ton tag partenaire [cite: 2026-01-04]
        return f"https://www.amazon.fr/s?k={query}&tag={AMAZON_PARTNER_ID}"
    else:
        # Fallback Google pour les autres catégories (ou séries en streaming)
        return f"https://www.google.com/search?q={query}"

# --- 4. DESIGN (STYLE LP WEB DESIGN) ---
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;800&display=swap');
    html, body, [data-testid="stAppViewContainer"], [data-testid="stHeader"], [data-testid="stSidebar"] {
        background-color: #0B1120 !important; color: #F9FAFB; font-family: 'Inter', sans-serif;
    }
    /* Le Logo Stylisé */
    .logo-container {
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 15px;
        margin-bottom: 30px;
    }
    .logo-icon {
        background: linear-gradient(135deg, #3B82F6 0%, #2563EB 100%);
        width: 45px;
        height: 45px;
        border-radius: 12px;
        display: flex;
        align-items: center;
        justify-content: center;
        font-weight: 900;
        font-size: 24px;
        box-shadow: 0 0 20px rgba(59, 130, 246, 0.5);
    }
    .logo-text {
        font-size: 32px;
        font-weight: 900;
        letter-spacing: -1px;
        background: linear-gradient(to right, #FFFFFF, #94A3B8);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .game-card {
        background-color: #111827; border-radius: 24px; padding: 25px;
        border: 1px solid rgba(255,255,255,0.05); min-height: 480px;
        display: flex; flex-direction: column; justify-content: space-between;
    }
    .stButton>button {
        background: #3B82F6 !important; color: white !important; border-radius: 9999px !important;
        box-shadow: 0 0 20px rgba(59, 130, 246, 0.4); border: none !important;
    }
    .price-action {
        display: inline-block; padding: 6px 16px; background: rgba(59, 130, 246, 0.1);
        color: #3B82F6 !important; text-decoration: none !important; border-radius: 8px; font-weight: 600; text-align: center;
    }
    </style>
    """, unsafe_allow_html=True)

# --- 5. SIDEBAR ---
with st.sidebar:
    st.title("⚙️ Configuration")
    app_mode = st.radio("Catégorie", ["🎮 Jeux Vidéo", "🎬 Films", "📺 Séries", "🧧 Animés", "🎋 Mangas", "📚 Livres"])
    st.write("---")
    with st.expander("⚖️ À propos & Légal"):
        st.write("""
        **The Shortlist** est votre curateur personnel piloté par IA.
        
        **Transparence :** En tant que Partenaire Amazon, nous réalisons un bénéfice sur les achats remplissant les conditions requises. Cela ne vous coûte pas plus cher mais nous aide à garder l'IA gratuite !
        
        **Contact :** theshortlistapp@proton.me
        """)
    
    # RESTAURATION DU SÉLECTEUR DE PLATEFORME
    selected_platform = "Toutes plateformes"
    if app_mode == "🎮 Jeux Vidéo":
        selected_platform = st.selectbox("Plateforme", ["Toutes plateformes", "PC", "PS5", "Xbox", "Switch"])
    
    st.write("---")
    if not st.session_state.user_email:
        email = st.text_input("Email")
        if st.button("Connexion"):
            st.session_state.user_email = email
            data = load_data(email, app_mode)
            st.session_state.seen_items = [d['title'] for d in data]
            st.rerun()
    else:
        st.write(f"Connecté : **{st.session_state.user_email}**")
        # Rafraîchissement automatique de la liste selon la catégorie
        data = load_data(st.session_state.user_email, app_mode)
        st.session_state.seen_items = [d['title'] for d in data]
        if st.button("Déconnexion"): st.session_state.user_email = None; st.rerun()

# --- 6. TABS ---
media_label = app_mode.split(" ")[1]
tab_search, tab_lib = st.tabs([f"🔎 Trouver un {media_label}", "📚 Ma Bibliothèque"])

with tab_search:
    st.markdown("""
    <div class="logo-container">
        <div class="logo-icon">3</div>
        <div class="logo-text">THE SHORTLIST</div>
    </div>
""", unsafe_allow_html=True)
    c_search = st.columns([1, 4, 1])[1]
    with c_search:
        query = st.text_input("Recherche", placeholder=f"Ex: Un {media_label} épique...", label_visibility="collapsed")
        b1, b2 = st.columns(2)
        with b1:
            if st.button("🔎 Trouver", use_container_width=True):
                st.session_state.last_query = query; st.session_state.current_recos = None
        with b2:
            if st.button("🎲 Surprends-moi", use_container_width=True):
                st.session_state.last_query = f"une pépite de type {media_label} méconnue"; st.session_state.current_recos = None

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
        RÔLE : Tu es un bibliothécaire et curateur spécialisé en {app_mode}.
        RECHERCHE ACTUELLE : "{st.session_state.last_query}"
        FAVORIS DE L'UTILISATEUR : {favs}
        DÉJÀ VUS/LUS (À EXCLURE) : {exclude}

        RÈGLES D'OR ABSOLUES :
        1. SOUS-GENRE STRICT : Si la recherche ou les favoris indiquent un genre précis (ex: Dark Romance, Soulslike, Seinen), tu as INTERDICTION de proposer un autre genre. Un fan de Dark Romance ne veut pas de livres de mathématiques ou de fantaisie classique.
        2. PAS DE DOUBLONS DE FRANCHISE : Ne propose JAMAIS deux titres de la même licence ou du même univers. (Ex: Si tu proposes un Naruto, les deux autres doivent être des mangas TOTALEMENT DIFFÉRENTS).
        3. PAS DE SEQUELS : Ne propose pas le "Tome 2" ou un "Spin-off" d'un titre déjà connu ou présent dans la liste.
        4. NOUVEAUTÉ : Priorise des pépites avec une ambiance identique mais d'auteurs/studios différents.
        5. PLATEFORME : {selected_platform}.
        6. EXCLUSIVITÉ : Propose 3 titres qui partagent la MÊME VIBE psychologique et thématique.

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

    # --- AFFICHAGE DES RÉSULTATS (Section 6) ---
    if st.session_state.current_recos:
        st.write("---")
        cols = st.columns(3)
        for i, item in enumerate(st.session_state.current_recos):
            with cols[i]:
                # --- 1. ON GÉNÈRE LE LIEN ICI (DANS LA BOUCLE) ---
                affiliate_link = get_smart_link(item['titre'], app_mode)
                
                img_url = item['img'] if item['img'] else "https://placehold.co/400x600"
                
                # --- 2. ON UTILISE LA VARIABLE DANS LE MARKDOWN ---
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
                
                # Correction du bouton d'ajout [cite: 2026-01-06]
                if st.button(f"✅ J'y ai joué/vu", key=f"p_{i}", use_container_width=True):
                    if st.session_state.user_email:
                        save_item(st.session_state.user_email, app_mode, item['titre'])
                        st.toast(f"Ajouté : {item['titre']} !")
                    st.session_state.seen_items.append(item['titre'])
                    st.session_state.current_recos = None
                    st.rerun()

# --- BOUTON DE PARTAGE WHATSAPP ---
share_text = f"Regarde ce que The Shortlist m'a déniché : {item['titre']} ! Ça a l'air top : {affiliate_link}"
whatsapp_url = f"https://wa.me/?text={urllib.parse.quote(share_text)}"

st.markdown(f"""
    <a href="{whatsapp_url}" target="_blank" style="text-decoration:none;">
        <button style="width:100%; background-color:#25D366 !important; color:black; border:none; border-radius:9999px; padding:10px; margin-top:10px; cursor:pointer; font-weight:bold;">
             📲 Partager sur WhatsApp
        </button>
    </a>
""", unsafe_allow_html=True)

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