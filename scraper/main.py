import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta
from typing import Optional, List
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from fastapi import FastAPI
from pydantic import BaseModel
from playwright.async_api import async_playwright

app = FastAPI(title="CUCEI FB Scraper API", version="5.2.0")

ARCH_COOKIES = "/app/cookies/fb_cookies.json"

MESES_MAP = {
    "enero": 1, "ene": 1, "febrero": 2, "feb": 2, "marzo": 3, "mar": 3,
    "abril": 4, "abr": 4, "mayo": 5, "may": 5, "junio": 6, "jun": 6,
    "julio": 7, "jul": 7, "agosto": 8, "ago": 8, "septiembre": 9, "sep": 9, "set": 9,
    "octubre": 10, "oct": 10, "noviembre": 11, "nov": 11, "diciembre": 12, "dic": 12
}

class PeticionScrap(BaseModel):
    page_url: str = "https://www.facebook.com/udegcucei"
    max_posts: Optional[int] = 15
    fecha_inicio: Optional[str] = None
    fecha_fin: Optional[str] = None
    dependencia_default: str = "Coordinacion de Computacion - CUCEI"
    formato_n8n_directo: bool = True

def limpiarCookies(cookiesCrudas: list) -> list:
    listaLimpia = []
    for c in cookiesCrudas:
        nom = c.get("name")
        val = c.get("value")
        if not nom or val is None:
            continue
        itemCookie = {
            "name": str(nom),
            "value": str(val),
            "domain": ".facebook.com",
            "path": c.get("path", "/"),
            "secure": True,
            "httpOnly": bool(c.get("httpOnly", False)),
        }
        exp = c.get("expirationDate") or c.get("expires")
        if exp:
            try:
                itemCookie["expires"] = float(exp)
            except Exception:
                pass

        sitio = str(c.get("sameSite", "")).lower()
        if sitio == "strict":
            itemCookie["sameSite"] = "Strict"
        elif sitio in ["none", "no_restriction"]:
            itemCookie["sameSite"] = "None"
        else:
            itemCookie["sameSite"] = "Lax"

        listaLimpia.append(itemCookie)
    return listaLimpia

def limpiarUrlFb(urlCruda: str) -> str:
    if not urlCruda:
        return ""
    u = urlCruda.strip()
    if u.startswith("/"):
        u = "https://www.facebook.com" + u
    if not u.startswith("http"):
        return ""
    try:
        parsed = urlparse(u)
        qs = parse_qs(parsed.query)
        paramsValidos = {}
        for k in ["fbid", "story_fbid", "id", "v"]:
            if k in qs:
                paramsValidos[k] = qs[k][0]
        nuevaQuery = urlencode(paramsValidos)
        ruta = parsed.path.rstrip("/")
        if "/photos/" in ruta and "/pcb." not in ruta:
            partes = ruta.split("/")
            if len(partes) >= 4 and partes[-1].isdigit():
                return f"https://www.facebook.com/photo/?fbid={partes[-1]}"
        return urlunparse((parsed.scheme, parsed.netloc, ruta, "", nuevaQuery, ""))
    except Exception:
        return u.split("&__cft__")[0].split("?__cft__")[0]

def esUrlPostValida(url: str, urlBase: str) -> bool:
    if not url:
        return False
    u = url.rstrip("/")
    b = urlBase.rstrip("/")
    if u == b or u == "https://www.facebook.com":
        return False
    patrones = ["/posts/", "/videos/", "/reel/", "/photos/", "/photo", "story_fbid=", "fbid=", "/events/"]
    return any(p in u for p in patrones)

