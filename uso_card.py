#!/usr/bin/env python3
"""Uso de Claude en una tarjeta (mismo estilo que el reloj): cuadrado blanco redondeado, número en Bebas Neue y marquitas en el borde.

Número grande: % del límite de la sesión de 5 h, tal como lo informa Anthropic para la cuenta (se pide con el token de
Claude Code en ~/.claude/.credentials.json). Un clic sobre el número pasa a la siguiente lectura: % de sesión → % semanal → tokens de hoy → vuelta al inicio.
Las marquitas se pintan a medida que pasa la ventana (5 h o 7 días). Abajo: la otra lectura y los tokens de hoy, leídos de
los registros locales (~/.claude/projects/**/*.jsonl; entrada + salida + creación de caché).
Si la API no responde, los % se estiman (se muestran con "~") con los tokens locales y la calibración de la última
lectura buena (~/.config/uso-claude/cache.json); solo si nunca hubo una lectura buena aparece el total de tokens.
El ícono de la ventana (dock, barra de tareas, minimizada) es la propia tarjeta y se actualiza con ella.
Clic derecho > Color: 5 combinaciones. La lectura, el color y el intervalo de actualización (1/2/5/10 min, por defecto 2) quedan en ~/.config/uso-claude/config.json.

Arrastrar con el botón izquierdo para mover. Clic derecho: menú (versión / Actualizar / Cerrar). Esc también cierra.
Uso: python3 uso_card.py [--captura RUTA] [--sesion|--semana|--tokens] [--tema N]   (N de 0 a 4; valen solo para esa vez)
"""
import json
import math
import os
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QActionGroup, QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication, QMenu, QWidget

FUENTE = Path(__file__).resolve().parent / "bebas-neue.woff2"
VERSION = "1.5"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "uso-claude" / "config.json"
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
PROYECTOS = CLAUDE / "projects"
API_USO = "https://api.anthropic.com/api/oauth/usage"
INTERVALOS = (1, 2, 5, 10)  # minutos entre actualizaciones (se elige en el menú)
CADA_DEFECTO = 2
API_ESPERA_429 = 600  # si responde "rate limited", esperar más
BLOQUE = timedelta(hours=5)
LADO = 240
MARGEN = 20  # aire para la sombra

ESTILO_MENU = """
QMenu { background: rgba(30,30,34,240); color: #f2f2f2; border: 1px solid rgba(255,255,255,35);
        border-radius: 14px; padding: 6px; }
QMenu::item { padding: 7px 24px 7px 28px; border-radius: 8px; }
QMenu::item:selected { background: rgba(255,255,255,40); }
QMenu::item:disabled { color: rgba(255,255,255,120); }
QMenu::separator { height: 1px; background: rgba(255,255,255,35); margin: 5px 8px; }
"""


