"""
Instagram Scraper — DiazUX Studio
Encuentra prospectos por hashtag o seguidores de competidora
y los exporta directo a ProspectorAI CRM.
"""
from instagrapi import Client
from instagrapi.exceptions import LoginRequired, ChallengeRequired
import json, os, time, requests

# ── CONFIGURACIÓN ─────────────────────────────────────────────────
IG_USER     = ""          # Tu usuario de Instagram
IG_PASS     = ""          # Tu contraseña
PROSPECTOR  = "http://localhost:5000"   # URL de ProspectorAI (local o Render)
SESSION_FILE = "ig_session.json"        # Guarda la sesión para no loguearse siempre

# ── LOGIN ─────────────────────────────────────────────────────────
def login():
    cl = Client()
    cl.delay_range = [2, 5]  # Delay entre requests para evitar ban

    if os.path.exists(SESSION_FILE):
        try:
            cl.load_settings(SESSION_FILE)
            cl.login(IG_USER, IG_PASS)
            cl.get_timeline_feed()
            print("✓ Sesión restaurada")
            return cl
        except Exception:
            print("⚠ Sesión expirada, relogueando...")

    try:
        cl.login(IG_USER, IG_PASS)
        cl.dump_settings(SESSION_FILE)
        print("✓ Login exitoso")
        return cl
    except ChallengeRequired:
        print("⚠ Instagram pide verificación — revisá tu email/SMS y volvé a intentar")
        exit(1)
    except Exception as e:
        print(f"✗ Error de login: {e}")
        exit(1)

# ── SCRAPER POR HASHTAG ───────────────────────────────────────────
def scrape_hashtag(cl, hashtag, max_posts=30):
    print(f"\n🔍 Buscando en #{hashtag}...")
    prospects = []
    try:
        medias = cl.hashtag_medias_recent(hashtag, amount=max_posts)
        print(f"   {len(medias)} posts encontrados")
        seen = set()
        for media in medias:
            user_id = str(media.user.pk)
            if user_id in seen: continue
            seen.add(user_id)
            try:
                info = cl.user_info(user_id)
                # Solo cuentas con bio (probablemente negocios)
                if not info.biography: continue
                prospect = build_prospect(info, f"#{hashtag}")
                if prospect:
                    prospects.append(prospect)
                    print(f"   ✓ @{info.username} — {info.full_name}")
                time.sleep(1)
            except Exception as e:
                print(f"   ⚠ Error en usuario {user_id}: {e}")
                continue
    except Exception as e:
        print(f"✗ Error scrapeando #{hashtag}: {e}")
    return prospects

# ── SCRAPER POR SEGUIDORES DE COMPETIDORA ────────────────────────
def scrape_followers(cl, target_username, max_users=50):
    print(f"\n🔍 Buscando seguidores de @{target_username}...")
    prospects = []
    try:
        user_id = cl.user_id_from_username(target_username)
        followers = cl.user_followers(user_id, amount=max_users)
        print(f"   {len(followers)} seguidores encontrados")
        for uid, user in list(followers.items())[:max_users]:
            try:
                info = cl.user_info(uid)
                if not info.biography: continue
                prospect = build_prospect(info, f"Seguidor de @{target_username}")
                if prospect:
                    prospects.append(prospect)
                    print(f"   ✓ @{info.username} — {info.full_name}")
                time.sleep(1.5)
            except Exception as e:
                print(f"   ⚠ Error en usuario {uid}: {e}")
                continue
    except Exception as e:
        print(f"✗ Error: {e}")
    return prospects

# ── CONSTRUIR PROSPECTO ───────────────────────────────────────────
def build_prospect(info, source):
    # Filtrar cuentas personales o sin datos útiles
    if info.follower_count < 100: return None
    if info.follower_count > 500000: return None  # Muy grandes, no son target

    # Detectar problema probable
    problem = detect_problem(info)

    # Buscar email en bio
    email = extract_email(info.biography or "")

    # Buscar URL
    url = str(info.external_url) if info.external_url else ""

    return {
        "brand":      info.full_name or info.username,
        "contact":    "",
        "email":      email,
        "url":        url,
        "problem":    problem,
        "source":     f"Instagram Scraper — {source}",
        "notes":      f"@{info.username} · {info.follower_count:,} seguidores · Bio: {(info.biography or '')[:100]}"
    }

