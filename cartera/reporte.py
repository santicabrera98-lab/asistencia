#!/usr/bin/env python3
"""
Reporte de cartera: mercado general, posiciones, condiciones de venta,
watchlist, dólar en Argentina y noticias.

Uso:  python3 reporte.py
Lee config.json (misma carpeta) y guarda reporte_AAAA-MM-DD_HHMM.md ahí mismo.
"""

import json
import logging
import re
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yfinance as yf

warnings.filterwarnings("ignore")
logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # silencia 404 de ETFs/futuros sin calendario

CARPETA = Path(__file__).resolve().parent
TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")
SIN_DATO = "sin dato"
DOLARAPI = "https://dolarapi.com/v1/dolares/{}"
CASAS_DOLAR = ["oficial", "cripto", "bolsa", "blue"]


# ----------------------------------------------------------------------------
# Formato
# ----------------------------------------------------------------------------
def es_num(x):
    return isinstance(x, (int, float)) and x == x  # descarta None y NaN


def fmt_num(x, dec=2):
    """1234.5 -> '1.234,50' (formato argentino)."""
    if not es_num(x):
        return SIN_DATO
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_usd(x, dec=2):
    if not es_num(x):
        return SIN_DATO
    signo = "-" if x < 0 else ""
    return f"{signo}${fmt_num(abs(x), dec)}"


def fmt_ars(x):
    if not es_num(x):
        return SIN_DATO
    signo = "-" if x < 0 else ""
    return f"{signo}AR$ {fmt_num(abs(x), 2)}"


def fmt_pct(x, dec=2):
    if not es_num(x):
        return SIN_DATO
    return f"{'+' if x > 0 else ''}{fmt_num(x, dec)}%"


def fmt_hora(ts):
    """Timestamp unix o datetime -> 'DD/MM HH:MM' en hora de Argentina."""
    if ts is None:
        return SIN_DATO
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif isinstance(ts, datetime):
            dt = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        elif isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            return SIN_DATO
        return dt.astimezone(TZ_AR).strftime("%d/%m %H:%M")
    except Exception:
        return SIN_DATO


def pct_cambio(precio, base):
    if es_num(precio) and es_num(base) and base:
        return (precio / base - 1) * 100
    return None


def tabla(encabezados, filas):
    out = ["| " + " | ".join(encabezados) + " |",
           "|" + "|".join("---" for _ in encabezados) + "|"]
    for f in filas:
        out.append("| " + " | ".join(str(c) for c in f) + " |")
    return "\n".join(out)


# ----------------------------------------------------------------------------
# Datos de Yahoo
# ----------------------------------------------------------------------------
def datos_ticker(ticker, con_calendario=True):
    """Trae info de un ticker. Nunca lanza: devuelve dict (vacío si falló)."""
    d = {"ticker": ticker}
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
    except Exception as e:
        d["error"] = str(e)
        return d

    d["nombre"] = info.get("shortName") or info.get("longName")
    d["estado"] = info.get("marketState")
    d["precio"] = info.get("regularMarketPrice")
    d["cierre_anterior"] = info.get("regularMarketPreviousClose") or info.get("previousClose")
    d["cambio_pct"] = info.get("regularMarketChangePercent")
    if d["cambio_pct"] is None:
        d["cambio_pct"] = pct_cambio(d["precio"], d["cierre_anterior"])
    d["hora"] = info.get("regularMarketTime")

    d["pre"] = info.get("preMarketPrice")
    d["pre_pct"] = info.get("preMarketChangePercent")
    if d["pre_pct"] is None:
        d["pre_pct"] = pct_cambio(d["pre"], d["precio"])
    d["pre_hora"] = info.get("preMarketTime")

    d["post"] = info.get("postMarketPrice")
    d["post_pct"] = info.get("postMarketChangePercent")
    if d["post_pct"] is None:
        d["post_pct"] = pct_cambio(d["post"], d["precio"])
    d["post_hora"] = info.get("postMarketTime")

    d["max52"] = info.get("fiftyTwoWeekHigh")
    d["min52"] = info.get("fiftyTwoWeekLow")
    d["ma50"] = info.get("fiftyDayAverage")
    d["ma200"] = info.get("twoHundredDayAverage")

    # Medias móviles: si Yahoo no las trae, calcularlas con el histórico
    if not es_num(d["ma50"]) or not es_num(d["ma200"]):
        try:
            h = t.history(period="1y", auto_adjust=False)["Close"].dropna()
            if len(h) >= 50 and not es_num(d["ma50"]):
                d["ma50"] = float(h.tail(50).mean())
            if len(h) >= 200 and not es_num(d["ma200"]):
                d["ma200"] = float(h.tail(200).mean())
        except Exception:
            pass

    # Próximos resultados
    d["earnings"] = None
    if not con_calendario:
        return d
    try:
        cal = t.calendar or {}
        fechas = cal.get("Earnings Date") or []
        if fechas:
            d["earnings"] = fechas[0]
    except Exception:
        pass
    if d["earnings"] is None and info.get("earningsTimestampStart"):
        try:
            d["earnings"] = datetime.fromtimestamp(
                info["earningsTimestampStart"], tz=timezone.utc).astimezone(TZ_AR).date()
        except Exception:
            pass
    # Yahoo a veces deja la fecha del último reporte: una fecha pasada no es "próxima"
    if d["earnings"] is not None and d["earnings"] < datetime.now(TZ_AR).date():
        d["earnings"] = None
    return d


