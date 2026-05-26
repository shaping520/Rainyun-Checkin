import io
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass

import cv2
import ddddocr
import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.webdriver import WebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.wait import WebDriverWait

from config import (
    APP_BASE_URL,
    APP_VERSION,
    CAPTCHA_RETRY_LIMIT,
    COOKIE_FILE,
    DOWNLOAD_MAX_RETRIES,
    DOWNLOAD_RETRY_DELAY,
    DOWNLOAD_TIMEOUT,
    POINTS_TO_CNY_RATE,
)

# 自定义异常：验证码处理过程中可重试的错误
class CaptchaRetryableError(Exception):
    """可重试的验证码处理错误（如下载失败、网络问题等）"""
    pass

try:
    from notify import send
    print("✅ 通知模块加载成功")
except Exception as e:
    print(f"⚠️ 通知模块加载失败：{e}")

    def send(title, content):
        pass

# 创建一个内存缓冲区，用于存储所有日志
log_capture_string = io.StringIO()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# 配置 logger
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')

#输出到字符串 (新增功能)
string_handler = logging.StreamHandler(log_capture_string)
string_handler.setFormatter(formatter)
logger.addHandler(string_handler)

@dataclass
class RuntimeContext:
    driver: WebDriver
    wait: WebDriverWait
    ocr: ddddocr.DdddOcr
    det: ddddocr.DdddOcr
    temp_dir: str


def build_app_url(path: str) -> str:
    return f"{APP_BASE_URL}/{path.lstrip('/')}"


def temp_path(ctx: RuntimeContext, filename: str) -> str:
    return os.path.join(ctx.temp_dir, filename)


def clear_temp_dir(temp_dir: str) -> None:
    if not os.path.exists(temp_dir):
        return
    for filename in os.listdir(temp_dir):
        file_path = os.path.join(temp_dir, filename)
        if os.path.isfile(file_path) or os.path.islink(file_path):
            os.remove(file_path)


def do_login(ctx: RuntimeContext, user: str, pwd: str) -> bool:
    """执行登录流程"""
    logger.info("发起登录请求")
    ctx.driver.get(build_app_url("/auth/login"))
    try:
        username = ctx.wait.until(EC.visibility_of_element_located((By.NAME, 'login-field')))
        password = ctx.wait.until(EC.visibility_of_element_located((By.NAME, 'login-password')))
        login_button = ctx.wait.until(EC.visibility_of_element_located((By.XPATH,
                                                                    '//*[@id="app"]/div[1]/div[1]/div/div[2]/fade/div/div/span/form/button')))
        username.send_keys(user)
        password.send_keys(pwd)
        login_button.click()
    except TimeoutException:
        logger.error("页面加载超时，请尝试延长超时时间或切换到国内网络环境！")
        return False
    try:
        login_captcha = ctx.wait.until(EC.visibility_of_element_located((By.ID, 'tcaptcha_iframe_dy')))
        logger.warning("触发验证码！")
        ctx.wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "tcaptcha_iframe_dy")))
        if not process_captcha(ctx):
            logger.error("登录验证码识别失败")
            return False
    except TimeoutException:
        logger.info("未触发验证码")
    time.sleep(2)  # 给页面一点点缓冲时间
    ctx.driver.switch_to.default_content()
    try:
        # 使用显式等待检测登录是否成功（通过判断 URL 变化）
        ctx.wait.until(EC.url_contains("dashboard"))
        logger.info("登录成功！")
        return True
    except TimeoutException:
        logger.error(f"登录超时或失败！当前 URL: {ctx.driver.current_url}")
        return False


def init_selenium(debug: bool, linux: bool) -> WebDriver:
    ops = Options()
    ops.add_argument("--no-sandbox")
    if debug:
        ops.add_experimental_option("detach", True)
    if linux:
        ops.add_argument("--headless")
        ops.add_argument("--disable-gpu")
        ops.add_argument("--disable-dev-shm-usage")
        # 设置 Chromium 二进制路径（支持 ARM 和 AMD64）
        chrome_bin = os.environ.get("CHROME_BIN")
        if chrome_bin and os.path.exists(chrome_bin):
            ops.binary_location = chrome_bin
        # 容器环境使用系统 chromedriver
        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH", "/usr/local/share/chromedriver-linux64/chromedriver")
        if os.path.exists(chromedriver_path):
            return webdriver.Chrome(service=Service(chromedriver_path), options=ops)
        return webdriver.Chrome(service=Service("./chromedriver"), options=ops)
    return webdriver.Chrome(service=Service("chromedriver.exe"), options=ops)


