import json, os, time, re, csv
from datetime import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import (
    TimeoutException, NoSuchElementException, StaleElementReferenceException
)

COOKIES_FILE = "x_cookies.json"
QUERY        = 'paro nacional lang:es'    # ajusta tu consulta
MAX_TWEETS   = 10000                        # límite de extracción (pequeña escala)
SCROLL_PAUSES = (1.5, 2.2)                # min/max pausa entre scrolls

def build_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1000")
    # User-Agent “normal”
    opts.add_argument("user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/126.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=opts)

def save_cookies(driver, path=COOKIES_FILE):
    cookies = driver.get_cookies()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cookies, f, ensure_ascii=False, indent=2)

def load_cookies(driver, path=COOKIES_FILE, domain=".x.com"):
    if not os.path.exists(path):
        return False
    with open(path, "r", encoding="utf-8") as f:
        cookies = json.load(f)
    driver.get("https://x.com/")  # necesario antes de añadir cookies
    for c in cookies:
        # Corrige dominio si fuese necesario
        if "domain" in c and not c["domain"].endswith("x.com"):
            c["domain"] = domain
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    return True

def login_once_and_cache(driver):
    """Abre la página de login para que el usuario inicie sesión manualmente.
       Cuando detecta la home, guarda cookies."""
    driver.get("https://x.com/login")
    print("-> Inicia sesión manualmente en X. El script detectará la home…")
    try:
        WebDriverWait(driver, 180).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="AppTabBar_Home_Link"]'))
        )
        print("(OK) Login detectado, guardando cookies…")
        save_cookies(driver)
    except TimeoutException:
        raise RuntimeError("(BAD) No se detectó inicio de sesión en el tiempo esperado.")

def go_to_search(driver, query):
    # Búsqueda “Latest” (f=live); X suele requerir sesión para ver resultados
    from urllib.parse import quote
    search_url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
    driver.get(search_url)
    # Espera a que aparezca el contenedor principal de resultados
    WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, 'div[aria-label="Timeline: Search timeline"], div[aria-label="Timeline: Results"]'))
    )

def human_pause(a=1.2, b=2.0):
    import random
    time.sleep(random.uniform(a, b))

def scroll_to_load(driver, rounds=20):
    last_height = driver.execute_script("return document.body.scrollHeight")
    loaded = 0
    for i in range(rounds):
        driver.find_element(By.TAG_NAME, "body").send_keys(Keys.END)
        human_pause(*SCROLL_PAUSES)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            # intenta un “nudge” extra
            human_pause(1.0, 1.5)
            driver.execute_script("window.scrollBy(0, 800)")
            human_pause(*SCROLL_PAUSES)
            newer = driver.execute_script("return document.body.scrollHeight")
            if newer == last_height:
                break
            new_height = newer
        last_height = new_height
        loaded += 1
    return loaded

def parse_int_from_text(text):
    m = re.search(r'(\d[\d,\.]*)', text or "")
    if not m: 
        return 0
    return int(m.group(1).replace(",", "").replace(".", ""))

def extract_tweets(driver):
    tweets_data = []
    seen = set()

    # Artículos-tweet (selector relativamente estable en X)
    articles = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
    for art in articles:
        try:
            # Texto
            text_spans = art.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetText"] span')
            text = " ".join([s.text for s in text_spans]) if text_spans else ""

            # Usuario (@handle) y display name
            user_box = art.find_element(By.CSS_SELECTOR, 'div[data-testid="User-Name"]')
            display_name = user_box.find_element(By.CSS_SELECTOR, 'span').text
            handle = ""
            for a in user_box.find_elements(By.TAG_NAME, "a"):
                href = a.get_attribute("href") or ""
                if "/status/" not in href and href.startswith("https://x.com/"):
                    handle = "@" + href.split("/")[-1]
                    break

            # Timestamp y permalink
            ts_iso, permalink = None, None
            try:
                t = art.find_element(By.TAG_NAME, "time")
                ts_iso = t.get_attribute("datetime")
                permalink = t.find_element(By.XPATH, "./parent::a").get_attribute("href")
            except NoSuchElementException:
                pass

            # Métricas (reply/retweet/like)
            def metric(testid):
                try:
                    el = art.find_element(By.CSS_SELECTOR, f'div[data-testid="{testid}"]')
                    aria = el.get_attribute("aria-label") or ""
                    return parse_int_from_text(aria)
                except NoSuchElementException:
                    return 0

            replies  = metric("reply")
            retweets = metric("retweet")
            likes    = metric("like")

            key = permalink or (handle + text[:1000])
            if key in seen:
                continue
            seen.add(key)

            tweets_data.append({
                "display_name": display_name,
                "handle": handle,
                "text": text,
                "timestamp": ts_iso,
                "permalink": permalink,
                "replies": replies,
                "retweets": retweets,
                "likes": likes,
            })
        except (NoSuchElementException, StaleElementReferenceException):
            continue
    return tweets_data

def to_csv(rows, query):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fn = f"x_tweets_{stamp}.csv"
    with open(fn, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=[
            "display_name","handle","text","timestamp","permalink","replies","retweets","likes"
        ])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return fn

def main():
    driver = build_driver(headless=False)  # headless=True en servidores
    try:
        if not load_cookies(driver):
            login_once_and_cache(driver)  # te pedirá login solo la 1a vez

        go_to_search(driver, QUERY)
        print("(SEARCH) Buscando… cargando resultados (scroll)…")
        scroll_to_load(driver, rounds=25)

        rows = extract_tweets(driver)
        print(f"(OK) Tweets extraídos (antes de límite): {len(rows)}")

        # Corta a MAX_TWEETS para trabajo de aula/pequeña escala
        rows = rows[:MAX_TWEETS]
        csv_path = to_csv(rows, QUERY)
        print(f"(TOUCH) Guardado en: {csv_path}")

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