def parsearFechaEsp(txtFecha: str) -> Optional[datetime]:
    if not txtFecha:
        return None
    s = txtFecha.lower().strip()
    ahora = datetime.now()

    if s.startswith("unix:"):
        try:
            ts = int(s.split(":")[1])
            return datetime.fromtimestamp(ts)
        except Exception:
            pass

    if "ayer" in s:
        return ahora - timedelta(days=1)
    if any(k in s for k in ["momento", "ahora"]):
        return ahora

    mMin = re.search(r"(\d+)\s*min", s)
    if mMin:
        return ahora - timedelta(minutes=int(mMin.group(1)))

    mHora = re.search(r"(\d+)\s*(?:h|hora|horas)\b", s)
    if mHora and len(s) < 20:
        return ahora - timedelta(hours=int(mHora.group(1)))

    mDias = re.search(r"(\d+)\s*(?:d|dia|dias|día|días)\b", s)
    if mDias and len(s) < 20:
        return ahora - timedelta(days=int(mDias.group(1)))

    mSem = re.search(r"(\d+)\s*sem", s)
    if mSem and len(s) < 20:
        return ahora - timedelta(weeks=int(mSem.group(1)))

    mNum = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b", s)
    if mNum:
        try:
            return datetime(int(mNum.group(3)), int(mNum.group(2)), int(mNum.group(1)))
        except ValueError:
            pass

    mTxt = re.search(r"(\d{1,2})\s*(?:de\s+)?([a-záéíóú]+)(?:\s*(?:de\s+|del\s+)?(\d{4}))?", s)
    if mTxt:
        dia = int(mTxt.group(1))
        nomMes = mTxt.group(2).replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
        mes = MESES_MAP.get(nomMes) or MESES_MAP.get(nomMes[:3])
        anio = int(mTxt.group(3)) if mTxt.group(3) else ahora.year
        if mes and 1 <= dia <= 31:
            try:
                dtCalc = datetime(anio, mes, dia)
                if not mTxt.group(3) and dtCalc > ahora + timedelta(days=2):
                    dtCalc = datetime(anio - 1, mes, dia)
                return dtCalc
            except ValueError:
                pass
    return None