def download_image(url: str, output_path: str) -> bool:
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    last_error = None
    for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
        try:
            response = requests.get(url, timeout=DOWNLOAD_TIMEOUT)
            if response.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(response.content)
                return True
            last_error = f"status_code={response.status_code}"
            logger.warning(f"下载图片失败 (第 {attempt} 次): {last_error}, URL: {url}")
        except requests.RequestException as e:
            last_error = str(e)
            logger.warning(f"下载图片失败 (第 {attempt} 次): {e}, URL: {url}")
        if attempt < DOWNLOAD_MAX_RETRIES:
            time.sleep(DOWNLOAD_RETRY_DELAY)
    logger.error(f"下载图片失败，已重试 {DOWNLOAD_MAX_RETRIES} 次: {last_error}, URL: {url}")
    return False


def get_url_from_style(style):
    # 修复：添加空值保护
    if not style:
        raise ValueError("style 属性为空，无法解析 URL")
    match = re.search(r"url\(([^)]+)\)", style, re.IGNORECASE)
    if not match:
        raise ValueError(f"无法从 style 中解析 URL: {style}")
    url = match.group(1).strip().strip('"').strip("'")
    return url


def get_width_from_style(style):
    # 修复：添加空值保护
    if not style:
        raise ValueError("style 属性为空，无法解析宽度")
    match = re.search(r"width\s*:\s*([\d.]+)px", style, re.IGNORECASE)
    if not match:
        raise ValueError(f"无法从 style 中解析宽度: {style}")
    return float(match.group(1))


def get_height_from_style(style):
    # 修复：添加空值保护
    if not style:
        raise ValueError("style 属性为空，无法解析高度")
    match = re.search(r"height\s*:\s*([\d.]+)px", style, re.IGNORECASE)
    if not match:
        raise ValueError(f"无法从 style 中解析高度: {style}")
    return float(match.group(1))


def get_element_size(element) -> tuple[float, float]:
    size = element.size or {}
    width = size.get("width", 0)
    height = size.get("height", 0)
    if not width or not height:
        raise ValueError("无法从元素尺寸解析宽高")
    return float(width), float(height)


