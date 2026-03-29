"""
IG Scraper Backend v3 — DiazUX Studio
Puerto: 8765
Mejoras: anti-ban real, jitter gaussiano, engagement rate, retry, LinkedIn mejorado
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
from instagrapi import Client
from instagrapi.exceptions import ChallengeRequired, UserNotFound, ClientError
import json, os, re, time, base64, random, threading

SESSION_FILE   = "ig_session.json"
FAVORITES_FILE = "ig_favorites.json"
cl = None
_lock = threading.Lock()   # evita requests simultáneos desde el frontend

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_OK = True
except ImportError:
    PLAYWRIGHT_OK = False

# ── ANTI-BAN: delays y fingerprint ───────────────────────────────
DEVICE_SETTINGS = {
    "app_version": "269.0.0.18.75",
    "android_version": 26,
    "android_release": "8.0.0",
    "dpi": "480dpi",
    "resolution": "1080x1920",
    "manufacturer": "Samsung",
    "device": "SM-G975F",
    "model": "beyond1",
    "cpu": "exynos9820",
    "version_code": "314665256",
}

def _delay(base=2.5, jitter=1.5):
    """Delay gaussiano para parecer humano. Nunca < 1s."""
    d = base + random.gauss(0, jitter / 2.5)
    time.sleep(max(1.0, d))

def _retry(fn, retries=3, base_wait=6, label="operación"):
    """Reintenta con backoff exponencial + jitter."""
    for attempt in range(retries):
        try:
            return fn()
        except (ClientError, Exception) as e:
            if attempt == retries - 1:
                raise
            wait = base_wait * (2 ** attempt) + random.uniform(0, 3)
            print(f"  ⚠ {label} falló (intento {attempt+1}/{retries}): {e}. Esperando {wait:.1f}s...")
            time.sleep(wait)

# ── SCREENSHOT ────────────────────────────────────────────────────
def screenshot_url(url):
    if not PLAYWRIGHT_OK or not url: return None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto(url, timeout=15000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)
            img = page.screenshot(type="jpeg", quality=70)
            browser.close()
            return base64.b64encode(img).decode()
    except Exception as e:
        print(f"  Screenshot error: {e}")
        return None

# ── FAVORITES ─────────────────────────────────────────────────────
def load_favorites():
    if os.path.exists(FAVORITES_FILE):
        with open(FAVORITES_FILE, encoding="utf-8") as f:
            return json.load(f)
    return []

def save_favorites(favs):
    with open(FAVORITES_FILE, "w", encoding="utf-8") as f:
        json.dump(favs, f, ensure_ascii=False, indent=2)

# ── LOGIN ─────────────────────────────────────────────────────────
def do_login(username, password):
    global cl
    new_cl = Client()
    new_cl.delay_range = [3, 8]
    try:
        new_cl.set_device(DEVICE_SETTINGS)
    except Exception:
        pass

    if os.path.exists(SESSION_FILE):
        try:
            new_cl.load_settings(SESSION_FILE)
            new_cl.login(username, password)
            new_cl.get_timeline_feed()
            cl = new_cl
            print(f"  ✓ Sesión restaurada — @{username}")
            return True, "ok"
        except Exception as e:
            print(f"  ⚠ Sesión expirada: {e}")

    try:
        new_cl.login(username, password)
        new_cl.dump_settings(SESSION_FILE)
        cl = new_cl
        print(f"  ✓ Login OK — @{username}")
        return True, "ok"
    except ChallengeRequired:
        return False, "Instagram pide verificación — revisá tu email/SMS"
    except Exception as e:
        return False, str(e)

# ── HELPERS DE ANÁLISIS ───────────────────────────────────────────
AR_KW = [
    "argentina", "buenos aires", "caba", "córdoba", "rosario", "mendoza",
    "bsas", "🇦🇷", "amba", "gba", "tiendanube", "mercadolibre",
    "envíos", "envios", "pesos", "$ar", "capital federal", "palermo",
    "belgrano", "san isidro", "microcentro", "mar del plata", "salta",
    "tucumán", "corrientes", "formosa", "misiones", "entre ríos", "la plata",
]

CATEGORY_KW = {
    "moda":      ["moda", "ropa", "indumentaria", "outfit", "fashion", "colección", "temporada", "look", "tendencia"],
    "belleza":   ["belleza", "cosmética", "makeup", "skincare", "nail", "pestañas", "cejas", "cabello", "piel"],
    "hogar":     ["hogar", "decoración", "deco", "muebles", "interiorismo", "diseño de interiores", "ambientes"],
    "gastronomía": ["restaurant", "café", "gastronomía", "comida", "food", "cocina", "pastelería", "panadería", "catering"],
    "fitness":   ["gym", "fitness", "entrenamiento", "crossfit", "pilates", "yoga", "nutrición", "salud"],
    "tech":      ["tech", "software", "app", "digital", "ecommerce", "startup", "saas", "desarrollador"],
    "arte":      ["arte", "diseño", "artista", "ilustración", "fotografía", "creativo", "estudio"],
}

def detect_category(info):
    bio = (info.biography or "").lower()
    for cat, kws in CATEGORY_KW.items():
        if any(kw in bio for kw in kws):
            return cat
    return "otro"

def is_argentina(info):
    text = ((info.biography or "") + " " + str(getattr(info, "location", "") or "")).lower()
    return any(kw in text for kw in AR_KW)

def extract_email(text):
    m = re.search(r"[\w\.\-\+]+@[\w\.\-]+\.\w{2,}", text or "")
    return m.group(0) if m else ""

def extract_phone(text):
    m = re.search(r"(\+54|0054|54)?\s*9?\s*[\(\-]?\d{2,4}[\)\-\s]?\d{4}[\s\-]?\d{4}", text or "")
    return m.group(0).strip() if m else ""

def calc_engagement_rate(info):
    """Tasa de engagement aproximada: avg_likes_per_post / followers * 100"""
    if not info.follower_count or not info.media_count:
        return 0.0
    # instagrapi a veces expone follower_count pero no avg likes
    # Usamos media_count como proxy de actividad
    activity = min(info.media_count / max(info.follower_count, 1) * 1000, 100)
    return round(activity, 2)

def detect_problem(info):
    bio = (info.biography or "").lower()
    url = str(info.external_url or "").lower()
    problems = []

    if not info.external_url:
        problems.append("sin sitio web")
        if any(x in bio for x in ["tienda", "shop", "ventas", "pedidos", "compras", "precio", "envío"]):
            problems.append("vende por IG sin ecommerce propio")
        if info.follower_count > 1000:
            problems.append("buena audiencia sin presencia web")
    elif any(x in url for x in ["wix", "blogspot", "wordpress.com", "webnode", "weebly", "linktree"]):
        problems.append("sitio en plataforma básica")
    elif any(x in url for x in ["linktr.ee", "bio.link", "beacons"]):
        problems.append("sólo landing de links, sin sitio real")
    else:
        problems.append("sitio web desactualizado o sin optimizar")

    return " · ".join(problems) if problems else "oportunidad de mejora web"

def build_prospect(info, source, min_f, max_f, fw, only_ar, min_er=0):
    if info.follower_count < min_f or info.follower_count > max_f:
        return None
    has_web = bool(info.external_url)
    if fw == "no" and has_web:   return None
    if fw == "yes" and not has_web: return None
    if only_ar and not is_argentina(info): return None
    er = calc_engagement_rate(info)
    if er < min_er: return None

    return {
        "brand":       info.full_name or info.username,
        "contact":     "",
        "email":       extract_email(info.biography),
        "phone":       extract_phone(info.biography),
        "url":         str(info.external_url) if info.external_url else "",
        "problem":     detect_problem(info),
        "source":      f"IG Scraper — {source}",
        "notes":       f"@{info.username} · {info.follower_count:,} seguidores · {(info.biography or '')[:120]}",
        "ig_username": info.username,
        "ig_id":       str(info.pk),
        "followers":   info.follower_count,
        "media_count": info.media_count or 0,
        "engagement_rate": er,
        "category":    detect_category(info),
        "has_web":     has_web,
        "is_ar":       is_argentina(info),
        "is_business": info.is_business,
        "is_verified": info.is_verified,
        "bio_preview": (info.biography or "")[:150],
    }

# ── SCRAPER POR HASHTAG ───────────────────────────────────────────
def scrape_hashtag(ht, amount, min_f, max_f, fw, only_ar, min_er=0):
    prospects = []
    seen = set()
    print(f"  🔍 #{ht}...")

    try:
        medias = _retry(
            lambda: cl.hashtag_medias_recent(ht, amount=amount),
            label=f"hashtag #{ht}"
        )
    except Exception as e:
        print(f"  ✗ Error obteniendo #{ht}: {e}")
        return []

    batch = 0
    for media in medias:
        uid = str(media.user.pk)
        if uid in seen:
            continue
        seen.add(uid)

        try:
            info = _retry(lambda u=uid: cl.user_info(u), label=f"user_info {uid}")
            if not info.biography:
                continue
            p = build_prospect(info, f"#{ht}", min_f, max_f, fw, only_ar, min_er)
            if p:
                prospects.append(p)
                print(f"    ✓ @{info.username} ({info.follower_count:,} seg · cat:{p['category']})")
        except Exception as e:
            print(f"    ⚠ Error en {uid}: {e}")

        # Delay adaptativo: cada 5 requests, pausa larga
        batch += 1
        if batch % 5 == 0:
            _delay(base=6, jitter=3)
        else:
            _delay(base=2, jitter=1)

    return prospects

# ── SCRAPER POR SEGUIDORES ────────────────────────────────────────
def scrape_followers(acc, amount, min_f, max_f, fw, only_ar, min_er=0):
    prospects = []
    print(f"  🔍 @{acc} seguidores...")

    try:
        uid = _retry(lambda: cl.user_id_from_username(acc), label=f"lookup @{acc}")
        followers = _retry(lambda: cl.user_followers(uid, amount=amount), label=f"followers @{acc}")
    except Exception as e:
        print(f"  ✗ Error: {e}")
        return []

    batch = 0
    for fuid in list(followers.keys())[:amount]:
        try:
            info = _retry(lambda u=fuid: cl.user_info(u), label=f"user_info {fuid}")
            if not info.biography:
                continue
            p = build_prospect(info, f"@{acc}", min_f, max_f, fw, only_ar, min_er)
            if p:
                prospects.append(p)
                print(f"    ✓ @{info.username} ({info.follower_count:,} seg)")
        except Exception as e:
            print(f"    ⚠ Error en {fuid}: {e}")

        batch += 1
        if batch % 4 == 0:
            _delay(base=8, jitter=4)
        else:
            _delay(base=2.5, jitter=1.5)

    return prospects

# ── SCRAPER POR LOCATION TAG ──────────────────────────────────────
def scrape_location(location_id, amount, min_f, max_f, fw, only_ar, min_er=0):
    """Scraping por ubicación de Instagram (location_id = ID numérico de IG)"""
    prospects = []
    seen = set()
    print(f"  🔍 Location {location_id}...")

    try:
        medias = _retry(
            lambda: cl.location_medias_recent(location_id, amount=amount),
            label=f"location {location_id}"
        )
    except Exception as e:
        print(f"  ✗ Error location: {e}")
        return []

    for media in medias:
        uid = str(media.user.pk)
        if uid in seen: continue
        seen.add(uid)
        try:
            info = _retry(lambda u=uid: cl.user_info(u), label="user_info")
            if not info.biography: continue
            p = build_prospect(info, f"location:{location_id}", min_f, max_f, fw, only_ar, min_er)
            if p:
                prospects.append(p)
                print(f"    ✓ @{info.username}")
        except: continue
        _delay(base=2, jitter=1)

    return prospects

# ── LINKEDIN SCRAPER MEJORADO ─────────────────────────────────────
import urllib.parse as _urlparse

li_cookies = None

def li_login(email, password):
    global li_cookies
    if not PLAYWRIGHT_OK:
        return False, "Playwright no instalado"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="es-AR",
            )
            page = ctx.new_page()
            page.goto("https://www.linkedin.com/login", wait_until="networkidle")
            page.wait_for_timeout(random.randint(800, 1500))
            page.fill("#username", email)
            page.wait_for_timeout(random.randint(300, 700))
            page.fill("#password", password)
            page.wait_for_timeout(random.randint(400, 900))
            page.click("[type=submit]")
            page.wait_for_timeout(5000)

            if "feed" in page.url or "checkpoint" in page.url or "home" in page.url:
                li_cookies = ctx.cookies()
                browser.close()
                print(f"  ✓ LinkedIn login OK — {email}")
                return True, "ok"
            else:
                current = page.url
                browser.close()
                return False, f"Login fallido — URL actual: {current}"
    except Exception as e:
        return False, str(e)


def scrape_linkedin(query, location, amount, only_ar):
    """
    Scraping LinkedIn mejorado:
    1. Usa cookies guardadas para buscar companies directamente en LinkedIn
    2. Fallback a Google si no hay sesión
    """
    if PLAYWRIGHT_OK and li_cookies:
        return _scrape_linkedin_direct(query, location, amount, only_ar)
    return _scrape_linkedin_google(query, location, amount, only_ar)


def _scrape_linkedin_direct(query, location, amount, only_ar):
    """Busca empresas directamente en LinkedIn con sesión activa."""
    prospects = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"]
            )
            ctx = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="es-AR",
            )
            ctx.add_cookies(li_cookies)
            page = ctx.new_page()

            loc_q = location if location and not only_ar else "Argentina" if only_ar else ""
            encoded = _urlparse.quote(f"{query} {loc_q}".strip())
            url = f"https://www.linkedin.com/search/results/companies/?keywords={encoded}&origin=GLOBAL_SEARCH_HEADER"

            page.goto(url, wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(3000)

            # Scroll para cargar más resultados
            for _ in range(3):
                page.keyboard.press("End")
                page.wait_for_timeout(random.randint(1200, 2000))

            # Extraer tarjetas de empresas
            cards = page.query_selector_all(".entity-result__item")
            print(f"  LinkedIn directo: {len(cards)} empresas")

            for card in cards[:amount]:
                try:
                    name_el  = card.query_selector(".entity-result__title-text a, .app-aware-link")
                    desc_el  = card.query_selector(".entity-result__primary-subtitle, .entity-result__secondary-subtitle")
                    loc_el   = card.query_selector(".entity-result__secondary-subtitle")
                    link_el  = card.query_selector("a.app-aware-link")

                    if not name_el: continue
                    name   = name_el.inner_text().strip().replace("| LinkedIn", "").strip()
                    link   = link_el.get_attribute("href") if link_el else ""
                    desc   = desc_el.inner_text().strip() if desc_el else ""
                    loc_t  = loc_el.inner_text().strip() if loc_el else ""

                    if not name or len(name) < 2: continue
                    text_lower = (name + " " + desc + " " + loc_t).lower()
                    is_ar = any(kw in text_lower for kw in ["argentina", "buenos aires", "córdoba", "rosario", "mendoza", "caba", "🇦🇷"])
                    if only_ar and not is_ar: continue

                    # Limpiar URL de LinkedIn (quitar params de tracking)
                    clean_link = link.split("?")[0] if link else ""

                    prospect = {
                        "brand":    name,
                        "contact":  "",
                        "email":    extract_email(desc),
                        "url":      "",
                        "problem":  "sin presencia web optimizada",
                        "source":   f"LinkedIn directo — {query}",
                        "notes":    f"{desc} · {loc_t}"[:200],
                        "li_url":   clean_link,
                        "industry": query,
                        "location": loc_t or location,
                        "is_ar":    is_ar,
                    }
                    prospects.append(prospect)
                    print(f"    ✓ {name}")
                except Exception as e:
                    print(f"    ⚠ Error en card: {e}")
                    continue

            browser.close()
    except Exception as e:
        print(f"  ✗ LinkedIn directo error: {e}")
        return _scrape_linkedin_google(query, location, amount, only_ar)

    return prospects


def _scrape_linkedin_google(query, location, amount, only_ar):
    """Fallback: busca empresas vía Google con site:linkedin.com/company"""
    if not PLAYWRIGHT_OK: return []
    prospects = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            loc_q = f" {location}" if location and not only_ar else " Argentina" if only_ar else ""
            google_q = _urlparse.quote(f'{query}{loc_q} site:linkedin.com/company')
            page.goto(
                f"https://www.google.com/search?q={google_q}&num={min(amount + 5, 20)}",
                wait_until="domcontentloaded",
                timeout=15000
            )
            page.wait_for_timeout(2000)

            results = page.query_selector_all("div.g")
            print(f"  Google → LinkedIn: {len(results)} resultados")

            for result in results[:amount]:
                try:
                    title_el = result.query_selector("h3")
                    link_el  = result.query_selector("a[href]")
                    desc_el  = result.query_selector("div[style*='webkit'], div.VwiC3b, span.st, div[data-sncf]")
                    if not title_el or not link_el: continue
                    title = title_el.inner_text().strip()
                    link  = link_el.get_attribute("href") or ""
                    desc  = desc_el.inner_text().strip() if desc_el else ""
                    if "linkedin.com/company" not in link: continue
                    name = re.sub(r"\s*[\|–\-]\s*LinkedIn.*$", "", title).strip()
                    if not name: continue
                    text_lower = (name + " " + desc).lower()
                    is_ar = any(kw in text_lower for kw in ["argentina", "buenos aires", "córdoba", "rosario", "mendoza", "caba", "🇦🇷"])
                    if only_ar and not is_ar: continue
                    prospects.append({
                        "brand":    name,
                        "contact":  "",
                        "email":    extract_email(desc),
                        "url":      "",
                        "problem":  "sin presencia web optimizada",
                        "source":   f"LinkedIn via Google — {query}",
                        "notes":    desc[:150],
                        "li_url":   link,
                        "industry": query,
                        "location": location,
                        "is_ar":    is_ar,
                    })
                    print(f"    ✓ {name}")
                except: continue
            browser.close()
    except Exception as e:
        print(f"  ✗ LinkedIn/Google error: {e}")
    return prospects

# ── HTTP HANDLER ──────────────────────────────────────────────────
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            self.send_json({
                "ok": True,
                "logged_in": cl is not None,
                "playwright": PLAYWRIGHT_OK,
                "li_logged_in": li_cookies is not None,
                "version": "v3"
            })
        elif self.path == "/favorites":
            self.send_json({"ok": True, "favorites": load_favorites()})
        else:
            self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = json.loads(self.rfile.read(length)) if length else {}

        if self.path == "/login":
            ok, msg = do_login(body.get("username", ""), body.get("password", ""))
            self.send_json({"ok": ok, "error": msg if not ok else None})

        elif self.path == "/scrape":
            if not cl:
                self.send_json({"ok": False, "error": "No logueado en Instagram"}); return
            mode    = body.get("mode", "hashtag")
            min_f   = int(body.get("min_followers", 100))
            max_f   = int(body.get("max_followers", 500000))
            fw      = body.get("filter_web", "any")
            only_ar = body.get("only_ar", False)
            min_er  = float(body.get("min_engagement", 0))

            with _lock:
                try:
                    prospects = []
                    if mode in ("hashtag", "combined"):
                        ht = body.get("hashtag", "").strip().replace("#", "")
                        if ht:
                            prospects += scrape_hashtag(
                                ht, int(body.get("hq", 30)),
                                min_f, max_f, fw, only_ar, min_er
                            )
                    if mode in ("followers", "combined"):
                        acc = body.get("account", "").strip().replace("@", "")
                        if acc:
                            prospects += scrape_followers(
                                acc, int(body.get("fq", 50)),
                                min_f, max_f, fw, only_ar, min_er
                            )
                    if mode == "location":
                        loc_id = body.get("location_id", "").strip()
                        if loc_id:
                            prospects += scrape_location(
                                loc_id, int(body.get("hq", 30)),
                                min_f, max_f, fw, only_ar, min_er
                            )

                    # Deduplicar por ig_username
                    seen = set(); deduped = []
                    for p in prospects:
                        key = p.get("ig_username") or p.get("brand")
                        if key and key not in seen:
                            seen.add(key); deduped.append(p)

                    self.send_json({"ok": True, "prospects": deduped})
                except Exception as e:
                    self.send_json({"ok": False, "error": str(e)})

        elif self.path == "/screenshot":
            img = screenshot_url(body.get("url", ""))
            self.send_json({"ok": bool(img), "image": img})

        elif self.path == "/favorites/save":
            favs = load_favorites()
            fav  = body.get("favorite", {})
            favs = [f for f in favs if f.get("name") != fav.get("name")]
            favs.insert(0, fav)
            save_favorites(favs[:20])
            self.send_json({"ok": True})

        elif self.path == "/favorites/delete":
            favs = [f for f in load_favorites() if f.get("name") != body.get("name", "")]
            save_favorites(favs)
            self.send_json({"ok": True})

        elif self.path == "/linkedin/login":
            ok, msg = li_login(body.get("email", ""), body.get("password", ""))
            self.send_json({"ok": ok, "error": msg if not ok else None})

        elif self.path == "/linkedin/scrape":
            try:
                results = scrape_linkedin(
                    body.get("query", ""),
                    body.get("location", "Argentina"),
                    int(body.get("amount", 20)),
                    body.get("only_ar", False),
                )
                self.send_json({"ok": True, "prospects": results})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})

        else:
            self.send_json({"error": "not found"}, 404)


if __name__ == "__main__":
    print("=" * 52)
    print("  IG Scraper Backend v3 — DiazUX Studio")
    print(f"  Playwright: {'✓ disponible' if PLAYWRIGHT_OK else '✗ no instalado'}")
    print("  Anti-ban: delays gaussianos + retry + fingerprint")
    print("=" * 52)
    print("\n  Abrí ig-scraper.html en el navegador\n")
    server = HTTPServer(("localhost", 8765), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Detenido.")
