"""
PrivacyScrub — Data Broker Scanning Engine

Threaded scanner that searches people-search sites for user profiles.
Features:
    - Rate limiting per domain to avoid blocks
    - User-agent rotation via fake-useragent
    - Proxy support (HTTP/SOCKS) via configuration
    - Concurrent scanning with configurable thread pool
    - Scan batching with unique batch IDs
    - Webhook notifications on completion

Architecture:
    ScanEngine.start_scan() → spawns a background thread that:
        1. Loads broker database from brokers.json
        2. Splits brokers across N worker threads
        3. Each worker: build search URL → fetch → parse → score depth
        4. Results saved to DB as they arrive
        5. On completion, fires webhook if configured
"""

import hashlib
import json
import os
import re
import time
import uuid
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional, Callable
from urllib.parse import quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from fake_useragent import UserAgent
    _ua = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
except Exception:
    _ua = None

from models import (
    save_scan_result, log_activity, get_setting, get_all_profiles,
    get_profile, db_session,
)

logger = logging.getLogger("privacyscrub.scanner")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BROKERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brokers.json")
DEFAULT_TIMEOUT = 15          # seconds per HTTP request
DEFAULT_CONCURRENCY = 3       # simultaneous scan threads
RATE_LIMIT_DELAY = 2.0        # seconds between requests to same domain
MAX_RETRIES = 2               # retry failed requests

# Data type keywords for depth scoring
DATA_TYPE_KEYWORDS = {
    "name": ["name", "full name", "first name", "last name"],
    "address": ["address", "street", "city", "state", "zip", "lives in", "resided"],
    "phone": ["phone", "telephone", "mobile", "cell", "landline", "call"],
    "email": ["email", "e-mail", "mail"],
    "age": ["age", "born", "birth", "years old", "dob"],
    "relatives": ["relative", "family", "associate", "related to", "mother", "father",
                   "spouse", "sibling", "brother", "sister", "son", "daughter"],
    "employment": ["work", "employ", "job", "occupation", "company", "employer"],
    "education": ["school", "university", "college", "education", "degree", "graduated"],
    "social": ["facebook", "twitter", "linkedin", "instagram", "social"],
    "criminal": ["criminal", "arrest", "court", "felony", "misdemeanor", "offense"],
    "property": ["property", "home value", "real estate", "owns", "deed"],
    "financial": ["income", "net worth", "financial", "credit", "salary"],
}


# ---------------------------------------------------------------------------
# Scan State — In-memory tracking of running scans
# ---------------------------------------------------------------------------