def extraerDatosJsonFb(cuerpoTxt: str, mapaTiempoTexto: dict, mapaUrlTexto: dict, mapaTiempoId: dict):
    try:
        for m in re.finditer(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"', cuerpoTxt):
            try:
                txtDecod = json.loads(f'"{m.group(1)}"')
            except Exception:
                continue
            llaveTxt = re.sub(r"\W+", "", txtDecod[:80]).lower()
            if len(llaveTxt) <= 15:
                continue

            fragIni = max(0, m.start() - 2500)
            fragFin = min(len(cuerpoTxt), m.end() + 2500)
            ventana = cuerpoTxt[fragIni:fragFin]

            mTime = re.search(r'"(?:creation_time|publish_time)"\s*:\s*(\d{10})\b', ventana)
            if mTime:
                mapaTiempoTexto[llaveTxt] = int(mTime.group(1))

            mUrl = re.search(r'"(?:permalink_url|url)"\s*:\s*"(https?:\\/\\/www\.facebook\.com\\/[^"]*(?:posts|videos|reel|photos|story_fbid|pfbid)[^"]*)"', ventana)
            if mUrl:
                try:
                    urlDecod = json.loads(f'"{mUrl.group(1)}"')
                    urlLimpia = limpiarUrlFb(urlDecod)
                    if urlLimpia:
                        mapaUrlTexto[llaveTxt] = urlLimpia
                except Exception:
                    pass
            else:
                mPfbid = re.search(r'\b(pfbid[a-zA-Z0-9]{15,})\b', ventana)
                if mPfbid and llaveTxt not in mapaUrlTexto:
                    mapaUrlTexto[llaveTxt] = f"https://www.facebook.com/udegcucei/posts/{mPfbid.group(1)}"

        for mId in re.finditer(r'"(?:post_id|story_fbid)"\s*:\s*"(\d{10,25}|pfbid[a-zA-Z0-9]+)"', cuerpoTxt):
            idVal = mId.group(1)
            fragIni = max(0, mId.start() - 1200)
            fragFin = min(len(cuerpoTxt), mId.end() + 1200)
            ventana = cuerpoTxt[fragIni:fragFin]
            mTime = re.search(r'"(?:creation_time|publish_time)"\s*:\s*(\d{10})\b', ventana)
            if mTime:
                mapaTiempoId[idVal] = int(mTime.group(1))
    except Exception:
        pass

async def extraerPubsFb(pet: PeticionScrap) -> List[dict]:
    listaRes = []
    firmasVistas = set()
    mapaTiempoTexto = {}
    mapaUrlTexto = {}
    mapaTiempoId = {}

    dtIni = datetime.strptime(pet.fecha_inicio, "%Y-%m-%d") if pet.fecha_inicio else None
    dtFin = datetime.strptime(pet.fecha_fin, "%Y-%m-%d").replace(hour=23, minute=59, second=59) if pet.fecha_fin else None
    metaPosts = pet.max_posts if (pet.max_posts and pet.max_posts > 0) else (2000 if dtIni else 15)

    async with async_playwright() as p:
        nav = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        ctx = await nav.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 1000},
            locale="es-MX"
        )

        await ctx.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

        if os.path.exists(ARCH_COOKIES):
            try:
                with open(ARCH_COOKIES, "r", encoding="utf-8") as f:
                    cookiesCrudas = json.load(f)
                    cookiesVal = limpiarCookies(cookiesCrudas)
                    await ctx.add_cookies(cookiesVal)
                    noms = [c["name"] for c in cookiesVal]
                    tieneSesion = "c_user" in noms and "xs" in noms
                    print(f"[OK] {len(cookiesVal)} cookies cargadas (Sesion activa: {tieneSesion}).")
            except Exception as e:
                print(f"[WARN] Error al leer cookies: {e}")

        pag = await ctx.new_page()

        async def escucharRed(resp):
            try:
                urlResp = resp.url
                if "graphql" in urlResp or "facebook.com" in urlResp:
                    ct = resp.headers.get("content-type", "")
                    if "json" in ct or "javascript" in ct or "text" in ct or "html" in ct:
                        txt = await resp.text()
                        if "creation_time" in txt or "publish_time" in txt or "pfbid" in txt:
                            extraerDatosJsonFb(txt, mapaTiempoTexto, mapaUrlTexto, mapaTiempoId)
            except Exception:
                pass

        pag.on("response", escucharRed)

        await pag.route("**/*.{mp4,webm,avi,mov,woff,woff2,ttf}", lambda route: route.abort())

        print(f"[INFO] Navegando a {pet.page_url} (Meta: {metaPosts} pubs)...")
        await pag.goto(pet.page_url, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3.5)

        try:
            htmlInicial = await pag.content()
            extraerDatosJsonFb(htmlInicial, mapaTiempoTexto, mapaUrlTexto, mapaTiempoId)
        except Exception:
            pass

        tituloPag = await pag.title()
        print(f"[INFO] Pagina cargada: '{tituloPag}' | URL: {pag.url}")

        await pag.evaluate("""() => {
            document.querySelectorAll('div[aria-label="Cerrar"], div[aria-label="Close"]').forEach(b => {
                try { b.click(); } catch(e) {}
            });
        }""")

        numScroll = 0
        scrollsVacios = 0
        rachaAntiguos = 0
        maxScrolls = max(35, metaPosts * 4)

        while len(listaRes) < metaPosts and numScroll < maxScrolls:
            numScroll += 1
            conteoPrev = len(listaRes)

            await pag.evaluate("""() => {
                document.querySelectorAll('a[role="link"], span[id] a, a[href*="/posts/"], a[href*="/videos/"], a[href*="/photo"]').forEach(a => {
                    try {
                        a.dispatchEvent(new PointerEvent('pointerover', { bubbles: true }));
                        a.dispatchEvent(new MouseEvent('mouseover', { bubbles: true }));
                        a.dispatchEvent(new FocusEvent('focus', { bubbles: true }));
                    } catch(e) {}
                });
            }""")
            await asyncio.sleep(0.35)

            loteDom = await pag.evaluate("""() => {
                document.querySelectorAll('div[role="button"], span[role="button"]').forEach(btn => {
                    const t = (btn.innerText || "").trim();
                    if (t === "Ver más" || t === "See more" || t.endsWith("Ver más")) {
                        try { btn.click(); } catch(e) {}
                    }
                });

                const raicesPost = new Set();

                document.querySelectorAll('div[data-ad-preview="message"], div[data-ad-comet-preview="message"]').forEach(msg => {
                    let act = msg;
                    for (let i = 0; i < 12; i++) {
                        if (!act.parentElement) break;
                        act = act.parentElement;
                        if (act.getAttribute('role') === 'article' || act.hasAttribute('data-pagelet')) break;
                    }
                    raicesPost.add(act);
                });

                document.querySelectorAll('div[role="article"]').forEach(art => {
                    const aria = (art.getAttribute('aria-label') || '').toLowerCase();
                    if (!aria.includes('comentario') && !aria.includes('comment') && !aria.includes('respuesta')) {
                        raicesPost.add(art);
                    }
                });

                document.querySelectorAll('a[href*="/posts/"], a[href*="/videos/"], a[href*="/reel/"], a[href*="story_fbid"], a[href*="fbid="]').forEach(a => {
                    if (a.closest('div[aria-label*="Comentario"], div[aria-label*="Comment"]')) return;
                    let act = a;
                    for (let i = 0; i < 10; i++) {
                        if (!act.parentElement) break;
                        act = act.parentElement;
                        if (act.getAttribute('role') === 'article' || act.offsetHeight > 220) {
                            raicesPost.add(act);
                            break;
                        }
                    }
                });

                const regexFecha = /(\\d{1,2}\\s+de\\s+[a-záéíóú]+(?:\\s+de\\s+\\d{4})?|\\d{1,2}\\s+[a-záéíóú]{3,4}\\s+\\d{4}|(?:hace\\s+)?\\d+\\s*(?:min|h|hora|horas|d|dia|dias|día|días|sem)|ayer)/i;
                const extraidos = [];

                raicesPost.forEach(raiz => {
                    if (raiz.closest('div[aria-label*="Comentario"], div[aria-label*="Comment"]')) return;

                    let urlPost = "";
                    let pistaFecha = "";

                    const nodoTime = raiz.querySelector('abbr[data-utime], time[datetime]');
                    if (nodoTime) {
                        const utime = nodoTime.getAttribute('data-utime');
                        if (utime) pistaFecha = "unix:" + utime;
                        else if (nodoTime.getAttribute('title')) pistaFecha = nodoTime.getAttribute('title');
                    }

                    const enlaces = raiz.querySelectorAll('a[href], span[aria-label], a[aria-label]');
                    let mejorPrioridad = 99;

                    for (const el of enlaces) {
                        if (el.closest('div[aria-label*="Comentario"], div[aria-label*="Comment"]')) continue;
                        const href = el.href || el.getAttribute("href") || "";
                        const txt = (el.innerText || "").trim();
                        const aria = (el.getAttribute("aria-label") || "").trim();
                        const title = (el.getAttribute("title") || "").trim();

                        for (const cand of [aria, title, txt]) {
                            if (!pistaFecha && cand && cand.length < 65 && regexFecha.test(cand)) {
                                pistaFecha = cand;
                                break;
                            }
                        }

                        if (!href || href.includes("/hashtag/") || href.includes("comment_id=") || href.includes("dialog/share") || href.includes("/groups/")) {
                            continue;
                        }

                        let prio = 99;
                        if (href.includes("/posts/pfbid") || href.includes("story_fbid=")) prio = 1;
                        else if (href.includes("/posts/") || href.includes("/videos/") || href.includes("/reel/") || href.includes("/events/")) prio = 2;
                        else if (href.includes("fbid=") || href.includes("/photos/") || href.includes("/photo")) prio = 3;

                        if (prio < mejorPrioridad) {
                            mejorPrioridad = prio;
                            urlPost = href;
                        }
                    }

                    let partesTxt = [];
                    const divsMsg = raiz.querySelectorAll('div[data-ad-preview="message"], div[data-ad-comet-preview="message"], div[dir="auto"]');
                    divsMsg.forEach(d => {
                        if (d.closest('div[aria-label*="Comentario"], div[aria-label*="Comment"]')) return;
                        const t = (d.innerText || "").trim();
                        if (t.length > 15 &&
                            !t.includes("Me gusta") &&
                            !t.includes("Compartir") &&
                            !t.startsWith("Ver los ") &&
                            !t.startsWith("Escribe un comentario")) {
                            partesTxt.push(t);
                        }
                    });

                    const txtCompleto = [...new Set(partesTxt)].join("\\n").trim();

                    if (!pistaFecha && txtCompleto) {
                        const mEnTexto = txtCompleto.match(/(\\d{1,2}\\s+de\\s+[a-záéíóú]+\\s+de\\s+\\d{4}|\\d{1,2}[/-]\\d{1,2}[/-]\\d{4})/i);
                        if (mEnTexto) pistaFecha = mEnTexto[1];
                    }

                    let urlImg = "";
                    const imgs = raiz.querySelectorAll('img[src]');
                    for (const img of imgs) {
                        const src = img.src || "";
                        const w = img.naturalWidth || img.width || img.clientWidth || 0;
                        if ((src.includes("scontent") || src.includes("fbcdn")) &&
                            !src.includes("emoji") &&
                            !src.includes("rsrc.php") &&
                            !src.includes("p50x50") &&
                            !src.includes("s40x40") &&
                            (w === 0 || w >= 150)) {
                            urlImg = src;
                            break;
                        }
                    }

                    if (txtCompleto.length >= 15 || (urlPost && urlImg)) {
                        extraidos.push({
                            texto: txtCompleto || "[Publicacion multimedia sin texto]",
                            url: urlPost,
                            img: urlImg,
                            fechaCruda: pistaFecha
                        });
                    }
                });

                return {
                    totalNodosDom: raicesPost.size,
                    items: extraidos
                };
            }""")

            numCand = loteDom.get("totalNodosDom", 0)
            pubsCrudas = loteDom.get("items", [])

            for item in pubsCrudas:
                if len(listaRes) >= metaPosts:
                    break

                txtLimpio = re.sub(r"\W+", "", item["texto"][:100]).lower()
                firma = txtLimpio if len(txtLimpio) > 20 else f"{item['url']}_{item['img'][:60]}"

                if not firma or firma in firmasVistas:
                    continue

                llaveBusquedaTxt = re.sub(r"\W+", "", item["texto"][:80]).lower()

                urlDomLimpia = limpiarUrlFb(item.get("url", ""))
                urlFinal = ""

                if esUrlPostValida(urlDomLimpia, pet.page_url):
                    urlFinal = urlDomLimpia
                elif llaveBusquedaTxt in mapaUrlTexto:
                    urlFinal = mapaUrlTexto[llaveBusquedaTxt]
                else:
                    urlFinal = pet.page_url

                dtPub = None
                if llaveBusquedaTxt in mapaTiempoTexto:
                    dtPub = datetime.fromtimestamp(mapaTiempoTexto[llaveBusquedaTxt])
                else:
                    for idPost, tsVal in mapaTiempoId.items():
                        if urlFinal and idPost in urlFinal:
                            dtPub = datetime.fromtimestamp(tsVal)
                            break

                if not dtPub:
                    dtPub = parsearFechaEsp(item.get("fechaCruda", ""))

                if dtFin and dtPub and dtPub > dtFin:
                    continue
                if dtIni and dtPub and dtPub < dtIni:
                    rachaAntiguos += 1
                    continue
                elif dtPub:
                    rachaAntiguos = 0

                firmasVistas.add(firma)
                idx = len(listaRes) + 1
                fechaFinal = dtPub.strftime("%d/%m/%Y") if dtPub else "Sin fecha"

                listaRes.append({
                    "id_registro": idx,
                    "nombre": "[FALTA - INFERIR DE DESCRIPCION]",
                    "fecha": fechaFinal,
                    "clasificacion": "[FALTA - CLASIFICAR]",
                    "link_publicacion": urlFinal,
                    "dependencia": pet.dependencia_default,
                    "carpeta_evidencia": item["img"] or "",
                    "descripcion_corta": item["texto"],
                    "lugar": "Campus CUCEI",
                    "ods_registrados": "[FALTA - ASIGNAR ODS]",
                    "asistencia_registrada": "[FALTA - ESTIMAR PARTICIPANTES APROXIMADOS]",
                    "es_servicio": "No"
                })

            print(f"[SCROLL #{numScroll}] (Nodos DOM: {numCand}): {len(listaRes)}/{metaPosts} pubs unicas...")

            if dtIni and rachaAntiguos >= 6:
                print(f"[FIN] Se alcanzo la fecha limite inferior ({pet.fecha_inicio}).")
                break

            if len(listaRes) >= metaPosts:
                break

            if len(listaRes) == conteoPrev:
                scrollsVacios += 1
            else:
                scrollsVacios = 0

            if scrollsVacios >= 8:
                print("[WARN] El DOM dejo de entregar publicaciones nuevas.")
                break

            pasoScroll = random.randint(1600, 2400)
            await pag.evaluate(f"window.scrollBy(0, {pasoScroll})")
            await pag.keyboard.press("PageDown")
            await asyncio.sleep(random.uniform(1.4, 2.0))

        await nav.close()
        print(f"[OK] Scrapeo finalizado: {len(listaRes)} publicaciones extraidas.")
        return listaRes

@app.post("/scrape")
@app.post("/scrape-sync")
async def endpointScrap(pet: PeticionScrap):
    return await extraerPubsFb(pet)
