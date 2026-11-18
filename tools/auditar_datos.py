"""Audita datos personales en el árbol del repo (y, si se pide, en mensajes de commit).

    python tools/auditar_datos.py --local [--data-dir DIR] [--commits RANGO]
    python tools/auditar_datos.py --ci [--commits RANGO]

- Modo local: los términos sensibles salen del perfil real (`<DATA_DIR>/candidato`: personales.txt y
  experiencia*.txt): nombre, apellido, mail, teléfono, LinkedIn, localidad, empleadores, escuelas, cronología
  (rangos de años de cada rol) y logros con número. Nunca se escriben a disco ni a la salida: todo sale enmascarado.
  Suma los patrones genéricos del modo CI.
- Modo CI: solo patrones genéricos (mails fuera de dominios de prueba, teléfonos, rutas de usuario de Windows,
  claves y tokens). No lee ningún perfil.
- Recorre `git ls-files` (también el interior de .docx y otros zip) y, con `--commits`, los mensajes del rango.

Sale con 0 si no hay hallazgos, 1 si los hay y 2 si falta el perfil. DATA_DIR: `--data-dir`, si no
`LINKEDOUT_DATA_DIR`, si no `~/LinkedOut/datos` (producción, solo lectura).
"""
import argparse
import io
import re
import subprocess
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, List, Optional, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.paths import variable_entorno  # noqa: E402

RAIZ = Path(__file__).resolve().parent.parent

# Dominios de prueba: reservados (RFC 2606) o con «test»/«example» antes de un ccTLD (acme.test.ar), más los que el
# parser descarta a propósito (sus tests prueban justamente eso).
RE_DOMINIO_DE_PRUEBA = re.compile(r"(?:^|\.)(?:example\.(?:com|org|net)|[\w-]+\.(?:test|example|invalid|localhost)"
                                  r"|[\w-]+\.(?:test|example)\.[a-z]{2}|localhost)$")
DOMINIOS_DESCARTADOS_POR_EL_PARSER = {"ejemplo.com", "linkedin.com"}
DOMINIOS_DE_EJEMPLO = {"ejemplo.com.ar"}  # para probar la señal «.com.ar», que un dominio reservado no tiene
DIRECCIONES_PERMITIDAS = {"noreply@anthropic.com"}
USUARIOS_DE_EJEMPLO = {"usuario", "user", "username", "ejemplo", "example", "public", "default", "todos"}
DIGITOS_FICTICIOS = "5555"  # teléfonos de relleno (+54 9 11 5555-0100): rango ficticio
MAXIMO_DIGITOS_DISTINTOS_FICTICIO = 3  # 0000-0000, 4444-3333, 222-3344: nadie tiene ese número
SECUENCIA_FICTICIA = 6  # 1234-5678, 987-6543
VENTANA_LISTA = 3  # líneas alrededor de una localidad para decidir si está en una lista genérica
MINIMO_LISTA = 4  # otras localidades del GBA en la ventana para que sea una lista genérica
MINIMO_LOGRO = 10  # un número menor que esto no identifica un logro
MINIMO_RANGOS_CRONOLOGIA = 2  # un rango de años suelto («2025 - 2026») no identifica; dos rangos reales juntos, sí
MINIMO_TOKEN_APELLIDO = 4
SECCIONES = ("habilidades", "educacion", "formacion", "estudios", "idiomas", "certificaciones", "cursos",
             "referencias", "technical skills", "interpersonal skills", "soft skills", "skills", "education",
             "languages", "certifications", "references", "experiencia", "professional experience")
