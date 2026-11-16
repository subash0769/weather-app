"""Single-file app: live clock + beautiful weather card for any city.

Run:  python src/app.py     -> http://localhost:8000
Try:  http://localhost:8000/?city=Pokhara
JSON: http://localhost:8000/api/weather?city=Pokhara
Health check for K8s probes: /healthz
Weather data: Open-Meteo (free, no API key). No external fonts/CDNs needed.
"""
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

DEFAULT_CITY = os.getenv("DEFAULT_CITY", "Kathmandu")
TIMEOUT = 5
CACHE_TTL = 600  # seconds; be nice to the free API
_cache = {}

# WMO code -> (label, day icon, night icon, theme)
WMO = {
    0: ("Clear sky", "☀️", "🌙", "clear"),
    1: ("Mainly clear", "🌤️", "🌙", "clear"),
    2: ("Partly cloudy", "⛅", "☁️", "cloudy"),
    3: ("Overcast", "☁️", "☁️", "cloudy"),
    45: ("Fog", "🌫️", "🌫️", "fog"),
    48: ("Rime fog", "🌫️", "🌫️", "fog"),
    51: ("Light drizzle", "🌦️", "🌧️", "rain"),
    53: ("Drizzle", "🌦️", "🌧️", "rain"),
    55: ("Heavy drizzle", "🌧️", "🌧️", "rain"),
    56: ("Freezing drizzle", "🌧️", "🌧️", "rain"),
    57: ("Freezing drizzle", "🌧️", "🌧️", "rain"),
    61: ("Light rain", "🌦️", "🌧️", "rain"),
    63: ("Rain", "🌧️", "🌧️", "rain"),
    65: ("Heavy rain", "🌧️", "🌧️", "rain"),
    66: ("Freezing rain", "🌧️", "🌧️", "rain"),
    67: ("Freezing rain", "🌧️", "🌧️", "rain"),
    71: ("Light snow", "🌨️", "🌨️", "snow"),
    73: ("Snow", "❄️", "❄️", "snow"),
    75: ("Heavy snow", "❄️", "❄️", "snow"),
    77: ("Snow grains", "❄️", "❄️", "snow"),
    80: ("Rain showers", "🌦️", "🌧️", "rain"),
    81: ("Rain showers", "🌧️", "🌧️", "rain"),
    82: ("Violent showers", "⛈️", "⛈️", "storm"),
    85: ("Snow showers", "🌨️", "🌨️", "snow"),
    86: ("Heavy snow showers", "🌨️", "🌨️", "snow"),
    95: ("Thunderstorm", "⛈️", "⛈️", "storm"),
    96: ("Thunderstorm, hail", "⛈️", "⛈️", "storm"),
    99: ("Thunderstorm, hail", "⛈️", "⛈️", "storm"),
}

PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="#0f172a">
  <title>{{ city }} &middot; Weather &amp; Time</title>
  <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E%3Ctext y='.9em' font-size='90'%3E%F0%9F%8C%A4%EF%B8%8F%3C/text%3E%3C/svg%3E">
  <style>
    :root {
      --glass: rgba(255,255,255,.12);
      --glass-strong: rgba(255,255,255,.18);
      --line: rgba(255,255,255,.22);
      --text: #f8fafc;
      --muted: rgba(248,250,252,.72);
    }
    body[data-theme="clear"]  { --a:#1d4ed8; --b:#38bdf8; --c:#fbbf24; }
    body[data-theme="clear"][data-day="0"] { --a:#0b1026; --b:#312e81; --c:#7c3aed; }
    body[data-theme="cloudy"] { --a:#334155; --b:#64748b; --c:#94a3b8; }
    body[data-theme="cloudy"][data-day="0"] { --a:#0f172a; --b:#1e293b; --c:#475569; }
    body[data-theme="rain"]   { --a:#0f2a43; --b:#1e5a8a; --c:#38bdf8; }
    body[data-theme="storm"]  { --a:#0b1020; --b:#2d2a55; --c:#a78bfa; }
    body[data-theme="snow"]   { --a:#3b5b80; --b:#8fb3d9; --c:#e0f2fe; }
    body[data-theme="fog"]    { --a:#3f4a57; --b:#7b8794; --c:#cbd5e1; }

    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0; color: var(--text);
      font-family: ui-rounded, "SF Pro Rounded", "Segoe UI Variable", "Segoe UI", system-ui, -apple-system, Roboto, "Helvetica Neue", sans-serif;
      background: linear-gradient(160deg, var(--a), var(--b));
      background-attachment: fixed;
      min-height: 100vh; display: flex; align-items: center; justify-content: center;
      padding: 24px 16px; overflow-x: hidden; position: relative;
    }
    /* soft glowing orbs */
    body::before, body::after {
      content: ""; position: fixed; border-radius: 50%; filter: blur(80px); opacity: .55; z-index: 0;
      animation: float 18s ease-in-out infinite alternate;
    }
    body::before { width: 46vmax; height: 46vmax; background: var(--c); top: -14vmax; right: -10vmax; }
    body::after  { width: 38vmax; height: 38vmax; background: var(--b); bottom: -16vmax; left: -10vmax; animation-delay: -6s; }
    @keyframes float { to { transform: translate(-4vmax, 5vmax) scale(1.12); } }

    #fx { position: fixed; inset: 0; pointer-events: none; z-index: 1; overflow: hidden; }
    #fx i { position: absolute; top: -20px; display: block; animation: fall linear infinite; }
    #fx.rain i { width: 2px; height: 18px; background: linear-gradient(transparent, rgba(255,255,255,.65)); border-radius: 2px; }
    #fx.snow i { width: 6px; height: 6px; background: #fff; border-radius: 50%; filter: blur(.5px); animation-name: snowfall; }
    @keyframes fall { to { transform: translateY(110vh); } }
    @keyframes snowfall { 50% { margin-left: 18px; } to { transform: translateY(110vh); } }

    main { position: relative; z-index: 2; width: 100%; max-width: 460px; animation: rise .7s cubic-bezier(.2,.8,.2,1) both; }
    @keyframes rise { from { opacity: 0; transform: translateY(18px) scale(.98); } }

    .card {
      background: var(--glass); border: 1px solid var(--line); border-radius: 32px;
      padding: 26px; backdrop-filter: blur(26px) saturate(150%); -webkit-backdrop-filter: blur(26px) saturate(150%);
      box-shadow: 0 30px 80px rgba(0,0,0,.35), inset 0 1px 0 rgba(255,255,255,.25);
    }

    .search { display: flex; gap: 8px; margin-bottom: 22px; }
    .search input {
      flex: 1; min-width: 0; padding: 13px 18px; border-radius: 999px; border: 1px solid var(--line);
      background: rgba(0,0,0,.18); color: var(--text); font: inherit; font-size: .95rem; outline: none; transition: .2s;
    }
    .search input::placeholder { color: var(--muted); }
    .search input:focus { background: rgba(0,0,0,.28); border-color: rgba(255,255,255,.5); box-shadow: 0 0 0 4px rgba(255,255,255,.12); }
    .search button, .unit {
      border: 1px solid var(--line); background: var(--glass-strong); color: var(--text);
      border-radius: 999px; font: inherit; cursor: pointer; transition: .2s;
    }
    .search button { padding: 0 20px; font-weight: 600; }
    .search button:hover, .unit:hover { background: rgba(255,255,255,.3); transform: translateY(-1px); }

    .top { display: flex; justify-content: space-between; align-items: center; gap: 10px; }
    .place { font-size: .95rem; color: var(--muted); display: flex; align-items: center; gap: 6px; }
    .place b { color: var(--text); font-weight: 650; font-size: 1.05rem; }
    .unit { padding: 6px 12px; font-size: .8rem; font-weight: 600; letter-spacing: .04em; }

    .greeting { margin: 18px 0 0; color: var(--muted); font-size: .95rem; }
    .time {
      font-size: clamp(3.2rem, 17vw, 4.6rem); font-weight: 750; letter-spacing: -.03em; line-height: 1.05;
      font-variant-numeric: tabular-nums; margin: 2px 0;
      background: linear-gradient(180deg, #fff, rgba(255,255,255,.7)); -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .date { color: var(--muted); font-size: .98rem; }

    .now { display: flex; align-items: center; justify-content: space-between; margin: 26px 0 18px; padding: 18px 20px;
           background: var(--glass); border: 1px solid var(--line); border-radius: 24px; }
    .now .icon { font-size: 4.2rem; line-height: 1; filter: drop-shadow(0 8px 18px rgba(0,0,0,.3)); animation: bob 4s ease-in-out infinite; }
    @keyframes bob { 50% { transform: translateY(-6px); } }
    .now .temp { font-size: 3.6rem; font-weight: 750; line-height: 1; letter-spacing: -.03em; text-align: right; }
    .now .desc { font-size: 1.05rem; font-weight: 600; text-align: right; margin-top: 6px; }
    .now .feels { color: var(--muted); font-size: .85rem; text-align: right; margin-top: 2px; }

    .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
    .stat { background: var(--glass); border: 1px solid var(--line); border-radius: 18px; padding: 12px 6px; text-align: center; }
    .stat .k { font-size: 1.15rem; }
    .stat .v { font-weight: 650; font-size: .95rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
    .stat .l { color: var(--muted); font-size: .68rem; text-transform: uppercase; letter-spacing: .08em; margin-top: 2px; }

    .forecast { margin-top: 18px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; }
    .day { background: var(--glass); border: 1px solid var(--line); border-radius: 18px; padding: 12px 4px; text-align: center; transition: .2s; }
    .day:hover { transform: translateY(-3px); background: var(--glass-strong); }
    .day .n { font-size: .75rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: .06em; }
    .day .i { font-size: 1.6rem; margin: 6px 0; }
    .day .hi { font-weight: 700; font-size: .95rem; }
    .day .lo { color: var(--muted); font-size: .8rem; }

    .error { margin: 22px 0 6px; padding: 18px; border-radius: 20px; background: rgba(0,0,0,.22); border: 1px solid var(--line); text-align: center; color: var(--muted); }
    .foot { text-align: center; color: var(--muted); font-size: .72rem; margin-top: 16px; opacity: .8; }
    .foot a { color: inherit; }

    @media (max-width: 420px) {
      .card { padding: 20px; border-radius: 28px; }
      .now .temp { font-size: 3rem; } .now .icon { font-size: 3.4rem; }
      .stats { gap: 6px; } .stat .v { font-size: .85rem; }
    }
    @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation: none !important; } }
  </style>
</head>
<body data-theme="{{ theme }}" data-day="{{ 1 if is_day else 0 }}" data-tz="{{ tz_name }}">
  <div id="fx" class="{{ theme }}"></div>
  <main>
    <div class="card">
      <form class="search" method="get" role="search">
        <input name="city" placeholder="Search any city…" aria-label="City" autocomplete="off">
        <button type="submit">Go</button>
      </form>

      <div class="top">
        <div class="place">📍 <b>{{ city }}</b>{% if country %}<span>, {{ country }}</span>{% endif %}</div>
        {% if w %}<button class="unit" id="unit" type="button" aria-label="Toggle temperature unit">°C</button>{% endif %}
      </div>

      <p class="greeting">{{ greeting }}</p>
      <div class="time" id="time">{{ time }}</div>
      <div class="date" id="date">{{ date }}{% if tz_name %} &middot; {{ tz_name }}{% endif %}</div>

      {% if w %}
      <section class="now" aria-label="Current weather">
        <div class="icon">{{ w.icon }}</div>
        <div>
          <div class="temp"><span class="t" data-c="{{ w.temp }}">{{ w.temp|round|int }}°</span></div>
          <div class="desc">{{ w.label }}</div>
          <div class="feels">Feels like <span class="t" data-c="{{ w.feels }}">{{ w.feels|round|int }}°</span></div>
        </div>
      </section>

      <section class="stats" aria-label="Details">
        <div class="stat"><div class="k">💧</div><div class="v">{{ w.humidity }}%</div><div class="l">Humidity</div></div>
        <div class="stat"><div class="k">💨</div><div class="v">{{ w.wind|round|int }} km/h</div><div class="l">Wind</div></div>
        <div class="stat"><div class="k">🌅</div><div class="v">{{ w.sunrise }}</div><div class="l">Sunrise</div></div>
        <div class="stat"><div class="k">🌇</div><div class="v">{{ w.sunset }}</div><div class="l">Sunset</div></div>
      </section>

      <section class="forecast" aria-label="5 day forecast">
        {% for d in days %}
        <div class="day">
          <div class="n">{{ d.name }}</div>
          <div class="i">{{ d.icon }}</div>
          <div class="hi"><span class="t" data-c="{{ d.hi }}">{{ d.hi|round|int }}°</span></div>
          <div class="lo"><span class="t" data-c="{{ d.lo }}">{{ d.lo|round|int }}°</span></div>
        </div>
        {% endfor %}
      </section>
      {% else %}
      <div class="error">{{ error or "Weather is unavailable right now." }}<br>Try another city above.</div>
      {% endif %}
    </div>
    <div class="foot">Data by <a href="https://open-meteo.com/" target="_blank" rel="noopener">Open-Meteo</a></div>
  </main>

  <script>
    (function () {
      var body = document.body, tz = body.dataset.tz;

      // Live clock in the searched city's timezone
      var tEl = document.getElementById("time"), dEl = document.getElementById("date");
      if (tz) {
        var tf, df;
        try {
          tf = new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
          df = new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric" });
        } catch (e) { tf = null; }
        if (tf) {
          var tick = function () {
            var n = new Date();
            tEl.textContent = tf.format(n);
            dEl.textContent = df.format(n) + " \\u00b7 " + tz;
          };
          tick(); setInterval(tick, 1000);
        }
      }

      // °C / °F toggle (remembered)
      var unit = "C", btn = document.getElementById("unit");
      try { unit = localStorage.getItem("unit") || "C"; } catch (e) {}
      function paint() {
        document.querySelectorAll(".t").forEach(function (el) {
          var c = parseFloat(el.dataset.c);
          el.textContent = Math.round(unit === "F" ? c * 9 / 5 + 32 : c) + "\\u00b0";
        });
        if (btn) btn.textContent = "\\u00b0" + unit;
      }
      if (btn) {
        btn.addEventListener("click", function () {
          unit = unit === "C" ? "F" : "C";
          try { localStorage.setItem("unit", unit); } catch (e) {}
          paint();
        });
        paint();
      }

      // Rain / snow particles
      var theme = body.dataset.theme, fx = document.getElementById("fx");
      var calm = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (!calm && (theme === "rain" || theme === "storm" || theme === "snow")) {
        var snow = theme === "snow", count = snow ? 60 : 90;
        for (var i = 0; i < count; i++) {
          var p = document.createElement("i");
          p.style.left = (Math.random() * 100) + "vw";
          p.style.animationDuration = (snow ? 6 + Math.random() * 7 : 0.6 + Math.random() * 0.7) + "s";
          p.style.animationDelay = (-Math.random() * 10) + "s";
          p.style.opacity = (0.35 + Math.random() * 0.6).toFixed(2);
          fx.appendChild(p);
        }
      }
    })();
  </script>
</body>
</html>
"""


def describe(code, is_day=True):
    label, day_icon, night_icon, theme = WMO.get(code, ("Unknown", "🌡️", "🌡️", "cloudy"))
    return label, (day_icon if is_day else night_icon), theme


def geocode(city):
    r = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1},
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    results = r.json().get("results")
    return results[0] if results else None


def get_forecast(lat, lon):
    r = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset",
            "forecast_days": 5,
            "timezone": "auto",
        },
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


def build_context(city):
    """Return a dict of everything the page/API needs, or None if the city is unknown."""
    key = city.strip().lower()
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL:
        data = hit[1]
    else:
        place = geocode(city)
        if not place:
            return None
        data = {"place": place, "fc": get_forecast(place["latitude"], place["longitude"])}
        _cache[key] = (time.time(), data)

    place, fc = data["place"], data["fc"]
    cur, daily = fc["current"], fc["daily"]
    tz_name = fc.get("timezone", "UTC")
    is_day = bool(cur.get("is_day", 1))
    label, icon, theme = describe(cur["weather_code"], is_day)

    days = []
    for i, date_str in enumerate(daily["time"][:5]):
        d_label, d_icon, _ = describe(daily["weather_code"][i])
        days.append({
            "name": "Today" if i == 0 else datetime.strptime(date_str, "%Y-%m-%d").strftime("%a"),
            "icon": d_icon,
            "label": d_label,
            "hi": daily["temperature_2m_max"][i],
            "lo": daily["temperature_2m_min"][i],
        })

    now = datetime.now(ZoneInfo(tz_name))
    return {
        "city": place["name"],
        "country": place.get("country", ""),
        "tz_name": tz_name,
        "time": now.strftime("%H:%M:%S"),
        "date": now.strftime("%A, %d %B %Y"),
        "greeting": greeting(now.hour),
        "theme": theme,
        "is_day": is_day,
        "w": {
            "temp": cur["temperature_2m"],
            "feels": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"],
            "wind": cur["wind_speed_10m"],
            "label": label,
            "icon": icon,
            "sunrise": daily["sunrise"][0][-5:],
            "sunset": daily["sunset"][0][-5:],
        },
        "days": days,
    }


def greeting(hour):
    if hour < 5:
        return "Burning the midnight oil 🌙"
    if hour < 12:
        return "Good morning ☕"
    if hour < 17:
        return "Good afternoon 🌞"
    if hour < 21:
        return "Good evening 🌆"
    return "Good night ✨"


def fallback_context(city, error):
    now = datetime.now()
    return {
        "city": city, "country": "", "tz_name": "",
        "time": now.strftime("%H:%M:%S"), "date": now.strftime("%A, %d %B %Y") + " · server time",
        "greeting": greeting(now.hour), "theme": "cloudy", "is_day": now.hour in range(6, 19),
        "w": None, "days": [], "error": error,
    }


@app.route("/")
def index():
    city = (request.args.get("city") or DEFAULT_CITY).strip()[:80]
    try:
        ctx = build_context(city)
        if ctx is None:
            ctx = fallback_context(city, f"Couldn't find a city called “{city}”.")
    except (requests.RequestException, KeyError, ValueError):
        ctx = fallback_context(city, "Couldn't reach the weather service.")
    return render_template_string(PAGE, **ctx)


@app.route("/api/weather")
def api_weather():
    city = (request.args.get("city") or DEFAULT_CITY).strip()[:80]
    try:
        ctx = build_context(city)
    except (requests.RequestException, KeyError, ValueError) as e:
        return jsonify(error=str(e)), 502
    if ctx is None:
        return jsonify(error="city not found"), 404
    return jsonify(ctx)


@app.route("/healthz")
def healthz():
    return jsonify(status="ok")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))