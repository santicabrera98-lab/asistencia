# Cartera

Reporte de inversiones en español: mercado, posiciones, condiciones de venta, watchlist, dólar en Argentina y noticias.
Imprime el reporte y lo guarda como `reporte_AAAA-MM-DD_HHMM.md` en esta carpeta.

## Instalar (Mac)

```bash
cd cartera
./instalar.sh
```

Eso instala `yfinance`, `requests` y `pandas`, programa el reporte con launchd (lunes a viernes 9:30 y 17:15, hora del Mac) y lo corre una vez.

Para correrlo a mano en cualquier momento:

```bash
python3 reporte.py
```

## Editar posiciones, efectivo y reglas

Todo está en `config.json`:

- `efectivo_usdc`: efectivo en USDc.
- `posiciones`: ticker, cantidad y costo total en USD.
- `reglas`: objetivo de LNG, palabras clave de GOOG, umbral de ±% diario.
- `watchlist`: tickers a seguir.
- `tarjeta`: monto en USD y fecha del pago.

## Desactivar el reporte automático

```bash
launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.cartera.reporte.plist
rm ~/Library/LaunchAgents/com.cartera.reporte.plist
```

Para volver a activarlo, correr `./instalar.sh` de nuevo.

## Notas

- Los precios de Yahoo Finance pueden tener demora de 15 minutos o más. Todas las horas se muestran en hora de Argentina.
- Si una fuente no devuelve un dato, se escribe "sin dato"; nunca se completa con valores viejos.
- `launchd` usa la hora local del Mac: tiene que estar configurado en hora de Argentina para que 9:30 y 17:15 coincidan.
- Si el Mac está apagado o dormido a la hora programada, esa corrida se salta (launchd no la recupera).
