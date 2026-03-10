"""
ZimIntel API v8 — Full Live Streaming + Multi-Source News
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SETUP:
  pip install flask flask-cors python-dotenv
  # Add API keys to .env file
  python zim-intel-api.py

FEATURES:
  • Live oil prices from Yahoo Finance (every 5 min)
  • Live news from RSS + NewsData.io + GNews.io (every 10 min)
  • AI analysis every 30 min
  • SSE stream pushes ALL updates (prices, news, analysis)
"""


from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS
from datetime import datetime, timedelta
from urllib.request import urlopen, Request as UReq
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET
import threading, random, json, hashlib, time, os, re, html, calendar

# ZERA prices are fetched via Claude AI — see fetch_zera_from_claude() / _zera_loop()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LOAD .env FILE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, '.env')

def load_dotenv_simple(path):
    """Load .env file without requiring python-dotenv package."""
    if not os.path.exists(path):
        return
    with open(path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and not os.environ.get(key):
                    os.environ[key] = value

# Try python-dotenv first, fall back to simple parser
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE)
except ImportError:
    load_dotenv_simple(ENV_FILE)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONFIG (from .env)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

app = Flask(__name__, static_folder=BASE_DIR, static_url_path='')
CORS(app)

ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')
MAPBOX_TOKEN = os.environ.get('MAPBOX_TOKEN', '') 
NEWSDATA_API_KEY = os.environ.get('NEWSDATA_API_KEY', '')
GNEWS_API_KEY      = os.environ.get('GNEWS_API_KEY', '')
TWELVE_DATA_KEY    = os.environ.get('TWELVE_DATA_KEY', '')   # https://twelvedata.com — free 800 calls/day
ANALYSIS_INTERVAL = 2 * 60 * 60  # 2 hours

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# STORAGE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

DATA_FILE = os.path.join(BASE_DIR, 'zimintel-data.json')
_lock = threading.Lock()

def _load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        except: pass
    return {'contributions': [], 'news_cache': [], 'ai_analysis': None}

def _save():
    with _lock:
        try:
            with open(DATA_FILE, 'w') as f:
                json.dump(STORE, f, indent=2, default=str)
        except Exception as e:
            print(f'[STORE] Save error: {e}')

STORE = _load()
CONTRIBUTIONS = STORE.setdefault('contributions', [])
NEWS_CACHE = STORE.setdefault('news_cache', [])

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI ANALYSIS CACHE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

