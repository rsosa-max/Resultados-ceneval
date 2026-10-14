#!/usr/bin/env python3
"""
Agrega los resultados individuales de una aplicación del EGEL al portal.

Uso básico (una aplicación nueva):

    python herramientas/agregar_resultados.py \
        --individuales Individuales_XXXX.zip \
        --global global_des_XXXX.pdf

Opcional:
    --apellidos apellidos.csv   CSV con columnas folio,apellido_paterno para
                                corregir o completar casos especiales.
    --examen "EGEL Plus Odontología"   Nombre del examen si no se detecta.
    --solo-revisar              Muestra lo que haría sin escribir archivos.

Cada persona abre sus resultados con su FOLIO + APELLIDO PATERNO.
Los PDFs se guardan cifrados (AES-256-GCM) en datos/r/; el repositorio nunca
contiene nombres, puntajes ni PDFs legibles.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import os
import re
import secrets
import sys
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path

try:
    import pdfplumber
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    sys.exit("Faltan dependencias. Ejecuta:  pip install -r herramientas/requirements.txt")

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / "datos" / "r"
ITERACIONES = 250_000
PREFIJO_ID = "egel:"

# ---------------------------------------------------------------- utilidades

def normalizar(texto: str) -> str:
    """Mayúsculas, sin acentos, sin espacios ni signos (igual que en el portal)."""
    t = unicodedata.normalize("NFD", texto or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^A-Z]", "", t.upper())


def palabras(texto: str):
    return [w for w in re.split(r"\s+", (texto or "").strip()) if w]


def id_registro(folio: str) -> str:
    return hashlib.sha256((PREFIJO_ID + folio).encode()).hexdigest()[:32]


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def titulo(texto: str) -> str:
    menores = {"de", "del", "la", "las", "los", "y", "e"}
    out = []
    for i, w in enumerate(texto.lower().split()):
        out.append(w if (i and w in menores) else w[:1].upper() + w[1:])
    return " ".join(out)


def texto_pdf(datos: bytes):
    with pdfplumber.open(io.BytesIO(datos)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


# ------------------------------------------------- lectura de PDF individual

NIVELES = ["Aún no satisfactorio", "Satisfactorio", "Sobresaliente"]
RE_NUM = re.compile(r"^\s*(\d{3,4})\s*$")


def nivel_por_puntaje(p, rangos):
    for (a, b), nombre in rangos:
        if a <= p <= b:
            return nombre
    return None


def leer_individual(datos: bytes):
    paginas = texto_pdf(datos)
    todo = "\n".join(paginas)

    def campo(patron):
        m = re.search(patron, todo)
        return m.group(1).strip() if m else ""

    info = {
        "nombre": campo(r"Nombre:\s*(.+?)\s+Folio:"),
        "folio": campo(r"Folio:\s*(\d+)"),
        "institucion": campo(r"Instituci[oó]n:\s*(.+?)\s+Fecha de aplicaci[oó]n:"),
        "fecha": campo(r"Fecha de aplicaci[oó]n:\s*(.+)"),
        "global": None,
        "secciones": [],
    }
    m = re.search(r"Nivel de desempe[nñ]o global\s+(" + "|".join(NIVELES) + ")", todo)
    if m:
        info["global"] = m.group(1)

    # Rangos del Índice Ceneval (p. ej. 700-999 1000-1149 1150-1300)
    rangos = []
    m = re.search(r"(\d{3,4})-(\d{3,4})\s+(\d{3,4})-(\d{3,4})\s+(\d{3,4})-(\d{3,4})", todo)
    if m:
        n = list(map(int, m.groups()))
        rangos = list(zip([(n[0], n[1]), (n[2], n[3]), (n[4], n[5])], NIVELES))
    else:
        rangos = list(zip([(700, 999), (1000, 1149), (1150, 1300)], NIVELES))

    secciones = {}
    orden = []

    def seccion(nombre):
        if nombre not in secciones:
            secciones[nombre] = {"nombre": nombre, "puntaje": None, "nivel": None, "areas": []}
            orden.append(nombre)
        return secciones[nombre]

    for pag in paginas:
        lineas = [l.strip() for l in pag.splitlines() if l.strip()]
        de_areas = "RESULTADOS EN LAS ÁREAS" in pag.upper()
        actual_sec = None
        pendiente = None      # área esperando su puntaje
        espera_nombre = False
        espera_num = False
        for l in lineas:
            if l.startswith("SECCIÓN ") or l.startswith("SECCION "):
                actual_sec = titulo(l.title().replace("Sección ", ""))
                actual_sec = actual_sec[:1].upper() + actual_sec[1:]
                seccion(actual_sec)
                espera_nombre = de_areas
                pendiente = None if de_areas else actual_sec
                continue
            if espera_nombre:
                if re.match(r"^(Aún no satisfactorio|\d{3,4}-)", l) or l.startswith("Índice"):
                    continue
                pendiente = l
                espera_nombre = False
                continue
            if l.startswith("Tu puntaje en Índice Ceneval"):
                espera_num = True
                continue
            if espera_num:
                mm = RE_NUM.match(l)
                if mm and actual_sec:
                    p = int(mm.group(1))
                    niv = nivel_por_puntaje(p, rangos)
                    if de_areas and pendiente:
                        secciones[actual_sec]["areas"].append({"nombre": pendiente, "puntaje": p, "nivel": niv})
                    else:
                        secciones[actual_sec]["puntaje"] = p
                        secciones[actual_sec]["nivel"] = niv
                    espera_num = False
                    pendiente = None
    info["secciones"] = [secciones[n] for n in orden if secciones[n]["puntaje"] or secciones[n]["areas"]]
    info["rangos"] = [[a, b, n] for (a, b), n in rangos]
    return info


# ------------------------------------------------------ lectura de reporte global

def leer_global(ruta: Path):
    """Devuelve {folio: 'APELLIDOS NOMBRES'} y datos generales del examen."""
    paginas = texto_pdf(ruta.read_bytes())
    todo = "\n".join(paginas)
    nombres = {}
    for l in todo.splitlines():
        m = re.match(r"^\s*(?:\d+\s+)?(.+?)\s+(\d{9})\b", l)
        if m:
            nombres[m.group(2)] = m.group(1).strip()
    examen = ""
    m = re.search(r"EGEL(?: Plus)?:\s*(.+)", todo)
    if m:
        es_plus = "EGEL Plus" in m.group(0)
        examen = ("EGEL Plus " if es_plus else "EGEL ") + titulo(m.group(1).strip())
    return nombres, examen


def candidatos_apellido(nombre_individual: str, nombre_global: str | None):
    """Posibles formas del apellido paterno que la persona podría escribir."""
    ind = palabras(nombre_individual)
    if nombre_global:
        glo = palabras(nombre_global)
        ni, ng = [normalizar(w) for w in ind], [normalizar(w) for w in glo]
        # nombres = sufijo del global que coincide con el prefijo del individual
        k = 0
        for j in range(1, len(glo)):
            if ng[-j:] == ni[:j]:
                k = j
        apellidos = glo[:-k] if k else glo[:-1]
    else:
        apellidos = ind[-2:] if len(ind) >= 3 else ind[-1:]
    if not apellidos:
        return []
    # "LEAL ISLA TORRES" -> LEAL, LEALISLA ; "RUIZ" -> RUIZ
    tope = max(1, len(apellidos) - 1)
    cands = {normalizar("".join(apellidos[:n])) for n in range(1, tope + 1)}
    return sorted(c for c in cands if c)


# ------------------------------------------------------------------- cifrado

def cifrar_registro(folio: str, apellidos: list[str], carga: dict) -> dict:
    sal = secrets.token_bytes(16)
    clave_datos = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    contenido = AESGCM(clave_datos).encrypt(iv, json.dumps(carga, ensure_ascii=False).encode(), None)
    envolturas = []
    for ap in apellidos:
        kek = hashlib.pbkdf2_hmac("sha256", f"{folio}:{ap}".encode(), sal, ITERACIONES, 32)
        iv_k = secrets.token_bytes(12)
        envolturas.append({"iv": b64(iv_k), "k": b64(AESGCM(kek).encrypt(iv_k, clave_datos, None))})
    secrets.SystemRandom().shuffle(envolturas)
    return {"v": 1, "it": ITERACIONES, "sal": b64(sal), "llaves": envolturas,
            "iv": b64(iv), "datos": b64(contenido)}


# ---------------------------------------------------------------- principal

def iterar_pdfs(ruta: Path):
    if ruta.is_file() and ruta.suffix.lower() == ".zip":
        with zipfile.ZipFile(ruta) as z:
            for n in sorted(z.namelist()):
                if n.lower().endswith(".pdf") and not n.startswith("__MACOSX"):
                    yield Path(n).name, z.read(n)
    elif ruta.is_dir():
        for p in sorted(ruta.rglob("*.pdf")):
            yield p.name, p.read_bytes()
    elif ruta.suffix.lower() == ".pdf":
        yield ruta.name, ruta.read_bytes()
    else:
        sys.exit(f"No encuentro PDFs en {ruta}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--individuales", required=True, type=Path, help="ZIP o carpeta con los PDFs individuales")
    ap.add_argument("--global", dest="glob", type=Path, help="PDF del reporte por sede (para separar apellidos)")
    ap.add_argument("--apellidos", type=Path, help="CSV folio,apellido_paterno (opcional)")
    ap.add_argument("--examen", help="Nombre del examen a mostrar")
    ap.add_argument("--solo-revisar", action="store_true", help="No escribe archivos")
    args = ap.parse_args()

    nombres_glob, examen = ({}, "")
    if args.glob:
        nombres_glob, examen = leer_global(args.glob)
    examen = args.examen or examen or "EGEL"

    manuales = {}
    if args.apellidos:
        with open(args.apellidos, newline="", encoding="utf-8-sig") as f:
            for fila in csv.DictReader(f):
                fol = (fila.get("folio") or "").strip()
                apel = fila.get("apellido_paterno") or ""
                if fol and apel:
                    manuales[fol] = sorted({normalizar(apel), normalizar(apel.split()[0])} - {""})

    DESTINO.mkdir(parents=True, exist_ok=True)
    ok, avisos = 0, []
    print(f"\nExamen: {examen}\n")
    for nombre_archivo, datos in iterar_pdfs(args.individuales):
        try:
            info = leer_individual(datos)
        except Exception as e:  # PDF ilegible: se reporta y se sigue
            avisos.append(f"{nombre_archivo}: no se pudo leer ({e})")
            continue
        folio = info["folio"] or re.sub(r"\D", "", nombre_archivo)
        if not folio:
            avisos.append(f"{nombre_archivo}: sin folio, se omitió")
            continue
        if folio in manuales:
            apellidos = manuales[folio]
        else:
            apellidos = candidatos_apellido(info["nombre"], nombres_glob.get(folio))
            if not nombres_glob.get(folio):
                avisos.append(f"{folio}: apellido deducido sin reporte global ({', '.join(apellidos)}); revísalo")
        if not apellidos:
            avisos.append(f"{folio}: no se pudo determinar el apellido; agrégalo en --apellidos")
            continue
        if not info["secciones"]:
            avisos.append(f"{folio}: no se reconocieron puntajes; solo estará la descarga del PDF")

        carga = {
            "nombre": titulo(info["nombre"]),
            "folio": folio,
            "examen": examen,
            "fecha": info["fecha"],
            "institucion": info["institucion"],
            "global": info["global"],
            "secciones": info["secciones"],
            "rangos": info["rangos"],
            "archivo": f"Resultados_{examen.replace(' ', '_')}_{folio}.pdf",
            "pdf": b64(datos),
        }
        destino = DESTINO / f"{id_registro(folio)}.json"
        estado = "actualizado" if destino.exists() else "nuevo"
        resumen = " · ".join(f"{s['nombre'].split()[0]} {s['puntaje']}" for s in info["secciones"] if s["puntaje"])
        print(f"  {folio}  {titulo(info['nombre'])[:38]:<38}  clave: {'/'.join(apellidos):<16} {resumen}  [{estado}]")
        if not args.solo_revisar:
            destino.write_text(json.dumps(cifrar_registro(folio, apellidos, carga)), encoding="utf-8")
        ok += 1

    print(f"\n{ok} registro(s) {'revisados' if args.solo_revisar else 'guardados en datos/r/'}.")
    for a in avisos:
        print("  AVISO:", a)
    if not args.solo_revisar and ok:
        bitacora = RAIZ / "datos" / "bitacora.txt"
        with open(bitacora, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M}  {examen}  {ok} registros\n")
        print("\nSiguiente paso: git add datos && git commit -m \"Resultados\" && git push")


if __name__ == "__main__":
    main()
