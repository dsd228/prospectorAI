"""
═══════════════════════════════════════════════════════════════
PARCHE PARA server.py — Claude API + Content Studio endpoints
DiazUX Studio · Fase 1
═══════════════════════════════════════════════════════════════

INSTRUCCIONES:
1. Agregá ANTHROPIC_API_KEY en tus variables de entorno (o en Ajustes del CRM)
2. Reemplazá el bloque @app.route('/api/ai/generate') existente con el de abajo
3. Pegá los endpoints nuevos ANTES de la línea:  @app.route('/')
"""

# ── Reemplazar get_cfg() — agregar anthropic_api_key ─────────────
# En get_cfg(), sumá esta línea al dict env_map:
#   'anthropic_api_key': 'ANTHROPIC_API_KEY',


# ══════════════════════════════════════════════════════════════════
# HELPER: llamada a Claude API  (sin sdk, solo urllib)
# ══════════════════════════════════════════════════════════════════
def _claude(prompt, system="Sos un experto en marketing digital argentino. Respondés siempre en español rioplatense, de forma directa y profesional.", max_tokens=1000, temperature=0.7):
    """Llama a Claude claude-sonnet-4-20250514 via HTTP. Devuelve (texto, error)."""
    cfg = get_cfg()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "") or cfg.get("anthropic_api_key", "")
    if not api_key:
        return None, "Configurá ANTHROPIC_API_KEY en Ajustes o en variables de entorno"

    body = json.dumps({
        "model": "claude-sonnet-4-20250514",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system,
        "messages": [{"role": "user", "content": prompt}]
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=45) as r:
            res = json.loads(r.read())
        text = res["content"][0]["text"]
        return text, None
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8")
        print(f"Claude API error: {err}")
        try:
            msg = json.loads(err).get("error", {}).get("message", err)
        except:
            msg = err
        return None, msg
    except Exception as e:
        return None, str(e)


# ══════════════════════════════════════════════════════════════════
# REEMPLAZAR: /api/ai/generate  (antes usaba Gemini)
# ══════════════════════════════════════════════════════════════════
@app.route("/api/ai/generate", methods=["POST"])
def ai_generate():
    """Genera texto con Claude (fallback a Gemini si no hay clave Anthropic)."""
    d = request.json
    prompt = d.get("prompt", "")
    if not prompt:
        return jsonify({"ok": False, "error": "Prompt vacío"})

    # 1) Intentar Claude
    cfg = get_cfg()
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "") or cfg.get("anthropic_api_key", "")
    if anthropic_key:
        text, err = _claude(prompt)
        if text:
            return jsonify({"ok": True, "response": text, "model": "claude"})
        print(f"  Claude falló, intentando Gemini: {err}")

    # 2) Fallback Gemini
    gemini_key = os.environ.get("GEMINI_API_KEY", "") or cfg.get("gemini_api_key", "")
    if not gemini_key:
        return jsonify({"ok": False, "error": "Configurá ANTHROPIC_API_KEY o GEMINI_API_KEY"})

    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 1024, "temperature": 0.7}
    }).encode()
    try:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key={gemini_key}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.loads(r.read())
        text = res["candidates"][0]["content"]["parts"][0]["text"]
        return jsonify({"ok": True, "response": text, "model": "gemini"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})