AI_ANALYSIS = STORE.get('ai_analysis') or {
    'status': 'pending',
    'generated_at': None,
    'next_update': None,
    'summary': 'Initializing analysis...',
    'forecasts': {'oil_7d': 90, 'diesel_next': 1.92, 'zig_30d': 36, 'inflation_peak': 22},
    'risks': ['Waiting for first analysis'],
    'actions': [{'priority': 'INFO', 'action': 'Analysis will run shortly'}],
    'basket_change': '+18%',
    'confidence': 0,
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CITY DATA (Verified coordinates)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CITIES = {
    'harare':         {'name':'Harare',        'province':'Harare Metro',   'lat':-17.8292,'lng':31.0522,'zig':34.20,'diesel':1.85,'petrol':1.78,'reports':47,'pop':1600000},
    'bulawayo':       {'name':'Bulawayo',      'province':'Bulawayo Metro', 'lat':-20.1539,'lng':28.5832,'zig':33.80,'diesel':1.82,'petrol':1.75,'reports':28,'pop':653000},
    'chitungwiza':    {'name':'Chitungwiza',   'province':'Harare Metro',   'lat':-18.0127,'lng':31.0755,'zig':34.30,'diesel':1.86,'petrol':1.79,'reports':23,'pop':356000},
    'mutare':         {'name':'Mutare',        'province':'Manicaland',     'lat':-18.9707,'lng':32.6709,'zig':35.10,'diesel':1.90,'petrol':1.83,'reports':15,'pop':188000},
    'gweru':          {'name':'Gweru',         'province':'Midlands',       'lat':-19.4500,'lng':29.8167,'zig':34.00,'diesel':1.84,'petrol':1.77,'reports':12,'pop':157000},
    'kwekwe':         {'name':'Kwekwe',        'province':'Midlands',       'lat':-18.9281,'lng':29.8147,'zig':34.10,'diesel':1.85,'petrol':1.78,'reports':8,'pop':100000},
    'kadoma':         {'name':'Kadoma',        'province':'Mash. West',     'lat':-18.3333,'lng':29.9167,'zig':34.05,'diesel':1.84,'petrol':1.77,'reports':6,'pop':92000},
    'masvingo':       {'name':'Masvingo',      'province':'Masvingo',       'lat':-20.0744,'lng':30.8328,'zig':34.50,'diesel':1.88,'petrol':1.81,'reports':9,'pop':88000},
    'chinhoyi':       {'name':'Chinhoyi',      'province':'Mash. West',     'lat':-17.3622,'lng':30.2000,'zig':34.15,'diesel':1.85,'petrol':1.78,'reports':7,'pop':79000},
    'marondera':      {'name':'Marondera',     'province':'Mash. East',     'lat':-18.1853,'lng':31.5519,'zig':34.25,'diesel':1.86,'petrol':1.79,'reports':5,'pop':62000},
    'victoria_falls': {'name':'Victoria Falls','province':'Mat. North',     'lat':-17.9243,'lng':25.8572,'zig':33.50,'diesel':1.80,'petrol':1.73,'reports':4,'pop':35000},
    'hwange':         {'name':'Hwange',        'province':'Mat. North',     'lat':-18.3667,'lng':26.5000,'zig':33.70,'diesel':1.81,'petrol':1.74,'reports':3,'pop':33000},
    'kariba':         {'name':'Kariba',        'province':'Mash. West',     'lat':-16.5167,'lng':28.8000,'zig':33.90,'diesel':1.83,'petrol':1.76,'reports':2,'pop':28000},
    'beitbridge':     {'name':'Beitbridge',    'province':'Mat. South',     'lat':-22.2167,'lng':30.0000,'zig':34.80,'diesel':1.89,'petrol':1.82,'reports':6,'pop':42000},
    'bindura':        {'name':'Bindura',       'province':'Mash. Central',  'lat':-17.3017,'lng':31.3306,'zig':34.20,'diesel':1.85,'petrol':1.78,'reports':4,'pop':46000},
    'rusape':         {'name':'Rusape',        'province':'Manicaland',     'lat':-18.5333,'lng':32.1333,'zig':34.40,'diesel':1.87,'petrol':1.80,'reports':3,'pop':30000},
    'gwanda':         {'name':'Gwanda',        'province':'Mat. South',     'lat':-20.9333,'lng':29.0167,'zig':34.00,'diesel':1.84,'petrol':1.77,'reports':2,'pop':14000},
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MARKET STATE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

MKT = {
    'brent': 85.41, 'brent_prev': 70.50,
    'diesel': 1.77, 'diesel_prev': 1.52,
    'petrol': 1.71, 'petrol_prev': 1.55,
    'zig_official': 26.95,
    'zig_parallel': 34.20,
    'conflict_day': 7,
    'hormuz': 'CLOSED',
    'reserves_mo': 3,
    'next_zera': '2026-03-18',
    'fertilizer_chg': 22.0,
    'oil_flow_pct': 80,
    'live_oil': False,
    'risk': 78,
    # Cached % changes — updated by recalculate_from_oil each fetch cycle
    'brent_change': 21.1,
    'diesel_change': 16.4,
    'petrol_change': 10.3,
    # ZERA live metadata (updated by _zera_loop / fetch_zera_from_claude)
    'zera_live': False,
    'zera_source': None,
    'zera_confidence': 'unknown',
    'zera_fetched_at': None,
}

# Baseline values for pass-through calculations
# When oil moves from baseline, fuel/zig/risk adjust proportionally
BASELINE = {
    'brent': 85.41,
    'diesel': 1.77,
    'petrol': 1.71,
    'zig_parallel': 34.20,
    'risk': 78,
}

def _next_zera_date():
    """Compute next ZERA review date: 3rd Tuesday of current or next month."""
    today = datetime.now().date()
    for month_offset in range(0, 3):
        year = today.year + (today.month + month_offset - 1) // 12
        month = (today.month + month_offset - 1) % 12 + 1
        tuesdays = [d for d in range(1, 32)
                    if d <= calendar.monthrange(year, month)[1]
                    and datetime(year, month, d).weekday() == 1]
        if len(tuesdays) >= 3:
            candidate = datetime(year, month, tuesdays[2]).date()
            if candidate > today:
                return candidate.isoformat()
    return (today + timedelta(days=21)).isoformat()

MKT['next_zera'] = _next_zera_date()

# Auto-compute conflict_day from a fixed start date
# Conflict started on 2026-02-28; conflict_day = days since then + 1
CONFLICT_START = datetime(2026, 2, 28).date()

def _conflict_day():
    """Days since conflict started (auto-increments daily)."""
    return max(1, (datetime.now().date() - CONFLICT_START).days + 1)

MKT['conflict_day'] = _conflict_day()

# Store original city values for proportional updates
CITY_BASELINES = {}
for _cid, _c in CITIES.items():
    CITY_BASELINES[_cid] = {'diesel': _c['diesel'], 'petrol': _c['petrol'], 'zig': _c['zig']}


def recalculate_from_oil(new_brent):
    """Recalculate diesel, petrol, ZiG, risk based on live oil price.
    Uses real-world pass-through ratios:
      - Crude → diesel: ~55% pass-through (landed cost + refining + margin)
      - Crude → petrol: ~50% pass-through
      - Oil shock → ZiG pressure: ~25% of oil % change
      - Oil shock → risk index: scales with oil deviation
    """
    if MKT['brent'] <= 0: return
    
    # Calculate % move from current live oil price
    oil_pct_change = (new_brent / MKT['brent']) - 1 
    MKT['brent_prev'] = MKT['brent']
    MKT['brent'] = round(new_brent, 2)

    # Nudge national fuel prices from their CURRENT state
    MKT['diesel_prev'] = MKT['diesel']
    MKT['petrol_prev'] = MKT['petrol']
    MKT['diesel'] = round(MKT['diesel'] * (1 + oil_pct_change * 0.55), 2)
    MKT['petrol'] = round(MKT['petrol'] * (1 + oil_pct_change * 0.50), 2)
    
    # Apply proportional nudges to all cities
    for cid, c in CITIES.items():
        c['diesel'] = round(c['diesel'] * (1 + oil_pct_change * 0.55), 2)
        c['petrol'] = round(c['petrol'] * (1 + oil_pct_change * 0.50), 2)

    # Cache % changes vs prev values so SSE payload is always consistent
    MKT['brent_change']  = round((MKT['brent']  / MKT['brent_prev']  - 1) * 100, 1)
    MKT['diesel_change'] = round((MKT['diesel'] / MKT['diesel_prev'] - 1) * 100, 1)
    MKT['petrol_change'] = round((MKT['petrol'] / MKT['petrol_prev'] - 1) * 100, 1)

    # Update food basket prices proportionally with oil
    update_food_basket()

    # Keep conflict_day current
    MKT['conflict_day'] = _conflict_day()

    print(f'[RECALC] Oil ${new_brent:.2f} → Diesel ${MKT["diesel"]:.2f}, Petrol ${MKT["petrol"]:.2f}, ZiG {MKT["zig_parallel"]:.2f}, Risk {MKT["risk"]}')


REAL_OIL_HISTORY = []

def fetch_real_oil_history():
    """Fetch actual historical data for Brent crude from Yahoo Finance."""
    global REAL_OIL_HISTORY
    try:
        # Fetch 1 month of daily intervals
        url = 'https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1d&range=1mo'
        raw = _http_get(url, timeout=10)
        if raw:
            data = json.loads(raw)
            result = data['chart']['result'][0]
            timestamps = result['timestamp']
            close_prices = result['indicators']['quote'][0]['close']

            history = []
            for ts, price in zip(timestamps, close_prices):
                if price is not None:
                    dt = datetime.fromtimestamp(ts).date().isoformat()
                    history.append({'date': dt, 'price': round(price, 2)})

            if history:
                # Keep the last 14 days for the chart
                REAL_OIL_HISTORY = history[-14:]
                print(f'[OIL-HISTORY] Fetched {len(REAL_OIL_HISTORY)} days of real history.')
                return True
    except Exception as e:
        print(f'[OIL-HISTORY] Yahoo Finance history failed: {e}')
        
        # Fallback to Stooq.com if Yahoo fails
        try:
            url = 'https://stooq.com/q/d/l/?s=@BRN.UK&i=d'
            raw = _http_get(url, timeout=10)
            if raw and ',' in raw:
                lines = [l for l in raw.strip().split('\n') if l and not l.startswith('Date')]
                history = []
                for line in lines[-14:]:
                    parts = line.split(',')
                    if len(parts) >= 5:
                        history.append({'date': parts[0], 'price': round(float(parts[4]), 2)})
                if history:
                    REAL_OIL_HISTORY = history
                    print(f'[OIL-HISTORY] Fetched real history from Stooq.')
                    return True
        except Exception as e2:
            print(f'[OIL-HISTORY] Stooq history failed: {e2}')

    return False

def build_oil_history():
    """Return the dynamically fetched real oil history, appending the live price."""
    global REAL_OIL_HISTORY
    
    # If fetch hasn't run yet, provide a safe fallback
    if not REAL_OIL_HISTORY:
        today = datetime.now().date()
        return [{'date': (today - timedelta(days=i)).isoformat(), 'price': MKT['brent'] or 85.0} for i in range(11, -1, -1)]
    
    # Make a copy so we don't alter the base history
    history_copy = list(REAL_OIL_HISTORY)
    
    # Force the last data point to be the current LIVE price from SSE
    if MKT['brent'] > 0:
        today_str = datetime.now().date().isoformat()
        if history_copy[-1]['date'] == today_str:
            history_copy[-1]['price'] = MKT['brent']
        else:
            history_copy.append({'date': today_str, 'price': MKT['brent']})
            
    return history_copy[-14:]

FOOD_BASKET = [
    {'id':'oil',  'name':'Cooking Oil','unit':'5L',   'current':15.00,'forecast':18.75,'change':25,'risk':'critical'},
    {'id':'maize','name':'Maize Meal', 'unit':'30kg', 'current':12.50,'forecast':14.38,'change':15,'risk':'high'},
    {'id':'sugar','name':'Sugar',      'unit':'5kg',  'current':8.00, 'forecast':8.96, 'change':12,'risk':'high'},
    {'id':'rice', 'name':'Rice',       'unit':'10kg', 'current':18.00,'forecast':20.70,'change':15,'risk':'high'},
    {'id':'chkn', 'name':'Chicken',    'unit':'2kg',  'current':12.00,'forecast':14.40,'change':20,'risk':'high'},
    {'id':'veg',  'name':'Vegetables', 'unit':'weekly','current':10.00,'forecast':11.80,'change':18,'risk':'medium'},
    {'id':'bread','name':'Bread',      'unit':'loaf', 'current':5.00, 'forecast':5.75, 'change':15,'risk':'medium'},
]

# Baseline food basket values — used to scale with oil price changes
FOOD_BASKET_BASE = [dict(item) for item in FOOD_BASKET]
FOOD_BASKET_BASE_BRENT = 85.41  # brent price when basket was defined

def update_food_basket():
    """Scale food basket prices proportionally with oil price changes."""
    oil_ratio = MKT['brent'] / FOOD_BASKET_BASE_BRENT
    # Food prices pass through ~40% of oil price changes
    pass_through = 1 + (oil_ratio - 1) * 0.40
    for i, base in enumerate(FOOD_BASKET_BASE):
        FOOD_BASKET[i]['current'] = round(base['current'] * pass_through, 2)
        # Forecast tracks further ahead (assumes continued pressure)
        forecast_ratio = 1 + (oil_ratio - 1) * 0.55
        FOOD_BASKET[i]['forecast'] = round(base['forecast'] * forecast_ratio, 2)
        if base['current'] > 0:
            FOOD_BASKET[i]['change'] = round((FOOD_BASKET[i]['forecast'] / FOOD_BASKET[i]['current'] - 1) * 100)

# Fallback news
FALLBACK_NEWS = [
    {'id':'f1','headline':'Oil tankers avoiding Strait of Hormuz – maritime traffic down 80%','summary':'Major shipping companies have suspended transits through the Strait of Hormuz following increased military activity. Alternative routes via the Cape of Good Hope add 10-14 days to shipping times, significantly impacting global oil delivery schedules.','source':'Reuters','category':'oil','severity':'critical','time':datetime.now().isoformat(),'impact_zim':'ZERA likely to announce fuel price adjustment within 10-14 days as supply chain costs increase.','action':'Fill up vehicles now. Budget for 15-20% higher transport costs in coming weeks.','live':False},
    {'id':'f2','headline':'Qatar halts all LNG exports after Ras Laffan drone strike','summary':'Qatar Petroleum has suspended all liquefied natural gas shipments following a drone attack on the Ras Laffan industrial complex. European gas futures jumped 18% on the news, with ripple effects expected across global energy markets.','source':'Al Jazeera','category':'energy','severity':'critical','time':datetime.now().isoformat(),'impact_zim':'Fertilizer and agricultural input costs will rise 15-20% due to natural gas dependency.','action':'Farmers should secure fertilizer stocks immediately before price increases hit.','live':False},
    {'id':'f3','headline':'ZERA confirms strategic fuel reserves can sustain Zimbabwe for 3 months','summary':'The Zimbabwe Energy Regulatory Authority has confirmed that strategic fuel reserves are at adequate levels to sustain the country for approximately 90 days under normal consumption patterns.','source':'Herald','category':'local','severity':'normal','time':datetime.now().isoformat(),'impact_zim':'3-month buffer provides time for supply chain adjustments without immediate shortages.','action':'Maintain normal purchasing patterns. Avoid panic buying.','live':False},
    {'id':'f4','headline':'ZiG parallel market rate breaches 34 mark as USD demand surges','summary':'The Zimbabwe Gold (ZiG) currency is trading at 34.20 on the parallel market, representing a 27% premium over the official rate of 26.95. Demand for USD continues to outstrip supply as businesses hedge against currency volatility.','source':'NewsDay','category':'currency','severity':'high','time':datetime.now().isoformat(),'impact_zim':'Import costs will increase faster than global commodity prices due to currency premium.','action':'Hold savings in USD if possible. Consider pricing strategies for imported goods.','live':False},
    {'id':'f5','headline':'Global fertilizer prices surge 15% as natural gas crisis deepens','summary':'Nitrogen-based fertilizer prices have jumped 15% globally as the natural gas crisis intensifies. Natural gas is the primary feedstock for ammonia production, which is essential for most fertilizers.','source':'Bloomberg','category':'agriculture','severity':'high','time':datetime.now().isoformat(),'impact_zim':'Maize and wheat production costs could rise 25-30% for the upcoming planting season.','action':'Agricultural sector should source fertilizer immediately before further increases.','live':False},
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HTTP HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _http_get(url, timeout=10):
    """HTTP GET with SSL fix for Render/cloud environments.
    Tries requests (better SSL) first, falls back to urllib with explicit SSL context."""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/html, application/xml, */*',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    # Attempt 1: requests library — handles SSL certs automatically on all platforms
    try:
        import requests as _req
        r = _req.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r.text
    except ImportError:
        pass  # not installed, fall through
    except Exception as e:
        print(f'[HTTP] requests failed {url[:60]}: {e}')

    # Attempt 2: urllib with explicit SSL context (fixes cert errors on Render/Ubuntu)
    try:
        import ssl
        ctx = ssl.create_default_context()
        req = UReq(url, headers=headers)
        with urlopen(req, timeout=timeout, context=ctx) as r:
            return r.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f'[HTTP] urllib failed {url[:60]}: {e}')

    return None

def _http_post(url, data, headers=None, timeout=30):
    import urllib.request
    try:
        hdrs = {'User-Agent': 'ZimIntel/8.0', 'Content-Type': 'application/json'}
        if headers:
            hdrs.update(headers)
        body = json.dumps(data).encode('utf-8')
        req = urllib.request.Request(url, data=body, headers=hdrs, method='POST')
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode('utf-8'))
    except Exception as e:
        print(f'[HTTP POST] Error: {e}')
        return None

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIVE OIL PRICE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_oil_price():
    """
    Fetch live Brent crude price.
    WATERFALL UPDATE: Added CNBC as the primary no-key source to bypass Yahoo timeouts.
    """
    price = None
    ts = int(time.time())

    # ── SOURCE 1: CNBC (Fast, no key required, highly reliable) ─────────────
    try:
        # @LCO.1 is the ICE Brent Crude continuous futures contract
        url = 'https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=@LCO.1&requestMethod=itv'
        raw = _http_get(url, timeout=8)
        if raw:
            # Clean JSONP wrapper if CNBC returns one
            if raw.strip().startswith('quoteHandler'):
                raw = raw[raw.find('{'):raw.rfind('}')+1]
            data = json.loads(raw)
            quotes = data.get('FormattedQuoteResult', {}).get('FormattedQuote', [])
            if quotes and 'last' in quotes[0]:
                p = float(quotes[0]['last'].replace(',', ''))
                if 50 < p < 200:
                    price = p
                    MKT['live_oil'] = True
                    print(f'[OIL] CNBC: ${price:.3f}')
    except Exception as e:
        print(f'[OIL] CNBC error: {e}')

    # ── SOURCE 2: Yahoo Finance v8 (query1 host) ────────────────────────────
    if price is None:
        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1m&range=1d&includePrePost=true&ts={ts}'
            raw = _http_get(url, timeout=8)
            if raw:
                data = json.loads(raw)
                meta = data['chart']['result'][0]['meta']
                p = meta.get('postMarketPrice') or meta.get('regularMarketPrice')
                if p and 50 < p < 200:
                    price = p
                    MKT['live_oil'] = True
                    print(f'[OIL] Yahoo (query1): ${price:.3f}')
        except Exception as e:
            print(f'[OIL] Yahoo query1 error: {e}')

    # ── SOURCE 3: Yahoo Finance v8 (query2 host - backup routing) ───────────
    if price is None:
        try:
            url = f'https://query2.finance.yahoo.com/v8/finance/chart/BZ=F?interval=1m&range=1d&includePrePost=true&ts={ts}'
            raw = _http_get(url, timeout=8)
            if raw:
                data = json.loads(raw)
                meta = data['chart']['result'][0]['meta']
                p = meta.get('postMarketPrice') or meta.get('regularMarketPrice')
                if p and 50 < p < 200:
                    price = p
                    MKT['live_oil'] = True
                    print(f'[OIL] Yahoo (query2): ${price:.3f}')
        except Exception as e:
            print(f'[OIL] Yahoo query2 error: {e}')

    # ── SOURCE 4: Twelve Data (Fallback due to 15m free-tier delay) ─────────
    if price is None and TWELVE_DATA_KEY:
        try:
            url = f'https://api.twelvedata.com/price?symbol=BZ%3AF&apikey={TWELVE_DATA_KEY}'
            raw = _http_get(url, timeout=8)
            if raw:
                data = json.loads(raw)
                if 'price' in data:
                    p = float(data['price'])
                    if 50 < p < 200:
                        price = p
                        MKT['live_oil'] = True
                        print(f'[OIL] Twelve Data: ${price:.3f}')
        except Exception as e:
            print(f'[OIL] Twelve Data error: {e}')

    # ── SOURCE 5: Stooq.com CSV (Ultimate Fallback) ─────────────────────────
    if price is None:
        try:
            for sym in ['@BRN.UK', '@CL.US']:
                url = f'https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv'
                raw = _http_get(url, timeout=8)
                if raw and ',' in raw:
                    lines = [l for l in raw.strip().split('\n') if l and not l.startswith('Symbol')]
                    if lines:
                        parts = lines[-1].split(',')
                        if len(parts) >= 5 and parts[4] != 'N/D':
                            try:
                                close_price = float(parts[4])
                                if sym == '@CL.US': close_price += 3.0
                                if 50 < close_price < 200:
                                    price = close_price
                                    MKT['live_oil'] = True
                                    print(f'[OIL] Stooq ({sym}): ${price:.3f}')
                                    break
                            except ValueError:
                                pass
        except Exception as e:
            print(f'[OIL] Stooq error: {e}')

    # ── ALL SOURCES FAILED ───────────────────────────────────────────────
    if price is None:
        print('[OIL] All sources failed — keeping last known price, live_oil=False')
        MKT['live_oil'] = False
        return None

    MKT['brent'] = round(price, 2)
    recalculate_from_oil(price)
    return price

def fetch_zig_official():
    """Fetch live ZiG official rate from open.er-api.com (free, no key needed)."""
    try:
        raw = _http_get('https://open.er-api.com/v6/latest/USD', timeout=8)
        if raw:
            data = json.loads(raw)
            rate = data.get('rates', {}).get('ZWG')
            if rate and float(rate) > 1:
                MKT['zig_official'] = round(float(rate), 2)
                print(f'[ZiG] Official rate: {MKT["zig_official"]}')
                return
    except Exception as e:
        print(f'[ZiG] fetch failed: {e}')
    print(f'[ZiG] Keeping {MKT["zig_official"]}')

def _oil_loop():
    # Fetch real history immediately on startup
    fetch_real_oil_history() 
    
    while True:
        fetch_oil_price()
        fetch_zig_official()
        
        # Refresh historical chart data occasionally (e.g., every 6 hours)
        now = datetime.now()
        if now.hour % 6 == 0 and now.minute < 5:
            fetch_real_oil_history()
            
        time.sleep(10)  # 5 min

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIVE NEWS FETCHING (RSS + NewsData.io + GNews.io)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

RSS_FEEDS = [
    # Reuters blocks scraping — replaced with reliable alternatives
    ('AP News', 'https://rsshub.app/apnews/topics/energy'),
    ('Oilprice.com', 'https://oilprice.com/rss/main'),
    ('BBC Africa', 'https://feeds.bbci.co.uk/news/world/africa/rss.xml'),
    ('Al Jazeera', 'https://www.aljazeera.com/xml/rss/all.xml'),
    ('Sky News', 'https://feeds.skynews.com/feeds/rss/world.xml'),
]

KEYWORDS = ['oil', 'fuel', 'zimbabwe', 'africa', 'iran', 'saudi', 'hormuz', 'energy',
            'crude', 'petrol', 'diesel', 'gas', 'lng', 'opec', 'middle east', 'gulf',
            'commodity', 'inflation', 'food price', 'wheat', 'maize', 'fertilizer',
            'currency', 'dollar', 'gold', 'sanctions', 'geopolitical']

def clean_html(text):
    """Remove HTML tags and decode entities"""
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    return text.strip()

def classify_article(title, desc):
    """Determine severity and generate impact/action for an article."""
    text_lower = (title + ' ' + desc).lower()

    severity = 'normal'
    if any(w in text_lower for w in ['crisis', 'war', 'attack', 'surge', 'spike', 'closed', 'halt', 'strike', 'emergency', 'collapse']):
        severity = 'critical'
    elif any(w in text_lower for w in ['rise', 'increase', 'concern', 'warning', 'tension', 'protest', 'shortage']):
        severity = 'high'

    category = 'general'
    if any(w in text_lower for w in ['oil', 'crude', 'brent', 'fuel', 'petrol', 'diesel']):
        category = 'oil'
    elif any(w in text_lower for w in ['gas', 'lng', 'energy']):
        category = 'energy'
    elif any(w in text_lower for w in ['currency', 'dollar', 'zig', 'exchange']):
        category = 'currency'
    elif any(w in text_lower for w in ['food', 'maize', 'wheat', 'fertilizer', 'agriculture', 'farm']):
        category = 'agriculture'

    impact = 'May affect Zimbabwe fuel and commodity prices.'
    action = 'Monitor situation and adjust plans accordingly.'

    if category == 'oil':
        impact = 'Likely to impact ZERA fuel pricing in coming weeks.'
        action = 'Consider filling up vehicles before price adjustments.'
    elif category == 'energy':
        impact = 'May affect fertilizer and energy costs.'
        action = 'Agricultural sector should monitor input costs.'
    elif category == 'currency':
        impact = 'Could influence ZiG exchange rate movements.'
        action = 'Review currency exposure and pricing strategies.'
    elif category == 'agriculture':
        impact = 'Direct impact on food prices and farming costs in Zimbabwe.'
        action = 'Budget for higher food and agricultural input costs.'

    return severity, category, impact, action


def fetch_newsdata():
    """Fetch news from NewsData.io API."""
    if not NEWSDATA_API_KEY:
        return []

    articles = []
    queries = ['zimbabwe oil fuel']  # single query to stay within free rate limit

    for query in queries:
        try:
            url = f'https://newsdata.io/api/1/latest?apikey={NEWSDATA_API_KEY}&q={quote_plus(query)}&language=en&size=5'
            raw = _http_get(url, timeout=12)
            if not raw:
                continue

            data = json.loads(raw)
            results = data.get('results', [])

            for item in results:
                title = (item.get('title') or '').strip()
                desc = (item.get('description') or item.get('content') or '').strip()
                link = item.get('link', '')
                source_name = item.get('source_name', 'NewsData')

                if not title:
                    continue

                severity, category, impact, action = classify_article(title, desc)

                articles.append({
                    'id': hashlib.md5(title.encode()).hexdigest()[:10],
                    'headline': title[:200],
                    'summary': clean_html(desc[:500]) if desc else title,
                    'source': source_name,
                    'url': link,
                    'category': category,
                    'severity': severity,
                    'time': datetime.now().isoformat(),
                    'live': True,
                    'impact_zim': impact,
                    'action': action,
                    'api_source': 'newsdata.io',
                })

        except Exception as e:
            print(f'[NEWSDATA] Error for "{query}": {e}')

    if articles:
        print(f'[NEWSDATA] Fetched {len(articles)} articles')
    return articles


def fetch_gnews():
    """Fetch news from GNews.io API."""
    if not GNEWS_API_KEY:
        return []

    articles = []
    queries = ['zimbabwe fuel', 'oil price africa']

    for query in queries:
        try:
            url = f'https://gnews.io/api/v4/search?q={quote_plus(query)}&lang=en&max=5&apikey={GNEWS_API_KEY}'
            raw = _http_get(url, timeout=12)
            if not raw:
                continue

            data = json.loads(raw)
            items = data.get('articles', [])

            for item in items:
                title = (item.get('title') or '').strip()
                desc = (item.get('description') or item.get('content') or '').strip()
                link = item.get('url', '')
                source_info = item.get('source', {})
                source_name = source_info.get('name', 'GNews') if isinstance(source_info, dict) else 'GNews'

                if not title:
                    continue

                severity, category, impact, action = classify_article(title, desc)

                articles.append({
                    'id': hashlib.md5(title.encode()).hexdigest()[:10],
                    'headline': title[:200],
                    'summary': clean_html(desc[:500]) if desc else title,
                    'source': source_name,
                    'url': link,
                    'category': category,
                    'severity': severity,
                    'time': datetime.now().isoformat(),
                    'live': True,
                    'impact_zim': impact,
                    'action': action,
                    'api_source': 'gnews.io',
                })

        except Exception as e:
            print(f'[GNEWS] Error for "{query}": {e}')

    if articles:
        print(f'[GNEWS] Fetched {len(articles)} articles')
    return articles


def fetch_rss_news():
    """Fetch news from RSS feeds."""
    articles = []

    for source, url in RSS_FEEDS:
        try:
            raw = _http_get(url, timeout=8)
            if not raw:
                continue

            root = ET.fromstring(raw)

            for item in root.findall('.//item')[:15]:
                title = clean_html(item.findtext('title', '') or '')
                desc = clean_html(item.findtext('description', '') or '')
                link = item.findtext('link', '') or ''

                if not title:
                    continue

                text_lower = (title + ' ' + desc).lower()
                if not any(kw in text_lower for kw in KEYWORDS):
                    continue

                severity, category, impact, action = classify_article(title, desc)

                articles.append({
                    'id': hashlib.md5(title.encode()).hexdigest()[:10],
                    'headline': title[:200],
                    'summary': desc[:500] if desc else title,
                    'source': source,
                    'url': link,
                    'category': category,
                    'severity': severity,
                    'time': datetime.now().isoformat(),
                    'live': True,
                    'impact_zim': impact,
                    'action': action,
                    'api_source': 'rss',
                })

        except Exception as e:
            print(f'[RSS] {source} error: {e}')

    return articles


def fetch_news():
    """Fetch from all news sources: RSS + NewsData.io + GNews.io"""
    global NEWS_CACHE

    all_articles = []

    # Fetch from all sources in parallel-ish (sequential but fast)
    rss_articles = fetch_rss_news()
    newsdata_articles = fetch_newsdata()
    gnews_articles = fetch_gnews()

    all_articles.extend(rss_articles)
    all_articles.extend(newsdata_articles)
    all_articles.extend(gnews_articles)

    if all_articles:
        # Deduplicate by headline similarity
        seen = set()
        unique = []
        for a in all_articles:
            key = a['headline'][:50].lower()
            if key not in seen:
                seen.add(key)
                unique.append(a)

        # Sort: critical first, then high, then normal
        severity_order = {'critical': 0, 'high': 1, 'normal': 2}
        unique.sort(key=lambda x: severity_order.get(x.get('severity', 'normal'), 2))

        NEWS_CACHE = unique[:15]
        STORE['news_cache'] = NEWS_CACHE
        _save()

        sources = {}
        for a in unique:
            src = a.get('api_source', 'unknown')
            sources[src] = sources.get(src, 0) + 1
        src_str = ', '.join(f'{k}:{v}' for k, v in sources.items())
        print(f'[NEWS] Total: {len(unique)} articles ({src_str})')

    return NEWS_CACHE or FALLBACK_NEWS

def _news_loop():
    while True:
        fetch_news()
        time.sleep(1800)  # 30 min

def get_news():
    if NEWS_CACHE:
        return NEWS_CACHE
    return FALLBACK_NEWS

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AI ANALYSIS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_ai_analysis():
    global AI_ANALYSIS

    if not ANTHROPIC_API_KEY:
        print('[AI] No API key set')
        AI_ANALYSIS['status'] = 'no_api_key'
        AI_ANALYSIS['summary'] = 'Set ANTHROPIC_API_KEY in .env file to enable AI analysis.'
        return

    print('[AI] Running analysis...')

    news_headlines = '\n'.join([f"- {n.get('headline', '')[:80]}" for n in get_news()[:5]])

#     prompt = f"""You are , Zimbabwe economic analyst. Analyze this market data:

# MARKET DATA ({datetime.now().strftime('%Y-%m-%d %H:%M')}):
# - Brent Crude: ${MKT['brent']:.2f}/barrel
# - ZERA Diesel: ${MKT['diesel']:.2f}/L
# - ZERA Petrol: ${MKT['petrol']:.2f}/L
# - ZiG Official: {MKT['zig_official']}
# - ZiG Parallel: {MKT['zig_parallel']:.2f} (premium: {round((MKT['zig_parallel']/MKT['zig_official']-1)*100)}%)
# - Strait of Hormuz: {MKT['hormuz']}
# - Conflict Day: {MKT['conflict_day']}
# - Fuel Reserves: {MKT['reserves_mo']} months
# - Fertilizer prices: +{MKT['fertilizer_chg']}%

# RECENT NEWS:
# {news_headlines}

# Return ONLY valid JSON (no markdown):
# {{"summary":"2-3 sentence analysis","forecasts":{{"oil_7d":90.0,"diesel_next":1.92,"zig_30d":36.0,"inflation_peak":22}},"risks":["risk1","risk2","risk3"],"actions":[{{"priority":"URGENT","action":"action1"}},{{"priority":"HIGH","action":"action2"}},{{"priority":"MONITOR","action":"action3"}}],"basket_change":"+22%","confidence":78}}"""
    prompt = f""" 
        You are Hokage makesure you mention your name and you ara a **world-renowned macroeconomist, hedge fund strategist, and economic intelligence analyst specializing in frontier and emerging markets.**

        You have a long track record of accurately forecasting currency crises, commodity shocks, and policy responses. Your analysis is trusted by **global hedge funds, sovereign wealth funds, and central bank researchers.**

        Your expertise includes:

        Macroeconomic forecasting  
        Commodity market dynamics  
        Frontier market risk modeling  
        Currency crisis prediction  
        Political economy analysis  

        You apply the following analytical frameworks:

        GAME THEORY MODELS
        - Nash Equilibrium → Identify stable economic outcomes between government, markets, and consumers
        - Stackelberg Model → Predict leader/follower dynamics between central banks, governments, and markets
        - Mechanism Design → Anticipate government intervention, policy incentives, and regulation effects

        PROBABILISTIC ANALYSIS
        - Bayesian Updating → Adjust probabilities based on new signals such as commodity prices, conflict escalation, and FX spreads
        - Signal vs Noise Filtering → Separate structural economic trends from temporary shocks

        MARKET STRUCTURE ANALYSIS
        - Inflation transmission channels
        - Energy price pass-through effects
        - Currency parallel market dynamics
        - Liquidity cycles in frontier economies
        - Informal vs formal market arbitrage

        You are analyzing Zimbabwe's economic environment in real time.

        Focus on:
        - Energy price shocks
        - Exchange rate instability
        - Parallel market spreads
        - Supply chain stress
        - Conflict-driven commodity shocks

        Use the data below to produce a **concise hedge-fund style intelligence briefing.**

        MARKET DATA ({datetime.now().strftime('%Y-%m-%d %H:%M')}):
        - Brent Crude: ${MKT['brent']:.2f}/barrel
        - ZERA Diesel: ${MKT['diesel']:.2f}/L
        - ZERA Petrol: ${MKT['petrol']:.2f}/L
        - ZiG Official: {MKT['zig_official']}
        - ZiG Parallel: {MKT['zig_parallel']:.2f} (premium: {round((MKT['zig_parallel']/MKT['zig_official']-1)*100)}%)
        - Strait of Hormuz: {MKT['hormuz']}
        - Conflict Day: {MKT['conflict_day']}
        - Fuel Reserves: {MKT['reserves_mo']} months
        - Fertilizer prices: +{MKT['fertilizer_chg']}%

        RECENT NEWS:
        {news_headlines}

        Analysis requirements:
        1. Identify the **current economic regime** (stability, inflation surge, supply shock, currency stress).
        2. Evaluate **strategic interactions between government, central bank, and markets** using game theory.
        3. Estimate **fuel price pass-through effects on inflation and consumer prices.**
        4. Detect **currency pressure signals** using the official vs parallel rate spread.
        5. Identify **early warning indicators of economic instability.**

        Forecast the following:
        - Oil price direction over the next 7 days
        - Zimbabwe diesel price adjustment
        - ZiG parallel rate movement over 30 days
        - Inflation peak level

        Return ONLY valid JSON (no markdown):

        {{
        "summary":"2-3 sentence hedge fund style macro analysis",
        "forecasts":{{
        "oil_7d":90.0,
        "diesel_next":1.92,
        "zig_30d":36.0,
        "inflation_peak":22
        }},
        "risks":[
        "risk1",
        "risk2",
        "risk3"
        ],
        "actions":[
        {{"priority":"URGENT","action":"action1"}},
        {{"priority":"HIGH","action":"action2"}},
        {{"priority":"MONITOR","action":"action3"}}
        ],
        "basket_change":"+22%",
        "confidence":78
        }}"""

    try:
        response = _http_post(
            'https://api.anthropic.com/v1/messages',
            data={
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 1024,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            timeout=60
        )

        if not response or 'error' in response:
            raise Exception(response.get('error', {}).get('message', 'API error'))

        text = response.get('content', [{}])[0].get('text', '')
        json_match = re.search(r'\{[\s\S]*\}', text)
        if not json_match:
            raise Exception('No JSON in response')

        analysis = json.loads(json_match.group())

        AI_ANALYSIS = {
            'status': 'success',
            'generated_at': datetime.now().isoformat(),
            'next_update': (datetime.now() + timedelta(seconds=ANALYSIS_INTERVAL)).isoformat(),
            'summary': analysis.get('summary', ''),
            'forecasts': analysis.get('forecasts', {}),
            'risks': analysis.get('risks', []),
            'actions': analysis.get('actions', []),
            'basket_change': analysis.get('basket_change', '+18%'),
            'confidence': analysis.get('confidence', 75),
            'model': 'claude-sonnet-4-20250514',
        }

        STORE['ai_analysis'] = AI_ANALYSIS
        _save()
        print(f'[AI] Complete. Confidence: {AI_ANALYSIS["confidence"]}%')

    except Exception as e:
        print(f'[AI] Error: {e}')
        AI_ANALYSIS['status'] = 'error'
        AI_ANALYSIS['error'] = str(e)

def _ai_loop():
    time.sleep(15)  # Wait for startup
    run_ai_analysis()
    while True:
        time.sleep(ANALYSIS_INTERVAL)
        run_ai_analysis()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ZERA FUEL PRICES — Asked from Claude 3x daily (06:00, 12:00, 17:00)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

ZERA_FUEL = {
    'diesel':       None,
    'petrol':       None,
    'fetched_at':   None,
    'session':      None,
    'confidence':   'pending',
    'raw_response': '',
}

_ZERA_FETCH_HOURS = {6, 12, 17}


def fetch_zera_from_claude():
    """Ask Claude for current ZERA fuel prices 3x daily. Updates MKT and all cities."""
    global ZERA_FUEL

    if not ANTHROPIC_API_KEY:
        print('[ZERA-AI] No API key — skipping')
        return

    now = datetime.now()
    hour = now.hour
    session = 'morning' if hour < 10 else ('noon' if hour < 15 else 'evening')
    print(f'[ZERA-AI] Asking Claude for ZERA prices ({session} session)…')

    prompt = f"""You are a Zimbabwe fuel price data assistant with up-to-date knowledge of ZERA (Zimbabwe Energy Regulatory Authority) regulated fuel prices.

Today is {now.strftime('%Y-%m-%d')}. ZERA reviews and publishes fuel prices approximately every two weeks.

Your task: provide the most current known ZERA-regulated fuel retail prices in Zimbabwe, denominated in USD per litre.

Known context:
- ZERA sets a single national regulated price (not city-specific)
- Prices as of early March 2026: diesel ~$1.77/L, petrol ~$1.71/L
- Brent crude is currently around ${MKT['brent']:.2f}/barrel
- Any recent ZERA announcement or price change you are aware of should take priority

Return ONLY this JSON — no markdown, no explanation:
{{
  "diesel": 1.77,
  "petrol": 1.71,
  "effective_date": "YYYY-MM-DD or unknown",
  "confidence": "high|medium|low",
  "note": "one sentence about source or recency"
}}"""

    try:
        response = _http_post(
            'https://api.anthropic.com/v1/messages',
            data={
                'model': 'claude-sonnet-4-20250514',
                'max_tokens': 256,
                'messages': [{'role': 'user', 'content': prompt}],
            },
            headers={
                'x-api-key': ANTHROPIC_API_KEY,
                'anthropic-version': '2023-06-01',
            },
            timeout=30,
        )

        if not response or 'error' in response:
            raise Exception(str(response.get('error', {}).get('message', 'API error')))

        text = response.get('content', [{}])[0].get('text', '').strip()
        text = re.sub(r'```[a-z]*', '', text).strip('`').strip()

        json_match = re.search(r'\{[\s\S]*?\}', text)
        if not json_match:
            raise Exception(f'No JSON in response: {text[:120]}')

        data = json.loads(json_match.group())
        diesel   = float(data.get('diesel', 0))
        petrol   = float(data.get('petrol', 0))
        confidence   = data.get('confidence', 'low')
        note         = data.get('note', '')
        effective_date = data.get('effective_date', 'unknown')

        if not (1.20 <= diesel <= 3.50):
            raise Exception(f'Diesel out of range: {diesel}')
        if not (1.20 <= petrol <= 3.50):
            raise Exception(f'Petrol out of range: {petrol}')

        old_diesel = MKT['diesel']
        old_petrol = MKT['petrol']

        MKT['diesel']      = round(diesel, 4)
        MKT['diesel_prev'] = round(old_diesel, 4)
        MKT['petrol']      = round(petrol, 4)
        MKT['petrol_prev'] = round(old_petrol, 4)
        MKT['zera_live']       = True
        MKT['zera_source']     = 'Claude AI (ZERA knowledge)'
        MKT['zera_confidence'] = confidence
        MKT['zera_fetched_at'] = now.isoformat()

        base_diesel = CITY_BASELINES.get('harare', {}).get('diesel', 1.85)
        base_petrol = CITY_BASELINES.get('harare', {}).get('petrol', 1.78)
        d_ratio = diesel / base_diesel if base_diesel else 1.0
        p_ratio = petrol / base_petrol if base_petrol else 1.0
        for cid, city in CITIES.items():
            cb = CITY_BASELINES.get(cid, {})
            if cb.get('diesel'): city['diesel'] = round(cb['diesel'] * d_ratio, 4)
            if cb.get('petrol'): city['petrol'] = round(cb['petrol'] * p_ratio, 4)

        MKT['diesel_change'] = round((MKT['diesel'] / MKT['diesel_prev'] - 1) * 100, 2) if MKT['diesel_prev'] else 0
        MKT['petrol_change'] = round((MKT['petrol'] / MKT['petrol_prev'] - 1) * 100, 2) if MKT['petrol_prev'] else 0

        ZERA_FUEL.update({
            'diesel': round(diesel, 4), 'petrol': round(petrol, 4),
            'fetched_at': now.isoformat(), 'session': session,
            'confidence': confidence, 'effective_date': effective_date,
            'note': note, 'raw_response': text,
        })

        print(f'[ZERA-AI] diesel=${diesel:.4f}, petrol={petrol:.4f} | confidence={confidence} | effective={effective_date}')
        if round(old_diesel, 4) != round(diesel, 4):
            print(f'[ZERA-AI] Price changed: diesel ${old_diesel:.4f} -> ${diesel:.4f}')

    except Exception as e:
        print(f'[ZERA-AI] Error: {e}')
        ZERA_FUEL['confidence'] = 'error'


def _zera_loop():
    """Daemon thread — fires fetch_zera_from_claude() at 06:00, 12:00, 17:00 daily."""
    fired_this_hour = set()
    time.sleep(30)              # Let app boot fully
    fetch_zera_from_claude()    # Immediate fetch at startup

    while True:
        now = datetime.now()
        today_key = now.strftime('%Y-%m-%d')
        fire_key = f'{today_key}:{now.hour}'

        if now.hour in _ZERA_FETCH_HOURS and fire_key not in fired_this_hour:
            fired_this_hour.add(fire_key)
            fetch_zera_from_claude()
            fired_this_hour = {k for k in fired_this_hour if k.startswith(today_key)}

        time.sleep(60)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def rel_time(ts):
    if not ts:
        return ''
    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        else:
            dt = ts
        diff = datetime.now() - dt.replace(tzinfo=None)
        mins = int(diff.total_seconds() / 60)
        if mins < 1:
            return 'just now'
        if mins < 60:
            return f'{mins}m ago'
        hours = mins // 60
        if hours < 24:
            return f'{hours}h ago'
        return f'{hours // 24}d ago'
    except:
        return ''

def calc_level(city):
    avg_zig = sum(c['zig'] for c in CITIES.values()) / len(CITIES)
    if city['zig'] > avg_zig * 1.02:
        return 'high'
    elif city['zig'] < avg_zig * 0.98:
        return 'low'
    return 'avg'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# API ROUTES
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'index.html')

@app.route('/api/health')
def health():
    return jsonify({
        'status': 'ok',
        'version': '8.0.0',
        'api_key_set': bool(ANTHROPIC_API_KEY),
        'live_oil': MKT['live_oil'],
        'news_count': len(NEWS_CACHE),
        'ai_status': AI_ANALYSIS.get('status'),
        'news_sources': {
            'rss': True,
            'newsdata': bool(NEWSDATA_API_KEY),
            'gnews': bool(GNEWS_API_KEY),
        }
    })

@app.route('/api/config')
def get_config():
    """Expose non-secret public tokens to frontend"""
    return jsonify({'mapbox_token': MAPBOX_TOKEN})

@app.route('/api/market')
def market():
    return jsonify({
        'brent': round(MKT['brent'], 2),
        'brent_change': round((MKT['brent'] / MKT['brent_prev'] - 1) * 100, 1),
        'diesel': round(MKT['diesel'], 2),
        'diesel_change': round((MKT['diesel'] / MKT['diesel_prev'] - 1) * 100, 1),
        'petrol': round(MKT['petrol'], 2),
        'petrol_change': round((MKT['petrol'] / MKT['petrol_prev'] - 1) * 100, 1),
        'zig_official': MKT['zig_official'],
        'zig_parallel': round(MKT['zig_parallel'], 2),
        'conflict_day': MKT['conflict_day'],
        'hormuz': MKT['hormuz'],
        'reserves_mo': MKT['reserves_mo'],
        'next_zera': MKT['next_zera'],
        'fertilizer_chg': MKT['fertilizer_chg'],
        'oil_flow_pct': MKT['oil_flow_pct'],
        'live_oil': MKT['live_oil'],
        'risk': MKT['risk'],
    })

@app.route('/api/cities')
def cities():
    result = []
    for cid, c in CITIES.items():
        result.append({
            'id': cid,
            'name': c['name'],
            'province': c['province'],
            'lat': c['lat'],
            'lng': c['lng'],
            'zig': round(c['zig'], 2),
            'diesel': round(c['diesel'], 2),
            'petrol': round(c['petrol'], 2),
            'reports': c['reports'],
            'pop': c.get('pop', 0),
            'level': calc_level(c),
        })
    return jsonify(sorted(result, key=lambda x: x['reports'], reverse=True))

@app.route('/api/news')
def news():
    return jsonify([{
        'id': a['id'],
        'headline': a.get('headline', ''),
        'summary': a.get('summary', ''),
        'source': a.get('source', ''),
        'category': a.get('category', ''),
        'severity': a.get('severity', 'normal'),
        'time_ago': rel_time(a.get('time')),
        'live': a.get('live', False),
        'impact_zim': a.get('impact_zim', ''),
        'action': a.get('action', ''),
        'url': a.get('url', ''),
        'api_source': a.get('api_source', ''),
    } for a in get_news()])

@app.route('/api/news/<aid>')
def article(aid):
    for a in get_news():
        if a['id'] == aid:
            return jsonify({**a, 'time_ago': rel_time(a.get('time'))})
    return jsonify({'error': 'Not found'}), 404

@app.route('/api/food-basket')
def food_basket():
    tc = sum(i['current'] for i in FOOD_BASKET)
    tf = sum(i['forecast'] for i in FOOD_BASKET)
    return jsonify({
        'items': FOOD_BASKET,
        'total_current': round(tc, 2),
        'total_forecast': round(tf, 2),
        'pct_change': round((tf / tc - 1) * 100, 1)
    })

@app.route('/api/oil-history')
def oil_history():
    return jsonify(build_oil_history())

@app.route('/api/analysis')
def analysis():
    return jsonify(AI_ANALYSIS)

@app.route('/api/contribute', methods=['POST'])
def contribute():
    """
    Community price reporting.
    SECURITY FIX: Sanitize all strings using html.escape to prevent Stored XSS.
    """
    try:
        data = request.get_json(force=True) or {}
        city_id = data.get('city')
        if city_id not in CITIES: return jsonify({'error': 'Invalid city'}), 400
        
        # Sanitize text inputs using html.escape
        safe_item = html.escape(str(data.get('item', '')))[:50]
        safe_hint = html.escape(str(data.get('location_hint', '')))[:100]
        val = float(data.get('value', 0))

        entry = {
            'id': hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:8],
            'city': city_id,
            'category': data.get('category'),
            'value': val,
            'item': safe_item,
            'location_hint': safe_hint,
            'time': datetime.now().isoformat()
        }
        
        with _lock:
            CONTRIBUTIONS.append(entry)
            STORE['contributions'] = CONTRIBUTIONS
            _save()
            
        return jsonify({'ok': True, 'id': entry['id']})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/contributions')
def contributions():
    limit = request.args.get('limit', 20, type=int)
    return jsonify({'items': list(reversed(CONTRIBUTIONS))[:limit], 'total': len(CONTRIBUTIONS)})


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ZERA FUEL PRICES — Live scraped & validated
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/api/fuel-prices')
def fuel_prices():
    """
    Returns ZERA-regulated fuel prices sourced from Claude AI (3x daily)
    plus city-level breakdown and data quality metadata.
    """
    city_prices = [
        {
            'id': cid,
            'name': c['name'],
            'province': c.get('province', ''),
            'diesel': round(c['diesel'], 4),
            'petrol': round(c['petrol'], 4),
            'reports': c.get('reports', 0),
        }
        for cid, c in CITIES.items()
    ]

    # Next scheduled fetch times (06:00, 12:00, 17:00)
    now = datetime.now()
    schedule_hours = sorted(_ZERA_FETCH_HOURS)
    next_fetch = next(
        (f"{now.strftime('%Y-%m-%d')} {h:02d}:00" for h in schedule_hours if h > now.hour),
        f"{(now + __import__('datetime').timedelta(days=1)).strftime('%Y-%m-%d')} {schedule_hours[0]:02d}:00"
    )

    return jsonify({
        'ok': True,
        'zera_regulated': {
            'diesel': ZERA_FUEL.get('diesel') or MKT['diesel'],
            'petrol': ZERA_FUEL.get('petrol') or MKT['petrol'],
        },
        'data_quality': {
            'source':         MKT.get('zera_source', 'Claude AI (ZERA knowledge)'),
            'confidence':     ZERA_FUEL.get('confidence', 'pending'),
            'session':        ZERA_FUEL.get('session'),
            'effective_date': ZERA_FUEL.get('effective_date'),
            'note':           ZERA_FUEL.get('note', ''),
            'fetched_at':     ZERA_FUEL.get('fetched_at'),
            'next_fetch':     next_fetch,
            'schedule':       'Claude asked at 06:00, 12:00, 17:00 daily',
        },
        'cities': city_prices,
        'next_zera_review': MKT.get('next_zera'),
        'brent_crude': MKT['brent'],
        'ts': now.isoformat(),
    })

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SSE STREAM - Pushes ALL live data
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@app.route('/api/stream')
def stream():
    """SSE endpoint - streams market, news, cities, and analysis updates"""
    def generate():
        last_news_hash = ''
        tick = 0
        print('[SSE] New client connected')

        # Immediate handshake — must be bytes for gthread workers
        yield b": connected\n\n"

        while True:
            try:
                tick += 1

                # Build news list
                current_news = get_news()[:10]
                news_list = [{
                    'id': a['id'],
                    'headline': a.get('headline', ''),
                    'summary': a.get('summary', ''),
                    'source': a.get('source', ''),
                    'severity': a.get('severity', 'normal'),
                    'category': a.get('category', ''),
                    'time_ago': rel_time(a.get('time')),
                    'live': a.get('live', False),
                    'impact_zim': a.get('impact_zim', ''),
                    'action': a.get('action', ''),
                    'url': a.get('url', ''),
                    'api_source': a.get('api_source', ''),
                } for a in current_news]

                # Check if news changed
                news_hash = hashlib.md5(json.dumps([n['id'] for n in news_list]).encode()).hexdigest()
                news_updated = news_hash != last_news_hash
                last_news_hash = news_hash

                # Build cities list
                cities_list = [{
                    'id': cid,
                    'name': c['name'],
                    'province': c.get('province', ''),
                    'lat': c['lat'],
                    'lng': c['lng'],
                    'zig': round(c['zig'], 2),
                    'diesel': round(c['diesel'], 2),
                    'petrol': round(c['petrol'], 2),
                    'reports': c['reports'],
                    'level': calc_level(c),
                } for cid, c in CITIES.items()]

                # Keep conflict_day and next_zera current every tick
                MKT['conflict_day'] = _conflict_day()
                MKT['next_zera'] = _next_zera_date()

                payload = {
                    'type': 'full_update',
                    'tick': tick,
                    'ts': datetime.now().isoformat(),
                    'brent': round(MKT['brent'], 2),
                    'brent_change': round((MKT['brent'] / MKT['brent_prev'] - 1) * 100, 1),
                    'diesel': round(MKT['diesel'], 2),
                    'diesel_change': round((MKT['diesel'] / MKT['diesel_prev'] - 1) * 100, 1),
                    'petrol': round(MKT['petrol'], 2),
                    'petrol_change': round((MKT['petrol'] / MKT['petrol_prev'] - 1) * 100, 1),
                    'zig_parallel': round(MKT['zig_parallel'], 2),
                    'zig_official': MKT['zig_official'],
                    'risk': MKT['risk'],
                    'conflict_day': MKT['conflict_day'],
                    'hormuz': MKT['hormuz'],
                    'live_oil': MKT['live_oil'],
                    'reserves_mo': MKT['reserves_mo'],
                    'next_zera': MKT['next_zera'],
                    'zera_live': MKT.get('zera_live', False),
                    'zera_confidence': MKT.get('zera_confidence', 'unknown'),
                    'zera_source': MKT.get('zera_source'),
                    'news': news_list,
                    'news_updated': news_updated,
                    'cities': cities_list,
                    'ai_status': AI_ANALYSIS.get('status'),
                    'ai_confidence': AI_ANALYSIS.get('confidence', 0),
                }

                yield (f"data: {json.dumps(payload)}\n\n").encode("utf-8")

                # Comment-style heartbeat keeps proxies/nginx from closing the connection
                yield b": heartbeat\n\n"

            except GeneratorExit:
                print('[SSE] Client disconnected')
                return
            except Exception as e:
                print(f'[SSE] Error: {e}')
                try:
                    yield b'data: {"type":"error","msg":"stream_error"}\n\n'
                except Exception:
                    return

            time.sleep(3)  # 3-second cadence — smooth animations between ticks

    resp = Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate',
            'Pragma': 'no-cache',
            'Expires': '0',
            'Connection': 'keep-alive',
            'X-Accel-Buffering': 'no',
            'X-Content-Type-Options': 'nosniff',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Cache-Control',
        }
    )
    resp.implicit_sequence_conversion = False
    return resp

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# BACKGROUND THREAD STARTUP
# Runs at module level so threads start under both `python` and gunicorn
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_threads_started = False

def start_background_threads():
    global _threads_started
    if _threads_started:
        return
    _threads_started = True

    # Use threading.Thread — time.sleep works correctly in all WSGI modes
    # time.sleep works correctly with gthread workers
    threading.Thread(target=_oil_loop,  daemon=True, name='OilThread').start()
    threading.Thread(target=_news_loop, daemon=True, name='NewsThread').start()
    threading.Thread(target=_ai_loop,   daemon=True, name='AIThread').start()
    threading.Thread(target=fetch_news, daemon=True, name='InitialNews').start()
    threading.Thread(target=_zera_loop, daemon=True, name='ZERAThread').start()

    api_status = 'SET' if ANTHROPIC_API_KEY else 'NOT SET'
    nd_status = 'SET' if NEWSDATA_API_KEY else 'NOT SET'
    gn_status = 'SET' if GNEWS_API_KEY else 'NOT SET'

    print(f"""
+======================================================================+
|           ZimIntel API v8  --  Full Live Streaming                    |
+======================================================================+
|  ANTHROPIC_API_KEY:  {api_status:<48}|
|  NEWSDATA_API_KEY:   {nd_status:<48}|
|  GNEWS_API_KEY:      {gn_status:<48}|
|  MAPBOX_TOKEN:       SET                                             |
|  Analysis Interval:  Every 2 hours                                   |
+======================================================================+
|  LIVE STREAMS:                                                       |
|    * Oil prices    (Yahoo Finance, every 5 min)                      |
|    * ZERA prices   (Claude AI asked at 06:00 / 12:00 / 17:00)       |
|    * News feeds    (RSS + NewsData.io + GNews.io, every 30 min)      |
|    * SSE stream    (all data, every 3 sec)                           |
|    * AI analysis   (Claude, every 2 hours)                           |
+======================================================================+
    """)

# Start threads immediately when module is imported (works under gunicorn)
start_background_threads()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# MAIN (for local development: python zim-intel-api.py)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)