def detect_problem(info):
    bio = (info.biography or "").lower()
    url = str(info.external_url or "").lower()
    problems = []
    if not info.external_url:
        problems.append("sin sitio web")
    elif any(x in url for x in ["wix", "blogspot", "wordpress.com", "webnode"]):
        problems.append("sitio en plataforma básica (Wix/Blogspot)")
    if any(x in bio for x in ["tienda", "shop", "ventas", "pedidos", "compras"]):
        if not info.external_url:
            problems.append("vende por Instagram sin ecommerce propio")
    if info.follower_count > 1000 and not info.external_url:
        problems.append("buena audiencia pero sin presencia web")
    return " · ".join(problems) if problems else "sitio web desactualizado"

def extract_email(text):
    import re
    match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
    return match.group(0) if match else ""

# ── EXPORTAR A PROSPECTORAI ───────────────────────────────────────
def export_to_prospector(prospects):
    if not prospects:
        print("\n⚠ No hay prospectos para exportar")
        return
    print(f"\n📤 Exportando {len(prospects)} prospectos a ProspectorAI...")
    try:
        r = requests.post(
            f"{PROSPECTOR}/api/prospects/bulk",
            json={"prospects": prospects},
            timeout=10
        )
        data = r.json()
        if data.get("ok"):
            print(f"✓ {data.get('added', 0)} prospectos agregados al CRM")
        else:
            print(f"✗ Error: {data}")
    except Exception as e:
        print(f"✗ No se pudo conectar a ProspectorAI: {e}")
        # Guardar en JSON como backup
        backup = "prospectos_ig.json"
        with open(backup, "w", encoding="utf-8") as f:
            json.dump(prospects, f, ensure_ascii=False, indent=2)
        print(f"💾 Guardado en {backup} como backup")

# ── MENÚ PRINCIPAL ────────────────────────────────────────────────
def main():
    print("=" * 50)
    print("  Instagram Scraper — DiazUX Studio")
    print("=" * 50)

    # Configurar credenciales si no están
    global IG_USER, IG_PASS, PROSPECTOR
    if not IG_USER:
        IG_USER = input("\nUsuario de Instagram: ").strip()
    if not IG_PASS:
        import getpass
        IG_PASS = getpass.getpass("Contraseña: ")

    prospector_url = input(f"\nURL ProspectorAI [{PROSPECTOR}]: ").strip()
    if prospector_url: PROSPECTOR = prospector_url

    cl = login()

    while True:
        print("\n── ¿Qué querés hacer? ──")
        print("1. Buscar por hashtag")
        print("2. Buscar seguidores de una cuenta")
        print("3. Buscar por hashtag + seguidores (combinado)")
        print("4. Salir")
        op = input("\nOpción: ").strip()

        prospects = []

        if op == "1":
            hashtag = input("Hashtag (sin #): ").strip()
            cantidad = input("Cantidad de posts a analizar [30]: ").strip() or "30"
            prospects = scrape_hashtag(cl, hashtag, int(cantidad))

        elif op == "2":
            cuenta = input("@Usuario de la cuenta competidora: ").strip().replace("@","")
            cantidad = input("Cantidad de seguidores a analizar [50]: ").strip() or "50"
            prospects = scrape_followers(cl, cuenta, int(cantidad))

        elif op == "3":
            hashtag = input("Hashtag (sin #): ").strip()
            cuenta  = input("@Cuenta competidora: ").strip().replace("@","")
            p1 = scrape_hashtag(cl, hashtag, 20)
            p2 = scrape_followers(cl, cuenta, 30)
            # Deduplicar por brand
            seen = set()
            for p in p1 + p2:
                if p["brand"] not in seen:
                    seen.add(p["brand"])
                    prospects.append(p)

        elif op == "4":
            print("Hasta luego!")
            break
        else:
            print("Opción inválida")
            continue

        if prospects:
            print(f"\n📋 Encontrados: {len(prospects)} prospectos")
            for i, p in enumerate(prospects[:5], 1):
                print(f"   {i}. {p['brand']} — {p['problem'][:50]}")
            if len(prospects) > 5:
                print(f"   ... y {len(prospects)-5} más")

            exportar = input("\n¿Exportar a ProspectorAI? (s/n): ").strip().lower()
            if exportar == "s":
                export_to_prospector(prospects)

if __name__ == "__main__":
    main()