# ══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/personalize  — email + DM personalizados por prospecto
# ══════════════════════════════════════════════════════════════════
@app.route("/api/ai/personalize", methods=["POST"])
def ai_personalize():
    """
    Genera email de outreach + DM de Instagram personalizados para un prospecto.
    Body: { prospect_id, sender_name, sender_company, sender_web, case_study, tone? }
    """
    d = request.json
    pid = d.get("prospect_id", "")
    
    if not pid:
        return jsonify({"ok": False, "error": "prospect_id requerido"})

    conn = get_db()
    p = conn.execute("SELECT * FROM prospects WHERE id=?", (pid,)).fetchone()
    conn.close()

    if not p:
        return jsonify({"ok": False, "error": "Prospecto no encontrado"})

    p = dict(p)
    cfg_data = get_cfg()
    sender  = d.get("sender_name",    "") or cfg_data.get("name", "David Díaz")
    company = d.get("sender_company", "") or cfg_data.get("company", "DiazUX Studio")
    web     = d.get("sender_web",     "") or cfg_data.get("web", "diazux.tech")
    case    = d.get("case_study",     "") or cfg_data.get("case_study", "")
    tone    = d.get("tone", "profesional pero cercano")

    prospect_ctx = f"""
Marca/empresa: {p.get("brand", "")}
URL del sitio: {p.get("url", "sin sitio web")}
Problema detectado: {p.get("problem", "")}
Nombre del contacto: {p.get("contact", "el/la dueño/a")}
Seguidores Instagram: {p.get("notes", "")[:100]}
Fuente: {p.get("source", "")}
""".strip()

    prompt = f"""
Sos {sender} de {company} ({web}), experto en diseño web, UX y conversión para marcas argentinas.

DATOS DEL PROSPECTO:
{prospect_ctx}

{"CASO DE ÉXITO PARA MENCIONAR: " + case if case else ""}

TONO: {tone}

Generá EXACTAMENTE este JSON (sin explicaciones, sin markdown, sin texto extra):
{{
  "email_subject": "Asunto del email (corto, directo, sin clichés como 'Espero que estés bien')",
  "email_body": "Cuerpo del email en texto plano. Máx 180 palabras. Párrafos separados con \\n\\n. Sin saludos genéricos. Empezá con un gancho basado en el problema específico. Mencioná el caso de éxito brevemente. CTA claro y específico al final.",
  "dm_ig": "Mensaje directo para Instagram. Máx 60 palabras. Informal, como hablaría un humano. Sin emojis de empresa. CTA con link o pedido de respuesta.",
  "dm_subject": "Primera línea del DM que llame la atención (máx 12 palabras)",
  "hook_audit": "Frase para el gancho del email basada en el problema detectado (máx 15 palabras)"
}}
"""

    text, err = _claude(prompt, max_tokens=700)
    if err:
        return jsonify({"ok": False, "error": err})

    # Parsear JSON de Claude
    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        # Buscar primer { y último }
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        return jsonify({"ok": True, **parsed})
    except Exception as e:
        return jsonify({"ok": True, "email_body": text, "raw": True})