def ultimo_precio(d):
    """Último precio disponible: after hours > premarket > regular. Devuelve (precio, pct, etiqueta)."""
    if es_num(d.get("post")):
        return d["post"], d.get("post_pct"), "after hours"
    if es_num(d.get("pre")):
        return d["pre"], d.get("pre_pct"), "premarket"
    return d.get("precio"), d.get("cambio_pct"), "regular"


def extendido(d):
    """Texto para la columna premarket / after hours."""
    if es_num(d.get("post")):
        return f"After: {fmt_usd(d['post'])} ({fmt_pct(d['post_pct'])}) {fmt_hora(d['post_hora'])}"
    if es_num(d.get("pre")):
        return f"Pre: {fmt_usd(d['pre'])} ({fmt_pct(d['pre_pct'])}) {fmt_hora(d['pre_hora'])}"
    return "—"


def tendencia(d):
    p, ma50, ma200 = d.get("precio"), d.get("ma50"), d.get("ma200")
    if not (es_num(p) and es_num(ma50) and es_num(ma200)):
        return SIN_DATO, SIN_DATO
    arriba50, arriba200, cruce = p > ma50, p > ma200, ma50 > ma200
    detalle = (f"{'↑' if arriba50 else '↓'}MA50 {'↑' if arriba200 else '↓'}MA200 "
               f"{'50>200' if cruce else '50<200'}")
    if arriba50 and arriba200 and cruce:
        return "ALCISTA", detalle
    if not arriba50 and not arriba200 and not cruce:
        return "BAJISTA", detalle
    return "MIXTA", detalle


def noticias_ticker(ticker, horas=24, maximo=3):
    """Títulos de las últimas `horas`. Devuelve (lista_mostrada, todos_los_titulos_24h)."""
    items = []
    try:
        items = yf.Ticker(ticker).news or []
    except Exception:
        items = []
    if not items:
        try:
            items = yf.Search(ticker, news_count=10).news or []
        except Exception:
            items = []

    limite = datetime.now(timezone.utc) - timedelta(hours=horas)
    out = []
    for it in items:
        c = it.get("content") if isinstance(it.get("content"), dict) else it
        titulo = c.get("title")
        fuente = (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) \
            else c.get("publisher")
        ts = c.get("pubDate") or c.get("providerPublishTime")
        resumen = c.get("summary") or ""
        try:
            if isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            else:
                dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            continue
        if not titulo or dt < limite:
            continue
        out.append({"titulo": titulo, "fuente": fuente or SIN_DATO, "dt": dt, "resumen": resumen})
    out.sort(key=lambda x: x["dt"], reverse=True)
    return out[:maximo], out


# ----------------------------------------------------------------------------
# Dólar
# ----------------------------------------------------------------------------
def datos_dolar():
    res = {}
    for casa in CASAS_DOLAR:
        try:
            r = requests.get(DOLARAPI.format(casa), timeout=15)
            r.raise_for_status()
            j = r.json()
            res[casa] = {"compra": j.get("compra"), "venta": j.get("venta"),
                         "hora": j.get("fechaActualizacion"), "nombre": j.get("nombre", casa)}
        except Exception as e:
            res[casa] = {"compra": None, "venta": None, "hora": None, "nombre": casa, "error": str(e)}
    return res