class ScanState:
    """Thread-safe container for tracking active scans."""

    def __init__(self):
        self._lock = threading.Lock()
        self._scans: dict[str, dict] = {}  # batch_id → state dict

    def start(self, batch_id: str, profile_id: int, total_brokers: int) -> None:
        with self._lock:
            self._scans[batch_id] = {
                "batch_id": batch_id,
                "profile_id": profile_id,
                "status": "running",
                "total": total_brokers,
                "completed": 0,
                "found": 0,
                "errors": 0,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "current_broker": "",
            }

    def update(self, batch_id: str, **kwargs) -> None:
        with self._lock:
            if batch_id in self._scans:
                self._scans[batch_id].update(kwargs)

    def increment(self, batch_id: str, field: str, amount: int = 1) -> None:
        with self._lock:
            if batch_id in self._scans:
                self._scans[batch_id][field] = self._scans[batch_id].get(field, 0) + amount

    def finish(self, batch_id: str, status: str = "completed") -> None:
        with self._lock:
            if batch_id in self._scans:
                self._scans[batch_id]["status"] = status
                self._scans[batch_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    def get(self, batch_id: str) -> Optional[dict]:
        with self._lock:
            return self._scans.get(batch_id, {}).copy()

    def get_latest(self, profile_id: int) -> Optional[dict]:
        with self._lock:
            for scan in sorted(
                self._scans.values(),
                key=lambda s: s.get("started_at", ""),
                reverse=True,
            ):
                if scan["profile_id"] == profile_id:
                    return scan.copy()
            return None

    def list_active(self) -> list[dict]:
        with self._lock:
            return [s.copy() for s in self._scans.values() if s["status"] == "running"]


# Singleton scan state
scan_state = ScanState()


# ---------------------------------------------------------------------------
# Broker Database Loader
# ---------------------------------------------------------------------------

def load_brokers(filepath: str | None = None) -> list[dict]:
    """
    Load the broker database from JSON.

    Args:
        filepath: Override path to brokers.json.

    Returns:
        List of broker dicts.
    """
    path = filepath or BROKERS_FILE
    if not os.path.exists(path):
        logger.warning("Brokers database not found at %s — returning empty list", path)
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            brokers = json.load(f)
        if isinstance(brokers, list):
            return brokers
        logger.error("brokers.json must be a JSON array")
        return []
    except (json.JSONDecodeError, IOError) as e:
        logger.error("Failed to load brokers.json: %s", e)
        return []


def get_brokers_by_category(category: str | None = None) -> list[dict]:
    """
    Get brokers, optionally filtered by category.

    Args:
        category: Category slug to filter (e.g. 'people_search').

    Returns:
        Filtered list of broker dicts.
    """
    brokers = load_brokers()
    if category:
        return [b for b in brokers if b.get("category") == category]
    return brokers


# ---------------------------------------------------------------------------
# User-Agent Rotation & Request Helpers
# ---------------------------------------------------------------------------

def _get_user_agent() -> str:
    """Return a randomised user-agent string."""
    if _ua:
        try:
            return _ua.random
        except Exception:
            pass
    return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"


def _get_proxies() -> dict | None:
    """Load proxy configuration from settings."""
    proxy_url = get_setting("proxy_url", "")
    if proxy_url:
        return {"http": proxy_url, "https": proxy_url}
    return None


def _make_request(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> Optional[requests.Response]:
    """
    Make an HTTP GET request with user-agent rotation, proxy support,
    and automatic retries.

    Args:
        url: Target URL.
        timeout: Request timeout in seconds.
        retries: Number of retry attempts.

    Returns:
        Response object or None on failure.
    """
    headers = {
        "User-Agent": _get_user_agent(),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    proxies = _get_proxies()

    for attempt in range(retries + 1):
        try:
            resp = requests.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                verify=True,
            )
            if resp.status_code == 200:
                return resp
            elif resp.status_code == 429:
                # Rate limited — back off
                wait = min(30, (2 ** attempt) * 5)
                logger.warning("Rate limited on %s — waiting %ds", url, wait)
                time.sleep(wait)
            elif resp.status_code in (403, 503):
                logger.info("Blocked (%d) on %s — attempt %d", resp.status_code, url, attempt + 1)
                time.sleep(2 * (attempt + 1))
            else:
                logger.debug("HTTP %d from %s", resp.status_code, url)
                return None
        except requests.RequestException as e:
            logger.debug("Request error for %s: %s", url, e)
            if attempt < retries:
                time.sleep(1 * (attempt + 1))

    return None


# ---------------------------------------------------------------------------
# Search URL Builders (per-broker-type strategies)
# ---------------------------------------------------------------------------

def _build_search_url(broker: dict, first_name: str, last_name: str,
                      city: str = "", state: str = "") -> str:
    """
    Build the search URL for a given broker and person.

    Uses broker-specific URL patterns when known, falls back to
    appending a search query to the broker's base URL.

    Args:
        broker: Broker dict from brokers.json.
        first_name: Person's first name.
        last_name: Person's last name.
        city: City (optional).
        state: State abbreviation (optional).

    Returns:
        Fully-formed search URL string.
    """
    broker_id = broker.get("id", "").lower()
    base_url = broker.get("url", "").rstrip("/")
    name_query = quote_plus(f"{first_name} {last_name}")
    location = f"{city} {state}".strip()
    location_query = quote_plus(location) if location else ""

    # --- Site-specific URL patterns ---
    patterns = {
        "whitepages": f"https://www.whitepages.com/name/{first_name}-{last_name}" +
                      (f"/{city}-{state}" if city and state else ""),
        "spokeo": f"https://www.spokeo.com/{first_name}-{last_name}",
        "beenverified": f"https://www.beenverified.com/people/{first_name}-{last_name}/",
        "truepeoplesearch": f"https://www.truepeoplesearch.com/results?name={name_query}" +
                            (f"&citystatezip={location_query}" if location_query else ""),
        "fastpeoplesearch": f"https://www.fastpeoplesearch.com/name/{first_name}-{last_name}" +
                            (f"_{city}-{state}" if city and state else ""),
        "radaris": f"https://radaris.com/p/{first_name}/{last_name}/",
        "intelius": f"https://www.intelius.com/people-search/{first_name}-{last_name}" +
                    (f"/{state}" if state else ""),
        "peoplefinders": f"https://www.peoplefinders.com/people/{first_name}-{last_name}" +
                         (f"/{state}" if state else ""),
        "mylife": f"https://www.mylife.com/pub/name/{first_name}-{last_name}",
        "nuwber": f"https://nuwber.com/search?name={name_query}",
        "usphonebook": f"https://www.usphonebook.com/{first_name}-{last_name}",
        "anywho": f"https://www.anywho.com/people/{first_name}+{last_name}" +
                  (f"/{city}+{state}" if city and state else ""),
        "peekyou": f"https://www.peekyou.com/{first_name}_{last_name}",
        "familytreenow": f"https://www.familytreenow.com/search/people?first={quote_plus(first_name)}&last={quote_plus(last_name)}",
        "thatsthem": f"https://thatsthem.com/name/{first_name}-{last_name}" +
                     (f"/{city}-{state}" if city and state else ""),
        "zabasearch": f"https://www.zabasearch.com/people/{first_name}+{last_name}/",
    }

    if broker_id in patterns:
        return patterns[broker_id]

    # Fallback: use the broker's base URL + /search or /people path
    if "search" in base_url.lower():
        return f"{base_url}?q={name_query}"
    return f"{base_url}/people/{first_name}-{last_name}"


# ---------------------------------------------------------------------------
# Content Analysis — Determine if a person's data appears on the page
# ---------------------------------------------------------------------------

def _analyze_page(
    html: str,
    first_name: str,
    last_name: str,
    city: str = "",
    state: str = "",
    phone: str = "",
) -> dict:
    """
    Analyse scraped HTML to determine if the target person is listed.

    Returns:
        {
            "found": bool,
            "data_types_found": list[str],
            "data_depth_score": float,  # 0.0–1.0
            "listing_url": str,
        }
    """
    result = {
        "found": False,
        "data_types_found": [],
        "data_depth_score": 0.0,
        "listing_url": "",
    }

    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    text = soup.get_text(separator=" ", strip=True).lower()

    # Check if the person's name appears on the page
    full_name_lower = f"{first_name} {last_name}".lower()
    if full_name_lower not in text:
        return result

    # Name found — now check what data types are exposed
    result["found"] = True
    found_types = set()

    for data_type, keywords in DATA_TYPE_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text:
                found_types.add(data_type)
                break

    # Extra checks: specific data present
    if city and city.lower() in text:
        found_types.add("address")
    if state and state.lower() in text:
        found_types.add("address")
    if phone:
        # Normalise phone: strip non-digits
        clean_phone = re.sub(r"\D", "", phone)
        if len(clean_phone) >= 7 and clean_phone[-7:] in re.sub(r"\D", "", text):
            found_types.add("phone")

    result["data_types_found"] = sorted(found_types)

    # Calculate data depth score (0.0–1.0)
    total_possible = len(DATA_TYPE_KEYWORDS)
    result["data_depth_score"] = round(len(found_types) / total_possible, 3) if total_possible else 0.0

    # Try to extract direct profile link
    for link in soup.find_all("a", href=True):
        href = link["href"]
        link_text = link.get_text(strip=True).lower()
        if first_name.lower() in link_text and last_name.lower() in link_text:
            if href.startswith("http"):
                result["listing_url"] = href
            break

    return result


# ---------------------------------------------------------------------------
# Single Broker Scanner
# ---------------------------------------------------------------------------

def _scan_single_broker(
    broker: dict,
    first_name: str,
    last_name: str,
    city: str = "",
    state: str = "",
    phone: str = "",
    profile_id: int = 0,
    batch_id: str = "",
) -> dict:
    """
    Scan a single broker for a person's data.

    Args:
        broker: Broker dict from brokers.json.
        first_name, last_name, city, state, phone: Search params.
        profile_id: DB profile ID.
        batch_id: Scan batch identifier.

    Returns:
        Scan result dict ready for save_scan_result().
    """
    broker_id = broker.get("id", "unknown")
    broker_name = broker.get("name", broker_id)

    result = {
        "profile_id": profile_id,
        "broker_id": broker_id,
        "broker_name": broker_name,
        "broker_category": broker.get("category", ""),
        "found": 0,
        "listing_url": "",
        "data_types_found": [],
        "data_depth_score": 0.0,
        "scan_batch_id": batch_id,
    }

    try:
        url = _build_search_url(broker, first_name, last_name, city, state)
        logger.debug("Scanning %s: %s", broker_name, url)

        response = _make_request(url)
        if response is None:
            logger.debug("No response from %s", broker_name)
            return result

        analysis = _analyze_page(
            response.text, first_name, last_name, city, state, phone
        )

        result["found"] = 1 if analysis["found"] else 0
        result["data_types_found"] = analysis["data_types_found"]
        result["data_depth_score"] = analysis["data_depth_score"]
        result["listing_url"] = analysis.get("listing_url", url if analysis["found"] else "")

    except Exception as e:
        logger.error("Error scanning broker %s: %s", broker_name, e)

    return result


# ---------------------------------------------------------------------------
# Scan Engine — Orchestrates threaded scanning
# ---------------------------------------------------------------------------

class ScanEngine:
    """
    Orchestrates data broker scanning with a thread pool.

    Usage:
        engine = ScanEngine()
        batch_id = engine.start_scan(profile_id=1)
        status = engine.get_status(batch_id)
    """

    def __init__(self, max_workers: int | None = None):
        """
        Args:
            max_workers: Max concurrent scan threads.
                         Defaults to scan_concurrency setting.
        """
        self.max_workers = max_workers or int(get_setting("scan_concurrency", str(DEFAULT_CONCURRENCY)))
        self._rate_limits: dict[str, float] = {}  # domain → last_request_time
        self._rate_lock = threading.Lock()

    def start_scan(
        self,
        profile_id: int,
        categories: list[str] | None = None,
        broker_ids: list[str] | None = None,
        on_complete: Callable | None = None,
    ) -> str:
        """
        Start a background scan for the given profile.

        Args:
            profile_id: Profile to scan for.
            categories: Filter to specific broker categories (optional).
            broker_ids: Filter to specific broker IDs (optional).
            on_complete: Callback fired when scan finishes (receives batch_id, results).

        Returns:
            batch_id: Unique identifier for this scan run.

        Raises:
            ValueError: If profile not found.
        """
        profile = get_profile(profile_id)
        if not profile:
            raise ValueError(f"Profile {profile_id} not found")

        batch_id = f"scan-{uuid.uuid4().hex[:12]}"
        brokers = load_brokers()

        # Apply filters
        if categories:
            brokers = [b for b in brokers if b.get("category") in categories]
        if broker_ids:
            brokers = [b for b in brokers if b.get("id") in broker_ids]

        if not brokers:
            logger.warning("No brokers to scan after filters")
            return batch_id

        scan_state.start(batch_id, profile_id, len(brokers))

        log_activity(
            None, profile_id, "scan_started", "scan",
            f"Scan started: {len(brokers)} brokers, batch {batch_id}",
            {"batch_id": batch_id, "broker_count": len(brokers)},
        )

        # Launch background thread
        thread = threading.Thread(
            target=self._run_scan,
            args=(batch_id, profile, brokers, on_complete),
            daemon=True,
            name=f"scan-{batch_id}",
        )
        thread.start()

        return batch_id

    def _run_scan(
        self,
        batch_id: str,
        profile: dict,
        brokers: list[dict],
        on_complete: Callable | None,
    ) -> None:
        """
        Execute the scan in background thread(s).

        This is the main scan loop that distributes work to a thread pool.
        """
        first_name = profile.get("first_name", "")
        last_name = profile.get("last_name", "")
        city = profile.get("city", "")
        state = profile.get("state", "")
        phone = profile.get("phone", "")
        profile_id = profile["id"]

        all_results = []

        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {}
                for broker in brokers:
                    # Rate-limit per domain
                    self._rate_limit(broker.get("url", ""))

                    future = executor.submit(
                        _scan_single_broker,
                        broker, first_name, last_name, city, state, phone,
                        profile_id, batch_id,
                    )
                    futures[future] = broker

                for future in as_completed(futures):
                    broker = futures[future]
                    scan_state.update(batch_id, current_broker=broker.get("name", ""))

                    try:
                        result = future.result(timeout=60)
                        all_results.append(result)

                        # Save to DB immediately
                        save_scan_result(result)

                        scan_state.increment(batch_id, "completed")
                        if result.get("found"):
                            scan_state.increment(batch_id, "found")

                    except Exception as e:
                        logger.error("Scan worker error for %s: %s",
                                     broker.get("name", "?"), e)
                        scan_state.increment(batch_id, "completed")
                        scan_state.increment(batch_id, "errors")

            scan_state.finish(batch_id, "completed")

            found_count = sum(1 for r in all_results if r.get("found"))
            log_activity(
                None, profile_id, "scan_completed", "scan",
                f"Scan complete: {found_count}/{len(brokers)} brokers found data",
                {"batch_id": batch_id, "found": found_count, "total": len(brokers)},
            )

            # Fire webhook if configured
            self._fire_webhook(batch_id, profile, all_results)

            # Dispatch to registered webhooks (/api/webhooks registry)
            try:
                from webhooks import dispatch
                dispatch("scan.complete", {
                    "scan_id": batch_id,
                    "profile_id": profile_id,
                    "total_scanned": len(all_results),
                    "found": found_count,
                })
            except Exception as e:
                logger.error("Webhook dispatch error: %s", e)

        except Exception as e:
            logger.exception("Scan batch %s failed: %s", batch_id, e)
            scan_state.finish(batch_id, "failed")
            log_activity(
                None, profile_id, "scan_failed", "scan",
                f"Scan failed: {e}",
                {"batch_id": batch_id, "error": str(e)},
            )

        # Completion callback
        if on_complete:
            try:
                on_complete(batch_id, all_results)
            except Exception as e:
                logger.error("Scan completion callback error: %s", e)

    def _rate_limit(self, url: str) -> None:
        """Enforce per-domain rate limiting."""
        if not url:
            return
        domain = urlparse(url).netloc
        if not domain:
            return

        with self._rate_lock:
            last_time = self._rate_limits.get(domain, 0)
            elapsed = time.time() - last_time
            if elapsed < RATE_LIMIT_DELAY:
                time.sleep(RATE_LIMIT_DELAY - elapsed)
            self._rate_limits[domain] = time.time()

    def _fire_webhook(self, batch_id: str, profile: dict, results: list[dict]) -> None:
        """Send scan results to the configured webhook URL."""
        webhook_url = get_setting("webhook_url", "")
        if not webhook_url:
            return

        found_count = sum(1 for r in results if r.get("found"))
        payload = {
            "event": "scan_completed",
            "batch_id": batch_id,
            "profile_name": f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip(),
            "total_scanned": len(results),
            "total_found": found_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            webhook_secret = get_setting("webhook_secret", "")
            headers = {"Content-Type": "application/json"}
            if webhook_secret:
                sig = hashlib.sha256(
                    (json.dumps(payload, sort_keys=True) + webhook_secret).encode()
                ).hexdigest()
                headers["X-PrivacyScrub-Signature"] = sig

            requests.post(
                webhook_url,
                json=payload,
                headers=headers,
                timeout=10,
            )
            logger.info("Webhook sent to %s", webhook_url)
        except Exception as e:
            logger.error("Webhook failed: %s", e)

    def get_status(self, batch_id: str) -> Optional[dict]:
        """Get current status of a scan batch."""
        return scan_state.get(batch_id)

    def get_active_scans(self) -> list[dict]:
        """Get all currently running scans."""
        return scan_state.list_active()


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

# Default engine instance
_default_engine: ScanEngine | None = None


def get_engine() -> ScanEngine:
    """Get or create the default scan engine."""
    global _default_engine
    if _default_engine is None:
        _default_engine = ScanEngine()
    return _default_engine


def start_scan(profile_id: int, **kwargs) -> str:
    """Convenience wrapper to start a scan with the default engine."""
    return get_engine().start_scan(profile_id, **kwargs)


def get_scan_status(batch_id: str) -> Optional[dict]:
    """Convenience wrapper to check scan status."""
    return get_engine().get_status(batch_id)


def get_broker_count() -> int:
    """Return total number of brokers in the database."""
    return len(load_brokers())


def get_broker_categories() -> list[str]:
    """Return sorted list of unique broker categories."""
    brokers = load_brokers()
    return sorted(set(b.get("category", "unknown") for b in brokers))