def process_captcha(ctx: RuntimeContext, retry_count_list=None):
    if retry_count_list is None:
        retry_count_list = [0]
    retry_count = retry_count_list[0]
    if retry_count >= CAPTCHA_RETRY_LIMIT:
        logger.error(f"验证码重试 {retry_count} 次后失败，任务终止")
        return False
    try:
        download_captcha_img(ctx)
        if check_captcha(ctx):
            logger.info(f"开始识别验证码 (第 {retry_count + 1} 次尝试)")
            captcha = cv2.imread(temp_path(ctx, "captcha.jpg"))
            # 修复：检查图片是否成功读取
            if captcha is None:
                logger.error("验证码背景图读取失败，可能下载不完整")
                raise CaptchaRetryableError("验证码图片读取失败")
            with open(temp_path(ctx, "captcha.jpg"), 'rb') as f:
                captcha_b = f.read()
            bboxes = ctx.det.detection(captcha_b)
            result = dict()
            for i in range(len(bboxes)):
                x1, y1, x2, y2 = bboxes[i]
                spec = captcha[y1:y2, x1:x2]
                cv2.imwrite(temp_path(ctx, f"spec_{i + 1}.jpg"), spec)
                for j in range(3):
                    similarity, matched = compute_similarity(
                        temp_path(ctx, f"sprite_{j + 1}.jpg"),
                        temp_path(ctx, f"spec_{i + 1}.jpg")
                    )
                    similarity_key = f"sprite_{j + 1}.similarity"
                    position_key = f"sprite_{j + 1}.position"
                    if similarity_key in result.keys():
                        if float(result[similarity_key]) < similarity:
                            result[similarity_key] = similarity
                            result[position_key] = f"{int((x1 + x2) / 2)},{int((y1 + y2) / 2)}"
                    else:
                            result[similarity_key] = similarity
                            result[position_key] = f"{int((x1 + x2) / 2)},{int((y1 + y2) / 2)}"
            if check_answer(result):
                for i in range(3):
                    similarity_key = f"sprite_{i + 1}.similarity"
                    position_key = f"sprite_{i + 1}.position"
                    positon = result[position_key]
                    logger.info(f"图案 {i + 1} 位于 ({positon})，匹配率：{result[similarity_key]}")
                    slide_bg = ctx.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBg"]')))
                    style = slide_bg.get_attribute("style")
                    x, y = int(positon.split(",")[0]), int(positon.split(",")[1])
                    width_raw, height_raw = captcha.shape[1], captcha.shape[0]
                    try:
                        width = get_width_from_style(style)
                        height = get_height_from_style(style)
                    except ValueError:
                        width, height = get_element_size(slide_bg)
                    x_offset, y_offset = float(-width / 2), float(-height / 2)
                    final_x, final_y = int(x_offset + x / width_raw * width), int(y_offset + y / height_raw * height)
                    ActionChains(ctx.driver).move_to_element_with_offset(slide_bg, final_x, final_y).click().perform()
                confirm = ctx.wait.until(
                    EC.element_to_be_clickable((By.XPATH, '//*[@id="tcStatus"]/div[2]/div[2]/div/div')))
                logger.info("提交验证码")
                confirm.click()
                time.sleep(5)
                result_el = ctx.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="tcOperation"]')))
                if 'show-success' in result_el.get_attribute("class"):
                    logger.info(f"验证码通过（重试 {retry_count} 次后成功）")
                    return True
                else:
                    logger.error("验证码未通过，正在重试")
            else:
                logger.error("验证码识别失败，正在重试")
        else:
            logger.error("当前验证码识别率低，尝试刷新")

        reload_btn = ctx.driver.find_element(By.XPATH, '//*[@id="reload"]')
        time.sleep(2)
        reload_btn.click()
        time.sleep(2)
        retry_count_list[0] = retry_count + 1
        return process_captcha(ctx, retry_count_list)
    except (TimeoutException, ValueError, CaptchaRetryableError) as e:
        # 修复：仅捕获预期异常（超时、解析失败、下载失败），其他程序错误直接抛出便于排查
        logger.error(f"验证码处理异常: {type(e).__name__} - {e}")
        # 尝试刷新验证码重试
        try:
            reload_btn = ctx.driver.find_element(By.XPATH, '//*[@id="reload"]')
            time.sleep(2)
            reload_btn.click()
            time.sleep(2)
            retry_count_list[0] = retry_count + 1
            return process_captcha(ctx, retry_count_list)
        except Exception as refresh_error:
            logger.error(f"无法刷新验证码，放弃重试: {refresh_error}")
            return False


def download_captcha_img(ctx: RuntimeContext):
    clear_temp_dir(ctx.temp_dir)
    slide_bg = ctx.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="slideBg"]')))
    img1_style = slide_bg.get_attribute("style")
    img1_url = get_url_from_style(img1_style)
    logger.info("开始下载验证码图片(1): " + img1_url)
    # 修复：检查下载是否成功
    if not download_image(img1_url, temp_path(ctx, "captcha.jpg")):
        raise CaptchaRetryableError("验证码背景图下载失败")
    sprite = ctx.wait.until(EC.visibility_of_element_located((By.XPATH, '//*[@id="instruction"]/div/img')))
    img2_url = sprite.get_attribute("src")
    logger.info("开始下载验证码图片(2): " + img2_url)
    # 修复：检查下载是否成功
    if not download_image(img2_url, temp_path(ctx, "sprite.jpg")):
        raise CaptchaRetryableError("验证码小图下载失败")


