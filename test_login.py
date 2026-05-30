from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time, os

ops = Options()
ops.add_argument("--no-sandbox")
driver = webdriver.Chrome(service=Service(os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")), options=ops)
wait = WebDriverWait(driver, 15)
driver.get("https://app.rainyun.com/auth/login")
time.sleep(3)
print("Title:", driver.title)
print("URL:", driver.current_url)
try:
    e = wait.until(EC.presence_of_element_located((By.NAME, "login-field")))
    print("login-field found:", e)
except Exception as ex:
    print("login-field NOT found:", ex)
    print("Page source (first 500 chars):", driver.page_source[:500])
driver.quit()
