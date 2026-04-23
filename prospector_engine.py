import os
import re
import time
import urllib.parse
from typing import Dict, List

import requests


FUENTES = [
    "google_maps",
    "instagram_hashtags",
    "linkedin_search",
    "google_search",
    "directorios_ar",
]


class ProspectorEngine:
    def __init__(self):
        self.google_maps_key = os.environ.get("GOOGLE_MAPS_API_KEY", "")
        self.google_search_key = os.environ.get("GOOGLE_SEARCH_API_KEY", "")
        self.google_cse_id = os.environ.get("GOOGLE_CSE_ID", "")

    def find_prospects(self, query: str, industry: str = "", location: str = "", max_prospects: int = 20) -> List[Dict]:
        collected: List[Dict] = []
        for source in FUENTES:
            try:
                fn = getattr(self, f"_from_{source}")
                items = fn(query=query, industry=industry, location=location, max_items=max_prospects)
                collected.extend(items)
            except Exception:
                continue

        # dedupe by domain/brand
        uniq = {}
        for p in collected:
            key = (p.get("url") or p.get("brand") or "").lower().strip()
            if not key:
                continue
            if key not in uniq:
                uniq[key] = p

        out = list(uniq.values())[:max_prospects]
        for p in out:
            p["qualified_score"] = self.score_prospect(p)
        return out

    def score_prospect(self, prospect: Dict, audit_data: Dict = None) -> int:
        score = 0
        if prospect.get("instagram_handle") and not prospect.get("url"):
            score += 40
        if audit_data:
            if int(audit_data.get("performance_score", 0)) < 50:
                score += 30
            if not audit_data.get("mobile_friendly", False):
                score += 25
            if not audit_data.get("has_ssl", False):
                score += 20
            if audit_data.get("last_updated") and "20" in str(audit_data.get("last_updated")):
                try:
                    year = int(str(audit_data["last_updated"])[:4])
                    if year <= (time.gmtime().tm_year - 5):
                        score += 20
                except Exception:
                    pass

        followers = int(prospect.get("followers", 0) or 0)
        if followers and followers < 500:
            score += 10

        if not prospect.get("email") and not prospect.get("phone"):
            score -= 10
        return max(0, min(score, 100))

    def _from_google_maps(self, query: str, industry: str, location: str, max_items: int = 10) -> List[Dict]:
        if not self.google_maps_key:
            return []
        q = " ".join([query or "", industry or "", location or ""]).strip()
        url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        params = {"query": q, "key": self.google_maps_key}
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        data = r.json().get("results", [])[:max_items]
        out = []
        for it in data:
            out.append({
                "brand": it.get("name", ""),
                "contact": "",
                "email": "",
                "url": "",
                "phone": "",
                "instagram_handle": "",
                "linkedin_url": "",
                "source": "google_maps",
                "industry": industry,
                "location": location or it.get("formatted_address", ""),
            })
        return out

    def _from_google_search(self, query: str, industry: str, location: str, max_items: int = 10) -> List[Dict]:
        if not (self.google_search_key and self.google_cse_id):
            return []
        q = " ".join([query or "", industry or "", location or ""]).strip()
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": self.google_search_key, "cx": self.google_cse_id, "q": q, "num": min(max_items, 10)}
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        items = r.json().get("items", [])
        return [self._from_search_item(it, "google_search", industry, location) for it in items]

    def _from_instagram_hashtags(self, query: str, industry: str, location: str, max_items: int = 10) -> List[Dict]:
        # Fallback práctico: usa Google CSE para encontrar perfiles de Instagram de negocios
        if not (self.google_search_key and self.google_cse_id):
            return []
        q = f"site:instagram.com {industry or query} {location}"
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": self.google_search_key, "cx": self.google_cse_id, "q": q, "num": min(max_items, 10)}
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        out = []
        for it in r.json().get("items", []):
            link = it.get("link", "")
            handle = ""
            m = re.search(r"instagram\.com/([^/?#]+)/?", link)
            if m:
                handle = "@" + m.group(1)
            out.append({
                "brand": it.get("title", "").replace("• Instagram", "").strip(),
                "contact": "",
                "email": "",
                "url": "",
                "phone": "",
                "instagram_handle": handle,
                "linkedin_url": "",
                "source": "instagram_hashtags",
                "industry": industry,
                "location": location,
            })
        return out

    def _from_linkedin_search(self, query: str, industry: str, location: str, max_items: int = 10) -> List[Dict]:
        if not (self.google_search_key and self.google_cse_id):
            return []
        q = f"site:linkedin.com/company {query or industry} {location}"
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": self.google_search_key, "cx": self.google_cse_id, "q": q, "num": min(max_items, 10)}
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        out = []
        for it in r.json().get("items", []):
            out.append({
                "brand": it.get("title", "").split("|")[0].strip(),
                "contact": "",
                "email": "",
                "url": "",
                "phone": "",
                "instagram_handle": "",
                "linkedin_url": it.get("link", ""),
                "source": "linkedin_search",
                "industry": industry,
                "location": location,
            })
        return out

    def _from_directorios_ar(self, query: str, industry: str, location: str, max_items: int = 10) -> List[Dict]:
        if not (self.google_search_key and self.google_cse_id):
            return []
        q = f"(site:paginasamarillas.com.ar OR site:guiaoleo.com.ar) {query or industry} {location}"
        url = "https://www.googleapis.com/customsearch/v1"
        params = {"key": self.google_search_key, "cx": self.google_cse_id, "q": q, "num": min(max_items, 10)}
        r = requests.get(url, params=params, timeout=20)
        if not r.ok:
            return []
        return [self._from_search_item(it, "directorios_ar", industry, location) for it in r.json().get("items", [])]

    def _from_search_item(self, it: Dict, source: str, industry: str, location: str) -> Dict:
        link = it.get("link", "")
        brand = (it.get("title", "") or "").split("|")[0].strip()
        domain = ""
        try:
            domain = urllib.parse.urlparse(link).netloc
        except Exception:
            domain = ""
        return {
            "brand": brand,
            "contact": "",
            "email": "",
            "url": domain,
            "phone": "",
            "instagram_handle": "",
            "linkedin_url": "",
            "source": source,
            "industry": industry,
            "location": location,
        }
