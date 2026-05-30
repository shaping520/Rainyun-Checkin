import time, os, sys
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

ops = Options()
ops.add_argument("--no-sandbox")
driver = webdriver.Chrome(service=Service(os.path.join(os.path.dirname(os.path.abspath(__file__)), "chromedriver.exe")), options=ops)
wait = WebDriverWait(driver, 15)
driver.get("https://app.rainyun.com/auth/login")
time.sleep(3)
try:
    username = wait.until(EC.visibility_of_element_located((By.NAME, "login-field")))
    password = wait.until(EC.visibility_of_element_located((By.NAME, "login-password")))
    login_btn = wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="app"]/div[1]/div[1]/div/div[2]/fade/div/div/span/form/button')))
    username.send_keys("wander_jake@hotmail.com")
    password.send_keys("511227Jj")
    login_btn.click()
    print("Clicked login, waiting...")
    time.sleep(5)
    print("URL after 5s:", driver.current_url)
    print("Page title:", driver.title)
    # Check for error message
    try:
        err = driver.find_element(By.XPATH, '//*[contains(text(), "错误") or contains(text(), "失败") or contains(text(), "error") or contains(@class, "error")]')
        print("Error found:", err.text)
    except:
        print("No obvious error element found")
    driver.save_screenshot("login_result.png")
    print("Screenshot saved: login_result.png")
    time.sleep(10)
except Exception as e:
    print("Exception:", e)
    driver.save_screenshot("login_error.png")
driver.quit()
