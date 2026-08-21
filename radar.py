#!/usr/bin/env python3
"""
Radar de vacantes.

Consulta las páginas de carreras de un conjunto de empresas, detecta puestos
nuevos y arma un reporte con los que pasan los filtros.

    python3 radar.py --check     diagnóstico: consulta todo, imprime tabla, NO guarda
    python3 radar.py             corrida normal: detecta novedades y escribe reporte
    python3 radar.py --reset     borra el estado y vuelve a marcar la línea base
    python3 radar.py --test      auto-test de la lógica de filtros (sin red)

La primera corrida (sin archivo de estado) marca la línea base y no notifica.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from datetime import datetime, timezone

import yaml

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.yaml")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
REPORT_PATH = os.path.join(BASE_DIR, "report.md")

STATE_VERSION = 1
USER_AGENT = "job-radar/1.0 (+https://github.com/)"

# Intentos por petición, con esperas crecientes entre medio.
#
# No es paranoia de escala: el Eightfold de PayPal devuelve HTTP 500 en ~40% de
# las peticiones, al azar y después de 25-40 segundos. Su catálogo necesita 10
# páginas, así que con 2 intentos la probabilidad de completar la corrida era
# del 17%. Con 5 sube a ~90%.
HTTP_ATTEMPTS = 5
HTTP_BACKOFF = (2, 5, 10, 20)

# El endpoint de Eightfold ignora num > 10; hay que paginar de a 10.
EIGHTFOLD_PAGE_SIZE = 10

# Corridas consecutivas que un puesto tiene que faltar antes de borrarlo.
#
# No es paranoia: la paginación de Eightfold es inestable y devuelve un
# subconjunto distinto en cada corrida (medido: 97 puestos estables, 101 en la
# unión de tres corridas seguidas). Con borrado inmediato, esos puestos que
# oscilan se borran y reaparecen como "nuevos" una y otra vez. Con la gracia,
# siguen en el estado y no generan nada.
CLEANUP_GRACE_RUNS = 3

# Workday: limit > 20 devuelve HTTP 400.
WORKDAY_PAGE_SIZE = 20
# Tope de consultas al detalle, para los puestos que dicen solo "2 Locations".
WORKDAY_MAX_DETAIL = 40

SMARTRECRUITERS_PAGE_SIZE = 100

# Oracle Fusion ignora limit > 200.
ORACLE_PAGE_SIZE = 200

# Amazon: result_limit > 100 devuelve cero, no un error.
AMAZON_PAGE_SIZE = 100

# Un puesto con esta antigüedad o menos se marca como recién publicado. Aplicar
# en las primeras 48-72 h pesa mucho más de lo que parece.
DIAS_RECIENTE = 3


# ---------------------------------------------------------------------------
# Utilidades de texto y coincidencia
# ---------------------------------------------------------------------------

def normalize(text: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


_PATTERN_CACHE: dict[tuple[str, str], re.Pattern] = {}


def _pattern(term: str, mode: str) -> re.Pattern:
    """
    Compila un término de filtro en dos modos distintos:

      "word"   -> palabra completa. "uk" NO matchea "Ukraine".
      "prefix" -> prefijo de palabra. "partner" SÍ matchea "Partnerships".

    Se usan lookarounds en vez de \\b para que términos que empiezan o terminan
    con caracteres no alfanuméricos (p.ej. "go-to-market", "c++") funcionen.
    """
    cached = _PATTERN_CACHE.get((term, mode))
    if cached is not None:
        return cached

    words = [re.escape(w) for w in normalize(term).split()]
    body = r"\s+".join(words) if words else re.escape(normalize(term))
    prefix = r"(?<![0-9a-z])"
    suffix = r"(?![0-9a-z])" if mode == "word" else ""
    compiled = re.compile(prefix + body + suffix)

    _PATTERN_CACHE[(term, mode)] = compiled
    return compiled


def matches_any(text: str, terms: list[str], mode: str) -> bool:
    """True si alguno de los términos coincide con el texto ya normalizado."""
    return any(_pattern(t, mode).search(text) for t in terms if t)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _ssl_context() -> ssl.SSLContext:
    # En macOS con Python de python.org el almacén de CA del sistema no se usa;
    # certifi resuelve eso. En Linux/CI el contexto por defecto ya funciona.
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL = _ssl_context()


# Momento límite de la fuente que se está consultando. Lo fija collect() antes
# de llamar a cada adaptador. Es una variable de módulo y no un parámetro para
# no tener que pasarla por los siete adaptadores; el script es de un solo hilo.
_LIMITE: float | None = None


def fetch_json(url: str, timeout: int, payload: dict | None = None) -> dict | list:
    """
    JSON por HTTP. Con `payload` hace POST (lo necesita Workday); si no, GET.

    Respeta el presupuesto de tiempo de la fuente: sin él, una fuente inestable
    con reintentos puede alargar la corrida indefinidamente y arrastrar a las
    otras treinta y una.
    """
    last_error: Exception | None = None
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    for attempt in range(HTTP_ATTEMPTS):
        if _LIMITE is not None and time.monotonic() > _LIMITE:
            raise TimeoutError(
                f"se agotó el presupuesto de tiempo de la fuente ({last_error or 'sin respuesta'})"
            )
        try:
            request = urllib.request.Request(url, data=body, headers=headers)
            with urllib.request.urlopen(request, timeout=timeout, context=_SSL) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception as error:  # noqa: BLE001 - se reporta hacia arriba
            last_error = error
            if attempt + 1 < HTTP_ATTEMPTS:
                time.sleep(HTTP_BACKOFF[min(attempt, len(HTTP_BACKOFF) - 1)])
    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Puestos normalizados
# ---------------------------------------------------------------------------

def make_job(
    company: dict,
    native_id,
    title: str,
    location: str,
    url: str,
    posted_at,
    department: str = "",
) -> dict:
    """
    Devuelve un puesto en el formato común, con clave de identidad estable.

    La clave usa el id nativo de la plataforma cuando existe. Como respaldo se
    genera una huella SHA-256 de título + ubicación normalizados.

    OJO: hay que usar un hash criptográfico, NO la función hash() de Python.
    Python aleatoriza el hash de strings en cada arranque del proceso, así que
    la misma huella daría un valor distinto en cada corrida y todos los puestos
    aparecerían como nuevos siempre. No falla ruidosamente: solo inunda de
    alertas falsas.
    """
    company_id = company["id"]
    if native_id not in (None, ""):
        key = f"{company_id}:{native_id}"
    else:
        seed = f"{normalize(title)}|{normalize(location)}".encode("utf-8")
        key = f"{company_id}:h:{hashlib.sha256(seed).hexdigest()[:16]}"

    return {
        "key": key,
        "source": company_id,
        "company": company.get("nombre", company_id),
        "platform": company["plataforma"],
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url or "",
        "posted_at": posted_at or "",
        # No todas las plataformas lo publican: Ashby, Lever, Eightfold,
        # SmartRecruiters y Workable sí; Greenhouse y Workday no.
        "department": (department or "").strip(),
    }


def _iso(value) -> str:
    """Normaliza fechas de las distintas plataformas a ISO-8601 (UTC)."""
    if value in (None, ""):
        return ""
    if isinstance(value, (int, float)):
        # Lever usa milisegundos, Eightfold segundos.
        seconds = value / 1000 if value > 10_000_000_000 else value
        return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Adaptadores — uno por PLATAFORMA, nunca uno por empresa.
#
# Cada adaptador recibe la config de una empresa y devuelve puestos ya
# normalizados. Todo lo que viene después ignora de dónde salió cada puesto.
# ---------------------------------------------------------------------------

def adapter_greenhouse(company: dict, options: dict) -> list[dict]:
    board = company["board"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=false"
    payload = fetch_json(url, options["timeout"])

    jobs = []
    for raw in payload.get("jobs", []):
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("title", ""),
                (raw.get("location") or {}).get("name", ""),
                raw.get("absolute_url", ""),
                _iso(raw.get("first_published") or raw.get("updated_at")),
            )
        )
    return jobs


def adapter_lever(company: dict, options: dict) -> list[dict]:
    board = company["board"]
    url = f"https://api.lever.co/v0/postings/{board}?mode=json"
    payload = fetch_json(url, options["timeout"])

    jobs = []
    for raw in payload:
        categories = raw.get("categories") or {}
        locations = raw.get("allLocations") or [categories.get("location", "")]
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("text", ""),
                ", ".join(x for x in locations if x),
                raw.get("hostedUrl", ""),
                _iso(raw.get("createdAt")),
                categories.get("department") or categories.get("team") or "",
            )
        )
    return jobs


def adapter_ashby(company: dict, options: dict) -> list[dict]:
    board = company["board"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
    payload = fetch_json(url, options["timeout"])

    jobs = []
    for raw in payload.get("jobs", []):
        if raw.get("isListed") is False:
            continue
        locations = [raw.get("location", "")]
        locations += [s.get("location", "") for s in raw.get("secondaryLocations") or []]
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("title", ""),
                ", ".join(x for x in locations if x),
                raw.get("jobUrl", ""),
                _iso(raw.get("publishedAt")),
                raw.get("department") or raw.get("team") or "",
            )
        )
    return jobs


def adapter_eightfold(company: dict, options: dict) -> list[dict]:
    """
    Eightfold PCS-X. El endpoint legacy /api/apply/v2/jobs devuelve
    403 "Not authorized for PCSX" en tenants migrados; el que funciona es
    /api/pcsx/search. Ignora num > 10, así que hay que paginar de a 10.
    """
    tenant = company["tenant"]
    domain = company.get("dominio", f"{tenant}.com")
    base = f"https://{tenant}.eightfold.ai/api/pcsx/search?domain={domain}&sort_by=timestamp"

    by_id: dict = {}
    start = 0
    for _ in range(options["max_pages"]):
        payload = fetch_json(f"{base}&start={start}&num={EIGHTFOLD_PAGE_SIZE}", options["timeout"])
        data = payload.get("data") or {}
        positions = data.get("positions") or []
        if not positions:
            break

        for raw in positions:
            path = raw.get("positionUrl") or ""
            url = path if path.startswith("http") else f"https://{tenant}.eightfold.ai{path}"
            by_id[raw.get("id")] = make_job(
                company,
                raw.get("id"),
                raw.get("name", ""),
                ", ".join(raw.get("locations") or raw.get("standardizedLocations") or []),
                url,
                _iso(raw.get("postedTs")),
                raw.get("department") or "",
            )

        start += EIGHTFOLD_PAGE_SIZE
        if start >= int(data.get("count") or 0):
            break

    return list(by_id.values())


def _facetas_de_ubicacion(data: dict, terminos: list[str]) -> dict:
    """
    Elige, entre las ubicaciones que Workday publica en sus facetas, las que
    corresponden a la región buscada, y devuelve el filtro listo para reenviar.

    Los identificadores no se codifican a mano: se leen de la respuesta, así que
    si la empresa abre una oficina nueva aparece sola.

    Es un filtro GRUESO a propósito: pide de más (cualquier cosa que suene a
    Reino Unido) y deja que `passes_filters` haga la selección exacta después.
    Si fuera más estricto que el filtro real, se perderían puestos en silencio.
    Si no reconoce ninguna ubicación, devuelve {} y se trae el catálogo entero,
    que es lento pero nunca miente.

    El nombre del parámetro NO es fijo: cada empresa expone sus ubicaciones por
    país (`locationCountry`) o por ciudad (`locations`), o las dos. Mandar los
    identificadores bajo el nombre equivocado devuelve HTTP 502. Se prefiere el
    de país porque es más grueso, y se manda uno solo: combinarlos los cruzaría
    con AND y dejaría fuera puestos válidos.
    """
    grupos = [
        f for f in (data.get("facets") or []) if f.get("facetParameter") == "locationMainGroup"
    ]
    por_parametro: dict[str, list[str]] = {}
    for grupo in grupos:
        for sub in grupo.get("values") or []:
            nombre = sub.get("facetParameter")
            if not nombre:
                continue
            for valor in sub.get("values") or []:
                descripcion = normalize(valor.get("descriptor", ""))
                if valor.get("id") and matches_any(descripcion, terminos, "word"):
                    por_parametro.setdefault(nombre, []).append(valor["id"])

    for nombre in ("locationCountry", "locations"):
        if por_parametro.get(nombre):
            return {nombre: por_parametro[nombre]}
    for nombre, ids in por_parametro.items():
        return {nombre: ids}
    return {}


def adapter_workday(company: dict, options: dict) -> list[dict]:
    """
    Workday. La llamada interna (CXS) es un POST con paginación por offset.

    Dos trampas que solo se ven probando contra el servidor real:

    1. `total` viene SOLO en la primera página; las siguientes devuelven 0. Si
       se lee en cada vuelta, el bucle corta en la página 2 y se pierde el 75%
       del catálogo, en silencio.
    2. `limit` mayor a 20 devuelve HTTP 400.
    """
    tenant = company["tenant"]
    pod = company.get("pod", "wd1")
    site = company["site"]
    host = f"https://{tenant}.{pod}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"

    postings: list[dict] = []
    offset = 0
    total = None
    facetas: dict = {}
    for _ in range(options["max_pages"]):
        payload = {
            "appliedFacets": facetas,
            "limit": WORKDAY_PAGE_SIZE,
            "offset": offset,
            "searchText": "",
        }
        data = fetch_json(f"{api}/jobs", options["timeout"], payload)
        if total is None:
            # Primera vuelta: se le pide al servidor que devuelva solo la región
            # que interesa. Mastercard pasa de 1.141 puestos y 58 peticiones a
            # 39 y 2. Sin esto los corporativos grandes son inviables.
            facetas = _facetas_de_ubicacion(data, options["locations"])
            if facetas:
                data = fetch_json(
                    f"{api}/jobs",
                    options["timeout"],
                    {"appliedFacets": facetas, "limit": WORKDAY_PAGE_SIZE, "offset": 0, "searchText": ""},
                )
            total = int(data.get("total") or 0)
        page = data.get("jobPostings") or []
        if not page:
            break
        postings += page
        offset += WORKDAY_PAGE_SIZE
        if offset >= total:
            break

    jobs = []
    ambiguas = 0
    for raw in postings:
        path = raw.get("externalPath") or ""
        location = raw.get("locationsText") or ""
        posted = raw.get("postedOn") or ""

        # "2 Locations" no dice dónde, así que el filtro de ubicación lo
        # descartaría en silencio. Para esos pocos vamos al detalle del puesto,
        # que sí trae las ubicaciones reales y la fecha de verdad.
        if re.fullmatch(r"\d+\s+locations?", normalize(location)) and ambiguas < WORKDAY_MAX_DETAIL:
            ambiguas += 1
            try:
                detail = fetch_json(f"{api}{path}", options["timeout"])
                info = detail.get("jobPostingInfo") or {}
                lugares = [info.get("location", "")] + (info.get("additionalLocations") or [])
                location = ", ".join(x for x in lugares if x) or location
                posted = info.get("startDate") or posted
            except Exception:  # noqa: BLE001 - si falla, nos quedamos con "N Locations"
                pass

        bullets = raw.get("bulletFields") or []
        jobs.append(
            make_job(
                company,
                bullets[0] if bullets else path,
                raw.get("title", ""),
                location,
                f"{host}/en-US/{site}{path}",
                posted,
            )
        )
    return jobs


def adapter_smartrecruiters(company: dict, options: dict) -> list[dict]:
    """SmartRecruiters. API pública documentada, paginada de a 100."""
    board = company["board"]
    base = f"https://api.smartrecruiters.com/v1/companies/{board}/postings"

    jobs = []
    offset = 0
    total = None
    for _ in range(options["max_pages"]):
        data = fetch_json(f"{base}?limit={SMARTRECRUITERS_PAGE_SIZE}&offset={offset}", options["timeout"])
        if total is None:
            total = int(data.get("totalFound") or 0)
        page = data.get("content") or []
        if not page:
            break

        for raw in page:
            loc = raw.get("location") or {}
            # fullLocation viene como "London, , United Kingdom" (con el hueco
            # de la región); si falta, se arma con lo que haya.
            location = loc.get("fullLocation") or ", ".join(
                x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x
            )
            jobs.append(
                make_job(
                    company,
                    raw.get("id"),
                    raw.get("name", ""),
                    location,
                    f"https://jobs.smartrecruiters.com/{board}/{raw.get('id')}",
                    raw.get("releasedDate", ""),
                    (raw.get("department") or {}).get("label")
                    or (raw.get("function") or {}).get("label")
                    or "",
                )
            )

        offset += SMARTRECRUITERS_PAGE_SIZE
        if offset >= total:
            break

    return jobs


def adapter_workable(company: dict, options: dict) -> list[dict]:
    """Workable. Una sola petición devuelve el board entero."""
    board = company["board"]
    url = f"https://apply.workable.com/api/v1/widget/accounts/{board}?details=true"
    payload = fetch_json(url, options["timeout"])

    jobs = []
    for raw in payload.get("jobs", []):
        lugares = [
            ", ".join(
                x for x in (sede.get("city"), sede.get("region"), sede.get("country")) if x
            )
            for sede in raw.get("locations") or []
        ]
        if not lugares:
            lugares = [
                ", ".join(
                    x for x in (raw.get("city"), raw.get("state"), raw.get("country")) if x
                )
            ]
        jobs.append(
            make_job(
                company,
                raw.get("shortcode"),
                raw.get("title", ""),
                " | ".join(x for x in lugares if x),
                raw.get("url") or raw.get("shortlink", ""),
                raw.get("published_on") or raw.get("created_at", ""),
                raw.get("department") or raw.get("function") or "",
            )
        )
    return jobs


def adapter_bamboohr(company: dict, options: dict) -> list[dict]:
    """BambooHR. Una sola petición devuelve el board entero."""
    board = company["board"]
    payload = fetch_json(f"https://{board}.bamboohr.com/careers/list", options["timeout"])

    jobs = []
    for raw in payload.get("result", []):
        sede = raw.get("location") or {}
        ats = raw.get("atsLocation") or {}
        lugares = [
            sede.get("city"),
            sede.get("state"),
            ats.get("city"),
            ats.get("state"),
            ats.get("province"),
            ats.get("country"),
        ]
        # BambooHR repite ciudad y estado entre los dos bloques; se deduplica
        # conservando el orden para que la ubicación se lea natural.
        vistos, limpio = set(), []
        for x in lugares:
            if x and x not in vistos:
                vistos.add(x)
                limpio.append(x)
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("jobOpeningName", ""),
                ", ".join(limpio),
                f"https://{board}.bamboohr.com/careers/{raw.get('id')}",
                "",  # el listado no trae fecha de publicación
                raw.get("departmentLabel") or "",
            )
        )
    return jobs


def adapter_pinpoint(company: dict, options: dict) -> list[dict]:
    """Pinpoint. Una sola petición devuelve el board entero."""
    board = company["board"]
    payload = fetch_json(f"https://{board}.pinpointhq.com/postings.json", options["timeout"])

    jobs = []
    for raw in payload.get("data", []):
        sede = raw.get("location") or {}
        location = sede.get("name") or sede.get("city") or ""
        if not location:
            location = raw.get("workplace_type_text") or ""
        departamento = ((raw.get("job") or {}).get("department") or {}).get("name") or ""
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("title", ""),
                location,
                raw.get("url") or f"https://{board}.pinpointhq.com/en/postings/{raw.get('id')}",
                "",  # el listado no trae fecha de publicación
                departamento,
            )
        )
    return jobs


def adapter_oracle(company: dict, options: dict) -> list[dict]:
    """
    Oracle Fusion Recruiting, la plataforma de muchos bancos grandes.

    Igual que Workday, filtra por región en el servidor: JPMorgan pasa de 7.427
    puestos a 659, y con páginas de 200 son cuatro peticiones en vez de 372.

    Dos detalles que solo se ven probando: sin `expand=requisitionList...` la
    respuesta trae el total pero la lista vacía, y `limit` se topa en 200 aunque
    se pida más.
    """
    host = company["host"]
    site = company.get("site", "CX_1")
    base = (
        f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
        "?onlyData=true&expand=requisitionList.secondaryLocations"
        f"&finder=findReqs;siteNumber={site},facetsList=LOCATIONS,sortBy=POSTING_DATES_DESC"
    )

    def pagina(offset: int, extra: str = "") -> dict:
        url = f"{base},limit={ORACLE_PAGE_SIZE},offset={offset}{extra}"
        return (fetch_json(url, options["timeout"]).get("items") or [{}])[0]

    primera = pagina(0)
    # Solo se filtra si aparece la entrada del PAÍS entero, no una ciudad
    # suelta. Oracle recorta las facetas a las diez ubicaciones con más puestos,
    # así que en empresas con poca presencia acá no hay ninguna británica: AMEX
    # publica 406 puestos y ninguna de sus diez ubicaciones principales es de
    # Reino Unido. Quedarse con una ciudad que sí aparezca dejaría fuera el
    # resto del país en silencio; preferimos traer el catálogo entero, que es
    # más lento pero completo.
    exactas = [
        f
        for f in primera.get("locationsFacet") or []
        if normalize(f.get("Name", "")) in {normalize(t) for t in options["locations"]}
    ]
    extra = ""
    if exactas:
        elegida = max(exactas, key=lambda f: f.get("TotalCount") or 0)
        extra = f",selectedLocationsFacet={elegida['Id']}"
        primera = pagina(0, extra)

    total = int(primera.get("TotalJobsCount") or 0)
    crudos = list(primera.get("requisitionList") or [])
    offset = ORACLE_PAGE_SIZE
    while offset < total and offset // ORACLE_PAGE_SIZE < options["max_pages"]:
        lote = pagina(offset, extra).get("requisitionList") or []
        if not lote:
            break
        crudos += lote
        offset += ORACLE_PAGE_SIZE

    jobs = []
    for raw in crudos:
        lugares = [raw.get("PrimaryLocation", "")]
        for sec in raw.get("secondaryLocations") or []:
            lugares.append(sec.get("Name") or sec.get("LocationName") or "")
        jobs.append(
            make_job(
                company,
                raw.get("Id"),
                raw.get("Title", ""),
                ", ".join(x for x in lugares if x),
                f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{raw.get('Id')}",
                raw.get("PostedDate", ""),
                raw.get("JobFamily") or raw.get("Department") or "",
            )
        )
    return jobs


def adapter_amazon(company: dict, options: dict) -> list[dict]:
    """
    Amazon. API pública con filtro de país incorporado, así que el catálogo
    global (decenas de miles) nunca se descarga: solo la región que interesa.
    `result_limit` se topa en 100; pedir más devuelve cero, no un error.
    """
    pais = company.get("pais", "GBR")
    base = f"https://www.amazon.jobs/en/search.json?base_query=&country={pais}&result_limit={AMAZON_PAGE_SIZE}"

    jobs = []
    offset = 0
    total = None
    for _ in range(options["max_pages"]):
        data = fetch_json(f"{base}&offset={offset}", options["timeout"])
        if total is None:
            total = int(data.get("hits") or 0)
        lote = data.get("jobs") or []
        if not lote:
            break
        for raw in lote:
            jobs.append(
                make_job(
                    company,
                    raw.get("id_icims"),
                    raw.get("title", ""),
                    raw.get("normalized_location") or raw.get("city", ""),
                    f"https://www.amazon.jobs{raw.get('job_path', '')}",
                    _fecha_larga(raw.get("posted_date", "")),
                    raw.get("business_category") or "",
                )
            )
        offset += AMAZON_PAGE_SIZE
        if offset >= total:
            break
    return jobs


def _fecha_larga(valor: str) -> str:
    """Amazon publica la fecha como 'August 20, 2026'; se pasa a ISO-8601."""
    try:
        return datetime.strptime(valor.strip(), "%B %d, %Y").replace(tzinfo=timezone.utc).isoformat()
    except (ValueError, AttributeError):
        return ""


def adapter_teamtailor(company: dict, options: dict) -> list[dict]:
    """
    Teamtailor. El board publica un JSON Feed en `<dominio>/jobs.json`, con la
    ficha schema.org de cada puesto adentro. `board` es el dominio completo,
    porque muchas empresas lo sirven desde su propio subdominio.
    """
    dominio = company["board"]
    payload = fetch_json(f"https://{dominio}/jobs.json", options["timeout"])

    jobs = []
    for raw in payload.get("items", []):
        ficha = raw.get("_jobposting") or {}
        lugares = []
        for sitio in ficha.get("jobLocation") or []:
            dire = sitio.get("address") or {}
            lugares.append(
                ", ".join(
                    x
                    for x in (
                        dire.get("addressLocality"),
                        dire.get("addressRegion"),
                        dire.get("addressCountry"),
                    )
                    if x
                )
            )
        jobs.append(
            make_job(
                company,
                raw.get("id"),
                raw.get("title", ""),
                " | ".join(x for x in lugares if x),
                raw.get("url", ""),
                raw.get("date_published") or ficha.get("datePosted") or "",
            )
        )
    return jobs


def _fecha_dotnet(valor: str) -> str:
    """
    Convierte `/Date(1784901092000+0200)/` —el formato de fecha de .NET que usa
    HR Manager— a ISO-8601. Las fechas vacías vienen como el año 1, que en
    milisegundos es un número negativo enorme; esas se descartan.
    """
    match = re.search(r"/Date\((-?\d+)", valor or "")
    if not match:
        return ""
    ms = int(match.group(1))
    if ms <= 0:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def adapter_hrmanager(company: dict, options: dict) -> list[dict]:
    """HR Manager (Talentech). API pública, una sola petición."""
    board = company["board"]
    url = f"https://api.hr-manager.net/JobPortal.svc/{board}/PositionList/json/"
    payload = fetch_json(url, options["timeout"])

    jobs = []
    for raw in payload.get("Items", []):
        ubicacion = (raw.get("PositionLocation") or {}).get("Name") or raw.get("WorkPlace") or ""
        departamento = (raw.get("Department") or {}).get("Name") or raw.get(
            "DepartmentNamePlainText"
        ) or ""
        jobs.append(
            make_job(
                company,
                raw.get("Id"),
                raw.get("Name", ""),
                ubicacion,
                raw.get("AdvertisementUrlSecure") or raw.get("AdvertisementUrl", ""),
                _fecha_dotnet(raw.get("Published", "")),
                departamento,
            )
        )
    return jobs


def adapter_palladium(company: dict, options: dict) -> list[dict]:
    """
    Palladium. ESTA ES LA ÚNICA EXCEPCIÓN a la regla de un adaptador por
    plataforma y no por empresa.

    Su ATS real es Cornerstone, pero su API pide autenticación (401). Lo único
    público es el endpoint que usa su propia web, `/ajaxjoblist`, que devuelve
    JSON limpio. Como es código para una sola empresa, es la parte del proyecto
    con más probabilidad de romperse; la detección de fallas silenciosas (cero
    puestos = aviso) es la red que lo cubre.
    """
    payload = fetch_json("https://thepalladiumgroup.com/ajaxjoblist", options["timeout"])
    paises = {c["iso3"]: c["name"] for c in payload.get("countries") or []}

    jobs = []
    for raw in payload.get("jobs", []):
        codigos = [raw.get("Country", "")] + (raw.get("multi_country") or "").split(",")
        nombres, vistos = [], set()
        for codigo in codigos:
            codigo = codigo.strip()
            if codigo and codigo not in vistos:
                vistos.add(codigo)
                nombres.append(paises.get(codigo, codigo))
        job_id = raw.get("job_id")
        jobs.append(
            make_job(
                company,
                job_id,
                raw.get("Title", ""),
                ", ".join(nombres),
                f"https://palladium.csod.com/ux/ats/careersite/2/home/requisition/{job_id}?c=palladium",
                raw.get("CreateDateLocal", ""),
            )
        )
    return jobs


ADAPTERS = {
    "greenhouse": adapter_greenhouse,
    "lever": adapter_lever,
    "ashby": adapter_ashby,
    "eightfold": adapter_eightfold,
    "workday": adapter_workday,
    "smartrecruiters": adapter_smartrecruiters,
    "workable": adapter_workable,
    "bamboohr": adapter_bamboohr,
    "pinpoint": adapter_pinpoint,
    "teamtailor": adapter_teamtailor,
    "oracle": adapter_oracle,
    "amazon": adapter_amazon,
    "hrmanager": adapter_hrmanager,
    "palladium": adapter_palladium,  # excepción, ver el adaptador
}


# ---------------------------------------------------------------------------
# Filtros — se aplican al MOSTRAR, nunca al guardar.
#
# Si se filtrara antes de guardar, el día que se amplíe un filtro decenas de
# puestos viejos aparecerían de golpe como "nuevos".
# ---------------------------------------------------------------------------

def passes_filters(job: dict, filters: dict) -> bool:
    title = normalize(job["title"])
    location = normalize(job["location"])

    if matches_any(title, filters["titulos_excluir"], "prefix"):
        return False
    if filters["ubicaciones_incluir"] and not matches_any(
        location, filters["ubicaciones_incluir"], "word"
    ):
        return False
    if filters["titulos_incluir"] and not matches_any(
        title, filters["titulos_incluir"], "prefix"
    ):
        return False

    # Las prácticas tienen una valla extra: solo interesan las de MBA. Un
    # "Graduate Scheme" o un internship común son un paso atrás con un MBA
    # en curso, así que se descartan aunque el rol encaje.
    requisito = filters.get("practicas_requieren") or []
    if requisito and is_internship(job, filters) and not matches_any(
        title, requisito, "prefix"
    ):
        return False

    return True


def antiguedad(job: dict) -> tuple[int | None, str]:
    """
    Días desde la publicación y su etiqueta.

    El aviso llega cuando un puesto entra al RADAR, que no es lo mismo que
    cuando se publicó: al agregar una empresa nueva, su catálogo entero llega
    como novedad aunque lleve meses abierto. Esta etiqueta separa una cosa de
    la otra. Workday devuelve texto relativo ("Posted 19 Days Ago") cuando no
    hay fecha; en ese caso se muestra tal cual.
    """
    crudo = job.get("posted_at") or ""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", crudo)
    if not match:
        return None, crudo

    publicado = datetime(
        int(match[1]), int(match[2]), int(match[3]), tzinfo=timezone.utc
    )
    dias = (datetime.now(timezone.utc) - publicado).days
    if dias < 0:
        return 0, "today"
    if dias == 0:
        return 0, "today"
    if dias == 1:
        return 1, "yesterday"
    if dias < 30:
        return dias, f"{dias} days ago"
    if dias < 365:
        meses = dias // 30
        return dias, "1 month ago" if meses == 1 else f"{meses} months ago"
    return dias, "over a year ago"


def terminos_que_coinciden(job: dict, filters: dict) -> list[str]:
    """
    Qué términos de `titulos_incluir` hicieron entrar a este puesto.

    Sirve para afinar los filtros sin tener que leer código: si tres avisos
    seguidos dicen `entró por: growth` y son basura, ya se sabe qué tocar.
    """
    title = normalize(job["title"])
    return [t for t in filters.get("titulos_incluir", []) if _pattern(t, "prefix").search(title)]


def is_internship(job: dict, filters: dict) -> bool:
    """Clasifica, no filtra: decide en qué sección del correo va el puesto."""
    return matches_any(normalize(job["title"]), filters["titulos_practicas"], "prefix")


# ---------------------------------------------------------------------------
# Configuración y estado
# ---------------------------------------------------------------------------

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    filters = config.get("filtros") or {}
    for key in (
        "ubicaciones_incluir",
        "titulos_incluir",
        "titulos_excluir",
        "titulos_practicas",
        "practicas_requieren",
        "ubicaciones_origen",
    ):
        filters[key] = [str(t) for t in (filters.get(key) or [])]

    raw_options = config.get("opciones") or {}
    options = {
        "notify": str(raw_options.get("notificar_a") or "").lstrip("@").strip(),
        "avisar_sin_novedades": str(
            raw_options.get("avisar_sin_novedades", "nunca")
        ).strip().lower(),
        "timeout": int(raw_options.get("timeout_segundos", 30)),
        "budget": int(raw_options.get("limite_segundos_por_fuente", 150)),
        # Términos gruesos para pedirle al servidor solo una región. Ver
        # adapter_workday. Si no se define, se usan los de ubicaciones_incluir.
        "locations": filters.get("ubicaciones_origen") or filters["ubicaciones_incluir"],
        "drop_pct": int(raw_options.get("alerta_caida_pct", 70)),
        "max_pages": int(raw_options.get("max_paginas", 300)),
    }

    # Casos de prueba propios, si el config los trae.
    pruebas = [
        (str(c.get("titulo", "")), str(c.get("ubicacion", "")), bool(c.get("espera")))
        for c in config.get("pruebas") or []
    ]

    companies = []
    for company in config.get("empresas") or []:
        if company.get("activa", True):
            companies.append(company)

    return {"companies": companies, "filters": filters, "options": options, "tests": pruebas}


def load_state() -> dict | None:
    if not os.path.exists(STATE_PATH):
        return None
    with open(STATE_PATH, encoding="utf-8") as handle:
        state = json.load(handle)
    state.setdefault("jobs", {})
    state.setdefault("sources", {})
    return state


def save_state(state: dict) -> None:
    state["version"] = STATE_VERSION
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    with open(STATE_PATH, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1, sort_keys=True)
        handle.write("\n")


# ---------------------------------------------------------------------------
# Corrida
# ---------------------------------------------------------------------------

def _limpiar_departamento_inutil(jobs: list[dict]) -> None:
    """
    Algunas empresas mandan el mismo departamento en todos los puestos
    (Checkout.com pone "All cost centres" en el 99%). Ese dato no informa nada
    y solo ensucia el correo, así que se descarta.
    """
    deps = [j["department"] for j in jobs if j["department"]]
    if not deps:
        return
    mayoritario = max(set(deps), key=deps.count)
    if deps.count(mayoritario) / len(jobs) >= 0.8:
        for job in jobs:
            if job["department"] == mayoritario:
                job["department"] = ""


def collect(config: dict) -> list[dict]:
    """
    Consulta todas las fuentes. Cada adaptador está aislado: si una fuente se
    cae, las demás siguen funcionando y reportando normal.
    """
    results = []
    for company in config["companies"]:
        platform = company.get("plataforma")
        adapter = ADAPTERS.get(platform)
        result = {
            "id": company["id"],
            "name": company.get("nombre", company["id"]),
            "platform": platform,
            "jobs": [],
            "ok": False,
            "error": None,
            # Boards que hoy están legítimamente vacíos: sin esto avisarían
            # "0 puestos" en cada corrida y el aviso perdería todo su valor.
            "allow_empty": bool(company.get("permitir_vacia", False)),
        }

        if adapter is None:
            result["error"] = f"plataforma desconocida: {platform!r}"
            results.append(result)
            continue

        global _LIMITE
        _LIMITE = time.monotonic() + config["options"]["budget"]
        try:
            result["jobs"] = adapter(company, config["options"])
            _limpiar_departamento_inutil(result["jobs"])
            result["ok"] = True
        except Exception as error:  # noqa: BLE001 - aislamiento entre fuentes
            result["error"] = f"{type(error).__name__}: {error}"

        results.append(result)
    return results


def check_health(results: list[dict], state: dict, drop_pct: int) -> list[str]:
    """
    El riesgo real no es fallar con un error visible, es fallar en silencio:
    la fuente deja de devolver puestos y "no hay alertas" se interpreta como
    "no hay vacantes nuevas". Eso es peor que no tener nada.
    """
    problems = []
    for result in results:
        previous = state["sources"].get(result["id"]) or {}
        before = previous.get("last_count")

        if not result["ok"]:
            problems.append(f"**{result['name']}** ({result['platform']}): failed — {result['error']}")
            continue

        now = len(result["jobs"])
        if now == 0 and result.get("allow_empty"):
            continue
        if now == 0:
            detail = f"previously returned {before}" if before else "has never returned any"
            problems.append(f"**{result['name']}** ({result['platform']}): 0 roles ({detail})")
        elif drop_pct > 0 and before and now < before * (100 - drop_pct) / 100:
            problems.append(
                f"**{result['name']}** ({result['platform']}): sharp drop, "
                f"{before} → {now} roles"
            )
    return problems


def update_state(results: list[dict], state: dict, now_iso: str) -> list[dict]:
    """Registra novedades y limpia. Devuelve los puestos nuevos (sin filtrar)."""
    new_jobs = []

    for result in results:
        source_id = result["id"]

        if not result["ok"]:
            # Una fuente que falló NO se limpia: si le borráramos los puestos,
            # la próxima corrida sana reportaría el catálogo entero como nuevo.
            previous = state["sources"].get(source_id) or {}
            previous.update(
                {
                    "name": result["name"],
                    "platform": result["platform"],
                    "last_status": "error",
                    "last_error": result["error"],
                    "last_attempt_at": now_iso,
                }
            )
            state["sources"][source_id] = previous
            continue

        seen_keys = set()
        for job in result["jobs"]:
            seen_keys.add(job["key"])
            if job["key"] not in state["jobs"]:
                record = dict(job)
                record["first_seen"] = now_iso
                state["jobs"][job["key"]] = record
                new_jobs.append(record)
            else:
                # Se refrescan los datos visibles sin tocar first_seen.
                record = state["jobs"][job["key"]]
                record.update(
                    {k: job[k] for k in ("title", "location", "url", "posted_at", "company")}
                )
                record.pop("missing_runs", None)

        # Limpieza: solo para fuentes que respondieron bien, y solo después de
        # CLEANUP_GRACE_RUNS ausencias seguidas.
        absent = [
            key
            for key, job in state["jobs"].items()
            if job.get("source") == source_id and key not in seen_keys
        ]
        for key in absent:
            missing = state["jobs"][key].get("missing_runs", 0) + 1
            if missing >= CLEANUP_GRACE_RUNS:
                del state["jobs"][key]
            else:
                state["jobs"][key]["missing_runs"] = missing

        state["sources"][source_id] = {
            "name": result["name"],
            "platform": result["platform"],
            "last_status": "ok",
            "last_error": None,
            "last_count": len(seen_keys),
            "last_ok_at": now_iso,
            "last_attempt_at": now_iso,
        }

    return new_jobs


# ---------------------------------------------------------------------------
# Reporte
# ---------------------------------------------------------------------------

def _section(title: str, jobs: list[dict], filters: dict) -> list[str]:
    lines = [f"## {title}\n"]
    by_company: dict[str, list[dict]] = {}
    for job in jobs:
        by_company.setdefault(job["company"], []).append(job)

    for company in sorted(by_company):
        lines.append(f"### {company}\n")
        # Lo más reciente primero: es lo que más conviene mirar antes.
        ordenados = sorted(
            by_company[company],
            key=lambda j: (antiguedad(j)[0] if antiguedad(j)[0] is not None else 9999, j["title"]),
        )
        for job in ordenados:
            dias, etiqueta = antiguedad(job)
            fresco = " 🔥" if dias is not None and dias <= DIAS_RECIENTE else ""
            lines.append(f"- **[{job['title']}]({job['url']})**{fresco}")

            detalle = [job["location"] or "location not specified"]
            if job.get("department"):
                detalle.append(job["department"])
            if etiqueta:
                detalle.append(etiqueta)
            lines.append(f"  - {' · '.join(detalle)}")

            terminos = terminos_que_coinciden(job, filters)
            if terminos:
                # Sin comillas invertidas a propósito: el formato `código` de
                # Markdown se ve con otra tipografía y otro tamaño, y desentona
                # con el resto de los sub-puntos.
                lines.append("  - matched: " + ", ".join(terminos))
        lines.append("")
    return lines


def build_report(
    matches: list[dict],
    problems: list[str],
    results: list[dict],
    notify: str = "",
    filters: dict | None = None,
) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []

    filters = filters or {"titulos_practicas": []}
    practicas, full_time = [], []
    for job in matches:
        (practicas if is_internship(job, filters) else full_time).append(job)

    if full_time:
        plural = "" if len(full_time) == 1 else "s"
        lines += _section(f"💼 {len(full_time)} full-time role{plural}", full_time, filters)
    if practicas:
        plural = "" if len(practicas) == 1 else "s"
        lines += _section(f"🎓 {len(practicas)} internship{plural}", practicas, filters)
    if not matches:
        lines.append("## Nothing new\n")
        if not problems:
            lines.append("All sources healthy.\n")

    if problems:
        lines.append("## ⚠️ Source problems\n")
        lines += [f"- {p}" for p in problems]
        lines.append(
            "\nA broken source sends no alerts, which can look like "
            "«no openings» when it is actually just broken.\n"
        )

    lines.append("## Source status\n")
    lines.append("| Source | Platform | Roles | Status |")
    lines.append("|---|---|---:|---|")
    for result in sorted(results, key=lambda r: r["name"]):
        status = "ok" if result["ok"] else f"ERROR — {result['error']}"
        count = len(result["jobs"]) if result["ok"] else "—"
        lines.append(f"| {result['name']} | {result['platform']} | {count} | {status} |")

    lines.append(f"\n_Generated {stamp}._")
    if notify:
        lines.append(f"\ncc @{notify}")
    return "\n".join(lines) + "\n"


def emit_outputs(pairs: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            handle.write(f"{key}={value}\n")


# ---------------------------------------------------------------------------
# Modos
# ---------------------------------------------------------------------------

def run_check(config: dict) -> int:
    """Diagnóstico: consulta todo, imprime la tabla, no guarda nada."""
    results = collect(config)
    print(f"{'FUENTE':<16} {'PLATAFORMA':<12} {'TOTAL':>7} {'PASAN':>7}  ESTADO")
    print("-" * 68)

    failed = False
    for result in sorted(results, key=lambda r: r["name"]):
        if result["ok"]:
            passing = sum(1 for j in result["jobs"] if passes_filters(j, config["filters"]))
            # La muestra no es decorativa: un board con el identificador
            # equivocado devuelve puestos y se ve perfectamente sano. Pasó con
            # Primer, donde "primer" resultó ser una empresa de educación de
            # EE.UU. que contrataba profesores. Leer dos títulos lo delata.
            muestra = " · ".join(j["title"][:34] for j in result["jobs"][:2])
            print(
                f"{result['name'][:15]:<16} {result['platform']:<12} "
                f"{len(result['jobs']):>7} {passing:>7}  ok   {muestra[:60]}"
            )
        else:
            failed = True
            print(f"{result['name'][:15]:<16} {result['platform']:<12} {'—':>7} {'—':>7}  {result['error']}")

    passing = [
        j for r in results for j in r["jobs"] if passes_filters(j, config["filters"])
    ]
    for label, wanted in (("FULL TIME", False), ("PRÁCTICAS / INTERNSHIPS", True)):
        grupo = [j for j in passing if is_internship(j, config["filters"]) == wanted]
        print(f"\n{label} ({len(grupo)}):")
        for job in grupo:
            print(f"  [{job['company']}] {job['title']} — {job['location']}")
        if not grupo:
            print("  (ninguno)")

    print("\nModo diagnóstico: no se guardó nada.")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# Descubrir la plataforma de una empresa
# ---------------------------------------------------------------------------

# Cada entrada: (plataforma, expresión que encuentra el identificador en el
# HTML de la página de carreras, plantilla de las líneas de config).
# El orden importa poco; se prueban todas y se reportan las que aparezcan.
HUELLAS = [
    ("greenhouse", r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_.\-]+)"),
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9_.\-]+)"),
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9_.\-]+)"),
    ("smartrecruiters", r"(?:jobs|api)\.smartrecruiters\.com/(?:v1/companies/)?([A-Za-z0-9_.\-]+)"),
    ("workable", r"apply\.workable\.com/(?:api/v1/widget/accounts/)?([A-Za-z0-9_.\-]+)"),
    ("bamboohr", r"([A-Za-z0-9_\-]+)\.bamboohr\.com"),
    ("pinpoint", r"([A-Za-z0-9_\-]+)\.pinpointhq\.com"),
    ("hrmanager", r"hr-manager\.net/[^\"'\s]*customer=([A-Za-z0-9_\-]+)"),
    ("eightfold", r"([A-Za-z0-9_\-]+)\.eightfold\.ai"),
    ("workday", r"([A-Za-z0-9_\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_\-]+)"),
    ("oracle", r"([A-Za-z0-9_.\-]+\.oraclecloud\.com)/hcmUI/CandidateExperience/[^/]+/sites/([A-Za-z0-9_\-]+)"),
    ("teamtailor", r"(careers\.[A-Za-z0-9_.\-]+)/jobs/\d"),
]

# Plataformas que se investigaron y no exponen datos legibles. Reconocerlas
# ahorra el rato de intentarlo.
SIN_SALIDA = {
    "jobs.deel.com": "Deel: sin API, los puestos van dentro del HTML de Next.js",
    "applytojob.com": "JazzHR: sin feed JSON ni RSS",
    "join.com": "join.com: su API pide un id numérico de empresa que no publica",
    "csod.com": "Cornerstone: su API devuelve 401 sin autenticación",
    "careers.hibob.com": "HiBob: aplicación Angular, su /api responde 406",
    "factorialhr.com": "Factorial HR: sin API ni datos embebidos",
}


def run_discover(config: dict, url: str) -> int:
    """
    Recibe la URL de una página de carreras y dice qué poner en config.yaml.

    Es el paso que más tiempo cuesta al adoptar el radar: hay una docena de
    plataformas y el identificador casi nunca es el nombre de la empresa.
    Además VERIFICA el hallazgo consultando la API e imprimiendo dos títulos,
    porque un identificador equivocado puede dar con el board de otra empresa
    y verse perfectamente sano (pasó con "primer", que es un colegio de EE.UU.).
    """
    print(f"Mirando {url} …\n")
    try:
        request = urllib.request.Request(
            url,
            headers={
                # Aquí sí conviene parecer un navegador: muchas webs de carreras
                # devuelven 403 a cualquier otra cosa.
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml",
            },
        )
        with urllib.request.urlopen(request, timeout=config["options"]["timeout"], context=_SSL) as r:
            html = r.read().decode("utf-8", errors="ignore")
    except Exception as error:  # noqa: BLE001
        print(f"No se pudo leer la página: {type(error).__name__}: {error}")
        print("Si devuelve 403, el sitio bloquea peticiones automatizadas y no hay nada que hacer.")
        return 1

    for marca, motivo in SIN_SALIDA.items():
        if marca in html:
            print(f"⚠️  Esta empresa usa una plataforma que NO se puede leer.\n    {motivo}")
            print("    Lo sensato es suscribirse a la alerta nativa de su portal.")
            return 1

    encontrados = []
    for plataforma, patron in HUELLAS:
        for grupos in re.findall(patron, html):
            encontrados.append((plataforma, grupos if isinstance(grupos, tuple) else (grupos,)))

    if not encontrados:
        print("No reconocí ninguna plataforma en el HTML.")
        print("Abre un puesto concreto y prueba con ESA url: el enlace de 'Apply' suele delatarla.")
        return 1

    vistos = set()
    for plataforma, grupos in encontrados:
        if (plataforma, grupos) in vistos:
            continue
        vistos.add((plataforma, grupos))

        if plataforma == "workday":
            lineas = f"    plataforma: workday\n    tenant: {grupos[0]}\n    pod: {grupos[1]}\n    site: {grupos[2]}"
            empresa = {"id": "x", "plataforma": "workday", "tenant": grupos[0], "pod": grupos[1], "site": grupos[2]}
        elif plataforma == "oracle":
            lineas = f"    plataforma: oracle\n    host: {grupos[0]}\n    site: {grupos[1]}"
            empresa = {"id": "x", "plataforma": "oracle", "host": grupos[0], "site": grupos[1]}
        elif plataforma == "eightfold":
            lineas = f"    plataforma: eightfold\n    tenant: {grupos[0]}\n    dominio: {grupos[0]}.com"
            empresa = {"id": "x", "plataforma": "eightfold", "tenant": grupos[0], "dominio": f"{grupos[0]}.com"}
        else:
            lineas = f"    plataforma: {plataforma}\n    board: {grupos[0]}"
            empresa = {"id": "x", "plataforma": plataforma, "board": grupos[0]}

        print(f"── {plataforma} ──")
        print(lineas)
        try:
            puestos = ADAPTERS[plataforma](empresa, config["options"])
            muestra = " · ".join(j["title"][:38] for j in puestos[:2])
            print(f"    ✓ verificado: {len(puestos)} puestos → {muestra}")
            print("      Lee esos títulos: si no parecen de esta empresa, el identificador está mal.\n")
        except Exception as error:  # noqa: BLE001
            print(f"    ✗ no respondió ({type(error).__name__}); probablemente no sea el correcto\n")

    print("Copia el bloque que verificó bien en config.yaml, con su 'id' y 'nombre'.")
    return 0


def run_demo(config: dict) -> int:
    """
    Prueba de punta a punta: consulta las fuentes de verdad, agarra un par de
    puestos reales que pasan los filtros y arma un aviso de ejemplo. No toca el
    estado. Sirve para comprobar de una sola vez que los conectores funcionan,
    que los filtros funcionan, y que el issue y el correo llegan.
    """
    results = collect(config)
    problems = check_health(results, {"sources": {}}, 0)

    examples = []
    for result in results:
        for job in result["jobs"]:
            if passes_filters(job, config["filters"]) and len(examples) < 3:
                examples.append(job)

    report = build_report(
        examples, problems, results, config["options"]["notify"], config["filters"]
    )
    report = (
        "> ⚠️ **This is a TEST — these are not new openings.**\n"
        "> They are real, already-open roles used as samples to confirm that\n"
        "> alerts are being delivered. You can close this issue.\n\n"
    ) + report

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(report)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    emit_outputs({"has_report": "true", "title": f"🧪 Radar test — {stamp} UTC"})
    print(f"Prueba armada con {len(examples)} puesto(s) de ejemplo. No se guardó nada.")
    return 0


def run_weekly(config: dict) -> int:
    """
    Resumen semanal: TODO lo que está abierto ahora y pasa los filtros, no solo
    lo nuevo. No toca el estado.

    Es la red de seguridad del sistema. Sin esto, una semana sin correos es
    ambigua: puede ser "no hay nada" o "se rompió y nadie se enteró". Con esto,
    el silencio total es imposible — y de paso rescata lo que se pasó por alto
    al cerrar un aviso sin leerlo.
    """
    results = collect(config)
    problems = check_health(results, {"sources": {}}, 0)
    abiertos = [
        j for r in results for j in r["jobs"] if passes_filters(j, config["filters"])
    ]

    report = build_report(
        abiertos, problems, results, config["options"]["notify"], config["filters"]
    )
    report = (
        "> 📋 **Weekly digest.** This is *everything currently open* that matches\n"
        "> your filters — not just what is new. It arrives every week even when\n"
        "> there is nothing new, precisely so that a quiet week can never be\n"
        "> mistaken for a broken one.\n\n"
    ) + report

    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(report)

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    emit_outputs(
        {
            "has_report": "true",  # siempre, incluso con cero: ese es el punto
            "title": f"📋 Weekly digest — {len(abiertos)} open role(s) · {stamp}",
        }
    )
    print(f"Resumen semanal: {len(abiertos)} puesto(s) abierto(s). No se guardó nada.")
    return 0


def _avisar_sin_novedades(config: dict) -> bool:
    """
    ¿Mandar el correo aunque no haya nada que contar?

    Sin esto, una mañana sin correo es ambigua: puede ser "no se publicó nada"
    o "se rompió y nadie se enteró". El costo es volumen, y el volumen mata la
    atención — por eso "manana" existe: da el latido diario sin 15 correos por
    semana. La corrida de la mañana se identifica por su cron, que el workflow
    pasa en RADAR_PRIMERA_DEL_DIA.
    """
    modo = config["options"]["avisar_sin_novedades"]
    if modo == "siempre":
        return True
    if modo == "manana":
        return os.environ.get("RADAR_PRIMERA_DEL_DIA") == "true"
    return False


def run_normal(config: dict, reset: bool) -> int:
    now_iso = datetime.now(timezone.utc).isoformat()
    state = None if reset else load_state()

    baseline = state is None
    if state is None:
        state = {"version": STATE_VERSION, "jobs": {}, "sources": {}}

    results = collect(config)
    problems = check_health(results, state, config["options"]["drop_pct"])
    new_jobs = update_state(results, state, now_iso)

    if baseline:
        # Línea base: se registra todo el catálogo y no se notifica nada.
        # Sin esto, la primera corrida mandaría cientos de "novedades".
        save_state(state)
        print(f"Línea base registrada: {len(state['jobs'])} puestos en {len(state['sources'])} fuentes.")
        for problem in problems:
            print(f"  aviso: {problem}")
        emit_outputs({"has_report": "false"})
        return 0

    matches = [j for j in new_jobs if passes_filters(j, config["filters"])]
    save_state(state)

    print(f"Nuevos: {len(new_jobs)} | pasan filtros: {len(matches)} | problemas: {len(problems)}")

    if not matches and not problems and not _avisar_sin_novedades(config):
        emit_outputs({"has_report": "false"})
        return 0

    report = build_report(
        matches, problems, results, config["options"]["notify"], config["filters"]
    )
    with open(REPORT_PATH, "w", encoding="utf-8") as handle:
        handle.write(report)

    practicas = sum(1 for j in matches if is_internship(j, config["filters"]))
    full_time = len(matches) - practicas
    partes = []
    if full_time:
        partes.append(f"{full_time} new full-time role{'' if full_time == 1 else 's'}")
    if practicas:
        partes.append(f"{practicas} internship{'' if practicas == 1 else 's'}")
    if problems:
        partes.append(f"⚠️ {len(problems)} source{'' if len(problems) == 1 else 's'} with problems")
    if not partes:
        partes.append("Nothing new, all sources healthy")
    title = " · ".join(partes)

    emit_outputs(
        {
            "has_report": "true",
            "title": f"{title} — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
        }
    )
    print(f"\nReporte escrito en {REPORT_PATH}")
    return 0


# ---------------------------------------------------------------------------
# Auto-test de la lógica de filtros (no toca la red)
# ---------------------------------------------------------------------------

TEST_CASES = [
    # (título, ubicación, esperado)
    ("Senior Manager, Operations Strategy", "UK - London", True),
    ("Senior Manager, Strategic Partnerships, EMEA", "UK - London", True),
    ("Alliance Partnership Manager", "London", True),  # singular, no plural
    ("Business Development Manager", "London, United Kingdom", True),
    ("Chief of Staff", "Cardiff, London or Remote (UK)", True),
    ("Head of Growth", "Edinburgh, GB", True),
    ("Go-To-Market Lead", "Manchester", True),
    ("Revenue Operations Analyst", "Bristol, UK", True),
    # la trampa: "uk" como subcadena matchea "Ukraine"
    ("Strategy Manager", "Kyiv, Ukraine", False),
    ("Partnerships Lead", "Kharkiv, Ukraine", False),
    # ubicación fuera de UK
    ("Commercial Director", "Singapore", False),
    ("Product Manager", "New York City, New York, United States of America", False),
    # roles de la lista de postulaciones previas
    ("Founders Associate", "London", True),
    ("Founder's Associate", "UK - London", True),
    ("Associate Managing Consultant, Strategy & Transformation", "London, UK", True),
    ("Proposition & Strategy Manager - Daily Banking UK", "Edinburgh", True),
    # producto: entra lo que toca estrategia, no la gestión de producto
    ("Product Development Manager", "London", True),
    ("Head of Product Strategy", "London", True),
    ("Senior Product Manager, Payments", "UK - London", False),
    ("Product Director, Business Banking", "London", False),
    ("Head of Product, Payments", "Manchester", False),
    ("Senior Technical Product Owner", "London", False),
    # marketing queda fuera entero, pero sin arrastrar los términos con "market"
    ("Senior Product Marketing Manager, EMEA", "UK - London", False),
    ("Growth Marketing Lead", "London", False),
    ("Senior Manager, New Market Development", "London", True),
    ("Strategy Lead, Market Intelligence", "Edinburgh", True),
    # "ops" no alcanza para "BizOps": la coincidencia es por prefijo de palabra
    ("BizOps Manager", "London", True),
    ("Head of Biz Ops, EMEA", "UK - London", True),
    ("Market Entry Lead, Europe", "London", True),
    # impacto: entra la creación de valor, no la inversión
    ("Data and Impact Senior Associate", "United Kingdom", True),
    ("Value Creation Manager, Portfolio", "London", True),
    ("Head of Financial Inclusion", "London", True),
    ("Investment Associate, Financial Services", "London", False),
    ("Investment Intern - FS Asia", "London", False),
    # áreas descartadas, y los términos que NO deben arrastrar
    ("Legal Counsel, Commercial", "London", False),
    ("Senior Relationship Manager - FX Partnerships", "London", False),
    ("IT Operations - Transaction Monitoring Associate", "London", False),
    ("Applied AI & ML Lead - Markets Operations", "London", False),
    ("Fraud Strategy Director", "London", False),
    ("Program Manager - Commodities Transformation", "London", False),
    ("Credit Operations Manager", "London", True),   # "it operations" no lo toca
    ("Senior Manager, Strategic Partnerships", "London", True),
    # prácticas: solo las de MBA, y van en su propia sección
    ("MBA Summer Internship, Strategy", "London, United Kingdom", True),
    ("Summer Associate - MBA Programme, Corporate Strategy", "London", True),
    ("Strategy Intern", "London, United Kingdom", False),
    ("Graduate - Operations Transformation Associate", "London", False),
    ("Operations & Process Improvement Intern", "London", False),
    # exclusiones que ganan sobre la lista de inclusión
    ("Growth Engineer", "UK - London", False),
    ("Junior Strategy Analyst", "London", False),
    ("Junior Operations Associate", "London", False),
    ("Talent Acquisition Partner", "UK - London", False),
    # título irrelevante en ubicación correcta
    ("Staff Data Scientist", "London, County London, England, United Kingdom", False),
]


def run_test(config: dict) -> int:
    """
    Los casos viven en config.yaml (sección `pruebas`) si están definidos, y si
    no se usan los de este archivo. Tienen que vivir junto a los filtros:
    describen una POLÍTICA —qué roles quiero— y no el mecanismo, así que quien
    copie el proyecto y cambie sus filtros debe cambiar también sus casos.
    """
    filters = config["filters"]
    casos = config.get("tests") or TEST_CASES
    failures = 0
    for title, location, expected in casos:
        job = {"title": title, "location": location}
        actual = passes_filters(job, filters)
        if actual != expected:
            failures += 1
            print(f"FALLA  {title!r} @ {location!r}: esperaba {expected}, dio {actual}")
        else:
            if not actual:
                etiqueta = "filtra    "
            elif is_internship(job, filters):
                etiqueta = "práctica  "
            else:
                etiqueta = "full time "
            print(f"ok     [{etiqueta}] {title} — {location}")

    print(f"\n{len(casos) - failures}/{len(casos)} casos correctos.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Radar de vacantes")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--check", action="store_true", help="diagnóstico, no guarda nada")
    group.add_argument("--demo", action="store_true", help="aviso de prueba, no guarda nada")
    group.add_argument(
        "--weekly", action="store_true", help="resumen semanal de todo lo abierto"
    )
    group.add_argument(
        "--descubrir", metavar="URL", help="dice qué plataforma usa una página de carreras"
    )
    group.add_argument("--reset", action="store_true", help="borra el estado y re-marca la línea base")
    group.add_argument("--test", action="store_true", help="auto-test de los filtros, sin red")
    args = parser.parse_args()

    config = load_config()
    if args.test:
        return run_test(config)
    if args.check:
        return run_check(config)
    if args.demo:
        return run_demo(config)
    if args.weekly:
        return run_weekly(config)
    if args.descubrir:
        return run_discover(config, args.descubrir)
    return run_normal(config, reset=args.reset)


if __name__ == "__main__":
    sys.exit(main())