# ----------------------------------------------------------------------------
# Reporte
# ----------------------------------------------------------------------------
def main():
    try:
        cfg = json.loads((CARPETA / "config.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"No pude leer config.json: {e}")
        sys.exit(1)

    ahora = datetime.now(TZ_AR)
    posiciones = cfg["posiciones"]
    reglas = cfg.get("reglas", {})
    umbral = float(reglas.get("umbral_dia_pct", 5))
    watchlist = cfg.get("watchlist", [])
    mercado = cfg.get("mercado", [])
    efectivo = float(cfg.get("efectivo_usdc", 0))
    tarjeta = cfg.get("tarjeta", {})

    # --- Descarga en paralelo -------------------------------------------------
    solo_precio = {m["ticker"] for m in mercado}
    tickers = [m["ticker"] for m in mercado] + [p["ticker"] for p in posiciones] + watchlist
    tickers = list(dict.fromkeys(tickers))
    with ThreadPoolExecutor(max_workers=8) as ex:
        datos = dict(zip(tickers, ex.map(
            lambda tk: datos_ticker(tk, con_calendario=tk not in solo_precio or tk in watchlist),
            tickers)))
        noticias_fut = {p["ticker"]: ex.submit(noticias_ticker, p["ticker"]) for p in posiciones}
        dolar_fut = ex.submit(datos_dolar)
        noticias = {k: f.result() for k, f in noticias_fut.items()}
        dolar = dolar_fut.result()

    alertas = []
    L = []  # líneas del reporte

    # --- 1. Mercado -----------------------------------------------------------
    filas = []
    for m in mercado:
        d = datos.get(m["ticker"], {})
        filas.append([m["nombre"], m["ticker"], fmt_num(d.get("precio")),
                      fmt_pct(d.get("cambio_pct")), fmt_hora(d.get("hora")), d.get("estado") or SIN_DATO])
    sec_mercado = "## 1. Mercado general\n\n" + tabla(
        ["Activo", "Ticker", "Precio", "% día", "Hora dato (AR)", "Estado"], filas)

    # --- 2. Cartera -----------------------------------------------------------
    filas, total_pos, total_costo = [], 0.0, 0.0
    valores = {}
    for p in posiciones:
        tk = p["ticker"]
        d = datos.get(tk, {})
        cant, costo = float(p["cantidad"]), float(p["costo_usd"])
        precio_ult, pct_ult, etiqueta = ultimo_precio(d)
        valor = cant * precio_ult if es_num(precio_ult) else None
        gl = valor - costo if valor is not None else None
        gl_pct = pct_cambio(valor, costo) if valor is not None else None
        valores[tk] = {"valor": valor, "gl": gl, "gl_pct": gl_pct,
                       "precio_ult": precio_ult, "pct_ult": pct_ult, "etiqueta": etiqueta}
        if valor is not None:
            total_pos += valor
        total_costo += costo
        filas.append([f"**{tk}**", fmt_num(cant, 4), fmt_usd(d.get("cierre_anterior")),
                      f"{fmt_usd(d.get('precio'))} ({fmt_pct(d.get('cambio_pct'))})",
                      extendido(d), fmt_usd(valor), fmt_usd(costo),
                      f"{fmt_usd(gl)} ({fmt_pct(gl_pct)})", fmt_hora(d.get("hora"))])
    total = total_pos + efectivo
    gl_total = total_pos - total_costo
    sec_cartera = "## 2. Mi cartera\n\n" + tabla(
        ["Ticker", "Cant.", "Cierre ant.", "Regular (% día)", "Premarket / After",
         "Valor", "Costo", "G/P", "Hora (AR)"], filas)
    sec_cartera += "\n\n" + tabla(["Concepto", "USD"], [
        ["Acciones (último precio disponible)", fmt_usd(total_pos)],
        ["Costo total acciones", fmt_usd(total_costo)],
        ["G/P acciones", f"{fmt_usd(gl_total)} ({fmt_pct(pct_cambio(total_pos, total_costo))})"],
        ["Efectivo USDc", fmt_usd(efectivo)],
        ["**Total cartera + efectivo**", f"**{fmt_usd(total)}**"],
    ])
    sec_cartera += ("\n\n_Valor y G/P usan el último precio disponible "
                    "(after hours > premarket > regular)._")

    # --- 3. Condiciones de venta ------------------------------------------------
    filas = []
    for p in posiciones:
        tk = p["ticker"]
        d = datos.get(tk, {})
        v = valores[tk]
        precio, pct = v["precio_ult"], v["pct_ult"]
        regla = reglas.get(tk, {})
        marca, estado = "", []

        if tk == "LNG" and regla:
            obj, vender = regla.get("precio_objetivo"), regla.get("vender_usd")
            if es_num(precio) and es_num(obj):
                falta = pct_cambio(obj, precio)
                if precio >= obj:
                    marca = "⚠️"
                    estado.append(f"LLEGÓ a {fmt_usd(obj)} → vender {fmt_usd(vender)}")
                    alertas.append(f"LNG llegó a {fmt_usd(obj)}: vender {fmt_usd(vender)}")
                else:
                    estado.append(f"Objetivo {fmt_usd(obj)}, faltan {fmt_pct(falta)}. {regla.get('nota', '')}")
            else:
                estado.append(SIN_DATO)

        if tk == "VIST" and regla.get("primera_en_salir"):
            if es_num(pct):
                if pct > 0:
                    marca = "⚠️"
                    estado.append(f"En verde ({fmt_pct(pct)}, {v['etiqueta']}): buen momento para vender")
                    alertas.append(f"VIST en verde ({fmt_pct(pct)}): buen momento para vender")
                else:
                    estado.append(f"En rojo ({fmt_pct(pct)}). Primera en salir si hace falta plata")
            else:
                estado.append(SIN_DATO)

        if tk == "GOOG" and regla:
            claves = regla.get("palabras_clave_noticias", [])
            _, todas = noticias.get(tk, ([], []))
            hits = []
            for n in todas:
                texto = f"{n['titulo']} {n.get('resumen', '')}"
                for k in claves:
                    if re.search(re.escape(k), texto, re.IGNORECASE):
                        hits.append((k, n["titulo"]))
            if hits:
                marca = "⚠️"
                estado.append("Noticia con " + ", ".join(sorted({h[0] for h in hits})) + ": "
                              + "; ".join(sorted({h[1] for h in hits})))
                alertas.append("GOOG: noticia menciona " + ", ".join(sorted({h[0] for h in hits})))
            else:
                estado.append(f"Sin noticias con {', '.join(claves)} en 24 h. {regla.get('nota', '')}")

        if es_num(pct) and abs(pct) >= umbral:
            marca = "⚠️"
            estado.append(f"Movimiento del día {fmt_pct(pct)} ({v['etiqueta']})")
            alertas.append(f"{tk} se mueve {fmt_pct(pct)} en el día")

        filas.append([marca or "—", f"**{tk}**", fmt_usd(precio), fmt_pct(pct), " / ".join(estado) or "—"])
    sec_ventas = "## 3. Condiciones de venta\n\n" + tabla(
        ["", "Ticker", "Último", "% (último)", "Estado"], filas)
    sec_ventas += f"\n\n_⚠️ también si cualquier posición se mueve ±{fmt_num(umbral, 0)}% en el día._"

    # --- 4. Watchlist -----------------------------------------------------------
    filas = []
    for tk in watchlist:
        d = datos.get(tk, {})
        precio = d.get("precio")
        dist_max = pct_cambio(precio, d.get("max52"))
        dist_min = pct_cambio(precio, d.get("min52"))
        tend, detalle = tendencia(d)
        earn = d.get("earnings")
        earn_txt = earn.strftime("%d/%m/%Y") if hasattr(earn, "strftime") else (str(earn) if earn else SIN_DATO)
        filas.append([f"**{tk}**", (d.get("nombre") or SIN_DATO)[:28], fmt_usd(precio),
                      fmt_pct(d.get("cambio_pct")), extendido(d),
                      fmt_pct(dist_max), fmt_pct(dist_min),
                      f"{tend} ({detalle})" if detalle != SIN_DATO else SIN_DATO, earn_txt])
    desde = cfg.get("watchlist_desde", "")
    sec_watch = f"## 4. Watchlist (candidatas para comprar después del {desde})\n\n" + tabla(
        ["Ticker", "Nombre", "Precio", "% día", "Pre / After", "vs máx 52s", "vs mín 52s",
         "Tendencia", "Próx. resultados"], filas)

    # --- 5. Dólar ---------------------------------------------------------------
    filas = []
    for casa in CASAS_DOLAR:
        x = dolar.get(casa, {})
        filas.append([x.get("nombre", casa).capitalize(), fmt_ars(x.get("compra")),
                      fmt_ars(x.get("venta")), fmt_hora(x.get("hora"))])
    sec_dolar = "## 5. Dólar en Argentina (dolarapi.com)\n\n" + tabla(
        ["Tipo", "Compra", "Venta", "Actualizado (AR)"], filas)

    cripto_compra = dolar.get("cripto", {}).get("compra")
    oficial_venta = dolar.get("oficial", {}).get("venta")
    monto_tarjeta = float(tarjeta.get("monto_usd", 0))
    fecha_tarjeta = tarjeta.get("fecha", "")
    tarjeta_linea = SIN_DATO
    if es_num(cripto_compra) and es_num(oficial_venta) and oficial_venta:
        spread = cripto_compra - oficial_venta
        spread_pct = spread / oficial_venta * 100
        ars_tarjeta = monto_tarjeta * oficial_venta
        usdc_necesarios = ars_tarjeta / cripto_compra
        diferencia = efectivo - usdc_necesarios
        sec_dolar += "\n\n### Rulo: vendo USDc al cripto (compra) y compro oficial (venta)\n\n" + tabla(
            ["Concepto", "Valor"], [
                ["Vendo USDc a (cripto compra)", fmt_ars(cripto_compra)],
                ["Compro oficial a (oficial venta)", fmt_ars(oficial_venta)],
                ["Spread por USD", f"{fmt_ars(spread)} ({fmt_pct(spread_pct)})"],
                [f"Tarjeta {fecha_tarjeta}: {fmt_usd(monto_tarjeta)} al oficial", fmt_ars(ars_tarjeta)],
                ["USDc que tengo que vender para cubrirla", fmt_usd(usdc_necesarios)],
                ["Efectivo USDc (config.json)", fmt_usd(efectivo)],
                ["**Sobra / falta**", f"**{'Sobran' if diferencia >= 0 else 'FALTAN'} {fmt_usd(abs(diferencia))}**"],
            ])
        tarjeta_linea = (f"necesito vender {fmt_usd(usdc_necesarios)} USDc; "
                         f"{'sobran' if diferencia >= 0 else 'FALTAN'} {fmt_usd(abs(diferencia))} "
                         f"(efectivo {fmt_usd(efectivo)})")
        if diferencia < 0:
            alertas.append(f"Tarjeta: faltan {fmt_usd(abs(diferencia))} de USDc")
    else:
        sec_dolar += "\n\nRulo / tarjeta: sin dato (falló dolarapi)."

    # --- 6. Noticias ------------------------------------------------------------
    sec_news = "## 6. Noticias (últimas 24 h, máx. 3 por acción)\n"
    for p in posiciones:
        tk = p["ticker"]
        lista, _ = noticias.get(tk, ([], []))
        sec_news += f"\n**{tk}**\n\n"
        if not lista:
            sec_news += "- sin dato (nada en 24 h)\n"
        for n in lista:
            sec_news += f"- {n['titulo']} — _{n['fuente']}_, {fmt_hora(n['dt'])}\n"

    # --- Resumen ----------------------------------------------------------------
    def pct_m(tk):
        return fmt_pct(datos.get(tk, {}).get("cambio_pct"))

    vix = datos.get("^VIX", {}).get("precio")
    mercado_linea = (f"S&P fut {pct_m('ES=F')}, Nasdaq fut {pct_m('NQ=F')}, "
                     f"VIX {fmt_num(vix)}, BTC {pct_m('BTC-USD')}, WTI {pct_m('CL=F')}")
    resumen = "\n".join([
        f"1. **Total cartera + efectivo: {fmt_usd(total)}** "
        f"(acciones {fmt_usd(total_pos)}, G/P {fmt_usd(gl_total)} / {fmt_pct(pct_cambio(total_pos, total_costo))}).",
        "2. **Gatillos ⚠️:** " + ("; ".join(alertas) if alertas else "ninguno."),
        f"3. **Mercado:** {mercado_linea}.",
        f"4. **Tarjeta {fecha_tarjeta} ({fmt_usd(monto_tarjeta)}):** {tarjeta_linea}.",
        f"5. **Posiciones:** " + ", ".join(
            f"{tk} {fmt_pct(valores[tk]['pct_ult'])}" for tk in valores) + ".",
    ])

    encabezado = (f"# Reporte de cartera — {ahora.strftime('%d/%m/%Y %H:%M')} (hora Argentina)\n\n"
                  "_Precios de Yahoo Finance (pueden tener demora de 15 min o más); "
                  "todas las horas en hora de Argentina (UTC-3). "
                  "\"sin dato\" = la fuente no lo devolvió._\n")

    L = [encabezado, "## Resumen\n\n" + resumen, sec_mercado, sec_cartera, sec_ventas,
         sec_watch, sec_dolar, sec_news]
    texto = "\n\n".join(L).rstrip() + "\n"

    print(texto)
    archivo = CARPETA / f"reporte_{ahora.strftime('%Y-%m-%d_%H%M')}.md"
    archivo.write_text(texto, encoding="utf-8")
    print(f"\nGuardado en {archivo}")


if __name__ == "__main__":
    main()