EMPLEADORES_GENERICOS = {"freelance", "independiente", "autonomo", "self-employed", "monotributista"}
RE_TELEFONO_PERFIL = re.compile(r"\+\d[\d\s().-]{7,}\d|wa\.me/\d{8,}")
GENERICAS = {"caba", "buenos aires", "capital federal", "argentina", "amba", "gba"}
PARTIDOS_GBA = {
    "caba", "gba", "amba", "buenos aires", "capital federal", "san martin", "vicente lopez", "san isidro",
    "san fernando", "tigre",
    "pilar", "escobar", "moron", "la matanza", "avellaneda", "lanus", "quilmes", "lomas de zamora", "tres de febrero",
    "san miguel", "jose c paz", "jose c. paz", "malvinas argentinas", "hurlingham", "ituzaingo", "moreno", "merlo",
    "berazategui", "florencio varela", "almirante brown", "esteban echeverria", "ezeiza", "presidente peron",
    "san vicente", "marcos paz", "general rodriguez", "la plata", "ensenada", "berisso", "campana", "zarate",
    "lujan", "exaltacion de la cruz", "canuelas", "brandsen", "general las heras",
}

PATRONES_CLAVES = [
    r"sk-ant-[\w-]{20,}", r"\bsk-[A-Za-z0-9]{32,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}", r"\bgithub_pat_\w{20,}",
    r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abpr]-[\w-]{10,}", r"\bAIza[\w-]{35}", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    r"\beyJ[\w-]{10,}\.eyJ[\w-]{10,}\.[\w-]{10,}",
]
RE_MAIL = re.compile(r"(?<![\w.+:/-])[\w.+-]+@((?:[\w-]+\.)+[A-Za-z]{2,})(?![\w-])")
RE_TELEFONO = re.compile(r"(?<![\w+])\+\d{1,3}(?:[\s.-]?\(?\d+\)?){3,6}(?!\d)"
                         r"|(?<!\d)(?:0?11|15)[\s-]\d{4}[\s-]\d{4}(?!\d)|wa\.me/\d{8,}")
RE_RUTA = re.compile(r"[A-Za-z]:(?:\\\\|\\|/)+Users(?:\\\\|\\|/)+([^\\/\s'\"<>%$`{}\[\]]+)", re.IGNORECASE)
RE_CLAVE = re.compile("|".join(PATRONES_CLAVES))


@dataclass(frozen=True)
class Termino:
    categoria: str
    valor: str


@dataclass(frozen=True)
class Hallazgo:
    lugar: str
    categoria: str
    enmascarado: str


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in sin_tildes if not unicodedata.combining(c)).lower()


def _enmascarar_pieza(pieza: str) -> str:
    return pieza[:1] + "*" * (len(pieza) - 1)


def enmascarar(valor: str) -> str:
    """Deja solo la inicial de cada palabra (y el dominio de primer nivel de un mail)."""
    partes = []
    for palabra in valor.split(" "):
        if "@" in palabra:
            local, _, dominio = palabra.partition("@")
            base, punto, tld = dominio.rpartition(".")
            dominio = _enmascarar_pieza(base) + punto + tld if punto else _enmascarar_pieza(dominio)
            partes.append(_enmascarar_pieza(local) + "@" + dominio)
        else:
            partes.append(_enmascarar_pieza(palabra))
    return " ".join(partes)


# ----------------------------------------------------------------- términos del perfil

def _leer(ruta: Path) -> str:
    return ruta.read_text(encoding="utf-8", errors="replace") if ruta.is_file() else ""