def check_captcha(ctx: RuntimeContext) -> bool:
    raw = cv2.imread(temp_path(ctx, "sprite.jpg"))
    # 修复：检查图片是否成功读取
    if raw is None:
        logger.error("验证码小图读取失败，可能下载不完整")
        return False
    for i in range(3):
        w = raw.shape[1]
        temp = raw[:, w // 3 * i: w // 3 * (i + 1)]
        cv2.imwrite(temp_path(ctx, f"sprite_{i + 1}.jpg"), temp)
        with open(temp_path(ctx, f"sprite_{i + 1}.jpg"), mode="rb") as f:
            temp_rb = f.read()
        if ctx.ocr.classification(temp_rb) in ["0", "1"]:
            return False
    return True


# 检查是否存在重复坐标,快速判断识别错误
def check_answer(d: dict) -> bool:
    # 修复：空字典或不完整结果直接返回 False
    # 需要 3 个 sprite 的 similarity + position = 6 个键
    if not d or len(d) < 6:
        logger.warning(f"验证码识别结果不完整，当前仅有 {len(d)} 个键，预期至少 6 个")
        return False
    flipped = dict()
    for key in d.keys():
        flipped[d[key]] = key
    return len(d.values()) == len(flipped.keys())


def compute_similarity(img1_path, img2_path):
    img1 = cv2.imread(img1_path, cv2.IMREAD_GRAYSCALE)
    img2 = cv2.imread(img2_path, cv2.IMREAD_GRAYSCALE)

    sift = cv2.SIFT_create()
    kp1, des1 = sift.detectAndCompute(img1, None)
    kp2, des2 = sift.detectAndCompute(img2, None)

    if des1 is None or des2 is None:
        return 0.0, 0

    bf = cv2.BFMatcher()
    matches = bf.knnMatch(des1, des2, k=2)

    good = [m for m_n in matches if len(m_n) == 2 for m, n in [m_n] if m.distance < 0.8 * n.distance]

    if len(good) == 0:
        return 0.0, 0

    similarity = len(good) / len(matches)
    return similarity, len(good)


def run_single_account(user, pwd, account_index=None):
    """执行单个账号签到"""
    ctx = None
    driver = None
    temp_dir = None
    debug = False
    try:
        # 从环境变量读取配置
        timeout = int(os.environ.get("TIMEOUT", "15"))
        max_delay = int(os.environ.get("MAX_DELAY", "90"))
        # GitHub Action 无状态
        debug = True
        # GitHub Actions 环境一定是linux
        linux = True

        if not user or not pwd:
            logger.error(f"账号 {account_index} 配置缺失，跳过")
            return False

        tag = f"账号{account_index}" if account_index else ""
        logger.info(f"━━━━━━ 雨云签到 v{APP_VERSION} {tag} ━━━━━━")

        delay = random.randint(0, max_delay)
        delay_sec = random.randint(0, 60)
        # 可选：随机延迟（可用于避免集中请求）
        # logger.info(f"随机延时等待 {delay} 分钟 {delay_sec} 秒")
        # time.sleep(delay * 60 + delay_sec)
        
        logger.info("初始化 ddddocr")
        ocr = ddddocr.DdddOcr(ocr=True, show_ad=False)
        det = ddddocr.DdddOcr(det=True, show_ad=False)
        
        logger.info("初始化 Selenium")
        driver = init_selenium(debug=debug, linux=linux)
        
        # 过 Selenium 检测
        with open("stealth.min.js", mode="r") as f:
            js = f.read()
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": js
        })
        
        wait = WebDriverWait(driver, timeout)
        temp_dir = tempfile.mkdtemp(prefix="rainyun-")
        ctx = RuntimeContext(
            driver=driver,
            wait=wait,
            ocr=ocr,
            det=det,
            temp_dir=temp_dir
        )

        # 进行登录
        logged_in = do_login(ctx, user, pwd)
        if not logged_in:
            logger.error("登录失败，任务终止")
            return

        logger.info("正在转到赚取积分页")
        ctx.driver.get(build_app_url("/account/reward/earn"))

        # 获取签到前的积分
        before_points = None
        try:
            points_raw = ctx.wait.until(EC.visibility_of_element_located((By.XPATH,
                '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[1]/div[1]/div/p/div/h3'))).get_attribute("textContent")
            before_points = int(''.join(re.findall(r'\d+', points_raw)))
            logger.info(f"签到前积分: {before_points}")
        except Exception:
            pass

        # 检查签到状态：先找"领取奖励"按钮，找不到就检查是否已签到
        try:
            # 使用显示等待寻找按钮
            earn = ctx.wait.until(EC.presence_of_element_located((By.XPATH,
                                       "//span[contains(text(), '每日签到')]/ancestor::div[1]//a[contains(text(), '领取奖励')]")))
            logger.info("点击赚取积分")
            earn.click()
        except TimeoutException:
            # 检查是否已经签到（按钮可能显示"已领取"、"已完成"等）
            already_signed_patterns = ['已领取', '已完成', '已签到', '明日再来']
            page_source = ctx.driver.page_source
            for pattern in already_signed_patterns:
                if pattern in page_source:
                    logger.info(f"今日已签到（检测到：{pattern}），跳过签到流程")
                    # 直接跳到获取积分信息
                    try:
                        points_raw = ctx.wait.until(EC.visibility_of_element_located((By.XPATH,
                            '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[1]/div[1]/div/p/div/h3'))).get_attribute("textContent")
                        after_points = int(''.join(re.findall(r'\d+', points_raw)))
                        earned = after_points - before_points if before_points else 0
                        logger.info(f"当前剩余积分: {after_points} | 已签到(+{earned}) | 约为 {after_points / POINTS_TO_CNY_RATE:.2f} 元")
                    except Exception:
                        logger.info("无法获取当前积分信息")
                    return
            # 如果既没找到领取按钮，也没检测到已签到，说明页面结构可能变了
            raise Exception("未找到签到按钮，且未检测到已签到状态，可能页面结构已变更")
        
        logger.info("处理验证码")
        try:
            ctx.wait.until(EC.frame_to_be_available_and_switch_to_it((By.ID, "tcaptcha_iframe_dy")))
        except TimeoutException:
            logger.error("验证码 iframe 加载超时")
            ctx.driver.switch_to.default_content()
            raise
        captcha_retry_count = [0]
        if not process_captcha(ctx, captcha_retry_count):
            failed = captcha_retry_count[0]
            logger.error(f"验证码重试 {failed} 次后失败，任务终止。当前页面状态: {ctx.driver.current_url}")
            raise Exception(f"验证码重试 {failed} 次后失败，签到失败")
        
        ctx.driver.switch_to.default_content()
        
        # 等待积分显示
        time.sleep(3)
        
        try:
            points_raw = ctx.wait.until(EC.visibility_of_element_located((By.XPATH,
                                     '//*[@id="app"]/div[1]/div[3]/div[2]/div/div/div[2]/div[1]/div[1]/div/p/div/h3'))).get_attribute(
                "textContent")
            after_points = int(''.join(re.findall(r'\d+', points_raw)))
            earned = after_points - before_points if before_points else 0
            logger.info(f"当前剩余积分: {after_points} | +{earned} | 约为 {after_points / POINTS_TO_CNY_RATE:.2f} 元")
        except Exception as e:
            logger.warning(f"获取积分信息失败: {e}")
        
        logger.info("任务执行成功！")
        
    except Exception as e:
        logger.error(f"脚本执行异常终止: {e}")

    finally:
        # === 清理资源 ===
        
        # 1. 关闭浏览器
        if driver:
            try:
                driver.quit()
            except Exception:
                pass

        # 2. 获取所有日志内容
        try:
            logger.removeHandler(log_capture_handler)
        except Exception:
            pass
        log_content = log_capture_string.getvalue()

        # 3. 提取关键结果，只推送摘要（按账号过滤，避免多账号日志混淆）
        tag = f"账号{account_index}" if account_index else ""
        in_target = False
        summary_lines = []
        for line in log_content.splitlines():
            # 遇到目标账号的头部开始记录，或在单账号/非多账号场景下记录所有关键行
            if tag and f"━━━━━━ 雨云签到 v{APP_VERSION} {tag} ━━━━━━" in line:
                in_target = True
            elif tag and f"━━━━━━ 雨云签到 v{APP_VERSION} 账号" in line and f"{tag} ━" not in line:
                in_target = False
            # 多账号：跳过非目标账号的行；单账号：跳过头部行
            if (tag and not in_target) or (not tag and "━━━━━━ 雨云签到 v" in line):
                continue
            if any(kw in line for kw in [
                "雨云签到 v",
                "登录成功",
                "登录失败",
                "今日已签到",
                "验证码通过",
                "验证码重试 ",
                "当前剩余积分",
                "任务执行成功",
                "脚本执行异常",
                "签到失败",
            ]):
                summary_lines.append(line)

        summary = "\n".join(summary_lines) if summary_lines else "签到流程结束，详见日志"
        # 清理
        if temp_dir and not debug:
            shutil.rmtree(temp_dir, ignore_errors=True)
        # 不再逐账号发送，改为汇总后统一发送（由 run() 汇总）
        return summary


