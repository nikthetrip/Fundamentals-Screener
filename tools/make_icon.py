#!/usr/bin/env python3
"""
make_icon.py — L'icona dell'app: un ritratto inciso, alla maniera degli hedcut.

PERCHE' NON UN DISEGNO VETTORIALE. I primi tentativi erano forme piene con i
tratti del viso disegnati sopra, ed erano sempre la stessa cosa: un adesivo. Il
motivo non e' il disegno, e' la tecnica. Un ritratto inciso non e' fatto di
contorni, e' fatto di TONO — righe sottili che si ingrossano dove l'ombra e'
piu' fitta. E' cosi' che si stampano le banconote, ed e' cosi' che il Wall
Street Journal ritrae da cinquant'anni chi finisce nelle sue pagine.

COME FUNZIONA, IN DUE PASSAGGI:

  1. Si costruisce una MAPPA DI TONI in scala di grigi — dove il viso e'
     chiaro, dove l'ombra scende sotto la mascella, dove la montatura e' nera.
     Questa parte e' geometria: profili di larghezza interpolati riga per riga,
     non poligoni.

  2. Si TRATTEGGIA. Si scorre l'immagine per righe orizzontali distanti fra
     loro un passo fisso, e su ognuna si disegna un segmento il cui SPESSORE e'
     proporzionale a quanto e' scuro il tono sotto. Dove e' chiaro il segmento
     sparisce, dove e' scuro riempie tutto il passo.

La seconda parte e' quella che fa il lavoro: le stesse forme, rese cosi',
smettono di sembrare disegnate al computer perche' l'occhio non legge piu' i
contorni, legge la trama.

Uso:
  python tools/make_icon.py
  python tools/make_icon.py --preview      # anche la mappa di toni, per capire
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

# Si lavora al doppio e si riduce alla fine: il tratteggio ha segmenti spessi
# anche mezzo pixel, e senza sovracampionamento diventano una scacchiera.
SIZE = 3072
OUT = Path("ios/FundamentalsScreener/Assets.xcassets/AppIcon.appiconset/AppIcon-1024.png")
SOURCE = Path("ios/icon-source.jpeg")

# Toni, da 0 (inchiostro pieno) a 255 (carta). Sono la sostanza del disegno:
# cambiare questi numeri cambia il ritratto piu' di qualunque contorno.
PAPER   = 255
# I VALORI SONO ALTI DI PROPOSITO. A puntinatura, un tono di 150 non e' un
# grigio medio: e' gia' una zona fitta di punti che si toccano. La pelle deve
# stare sopra 200 quasi ovunque, o il ritratto diventa un teschio.
FACE    = 240        # il viso prende quasi tutta la luce: e' li' che si guarda
SHADOW  = 214
DEEP    = 196        # sotto la mascella, il punto piu' scuro della pelle
HAIR    = 212        # radi e bianchi: chiari con una trama, non una calotta nera
SUIT    = 178        # scuro ma non pieno: a tratteggio pieno diventa un blocco
SHIRT   = 252
TIE     = 186
FRAME   = 6           # la montatura: quasi inchiostro pieno.
                      # A 60 pixel un ritratto a tratteggio diventa una macchia
                      # grigia — e' il limite della tecnica, lo stesso che ha
                      # un hedcut stampato piccolo. Cio' che si puo' fare e'
                      # scegliere che cosa sopravvive: la montatura e la
                      # sagoma dell'abito. Quindi questi due sono gli unici
                      # elementi portati al nero.


def _profile(ys: list[float], ws: list[float], y: np.ndarray) -> np.ndarray:
    """La semilarghezza della figura a ogni altezza, interpolata."""
    return np.interp(y, ys, ws, left=0.0, right=0.0)


def tone_map(n: int) -> np.ndarray:
    """
    La mappa di toni del ritratto, in scala di grigi.

    LA SAGOMA E' DEFINITA PER PROFILI, non per forme chiuse: a ogni altezza
    corrisponde una semilarghezza, interpolata fra pochi punti di controllo.
    E' il modo in cui si disegna una testa a mano — si segna dove sta il
    contorno a varie altezze e si uniscono — e permette di correggere la
    mascella senza rifare tutto il resto.
    """
    img = np.full((n, n), float(PAPER))
    yy, xx = np.mgrid[0:n, 0:n].astype(np.float64) / n
    cx = 0.5

    # --- SPALLE E ABITO ----------------------------------------------------
    shoulder_hw = _profile(
        [0.735, 0.790, 0.860, 0.930, 1.000],
        [0.095, 0.205, 0.290, 0.345, 0.385], yy)
    shoulders = np.abs(xx - cx) < shoulder_hw
    img[shoulders] = SUIT

    # --- COLLO -------------------------------------------------------------
    neck_hw = _profile([0.580, 0.630, 0.700, 0.740],
                       [0.082, 0.078, 0.076, 0.076], yy)
    neck = (np.abs(xx - cx) < neck_hw) & (yy > 0.575) & (yy < 0.740)
    img[neck] = SHADOW - 8

    # --- CAMICIA E CRAVATTA ------------------------------------------------
    # Il colletto e' una V; la cravatta scende dal suo vertice. Sono le due
    # cose che dicono "abito" senza disegnare un abito.
    v = (yy - 0.705) * 1.05
    collar = (yy > 0.700) & (yy < 0.940) & (np.abs(xx - cx) < np.clip(v, 0, 0.15))
    img[collar] = SHIRT
    tie = (yy > 0.800) & (np.abs(xx - cx) < 0.026 + (yy - 0.800) * 0.070)
    img[tie] = TIE

    # --- TESTA -------------------------------------------------------------
    # Cranio largo, zigomi pieni, mascella che tiene quasi la larghezza: sono
    # le proporzioni che fanno un uomo anziano invece di un ovale.
    head_hw = _profile(
        [0.120, 0.155, 0.205, 0.265, 0.335, 0.400, 0.455, 0.510, 0.556, 0.594, 0.625],
        [0.060, 0.135, 0.180, 0.207, 0.219, 0.219, 0.211, 0.191, 0.158, 0.110, 0.034],
        yy)
    head = (np.abs(xx - cx) < head_hw) & (yy > 0.120) & (yy < 0.625)

    # Orecchie: due sporgenze all'altezza degli occhi.
    ear = (np.abs(np.abs(xx - cx) - 0.226) < 0.022) & (np.abs(yy - 0.385) < 0.050)
    head = head | ear
    img[head] = FACE

    # --- VOLUMI DEL VISO ---------------------------------------------------
    # Una luce sola, da sinistra in alto. E' l'ombra a dire che questa e' una
    # testa e non una sagoma.
    inside = head & (yy > 0.150)
    lateral = np.clip(np.abs(xx - cx) / np.maximum(head_hw, 1e-6), 0, 1)
    lighting = (FACE - SHADOW) * (lateral ** 3.0)
    # la meta' destra e' quella in ombra
    lighting = lighting * np.where(xx > cx, 1.30, 0.55)
    # sotto la mascella il tono scende: e' li' che sta la giogaia
    lighting += (FACE - DEEP) * np.clip((yy - 0.515) / 0.10, 0, 1) ** 1.8 * 0.70
    img[inside] = np.clip(img[inside] - lighting[inside], DEEP - 10, PAPER)

    # --- CAPELLI -----------------------------------------------------------
    # Radi e bianchi, ma piu' scuri della fronte perche' la fronte prende luce.
    # L'attaccatura alta e' meta' del riconoscimento.
    # L'ATTACCATURA E' UNA SFUMATURA, NON UN BORDO. Con una soglia netta i
    # capelli diventavano una calotta incollata sul cranio e la fronte una
    # fascia bianca. I capelli veri sono radi ai bordi: qui l'ombra dei capelli
    # entra e svanisce nell'arco di qualche punto percentuale di altezza.
    hairline = 0.212 + 0.020 * np.cos((xx - cx) / 0.22 * np.pi)
    fade = np.clip((hairline - yy) / 0.080, 0, 1) ** 0.55
    hair_shade = (FACE - HAIR) * fade * (0.55 + 0.45 * lateral)
    img[head] = np.clip(img[head] - hair_shade[head], HAIR - 26, PAPER)

    return img


def features(img: np.ndarray, n: int) -> np.ndarray:
    """
    Sopracciglia, occhi, naso, bocca e montatura, dipinti sulla mappa di toni.

    Vanno sulla MAPPA e non sopra il tratteggio: un tratto disegnato dopo
    starebbe sopra la trama invece che dentro, e si vedrebbe che e' stato
    aggiunto. Passando di qui, la montatura viene tratteggiata come tutto il
    resto — solo molto piu' fitta, perche' e' molto piu' scura.
    """
    layer = Image.fromarray(np.uint8(np.clip(img, 0, 255)))
    d = ImageDraw.Draw(layer)
    S = n

    def box(x0, y0, x1, y1, fill):
        d.rectangle([x0 * S, y0 * S, x1 * S, y1 * S], fill=fill)

    def oval(cx, cy, rx, ry, fill):
        d.ellipse([(cx - rx) * S, (cy - ry) * S, (cx + rx) * S, (cy + ry) * S],
                  fill=fill)

    def line(pts, fill, w):
        d.line([(x * S, y * S) for x, y in pts], fill=fill, width=int(w * S),
               joint="curve")

    cx = 0.5

    # LA GRIGLIA DEL VISO. Calotta a 0.120, mento a 0.625: gli occhi cadono a
    # meta' altezza (0.373), la base del naso a 0.486, la bocca a 0.544. Sono
    # le proporzioni di una testa vera, e sbagliarle e' cio' che fa sembrare
    # disegnato al computer un disegno fatto al computer.

    # --- sopracciglia: folte, estremita' interne piu' basse ---------------
    line([(0.338, 0.330), (0.395, 0.315), (0.452, 0.324)], 96, 0.016)
    line([(0.662, 0.330), (0.605, 0.315), (0.548, 0.324)], 88, 0.016)

    # --- occhi: palpebra e iride, non due pallini -------------------------
    for sx in (-1, 1):
        ex = cx + sx * 0.078
        oval(ex, 0.372, 0.034, 0.020, FACE - 4)
        oval(ex, 0.374, 0.014, 0.014, 92)
        line([(ex - 0.036, 0.364), (ex, 0.353), (ex + 0.036, 0.364)], 120, 0.006)

    # --- naso: un fianco e la base, non un contorno -----------------------
    line([(0.492, 0.398), (0.478, 0.452), (0.464, 0.480)], SHADOW - 16, 0.009)
    line([(0.464, 0.486), (0.500, 0.498), (0.536, 0.486)], SHADOW - 24, 0.008)

    # --- pieghe naso-labiali: l'eta' di un viso sta qui --------------------
    line([(0.446, 0.492), (0.428, 0.524), (0.436, 0.554)], SHADOW - 8, 0.008)
    line([(0.554, 0.492), (0.572, 0.524), (0.564, 0.554)], SHADOW - 18, 0.008)

    # --- bocca: una riga ferma, appena piegata ----------------------------
    line([(0.436, 0.546), (0.500, 0.540), (0.564, 0.546)], 112, 0.011)
    line([(0.450, 0.566), (0.500, 0.572), (0.550, 0.566)], SHADOW - 12, 0.006)

    # --- montatura ---------------------------------------------------------
    # E' il tratto che si riconosce per primo e l'unico che sopravvive alla
    # miniatura di venti pixel: larga quanto il viso e spessa come una cornice.
    #
    # Il vetro viene schiarito PRIMA di posare la cornice. Senza, l'ombra
    # laterale del viso passa dentro la lente e gli occhiali si leggono come
    # una benda: la lente e' vetro, deve restare la parte piu' chiara del viso.
    t = 0.0105
    for x0, x1 in ((0.300, 0.468), (0.532, 0.700)):
        y0, y1 = 0.336, 0.414
        box(x0, y0, x1, y1, FACE + 6)
        box(x0, y0, x1, y0 + t, FRAME)
        box(x0, y1 - t, x1, y1, FRAME)
        box(x0, y0, x0 + t, y1, FRAME)
        box(x1 - t, y0, x1, y1, FRAME)
    box(0.468, 0.344, 0.532, 0.344 + t, FRAME)           # ponte
    line([(0.300, 0.350), (0.264, 0.366)], FRAME, t)      # aste
    line([(0.700, 0.350), (0.736, 0.366)], FRAME, t)

    # gli occhi tornano sopra il vetro schiarito
    for sx in (-1, 1):
        ex = cx + sx * 0.078
        oval(ex, 0.374, 0.014, 0.014, 92)
        line([(ex - 0.036, 0.364), (ex, 0.353), (ex + 0.036, 0.364)], 120, 0.006)

    return np.asarray(layer, dtype=np.float64)


def from_engraving(path: Path, side: int = 1024) -> Image.Image:
    """
    Adatta a icona un'incisione gia' fatta.

    QUESTA E' LA STRADA GIUSTA quando il ritratto esiste gia'. Le due modalita'
    qui sotto — il volto costruito per profili e la puntinatura di una foto —
    servono a produrre un'incisione da zero. Se qualcuno l'ha gia' disegnata,
    ridisegnarla e' solo un modo di peggiorarla: qui non si tocca il disegno, si
    risolve un problema di formato.

    I TRE PROBLEMI DA RISOLVERE, in ordine:

    1. LA SORGENTE E' MINUSCOLA — 160x148 — e l'icona ne vuole 1024. Sono sei
       ingrandimenti e mezzo. Su un disegno a tratto l'interpolazione impasta
       le linee sottili, che sono esattamente cio' di cui e' fatta
       un'incisione.
    2. E' UN JPEG, quindi attorno a ogni linea nera c'e' un alone di
       compressione. Ingrandito, quell'alone diventa una sfocatura grigia.
    3. NON E' QUADRATA e il disegno tocca il bordo superiore. Un'icona iOS
       viene smussata agli angoli: la testa finirebbe tagliata.

    Si allarga il contrasto PRIMA di ingrandire (cosi' l'alone del JPEG viene
    schiacciato sul bianco invece di essere interpolato), si ingrandisce, e si
    riaffilano le linee con una maschera di contrasto. L'ordine conta: al
    contrario si ottiene una sfocatura nitida.
    """
    img = Image.open(path).convert("L")

    # --- 2. l'alone del JPEG, schiacciato sul bianco ------------------------
    arr = np.asarray(img, dtype=np.float64)
    lo, hi = np.percentile(arr, 2), np.percentile(arr, 98)
    arr = np.clip((arr - lo) / max(hi - lo, 1e-6), 0, 1) ** 1.15
    img = Image.fromarray(np.uint8(arr * 255))

    # --- 3. ritaglio sul disegno, poi tela quadrata con margine ------------
    ink = np.argwhere(np.asarray(img) < 205)
    (top, left), (bottom, right) = ink.min(0), ink.max(0)
    img = img.crop((left, top, right + 1, bottom + 1))

    work = side * 3                                   # si lavora in grande
    scale = (work * 0.80) / img.height                # il disegno occupa l'80%
    img = img.resize((max(1, int(img.width * scale)),
                      max(1, int(img.height * scale))), Image.LANCZOS)

    # --- 1. le linee riaffilate dopo l'ingrandimento ------------------------
    img = img.filter(ImageFilter.UnsharpMask(radius=work / 340,
                                             percent=190, threshold=2))
    arr = np.asarray(img, dtype=np.float64) / 255.0
    arr = np.clip((arr - 0.06) / 0.88, 0, 1) ** 1.05  # neri pieni

    # I CHIARI VANNO PORTATI A CARTA PIENA. La compressione lascia sulla fronte
    # e sulle guance una chiazzatura di grigi appena sotto il bianco: invisibile
    # a 160 pixel, ben visibile ingrandita sei volte. Tutto cio' che e' quasi
    # bianco diventa bianco, con una rampa morbida per non mangiare i tratti
    # piu' sottili dell'incisione.
    arr = np.clip((arr - 0.62) / 0.26, 0, 1) ** 0.85 * (1 - arr) + arr
    arr = np.clip(arr, 0, 1)

    canvas = np.ones((work, work))
    x0 = (work - arr.shape[1]) // 2
    y0 = int(work * 0.10)                             # piu' aria sopra la testa
    y1 = min(work, y0 + arr.shape[0])
    canvas[y0:y1, x0:x0 + arr.shape[1]] = arr[:y1 - y0]

    # IL BORDO DELLA SORGENTE VA SCIOLTO. Il disegno originale arriva fino ai
    # propri margini: incollato su una tela piu' grande lascia un rettangolo
    # visibile, e un'icona con dentro un francobollo si vede subito. Le spalle
    # sfumano verso la carta come in un ritratto inciso, che si dissolve nel
    # foglio invece di finire con un taglio netto.
    h, w = arr.shape
    fy = np.ones(work)
    fx = np.ones(work)
    bottom_fade = int(h * 0.16)
    fy[y1 - bottom_fade:y1] = np.linspace(1, 0, bottom_fade)
    side_fade = int(w * 0.10)
    fx[x0:x0 + side_fade] = np.linspace(0, 1, side_fade)
    fx[x0 + w - side_fade:x0 + w] = np.linspace(1, 0, side_fade)
    mask = np.minimum(fy[:, None], fx[None, :])
    canvas = canvas * mask + 1.0 * (1 - mask)

    return Image.fromarray(np.uint8(np.clip(canvas, 0, 1) * 255)) \
                .resize((side, side), Image.LANCZOS)


def tone_from_photo(path: Path, n: int) -> np.ndarray:
    """
    La mappa di toni ricavata da una fotografia, invece che costruita a mano.

    PERCHE' QUESTA STRADA ESISTE. Il ritratto disegnato qui sotto e' fatto di
    profili interpolati e di ombre calcolate: puo' diventare un uomo anziano
    credibile con gli occhiali pesanti, ma non diventera' mai QUELLA persona.
    Una somiglianza non si ottiene regolando parametri — sta nelle asimmetrie,
    nelle proporzioni irripetibili di un viso vero, cioe' esattamente in cio'
    che una costruzione geometrica non ha.

    Il motore a puntinatura, pero', non sa da dove arriva la mappa dei toni.
    Dandogli una fotografia produce un'incisione vera, con la somiglianza
    inclusa, perche' la somiglianza e' gia' nel materiale di partenza.

    COSA FA ALL'IMMAGINE. Ritaglio quadrato al centro, scala di grigi,
    equalizzazione dei livelli e una gamma che spinge la pelle verso il chiaro:
    a puntinatura, una foto lasciata com'e' produce un ritratto scurissimo,
    perche' cio' che su uno schermo e' un grigio medio qui e' gia' una zona
    fitta di punti.
    """
    img = Image.open(path).convert("L")
    side = min(img.size)
    left = (img.width - side) // 2
    top = int((img.height - side) * 0.35)      # i ritratti hanno la testa in alto
    img = img.crop((left, top, left + side, top + side)).resize((n, n), Image.LANCZOS)

    img = ImageOps.autocontrast(img, cutoff=2)
    arr = np.asarray(img, dtype=np.float64) / 255.0
    arr = arr ** 0.62                          # gamma: schiarisce i mezzi toni
    arr = 0.10 + 0.90 * arr                    # nessun nero assoluto sulla pelle

    # Sfondo verso il bianco ai bordi: un hedcut sta su carta, non dentro una
    # fotografia. La maschera e' un ovale morbido — netta si vedrebbe il taglio.
    yy, xx = np.mgrid[0:n, 0:n] / n
    d = np.sqrt(((xx - 0.5) / 0.52) ** 2 + ((yy - 0.52) / 0.60) ** 2)
    vignette = np.clip((d - 0.78) / 0.30, 0, 1)
    arr = arr + (1.0 - arr) * vignette

    return np.clip(arr * 255.0, 0, 255)


def stipple(tone: np.ndarray, n: int, pitch: int = 9) -> Image.Image:
    """
    La puntinatura: punti di raggio variabile, non righe.

    PERCHE' NON LE RIGHE. Un tratteggio a scanline orizzontali e' la retinatura
    di un giornale: da lontano da' il tono giusto, da vicino si vede la griglia
    e il ritratto sembra stampato male. Un hedcut e' fatto d'altro — punti fitti
    dove l'ombra e' profonda, radi dove la luce batte — e la differenza si vede
    subito perche' l'occhio non trova piu' un ritmo regolare da riconoscere.

    LA GRIGLIA E' PERTURBATA. Punti su un reticolo esatto producono moire' e
    file diagonali che non esistono nel disegno: spostando ogni punto di una
    frazione casuale del passo, la trama diventa irregolare come quella fatta a
    mano. Il seme e' fisso, cosi' l'icona e' sempre la stessa.

    NEL BUIO PIU' FITTO I PUNTI SI TOCCANO e diventano una campitura piena. Non
    e' un effetto collaterale: e' il modo in cui un'incisione rende il nero, e
    per questo la montatura degli occhiali risulta solida senza essere
    disegnata a parte.
    """
    canvas = Image.new("L", (n, n), 255)
    draw = ImageDraw.Draw(canvas)
    rng = np.random.default_rng(20231128)      # seme fisso: icona riproducibile

    darkness = np.clip(1.0 - tone / 255.0, 0.0, 1.0)
    # L'esponente decide quanto respira il ritratto. Sotto 1 le zone chiare si
    # sporcano e il viso diventa una trama uniforme; sopra 1 la carta resta
    # carta e i punti si concentrano dove c'e' davvero ombra.
    darkness = darkness ** 1.15

    # Raggio massimo poco oltre mezzo passo: e' cio' che permette ai punti di
    # toccarsi nel nero pieno senza impastare i mezzi toni.
    r_max = pitch * 0.62
    jitter = pitch * 0.42

    coords = np.arange(pitch // 2, n, pitch)
    for gy in coords:
        # Le righe dispari sono sfalsate di mezzo passo: un reticolo quadrato
        # lascia corridoi bianchi verticali che si vedono a occhio nudo.
        offset = (pitch // 2) if (gy // pitch) % 2 else 0
        jx = rng.uniform(-jitter, jitter, coords.size)
        jy = rng.uniform(-jitter, jitter, coords.size)
        for i, gx in enumerate(coords):
            x = gx + offset + jx[i]
            y = gy + jy[i]
            if not (0 <= x < n and 0 <= y < n):
                continue
            d = darkness[int(y), int(x)]
            if d < 0.06:
                continue
            r = r_max * np.sqrt(d)          # l'AREA segue il tono, non il raggio
            if r < 0.35:
                continue
            draw.ellipse([x - r, y - r, x + r, y + r], fill=0)

    return canvas


def build(preview: bool, photo: Path | None = None,
          engraving: Path | None = None) -> None:
    # Quando esiste un'incisione di partenza si adatta quella e basta: e' gia'
    # un ritratto, e nessuna delle due modalita' generative puo' migliorarlo.
    if engraving is not None and engraving.exists():
        art = from_engraving(engraving)
        _save(art)
        return

    n = SIZE
    if photo is not None:
        tone = tone_from_photo(photo, n)
    else:
        tone = features(tone_map(n), n)
    tone = np.asarray(
        Image.fromarray(np.uint8(np.clip(tone, 0, 255)))
        .filter(ImageFilter.GaussianBlur(radius=n / 200)),
        dtype=np.float64)

    if preview:
        Image.fromarray(np.uint8(tone)).resize((1024, 1024), Image.LANCZOS) \
            .save("/tmp/icon_tone.png")

    art = stipple(tone, n).resize((1024, 1024), Image.LANCZOS)
    _save(art)


def _save(art: Image.Image) -> None:
    # La carta non e' bianca e l'inchiostro non e' nero: un'icona in bianco
    # puro su uno schermo scuro taglia, e il grigio caldo la fa somigliare a
    # una stampa invece che a uno screenshot.
    rgb = Image.merge("RGB", [
        Image.eval(art, lambda v: int(24 + v * (245 - 24) / 255)),
        Image.eval(art, lambda v: int(26 + v * (241 - 26) / 255)),
        Image.eval(art, lambda v: int(31 + v * (232 - 31) / 255)),
    ])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rgb.save(OUT)
    print(f"{OUT}  {OUT.stat().st_size / 1024:.0f} KB")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--preview", action="store_true",
                    help="salva anche la mappa di toni in /tmp/icon_tone.png")
    ap.add_argument("--photo", type=Path, default=None,
                    help="incide una fotografia invece del ritratto costruito")
    ap.add_argument("--no-source", action="store_true",
                    help="ignora ios/icon-source.jpeg e ridisegna da zero")
    args = ap.parse_args()
    build(args.preview, args.photo,
          None if (args.no_source or args.photo) else SOURCE)