# ══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/content  — captions para Stories, Reels y posts
# ══════════════════════════════════════════════════════════════════
@app.route("/api/ai/content", methods=["POST"])
def ai_content():
    """
    Genera contenido optimizado para Stories, Reels y posts de Instagram/LinkedIn.
    Body: {
      type: "story" | "reel" | "post" | "carousel",
      topic: "descripción del contenido o de la marca",
      niche: "moda" | "belleza" | "gastronomía" | "tech" | ...,
      objective: "vender" | "crecer" | "educar" | "fidelizar",
      tone: "premium" | "cercano" | "disruptivo" | "educativo",
      platform: "instagram" | "linkedin" | "ambas",
      include_hashtags: true/false
    }
    """
    d = request.json
    content_type = d.get("type", "post")
    topic        = d.get("topic", "")
    niche        = d.get("niche", "marca")
    objective    = d.get("objective", "crecer")
    tone_        = d.get("tone", "cercano")
    platform     = d.get("platform", "instagram")
    inc_tags     = d.get("include_hashtags", True)

    if not topic:
        return jsonify({"ok": False, "error": "Necesito saber de qué trata el contenido"})

    type_guide = {
        "story": "Historia de Instagram (texto corto, máx 3 slides, muy visual, CTA en el último slide)",
        "reel":  "Reel de Instagram (gancho en los primeros 2 seg, guión de 30-60 seg, transiciones, CTA al final)",
        "post":  "Post estático/carrusel para feed (caption persuasivo, estructura AIDA, CTA)",
        "carousel": "Carrusel de 5-8 slides (slide 1: gancho, slides 2-6: contenido de valor, slide final: CTA)",
    }.get(content_type, "Publicación en redes sociales")

    prompt = f"""
Sos un experto en contenido digital para marcas argentinas con tono {tone_}.

TIPO DE CONTENIDO: {type_guide}
TEMA/CONTEXTO: {topic}
NICHO: {niche}
OBJETIVO: {objective}
PLATAFORMA: {platform}

Generá EXACTAMENTE este JSON (sin markdown, sin texto extra afuera del JSON):
{{
  "hook": "Gancho principal — primera oración/texto que aparece (máx 10 palabras, que genere curiosidad o impacto)",
  "caption": "Caption completo listo para copiar. Párrafos separados con \\n\\n. Emojis moderados y estratégicos. Incluí el CTA al final.",
  "slides": ["Texto slide 1", "Texto slide 2", "..."],
  "cta": "Call to action específico y directo",
  "best_time": "Mejor horario para publicar este tipo de contenido (hora Argentina)",
  "hashtags": {{"instagram": ["tag1", "tag2", "...20 tags mix de nicho y masivos"], "linkedin": ["tag1", "tag2", "...5 tags profesionales"]}},
  "tips": ["Tip visual 1", "Tip visual 2", "Tip de producción"],
  "reel_script": "Guión completo del reel con timestamps si aplica (solo si type es reel)"
}}

IMPORTANTE: 
- Para stories: slides debe tener 3 elementos
- Para carruseles: slides debe tener 7-8 elementos  
- Para posts y reels: slides puede ser array vacío []
- Hashtags solo si include_hashtags = {inc_tags}
"""

    text, err = _claude(prompt, max_tokens=1200)
    if err:
        return jsonify({"ok": False, "error": err})

    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        start = clean.find("{")
        end   = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        parsed["type"]     = content_type
        parsed["platform"] = platform
        return jsonify({"ok": True, **parsed})
    except Exception as e:
        return jsonify({"ok": True, "caption": text, "raw": True})


# ══════════════════════════════════════════════════════════════════
# NUEVO: /api/ai/dm-campaign  — genera secuencia de DMs para prospectos
# ══════════════════════════════════════════════════════════════════
@app.route("/api/ai/dm-campaign", methods=["POST"])
def ai_dm_campaign():
    """
    Genera 3 mensajes de DM para una campaña de prospección.
    Body: { brand, problem, niche, sender_name, sender_company }
    """
    d = request.json
    prompt = f"""
Sos {d.get("sender_name","David")} de {d.get("sender_company","DiazUX Studio")}, experto en diseño web y conversión.

PROSPECTO:
- Marca: {d.get("brand","")}
- Problema: {d.get("problem","")}
- Nicho: {d.get("niche","")}

Generá una secuencia de 3 DMs de Instagram para esta marca. Cada uno en días distintos.
Respondé SOLO JSON sin markdown:
{{
  "dm1": {{
    "day": 0,
    "message": "Primer DM. Máx 50 palabras. Presentación + gancho basado en el problema. Sin sonar vendedor.",
    "subject_line": "Primera oración gancho"
  }},
  "dm2": {{
    "day": 3,
    "message": "Seguimiento. Máx 60 palabras. Aportá valor (insight o dato relevante al nicho). CTA suave.",
    "subject_line": "Primera oración"
  }},
  "dm3": {{
    "day": 7,
    "message": "Último intento. Máx 45 palabras. Directo, sin rodeos. Cierre o apertura de conversación.",
    "subject_line": "Primera oración"
  }}
}}
"""
    text, err = _claude(prompt, max_tokens=600)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        clean = text.strip().replace("```json", "").replace("```", "").strip()
        start = clean.find("{"); end = clean.rfind("}") + 1
        parsed = json.loads(clean[start:end])
        return jsonify({"ok": True, **parsed})
    except:
        return jsonify({"ok": True, "raw": text})