def _terminos_personales(texto: str) -> Iterator[Termino]:
    lineas = [l.strip() for l in texto.splitlines()]
    nombre = next((l for l in lineas if l), "")
    if nombre:
        yield Termino("nombre", nombre)
        palabras = nombre.split()
        if len(palabras) > 1:
            yield Termino("apellido", " ".join(palabras[1:]))
            for palabra in palabras[1:]:
                if len(palabra) >= MINIMO_TOKEN_APELLIDO:
                    yield Termino("apellido", palabra)
    for linea in lineas:
        for mail in RE_MAIL.finditer(linea):
            yield Termino("mail", mail.group(0))
            local = mail.group(0).split("@")[0]
            if len(local) >= 5:
                yield Termino("mail", local)
        for slug in re.finditer(r"linkedin\.com/in/([^/?\s|]+)", linea, re.IGNORECASE):
            yield Termino("linkedin", slug.group(1))
        for tel in RE_TELEFONO_PERFIL.finditer(linea):
            digitos = re.sub(r"\D", "", tel.group(0))
            if len(digitos) >= 8:
                yield Termino("telefono", digitos[-8:])
        if "|" in linea:
            localidad = linea.split("|")[0].split(",")[0].strip()
            if localidad and normalizar(localidad) not in GENERICAS and not re.search(r"\d|@", localidad):
                yield Termino("localidad", localidad)
    seccion = ""
    for linea in lineas:
        if _es_seccion(linea):
            seccion = normalizar(linea)
            continue
        partes = [p.strip() for p in re.split(r"\s[-–—]\s", linea.lstrip("•-* ").split(":")[0])]
        if seccion.startswith(("educacion", "formacion", "estudios", "education")) and len(partes) >= 2:
            escuela = partes[1].split("(")[0].strip()
            if escuela and not re.fullmatch(r"[\d\s]+", escuela):
                yield Termino("escuela", escuela)
        if seccion.startswith(("referencias", "references")) and partes[0]:
            yield Termino("referencia", partes[0])


def _es_seccion(linea: str) -> bool:
    texto = normalizar(linea.lstrip("#").strip())
    return bool(texto) and any(texto == s or texto.startswith(s + " ") for s in SECCIONES)


def _terminos_experiencia(texto: str) -> Iterator[Termino]:
    for linea in texto.splitlines():
        linea = linea.strip()
        m = re.match(r"^##\s+(?!#)(.+)$", linea)
        if m:
            titulo = m.group(1).strip()
            if _es_seccion(titulo) or normalizar(titulo) in EMPLEADORES_GENERICOS:
                continue
            principal, _, resto = titulo.partition("(")
            yield Termino("empleador", principal.strip())
            for interno in re.split(r"[,;/]", resto.rstrip(")")):
                interno = re.sub(r"^\s*(cliente|para|en)\s*:?\s*", "", interno.strip(), flags=re.IGNORECASE)
                if len(interno) >= 3:
                    yield Termino("empleador", interno)
            continue
        m = re.match(r"^###\s+.+\|\s*(\d{4})\s*[-–—]\s*(\d{4})", linea)
        if m:
            yield Termino("cronologia", f"{m.group(1)} - {m.group(2)}")
            continue
        if linea.startswith(("•", "-", "*")):
            for logro in re.finditer(r"(?<![\w.,])(\d[\d.,]*)\s*(%|[A-Za-zÁÉÍÓÚÑáéíóúñ]{4,})", linea):
                numero = re.sub(r"\D", "", logro.group(1))
                if not numero or int(numero) < MINIMO_LOGRO or re.fullmatch(r"(19|20)\d\d", numero):
                    continue
                yield Termino("logro", f"{logro.group(1)} {logro.group(2)}".replace(" %", "%"))


def terminos_candidato(data_dir: Path) -> List[Termino]:
    carpeta = Path(data_dir) / "candidato"
    terminos = list(_terminos_personales(_leer(carpeta / "personales.txt")))
    for experiencia in sorted(carpeta.glob("experiencia*.txt")):
        terminos += _terminos_experiencia(_leer(experiencia))
    vistos: Set[Termino] = set()
    return [t for t in terminos if not (t in vistos or vistos.add(t))]


def _patron(termino: Termino) -> re.Pattern:
    valor = normalizar(termino.valor)
    if termino.categoria == "telefono":
        cuerpo = r"[\s().-]?".join(valor)
        return re.compile(cuerpo + r"(?!\d)")
    if termino.categoria == "cronologia":
        a, b = re.findall(r"\d{4}", valor)
        return re.compile(rf"(?<!\d){a}\s*(?:-|–|—|/|a|al|hasta)\s*{b}(?!\d)")
    palabras = [re.escape(p) for p in re.split(r"[\s_-]+", valor) if p]
    return re.compile(r"(?<![a-z0-9])" + r"[\s_.-]*".join(palabras) + r"(?![a-z0-9])")


