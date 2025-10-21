import json, os, time, re, csv
from datetime import datetime
from urllib.parse import quote
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

# ===================== Config =====================
COOKIES_FILE  = "x_cookies.json"

# Palabras del query (puedes editarlas) + idioma
KEYWORDS      = ["paro", "nacional","ecuador"]  # <= variables de palabras
LANG          = "es"                  # <= variable de idioma
QUERY         = " ".join(KEYWORDS) + f" lang:{LANG}"

MAX_TWEETS    = 500                   # <= controla cuántos tweets extraer
HEADLESS      = True                 # True en servidores
SAVE_CSV      = True                 # True si también quieres CSV
SCROLL_PAUSES = (1.5, 2.2)            # min/max pausa entre scrolls
MAX_SCROLL_ROUNDS = 300               # tope de rondas de scroll por seguridad
# ==================================================

def build_driver(headless=False):
    opts = Options()
    if headless:
        opts.add_argument("--headless=new")
    # Forzar idioma a inglés para aria-labels consistentes
    opts.add_argument("--lang=en-US")
    opts.add_experimental_option("prefs", {"intl.accept_languages": "en,en_US"})
    # Hardening básico
    opts.add_argument("--disable-blink-features=AutomationControlled")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--window-size=1280,1000")
    opts.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    )
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
    driver.get("https://x.com/")
    for c in cookies:
        if "domain" in c and not c["domain"].endswith("x.com"):
            c["domain"] = domain
        try:
            driver.add_cookie(c)
        except Exception:
            pass
    return True

def has_auth_cookie(path=COOKIES_FILE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            for c in json.load(f):
                if c.get("name") == "auth_token" and c.get("value"):
                    return True
    except Exception:
        pass
    return False

def login_once_and_cache(driver):
    driver.get("https://x.com/login")
    print("-> Inicia sesión manualmente en X; detectaré la Home y guardaré cookies…")
    try:
        WebDriverWait(driver, 180).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[data-testid="AppTabBar_Home_Link"]'))
        )
        print("(OK) Login detectado, guardando cookies…")
        save_cookies(driver)
    except TimeoutException:
        raise RuntimeError("(BAD) No se detectó inicio de sesión en el tiempo esperado.")

def go_to_search(driver, query, timeout=60):
    url = f"https://x.com/search?q={quote(query)}&src=typed_query&f=live"
    driver.get(url)
    # Intenta cerrar diálogos (consent)
    try:
        WebDriverWait(driver, 5).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR,
                'div[role="dialog"] [data-testid="confirmationSheetConfirm"], '
                'div[role="dialog"] [data-testid="sheetDialogPrimaryAction"]'))
        ).click()
    except Exception:
        pass

    # 1) Si aparecen artículos, listo
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'article[data-testid="tweet"]'))
        )
        return
    except TimeoutException:
        pass

    # 2) Fallbacks por XPath (agnóstico al idioma)
    candidates_xpath = [
        '//div[starts-with(@aria-label,"Timeline") and contains(@aria-label,"Search")]',
        '//div[contains(@aria-label,"Results")]',
        '//section[starts-with(@aria-labelledby, "accessible-list")]',
        '//main[@role="main"]',
    ]
    for xp in candidates_xpath:
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, xp))
            )
            return
        except TimeoutException:
            continue

    # 3) Artefactos de depuración
    try:
        with open("debug_x_search_source.html", "w", encoding="utf-8") as fh:
            fh.write(driver.page_source)
        driver.save_screenshot("debug_x_search_screen.png")
    except Exception:
        pass
    raise TimeoutException(
        "No se detectó la timeline ni artículos. "
        "Puede ser muro de login o cambio de DOM. Revisa debug_x_search_source.html / debug_x_search_screen.png."
    )

def human_pause(a=1.2, b=2.0):
    import random
    time.sleep(random.uniform(a, b))

def parse_int_from_text(text):
    m = re.search(r'(\d[\d,\.]*)', text or "")
    if not m:
        return 0
    return int(m.group(1).replace(",", "").replace(".", ""))

def extract_visible_tweets(driver):
    tweets = []
    articles = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
    for art in articles:
        try:
            # Texto
            text_spans = art.find_elements(By.CSS_SELECTOR, 'div[data-testid="tweetText"] span')
            text = " ".join([s.text for s in text_spans]) if text_spans else ""

            # Usuario y handle
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

            # Métricas
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

            tweets.append({
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
    return tweets

def dedup_merge(existing_by_key, new_rows):
    added = 0
    for r in new_rows:
        key = r.get("permalink") or (r.get("handle","") + (r.get("text","")[:1000]))
        if key not in existing_by_key:
            existing_by_key[key] = r
            added += 1
    return added

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

def to_json(rows, query):
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    fn = f"x_tweets_{stamp}.json"
    payload = {
        "query": query,
        "generated_at": datetime.now().isoformat(),
        "count": len(rows),
        "tweets": rows
    }
    with open(fn, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return fn

def main():
    # Si no hay cookie de sesión, haz el primer login sin headless
    need_login = not has_auth_cookie()
    driver = build_driver(headless=(HEADLESS and not need_login))
    by_key = {}
    try:
        if not load_cookies(driver) or need_login:
            login_once_and_cache(driver)
            if HEADLESS:
                # reabrir en headless ya con cookies cargadas
                driver.quit()
                driver = build_driver(headless=True)
                load_cookies(driver)

        go_to_search(driver, QUERY, timeout=60)

        # Debug inicial: cuántos artículos visibles
        arts = driver.find_elements(By.CSS_SELECTOR, 'article[data-testid="tweet"]')
        print(f"[DEBUG] Artículos visibles tras cargar búsqueda: {len(arts)}")

        print(f"(SEARCH) '{QUERY}' — extrayendo hasta {MAX_TWEETS} tweets…")
        rounds = 0
        body = driver.find_element(By.TAG_NAME, "body")

        while len(by_key) < MAX_TWEETS and rounds < MAX_SCROLL_ROUNDS:
            new_rows = extract_visible_tweets(driver)
            added = dedup_merge(by_key, new_rows)
            if added:
                print(f"  [+] Nuevos: {added} | Total: {len(by_key)}")

            if len(by_key) >= MAX_TWEETS:
                break

            # Scroll una vez y pausa "humana"
            body.send_keys(Keys.END)
            human_pause(*SCROLL_PAUSES)
            rounds += 1

        rows = list(by_key.values())[:MAX_TWEETS]
        print(f"(OK) Tweets extraídos: {len(rows)}")

        json_path = to_json(rows, QUERY)
        print(f"(SAVE) JSON en: {json_path}")
        if SAVE_CSV:
            csv_path = to_csv(rows, QUERY)
            print(f"(SAVE) CSV  en: {csv_path}")

    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
