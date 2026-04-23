import json
import os
import re
import socket
import ssl
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

import requests
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None


class _SimpleHTMLInspector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta_description = False
        self.h1_count = 0
        self.img_without_alt = 0
        self.total_img = 0
        self.has_contact_form = False
        self.has_whatsapp = False
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "meta" and attrs.get("name", "").lower() == "description" and attrs.get("content"):
            self.meta_description = True
        if tag == "h1":
            self.h1_count += 1
        if tag == "img":
            self.total_img += 1
            if not attrs.get("alt"):
                self.img_without_alt += 1
        if tag == "form":
            blob = (attrs.get("id", "") + " " + attrs.get("class", "") + " " + attrs.get("action", "")).lower()
            if any(k in blob for k in ["contact", "consulta", "lead", "whatsapp"]):
                self.has_contact_form = True
        if tag == "a":
            href = attrs.get("href", "")
            if href:
                self.links.append(href)
                if "wa.me" in href or "whatsapp" in href:
                    self.has_whatsapp = True


def _detect_stack(html: str, headers: dict) -> list:
    low = html.lower()
    stack = []
    checks = {
        "wordpress": ["wp-content", "wordpress"],
        "wix": ["wix.com", "wixstatic"],
        "squarespace": ["squarespace"],
        "shopify": ["cdn.shopify.com", "shopify"],
        "react": ["react", "__next"],
    }
    for tech, needles in checks.items():
        if any(n in low for n in needles):
            stack.append(tech)
    server = (headers.get("server", "") or "").lower()
    if "cloudflare" in server:
        stack.append("cloudflare")
    if not stack:
        stack.append("custom")
    return sorted(set(stack))


def _extract_social_links(links: list) -> dict:
    out = {"instagram": "", "facebook": "", "linkedin": ""}
    for link in links:
        ll = link.lower()
        if "instagram.com" in ll and not out["instagram"]:
            out["instagram"] = link
        if "facebook.com" in ll and not out["facebook"]:
            out["facebook"] = link
        if "linkedin.com" in ll and not out["linkedin"]:
            out["linkedin"] = link
    return out


def _seo_score(inspector: _SimpleHTMLInspector, has_sitemap: bool) -> int:
    score = 0
    if inspector.meta_description:
        score += 30
    if inspector.h1_count > 0:
        score += 25
    if inspector.total_img == 0 or (inspector.img_without_alt / max(inspector.total_img, 1)) < 0.35:
        score += 25
    if has_sitemap:
        score += 20
    return min(score, 100)


def _has_ssl(url: str) -> bool:
    return urllib.parse.urlparse(url).scheme == "https"


def _mobile_friendly_from_pagespeed(data: dict) -> bool:
    try:
        categories = data["lighthouseResult"]["categories"]
        perf = categories["performance"]["score"] * 100
        return perf >= 50
    except Exception:
        return False


def _safe_get(url: str, timeout=15):
    try:
        return requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 ProspectorAI/2.0"})
    except Exception:
        return None


def audit_website(url: str) -> dict:
    if not url:
        return {
            "performance_score": 0,
            "mobile_friendly": False,
            "has_ssl": False,
            "load_time_ms": 0,
            "has_contact_form": False,
            "has_whatsapp": False,
            "tech_stack": [],
            "last_updated": "unknown",
            "seo_score": 0,
            "social_links": {},
            "missing_pages": ["servicios", "contacto"],
            "problems_detected": ["Sin sitio web"],
            "opportunity_summary": "No tiene sitio web propio, gran oportunidad de captación.",
            "recommended_service": "new_site",
        }

    parsed = urllib.parse.urlparse(url if url.startswith("http") else f"https://{url}")
    norm_url = parsed.geturl()

    perf_score = 0
    load_ms = 0
    mobile_friendly = False
    pagespeed_key = os.environ.get("GOOGLE_PAGESPEED_API_KEY", "")

    if pagespeed_key:
        try:
            ps_url = (
                "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
                f"?url={urllib.parse.quote(norm_url, safe='')}&key={pagespeed_key}&category=performance"
            )
            ps = _safe_get(ps_url, timeout=25)
            if ps is not None and ps.ok:
                data = ps.json()
                perf_score = int((data.get("lighthouseResult", {}).get("categories", {}).get("performance", {}).get("score", 0) or 0) * 100)
                load_ms = int(
                    (data.get("lighthouseResult", {})
                        .get("audits", {})
                        .get("speed-index", {})
                        .get("numericValue", 0)
                     ) or 0
                )
                mobile_friendly = _mobile_friendly_from_pagespeed(data)
        except Exception:
            pass

    r = _safe_get(norm_url)
    html = ""
    headers = {}
    if r is not None:
        html = r.text[:500000]
        headers = r.headers
        if load_ms == 0:
            load_ms = int((r.elapsed.total_seconds() or 0) * 1000)

    inspector = _SimpleHTMLInspector()
    if html:
        try:
            inspector.feed(html)
        except Exception:
            pass
    # Si BeautifulSoup está disponible, reforzar detecciones.
    if html and BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            if soup.find("meta", attrs={"name": re.compile("^description$", re.I)}):
                inspector.meta_description = True
            if soup.find("form"):
                inspector.has_contact_form = True
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if href not in inspector.links:
                    inspector.links.append(href)
                    if "wa.me" in href or "whatsapp" in href:
                        inspector.has_whatsapp = True
        except Exception:
            pass

    has_sitemap = _safe_get(norm_url.rstrip("/") + "/sitemap.xml") is not None
    seo = _seo_score(inspector, has_sitemap)

    if perf_score == 0:
        perf_score = 35 if load_ms > 5000 else 55 if load_ms > 2500 else 75
    if not mobile_friendly:
        viewport_ok = "name=\"viewport\"" in html.lower() or "name='viewport'" in html.lower()
        mobile_friendly = viewport_ok and perf_score >= 45

    missing_pages = []
    full = " ".join(inspector.links).lower() + " " + html.lower()
    for p in ["sobre", "servicios", "contacto"]:
        if p not in full:
            missing_pages.append(p)

    problems = []
    if perf_score < 50:
        problems.append(f"Performance baja ({perf_score}/100)")
    if not mobile_friendly:
        problems.append("No parece mobile-friendly")
    if not _has_ssl(norm_url):
        problems.append("No usa HTTPS")
    if missing_pages:
        problems.append(f"Faltan páginas clave: {', '.join(missing_pages)}")
    if not inspector.has_contact_form:
        problems.append("No se detectó formulario de contacto")

    recommended = "redesign"
    if "Sin sitio web" in problems or "404" in (html[:200] if html else ""):
        recommended = "new_site"
    elif perf_score < 35 and mobile_friendly is False:
        recommended = "full_package"

    opportunity = (
        "Detectamos oportunidades concretas para mejorar conversión, velocidad y experiencia mobile. "
        "DiazUX puede rediseñar o construir un sitio optimizado para captar más consultas."
    )

    return {
        "performance_score": int(perf_score),
        "mobile_friendly": bool(mobile_friendly),
        "has_ssl": _has_ssl(norm_url),
        "load_time_ms": int(load_ms),
        "has_contact_form": bool(inspector.has_contact_form),
        "has_whatsapp": bool(inspector.has_whatsapp),
        "tech_stack": _detect_stack(html, headers),
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
        "seo_score": int(seo),
        "social_links": _extract_social_links(inspector.links),
        "missing_pages": missing_pages,
        "problems_detected": problems,
        "opportunity_summary": opportunity,
        "recommended_service": recommended,
    }