# ----------------------------------------------------------------- búsqueda

def _en_lista_generica(lineas_norm: List[str], i: int, localidad: str) -> bool:
    ventana = " ".join(lineas_norm[max(0, i - VENTANA_LISTA): i + VENTANA_LISTA + 1])
    otras = {p for p in PARTIDOS_GBA if p != localidad and re.search(rf"(?<![a-z]){re.escape(p)}(?![a-z])", ventana)}
    return len(otras) >= MINIMO_LISTA


def mail_de_prueba(mail: str) -> bool:
    dominio = mail.rsplit("@", 1)[-1]
    # «rrhh@acme.test.Gracias»: el texto pegado al dominio no es parte de él
    dominio = re.sub(r"(?:\.[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+)+$", "", dominio).lower()
    return (mail.lower() in DIRECCIONES_PERMITIDAS
            or dominio in DOMINIOS_DESCARTADOS_POR_EL_PARSER | DOMINIOS_DE_EJEMPLO
            or bool(RE_DOMINIO_DE_PRUEBA.search(dominio)))


def telefono_ficticio(digitos: str) -> bool:
    """Relleno evidente: el rango 5555, pocos dígitos distintos o una escalera (1234567, 9876543)."""
    final = digitos[-7:]
    escaleras = [
        "".join(str((i + k) % 10) for k in range(SECUENCIA_FICTICIA)) for i in range(10)
    ] + ["".join(str((i - k) % 10) for k in range(SECUENCIA_FICTICIA)) for i in range(10)]
    return (DIGITOS_FICTICIOS in digitos or len(set(final)) <= MAXIMO_DIGITOS_DISTINTOS_FICTICIO
            or any(e in digitos for e in escaleras))


def _genericos(linea: str) -> Iterator[Tuple[str, str]]:
    for m in RE_MAIL.finditer(linea):
        if not mail_de_prueba(m.group(0)):
            yield "mail", enmascarar(m.group(0))
    for m in RE_TELEFONO.finditer(linea):
        digitos = re.sub(r"\D", "", m.group(0))
        if len(digitos) >= 10 and not telefono_ficticio(digitos):
            yield "telefono", _enmascarar_pieza(digitos)
    for m in RE_RUTA.finditer(linea):
        if normalizar(m.group(1)) not in USUARIOS_DE_EJEMPLO:
            yield "ruta de usuario", "C:\\Users\\" + _enmascarar_pieza(m.group(1))
    for m in RE_CLAVE.finditer(linea):
        yield "clave", m.group(0)[:4] + "*" * 8


def buscar(lugar: str, texto: str, terminos: List[Tuple[Termino, re.Pattern]]) -> List[Hallazgo]:
    hallazgos: List[Hallazgo] = []
    rangos: Set[str] = set()
    lineas = texto.splitlines()
    lineas_norm = [normalizar(l) for l in lineas] if terminos else []
    for i, linea in enumerate(lineas):
        donde = f"{lugar}:{i + 1}"
        for categoria, enmascarado in _genericos(linea):
            hallazgos.append(Hallazgo(donde, categoria, enmascarado))
        for termino, patron in terminos:
            if patron.search(lineas_norm[i]):
                if termino.categoria == "localidad" and _en_lista_generica(lineas_norm, i, normalizar(termino.valor)):
                    continue
                hallazgos.append(Hallazgo(donde, termino.categoria, enmascarar(termino.valor)))
                if termino.categoria == "cronologia":
                    rangos.add(termino.valor)
    if len(rangos) < MINIMO_RANGOS_CRONOLOGIA:
        hallazgos = [h for h in hallazgos if h.categoria != "cronologia"]
    return hallazgos


# ----------------------------------------------------------------- fuentes