def run():
    """支持多账号签到，优先从 RAINYUN_ACCOUNTS 读取（格式: user1|pwd1&user2|pwd2）"""
    # 方式1：单个变量多账号 (推荐)
    accounts_str = os.environ.get("RAINYUN_ACCOUNTS", "").strip()
    
    if accounts_str:
        accounts = []
        for pair in accounts_str.split("&"):
            parts = pair.split("|", 1)
            if len(parts) == 2:
                accounts.append((parts[0].strip(), parts[1].strip()))
        logger.info(f"RAINYUN_ACCOUNTS: 共 {len(accounts)} 个账号")
    else:
        # 方式2：兼容旧配置 (ACCOUNT_COUNT + 编号secrets)
        account_count = int(os.environ.get("ACCOUNT_COUNT", "0"))
        accounts = []
        for i in range(1, account_count + 1):
            u = os.environ.get(f"RAINYUN_USER_{i}", "")
            p = os.environ.get(f"RAINYUN_PWD_{i}", "")
            if u and p:
                accounts.append((u, p))
        # 方式3：单个账号兼容 (RAINYUN_USER + RAINYUN_PWD)
        if not accounts:
            u = os.environ.get("RAINYUN_USER", "")
            p = os.environ.get("RAINYUN_PWD", "")
            if u and p:
                accounts = [(u, p)]
        logger.info(f"兼容模式: 共 {len(accounts)} 个账号")
    
    if not accounts:
        logger.error("未找到任何账号配置，请设置 RAINYUN_ACCOUNTS 环境变量")
        return
    
    all_summaries = []
    success_count = 0
    for idx, (user, pwd) in enumerate(accounts, 1):
        if idx > 1:
            delay = random.randint(5, 15)
            logger.info(f"等待 {delay} 秒后处理下一个账号...")
            time.sleep(delay)
        try:
            result = run_single_account(user, pwd, account_index=idx if len(accounts) > 1 else None)
            if result and result is not True:
                # result 是摘要字符串
                label = f"账号{idx}" if len(accounts) > 1 else ""
                if label:
                    all_summaries.append(f"【{label}】\n{result}")
                else:
                    all_summaries.append(result)
                success_count += 1
            elif result is True:
                success_count += 1
            else:
                label = f"账号{idx}" if len(accounts) > 1 else ""
                all_summaries.append(f"【{label}】签到失败")
        except Exception as e:
            logger.error(f"账号 {idx} 执行异常: {e}")
            label = f"账号{idx}" if len(accounts) > 1 else ""
            all_summaries.append(f"【{label}】执行异常: {e}")
    
    # 汇总发送一条通知
    combined = "\n\n".join(all_summaries) if all_summaries else "无签到结果"
    title = f"雨云签到 {success_count}/{len(accounts)}成功"
    logger.info(f"签到完成: {success_count}/{len(accounts)} 个账号成功，发送汇总通知")
    send(title, combined)


if __name__ == "__main__":
    run()