class Menu(QMenu):
    """QMenu con esquinas redondeadas: sin marco del sistema y con fondo transparente, así se ve el radio."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet(ESTILO_MENU)


_cache = {}  # ruta -> (mtime, size, [(datetime, tokens, id)])


def _leer_archivo(ruta):
    out = []
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                if '"usage"' not in linea:
                    continue
                try:
                    d = json.loads(linea)
                    u = d["message"]["usage"]
                    ts = datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00"))
                except (ValueError, KeyError, TypeError):
                    continue
                tok = (u.get("input_tokens") or 0) + (u.get("output_tokens") or 0) + (u.get("cache_creation_input_tokens") or 0)
                out.append((ts, tok, (d["message"].get("id"), d.get("requestId"))))
    except OSError:
        pass
    return out


def leer_eventos(desde):
    """Eventos (hora, tokens) desde `desde`, sin duplicar mensajes repetidos entre sesiones."""
    vistos, eventos = set(), []
    limite = desde.timestamp()
    for ruta in PROYECTOS.glob("**/*.jsonl"):
        try:
            st = ruta.stat()
        except OSError:
            continue
        if st.st_mtime < limite:
            continue
        clave = (st.st_mtime, st.st_size)
        if _cache.get(ruta, (None, None))[:2] != clave:
            _cache[ruta] = (*clave, _leer_archivo(ruta))
        for ts, tok, ident in _cache[ruta][2]:
            if ts < desde or (ident[0] and ident in vistos):
                continue
            vistos.add(ident)
            eventos.append((ts, tok))
    eventos.sort()
    return eventos


_api = {"t": 0.0, "datos": None}


def limites_api(cada):
    """% de sesión (5 h) y semanal según la cuenta; None si no hay token o red. Claude Code mantiene el token vigente."""
    if time.time() < _api["t"]:
        return _api["datos"]
    _api["t"] = time.time() + cada * 60 - 1
    try:
        token = json.loads((CLAUDE / ".credentials.json").read_text(encoding="utf-8"))["claudeAiOauth"]["accessToken"]
        req = urllib.request.Request(API_USO, headers={"Authorization": f"Bearer {token}",
                                                       "anthropic-beta": "oauth-2025-04-20"})
        with urllib.request.urlopen(req, timeout=10) as r:
            d = json.loads(r.read())
        ses, sem = d["five_hour"], d["seven_day"]
        _api["datos"] = {"pct_sesion": ses["utilization"], "pct_semana": sem["utilization"],
                         "reinicia": datetime.fromisoformat(ses["resets_at"]),
                         "reinicia_semana": datetime.fromisoformat(sem["resets_at"])}
    except (OSError, ValueError, KeyError, TypeError) as ex:
        if getattr(ex, "code", None) == 429:
            _api["t"] = time.time() + API_ESPERA_429  # la API pidió calma: no insistir
        # se conserva el último dato bueno mientras su ventana de 5 h no haya terminado
        if _api["datos"] and _api["datos"]["reinicia"] < datetime.now(timezone.utc):
            _api["datos"] = None
    return _api["datos"]


CACHE = CONFIG.with_name("cache.json")  # última calibración buena: cuándo reinician las ventanas y cuántos tokens vale 1 %


def _cargar_cache():
    try:
        c = json.loads(CACHE.read_text(encoding="utf-8"))
        return {"k_ses": c.get("k_ses"), "k_sem": c.get("k_sem"),
                "reinicia": datetime.fromisoformat(c["reinicia"]),
                "reinicia_semana": datetime.fromisoformat(c["reinicia_semana"])}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _guardar_cache(c):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps({**c, "reinicia": c["reinicia"].isoformat(),
                                   "reinicia_semana": c["reinicia_semana"].isoformat()}), encoding="utf-8")
        tmp.replace(CACHE)
    except OSError as ex:
        print("no se pudo guardar la calibración:", ex, file=sys.stderr)


def calcular(cada=CADA_DEFECTO):
    """Lecturas para la tarjeta. Con la API los % son exactos; sin ella se estiman (est=True) con los tokens de los
    registros locales y los tokens-por-1 % de la última vez que la API respondió."""
    ahora = datetime.now(timezone.utc)
    ev = leer_eventos(ahora - timedelta(days=7))
    api = limites_api(cada)
    cache = _cargar_cache()
    ref = api or cache
    fin_ses, fin_sem = ref.get("reinicia"), ref.get("reinicia_semana")
    if fin_sem:
        while fin_sem <= ahora:
            fin_sem += timedelta(days=7)
    if not (fin_ses and fin_ses > ahora):
        # sin ventana conocida: bloque de 5 h como ccusage (arranca en la hora redonda del primer mensaje)
        fin_ses = None
        for ts, _ in ev:
            if fin_ses is None or ts >= fin_ses:
                fin_ses = ts.replace(minute=0, second=0, microsecond=0) + BLOQUE
        if fin_ses and fin_ses <= ahora:
            fin_ses = None
    tok_ses = sum(t for ts, t in ev if fin_ses and ts >= fin_ses - BLOQUE)
    tok_sem = sum(t for ts, t in ev if ts >= (fin_sem - timedelta(days=7) if fin_sem else ahora - timedelta(days=7)))
    hoy0 = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    d = {
        "bloque": tok_ses,
        "fraccion": 0.0 if fin_ses is None else 1 - (fin_ses - ahora) / BLOQUE,
        "activo": fin_ses is not None,
        "hoy": sum(t for ts, t in ev if ts >= hoy0),
        "semana": tok_sem,
        "pct_sesion": None,
        "pct_semana": None,
        "fraccion_semana": 0.0 if fin_sem is None else 1 - (fin_sem - ahora) / timedelta(days=7),
        "est": False,
        "fraccion_dia": (datetime.now().astimezone() - hoy0) / timedelta(days=1),
    }
    if api:
        d.update(pct_sesion=api["pct_sesion"], pct_semana=api["pct_semana"])
        k_ses = tok_ses / api["pct_sesion"] if api["pct_sesion"] >= 5 else cache.get("k_ses")
        k_sem = tok_sem / api["pct_semana"] if api["pct_semana"] >= 5 else cache.get("k_sem")
        _guardar_cache({"k_ses": k_ses, "k_sem": k_sem, "reinicia": api["reinicia"],
                        "reinicia_semana": api["reinicia_semana"]})
    else:
        d["est"] = True
        if cache.get("k_ses"):
            d["pct_sesion"] = min(100.0, tok_ses / cache["k_ses"])
        if cache.get("k_sem"):
            d["pct_semana"] = min(100.0, tok_sem / cache["k_sem"])
    return d


def corto(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


MODOS = ("sesion", "semana", "tokens")

# (nombre, tarjeta, texto, secundario, marquita apagada, acento)
TEMAS = [
    ("Clásico", (247, 247, 247), (30, 30, 30), (110, 110, 110), (200, 200, 200), (255, 78, 1)),
    ("Noche", (28, 28, 30), (245, 245, 247), (160, 160, 166), (72, 72, 76), (255, 159, 10)),
    ("Océano", (232, 242, 255), (16, 42, 84), (90, 120, 160), (190, 208, 232), (10, 110, 255)),
    ("Menta", (232, 247, 240), (14, 60, 44), (80, 130, 110), (186, 214, 202), (16, 185, 129)),
    ("Violeta", (36, 22, 58), (244, 236, 255), (176, 150, 214), (82, 62, 114), (217, 70, 239)),
]


def cargar_config():
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_config(cfg):
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG.with_suffix(".tmp")
        tmp.write_text(json.dumps(cfg), encoding="utf-8")
        tmp.replace(CONFIG)  # escritura atómica
    except OSError as ex:
        print("no se pudo guardar la configuración:", ex, file=sys.stderr)


class Datos(QObject):
    listo = Signal(dict)


class UsoCard(QWidget):
    def __init__(self, modo=None, tema=None):
        super().__init__()
        cfg = cargar_config()
        m = cfg.get("modo", "semana" if cfg.get("semana") else "sesion") if modo is None else modo  # "semana" = viejo
        self.modo = m if m in MODOS else MODOS[0]
        t = cfg.get("tema", 0) if tema is None else tema
        self.tema = t if isinstance(t, int) and 0 <= t < len(TEMAS) else 0
        c = cfg.get("cada", CADA_DEFECTO)
        self.cada = c if c in INTERVALOS else CADA_DEFECTO
        self.pulso = None  # posición del clic sobre el número, a la espera de saber si es clic o arrastre
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(LADO + 2 * MARGEN, LADO + 2 * MARGEN)
        fid = QFontDatabase.addApplicationFont(str(FUENTE))
        fams = QFontDatabase.applicationFontFamilies(fid) if fid >= 0 else []
        self.familia = fams[0] if fams else "Impact"
        self.arrastre = None
        self.datos = {"bloque": 0, "fraccion": 0.0, "activo": False, "hoy": 0, "semana": 0,
                      "pct_sesion": None, "pct_semana": None, "fraccion_semana": 0.0, "est": False, "fraccion_dia": 0.0}
        self.cargando = False
        self.canal = Datos()
        self.canal.listo.connect(self._recibir)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.actualizar)
        self.timer.start(self.cada * 60_000)
        self.actualizar()

    def actualizar(self):
        if self.cargando:
            return
        self.cargando = True
        threading.Thread(target=lambda: self.canal.listo.emit(calcular(self.cada)), daemon=True).start()

    def _recibir(self, d):
        self.datos, self.cargando = d, False
        self.update()
        self._icono()

    def _icono(self):
        """El ícono de la ventana (barra de tareas / dock / minimizada) es la propia tarjeta, así el número se ve vivo."""
        self.setWindowIcon(QIcon(self.grab(QRect(MARGEN, MARGEN, LADO, LADO)).scaled(
            128, 128, Qt.KeepAspectRatio, Qt.SmoothTransformation)))

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        card = QRectF(MARGEN, MARGEN, LADO, LADO)

        # sombra: capas translúcidas cada vez más chicas
        for i in range(MARGEN, 0, -1):
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, int(2 + 26 * (1 - i / MARGEN) ** 2)))
            p.drawRoundedRect(card.adjusted(-i, -i + 6, i, i + 6), 44 + i, 44 + i)

        _, c_card, c_texto, c_sec, c_apagada, c_acento = TEMAS[self.tema]
        d = self.datos

        # tarjeta
        p.setBrush(QColor(*c_card))
        p.drawRoundedRect(card, 44, 44)

        # marquitas: 60 alrededor del rectángulo redondeado; las del tiempo ya transcurrido de la ventana van en el color de acento
        borde = QPainterPath()
        borde.addRoundedRect(card.adjusted(13, 13, -13, -13), 32, 32)
        cima = QPointF(card.center().x(), card.top() + 13)
        inicio = min((t / 2000 for t in range(2000)),
                     key=lambda t: (borde.pointAtPercent(t) - cima).manhattanLength())
        a, b = borde.pointAtPercent(inicio), borde.pointAtPercent(inicio + 0.01)
        sentido = 1 if b.x() > a.x() else -1  # que avance en sentido horario
        if self.modo == "tokens":
            hechas = int(d["fraccion_dia"] * 60)  # los tokens son de hoy: las marquitas siguen el día
        elif self.modo == "semana":
            hechas = int(d["fraccion_semana"] * 60) if d["pct_semana"] is not None else -1
        else:
            hechas = int(d["fraccion"] * 60) if d["activo"] else -1
        n = 60
        for k in range(n):
            pct = (inicio + sentido * k / n) % 1.0
            pt = borde.pointAtPercent(pct)
            ang = borde.angleAtPercent(pct)
            largo = 7 if k % 5 == 0 else 3.5
            nx, ny = -math.sin(math.radians(ang)), -math.cos(math.radians(ang))
            pasado = k <= hechas
            p.setPen(QPen(QColor(*c_acento) if pasado else QColor(*c_apagada), 1.8 if pasado else 1.6,
                          Qt.SolidLine, Qt.RoundCap))
            p.drawLine(pt, QPointF(pt.x() - nx * largo, pt.y() - ny * largo))

        # rótulo, número grande y pie
        ps, pw = d["pct_sesion"], d["pct_semana"]
        aprox = "~" if d["est"] else ""  # estimado: la API no respondió
        f = QFont(self.familia)
        f.setPixelSize(22)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 2)
        p.setFont(f)
        p.setPen(QColor(*c_sec))
        p.drawText(QRectF(card.left(), card.top() + 36, LADO, 30), Qt.AlignCenter,
                   {"sesion": "SESIÓN 5H", "semana": "SEMANA", "tokens": "TOKENS HOY"}[self.modo])

        f.setLetterSpacing(QFont.AbsoluteSpacing, 0)
        f.setPixelSize(104)
        p.setFont(f)
        p.setPen(QColor(*c_texto))
        if self.modo == "tokens":
            grande = corto(d["hoy"])
        elif self.modo == "semana":
            grande = f"{aprox}{pw:.0f}%" if pw is not None else corto(d["semana"])
        else:
            grande = f"{aprox}{ps:.0f}%" if ps is not None else corto(d["bloque"])
        p.drawText(card.adjusted(0, 6, 0, 0), Qt.AlignCenter, grande)

        f.setPixelSize(21 if self.modo == "tokens" else 24)  # el pie de tokens es más ancho
        p.setFont(f)
        p.setPen(QColor(*c_sec))
        if self.modo == "tokens":
            pie = f"SESIÓN {corto(d['bloque'])}  ·  SEMANA {corto(d['semana'])}"
        else:
            otra = (ps, "SESIÓN") if self.modo == "semana" else (pw, "SEMANA")
            pie = f"HOY {corto(d['hoy'])}" if otra[0] is None else f"{otra[1]} {aprox}{otra[0]:.0f}%  ·  HOY {corto(d['hoy'])}"
        p.drawText(QRectF(card.left(), card.bottom() - 66, LADO, 30), Qt.AlignCenter, pie)

    def _zona_numero(self):
        return QRectF(MARGEN + 10, MARGEN + 62, LADO - 20, 112)

    def set_modo(self, modo):
        self.modo = modo
        cfg = cargar_config()
        cfg["modo"] = modo
        guardar_config(cfg)
        self.update()
        self._icono()

    def set_cada(self, minutos):
        self.cada = minutos
        self.timer.start(minutos * 60_000)
        _api["t"] = 0.0  # que la próxima consulta no espere al intervalo anterior
        cfg = cargar_config()
        cfg["cada"] = minutos
        guardar_config(cfg)
        self.actualizar()

    def set_tema(self, i):
        self.tema = i
        cfg = cargar_config()
        cfg["tema"] = i
        guardar_config(cfg)
        self.update()
        self._icono()

    def contextMenuEvent(self, e):
        menu = Menu(self)
        menu.addAction(f"v{VERSION}").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Actualizar", self.actualizar)
        sub = Menu("Color", menu)
        menu.addMenu(sub)
        grupo = QActionGroup(sub)
        for k, tema in enumerate(TEMAS):
            a = sub.addAction(tema[0])
            a.setCheckable(True)
            a.setChecked(k == self.tema)
            a.triggered.connect(lambda _=False, v=k: self.set_tema(v))
            grupo.addAction(a)
        sub = Menu("Actualizar cada", menu)
        menu.addMenu(sub)
        grupo = QActionGroup(sub)
        for m in INTERVALOS:
            a = sub.addAction(f"{m} min")
            a.setCheckable(True)
            a.setChecked(m == self.cada)
            a.triggered.connect(lambda _=False, v=m: self.set_cada(v))
            grupo.addAction(a)
        menu.addSeparator()
        menu.addAction("Cerrar", self.close)
        menu.exec(e.globalPos())

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        if self._zona_numero().contains(e.position()):
            self.pulso = e.globalPosition().toPoint()  # clic (cambia de lectura) o arrastre: se decide al mover/soltar
            return
        self._arrastrar(e)

    def _arrastrar(self, e):
        # el compositor mueve la ventana (en Wayland move() no funciona); si no hay soporte, arrastre manual
        if not (self.windowHandle() and self.windowHandle().startSystemMove()):
            self.arrastre = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self.pulso is not None and (e.globalPosition().toPoint() - self.pulso).manhattanLength() > 6:
            self.pulso = None
            self._arrastrar(e)
        elif self.arrastre is not None:
            self.move(e.globalPosition().toPoint() - self.arrastre)

    def mouseReleaseEvent(self, e):
        if self.pulso is not None and e.button() == Qt.LeftButton:
            self.set_modo(MODOS[(MODOS.index(self.modo) + 1) % len(MODOS)])
        self.pulso = None
        self.arrastre = None

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.close()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setApplicationName("uso-claude")
    app.setDesktopFileName("uso-claude")
    tema = int(sys.argv[sys.argv.index("--tema") + 1]) if "--tema" in sys.argv else None
    modo = next((m for m in MODOS if f"--{m}" in sys.argv), None)
    w = UsoCard(modo=modo, tema=tema)
    w.show()
    if "--captura" in sys.argv:
        ruta = sys.argv[sys.argv.index("--captura") + 1]  # --captura RUTA: guarda una imagen y sale
        QTimer.singleShot(5000, lambda: (w.grab().save(ruta), app.quit()))
    sys.exit(app.exec())