def _git(raiz: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(raiz), *args], capture_output=True, check=True)
    return r.stdout.decode("utf-8", errors="replace")


def _sin_etiquetas(xml: str) -> str:
    texto = re.sub(r"</w:p>|<w:br/>|</(?:p|div|li|tr)>", "\n", xml)
    return re.sub(r"<[^>]+>", "", texto)


def fuentes_arbol(raiz: Path) -> Iterator[Tuple[str, str, bool]]:
    """(lugar, texto, es_parte_de_zip) de cada archivo versionado y de cada parte de los zip."""
    for nombre in sorted(n for n in _git(raiz, "ls-files", "-z").split("\0") if n):
        ruta = raiz / nombre
        if not ruta.is_file():
            continue
        datos = ruta.read_bytes()
        if datos[:4] == b"PK\x03\x04":
            try:
                with zipfile.ZipFile(io.BytesIO(datos)) as z:
                    for parte in z.namelist():
                        yield f"{nombre}!{parte}", z.read(parte).decode("utf-8", errors="replace"), True
            except zipfile.BadZipFile:
                pass
            continue
        if b"\0" in datos:
            continue
        yield nombre, datos.decode("utf-8", errors="replace"), False


def fuentes_commits(raiz: Path, rango: str) -> Iterator[Tuple[str, str, bool]]:
    salida = _git(raiz, "log", "--format=%H%x00%B%x1e", rango)
    for bloque in salida.split("\x1e"):
        sha, _, mensaje = bloque.strip("\n").partition("\0")
        if sha:
            yield f"commit {sha[:10]}", mensaje, False


def auditar(raiz: Path, terminos: List[Termino], rango: Optional[str] = None) -> List[Hallazgo]:
    compilados = [(t, _patron(t)) for t in terminos]
    fuentes: Iterable[Tuple[str, str, bool]] = fuentes_arbol(raiz)
    if rango:
        fuentes = [*fuentes, *fuentes_commits(raiz, rango)]
    hallazgos: List[Hallazgo] = []
    for lugar, texto, es_zip in fuentes:
        propios = buscar(lugar, texto, compilados)
        if es_zip:
            vistos = {(h.categoria, h.enmascarado) for h in propios}
            propios += [Hallazgo(f"{lugar} (texto)", h.categoria, h.enmascarado)
                        for h in buscar(lugar, _sin_etiquetas(texto), compilados)
                        if (h.categoria, h.enmascarado) not in vistos]
        hallazgos += propios
    unicos: List[Hallazgo] = []
    for h in hallazgos:
        if h not in unicos:
            unicos.append(h)
    return unicos


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    modo = parser.add_mutually_exclusive_group(required=True)
    modo.add_argument("--local", action="store_true", help="patrones genéricos y términos del perfil real")
    modo.add_argument("--ci", action="store_true", help="solo patrones genéricos")
    parser.add_argument("--data-dir", help="carpeta de datos (por defecto LINKEDOUT_DATA_DIR o ~/LinkedOut/datos)")
    parser.add_argument("--raiz", default=str(RAIZ), help="repo a auditar")
    parser.add_argument("--commits", help="rango de commits cuyos mensajes también se auditan (p. ej. A..B)")
    args = parser.parse_args(argv)

    terminos: List[Termino] = []
    if args.local:
        data_dir = Path(args.data_dir or variable_entorno("DATA_DIR") or Path.home() / "LinkedOut" / "datos")
        if not (data_dir / "candidato" / "personales.txt").is_file():
            print("Falta el perfil: <DATA_DIR>/candidato/personales.txt (usá --data-dir).", file=sys.stderr)
            return 2
        terminos = terminos_candidato(data_dir)
    hallazgos = auditar(Path(args.raiz), terminos, args.commits)
    for h in hallazgos:
        print(f"{h.lugar}  {h.categoria}  {h.enmascarado}")
    print(f"{len(hallazgos)} hallazgos ({len(terminos)} términos del perfil)")
    return 1 if hallazgos else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
