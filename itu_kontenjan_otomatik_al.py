# -*- coding: utf-8 -*-
"""
İTÜ OBS / Kepler - Tek Dosyalık Kontenjan Takip + Otomatik Ders Alma
====================================================================

Ne yapar?
1) İTÜ public ders programından HEDEFLER listesindeki CRN'leri takip eder.
2) İlk gördüğü kontenjanı başlangıç değeri olarak kaydeder.
3) Kontenjan başlangıç değerinin üstüne çıkarsa (ve istersen boş yer oluşursa),
   Kepler hesabına girişte elde edilen Authorization tokenı ile o CRN'yi almaya çalışır.
4) Başarıyla alınan CRN'yi takipten çıkarır.

Gereken paketler:
    pip install requests beautifulsoup4 selenium python-dotenv

Çalıştırma:
    python itu_kontenjan_otomatik_al.py

Notlar:
- Chrome bilgisayarda kurulu olmalı. Selenium 4, ChromeDriver'ı Selenium Manager ile yönetir.
- CAPTCHA/MFA gibi ek doğrulama çıkarsa program bunu aşmaya çalışmaz. Tarayıcı görünürken
  normal şekilde tamamlamanız gerekir.
- Kullanıcı adı ve şifre Python dosyasına yazılmaz. Aynı klasördeki .env dosyasından okunur.
- .env dosyasında ITU_USERNAME ve ITU_PASSWORD değerlerini tanımlayın.
"""

from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from dotenv import load_dotenv
except ImportError:
    print("Eksik paket. Önce şu komutu çalıştırın:")
    print("    pip install requests beautifulsoup4 selenium python-dotenv")
    sys.exit(1)


# ============================================================================
# AYARLAR - esas olarak sadece bu bölümü düzenleyin
# ============================================================================

# Giriş bilgileri bu Python dosyasına yazılmaz.
# Script ile aynı klasördeki .env dosyası otomatik okunur.
ENV_DOSYASI = Path(__file__).resolve().with_name(".env")
load_dotenv(dotenv_path=ENV_DOSYASI)

ITU_USERNAME = os.getenv("ITU_USERNAME", "").strip()
ITU_PASSWORD = os.getenv("ITU_PASSWORD", "")

# Takip edilecek dersler.
# crnler boş [] olursa o ders kodunun bulunan bütün şubeleri takip edilir.
HEDEFLER = [
    {"ders": "ATA121",  "brans": "ATA", "crnler": ["10134"]},
     {"ders": "SNT211E",  "brans": "SNT", "crnler": ["14638"]}
]

SEVIYE = "LS"                 # LS=Lisans, OL=Önlisans, LU=Lisansüstü
KONTROL_ARALIGI = 30          # Public ders programını kaç saniyede bir kontrol etsin (min 15)
BOS_YERDE_DE_TETIKLE = False  # True: kontenjan artmasa bile yazılan < kontenjan olunca dene

# Kontenjan açıldığında Kepler'e kaç kez peş peşe deneme yapılsın?
KAYIT_DENEME_SAYISI = 3
KAYIT_DENEME_ARALIGI = 3      # saniye

# Tokenı Chrome üzerinden ne sıklıkta yenilemeye çalışsın?
TOKEN_YENILEME_ARALIGI = 60

# False önerilir: giriş sırasında Chrome'u görürsünüz ve gerekirse MFA/CAPTCHA'yı elle tamamlarsınız.
# Her şeyin sorunsuz çalıştığını doğruladıktan sonra True deneyebilirsiniz.
HEADLESS = False

# ============================================================================

PUBLIC_BASE = "https://obs.itu.edu.tr/public/DersProgram"
BRANS_URL = PUBLIC_BASE + "/SearchBransKoduByProgramSeviye"
ARAMA_URL = PUBLIC_BASE + "/DersProgramSearch"

KEPLER_DERS_SAYFASI = "https://obs.itu.edu.tr/ogrenci/DersKayitIslemleri/DersKayit"
KEPLER_DERS_KAYIT_API = "https://obs.itu.edu.tr/api/ders-kayit/v21/"
KEPLER_TOKEN_ISTEGI = "https://obs.itu.edu.tr/api/ogrenci/Takvim/KayitZamaniKontrolu"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36"
)

PUBLIC_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": PUBLIC_BASE,
    "X-Requested-With": "XMLHttpRequest",
}

public_session = requests.Session()
public_session.headers.update(PUBLIC_HEADERS)

# Kepler'ın bilinen dönüş kodlarından bazıları.
SUCCESS_CODES = {"successResult", "Ekleme İşlemi Başarılı"}
RETRY_CODES = {
    "VAL01", "VAL02", "VAL06", "VAL13", "VAL14", "VAL16",
    "ERRLoad", "NULLParam-CheckOgrenciKayitZamaniKontrolu", "Kontenjan Dolu",
}
TIMEOUT_CODES = {"VAL21"}  # Repo açıklamasına göre istek limiti / 1 saatlik engel.

RESULT_MESSAGES = {
    "successResult": "işlem başarıyla tamamlandı",
    "Ekleme İşlemi Başarılı": "ekleme işlemi başarılı",
    "VAL01": "geçici/genel bir problem oluştu",
    "VAL02": "kayıt zamanı uygun değil",
    "VAL03": "ders bu dönem zaten alınmış",
    "VAL04": "ders planında yer almıyor",
    "VAL05": "maksimum kredi sınırı aşılıyor",
    "VAL06": "kontenjan yetersiz",
    "VAL07": "ders daha önce AA ile verilmiş",
    "VAL08": "program şartı sağlanmıyor",
    "VAL09": "başka bir dersle çakışıyor",
    "VAL11": "önşart sağlanmıyor",
    "VAL12": "ders bu dönemde açılmamış",
    "VAL13": "geçici engel",
    "VAL14": "sistem geçici olarak yanıt vermiyor",
    "VAL15": "maksimum CRN sayısı aşılıyor",
    "VAL16": "aktif bir işlem devam ediyor",
    "VAL18": "CRN engellenmiş",
    "VAL19": "önlisans dersi",
    "CRNListEmpty": "CRN listesi boş",
    "CRNNotFound": "CRN bulunamadı",
    "ERRLoad": "sistem geçici olarak yanıt vermiyor",
    "NULLParam-CheckOgrenciKayitZamaniKontrolu": "kayıt zamanı uygun değil",
    "Kontenjan Dolu": "kontenjan dolu",
    "VAL21": "istek limiti aşıldı / geçici ders seçim engeli",
    "VAL22": "ders yükseltmeye uygun değil",
}


def log(mesaj: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {mesaj}", flush=True)


def normalize(metin: str | None) -> str:
    return re.sub(r"\s+", "", metin or "").upper()


def sayi(metin: str | None) -> int | None:
    m = re.search(r"-?\d+", metin or "")
    return int(m.group()) if m else None


# ============================================================================
# PUBLIC OBS KONTENJAN TAKİBİ
# ============================================================================

def brans_id_bul(brans_kodu: str) -> str | int:
    r = public_session.get(
        BRANS_URL,
        params={"programSeviyeTipiAnahtari": SEVIYE},
        timeout=20,
    )
    r.raise_for_status()
    liste = r.json()

    for eleman in liste:
        kod = eleman.get("dersBransKodu") or eleman.get("DersBransKodu") or ""
        if normalize(kod) == normalize(brans_kodu):
            bulunan = eleman.get("bransKoduId") or eleman.get("BransKoduId")
            if bulunan is not None:
                return bulunan

    raise RuntimeError(f"'{brans_kodu}' branş kodu listede bulunamadı.")


def ders_satirlarini_getir(brans_id: str | int, hedefler: list[dict]) -> list[dict]:
    r = public_session.get(
        ARAMA_URL,
        params={
            "programSeviyeTipiAnahtari": SEVIYE,
            "dersBransKoduId": brans_id,
        },
        timeout=20,
    )
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    tum_satirlar = soup.find_all("tr")
    baslik_idx = None
    basliklar: list[str] = []

    for idx, tr in enumerate(tum_satirlar):
        hucre_metinleri = [
            c.get_text(" ", strip=True).lower()
            for c in tr.find_all(["th", "td"])
        ]
        if any(h == "crn" or h.startswith("crn") for h in hucre_metinleri):
            baslik_idx = idx
            basliklar = hucre_metinleri
            break

    if baslik_idx is None:
        debug_yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "debug_sayfa.html")
        with open(debug_yol, "w", encoding="utf-8") as f:
            f.write(r.text)
        raise RuntimeError(f"OBS tablo başlığı bulunamadı. Gelen sayfa: {debug_yol}")

    def sutun(*adaylar: str) -> int | None:
        for i, baslik in enumerate(basliklar):
            if any(aday in baslik for aday in adaylar):
                return i
        return None

    i_crn = sutun("crn")
    i_kod = sutun("ders kodu", "course code")
    i_kont = sutun("kontenjan", "capacity")
    i_yaz = sutun("yazılan", "yazilan", "enrolled")
    i_hoca = sutun("eğitmen", "egitmen", "instructor")

    if None in (i_crn, i_kod, i_kont):
        raise RuntimeError(f"Gerekli sütunlar bulunamadı. Başlıklar: {basliklar}")

    sonuc: list[dict] = []

    for tr in tum_satirlar[baslik_idx + 1:]:
        hucreler = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(hucreler) <= max(i_crn, i_kod, i_kont):
            continue

        kod = normalize(hucreler[i_kod])
        crn = hucreler[i_crn].strip()

        eslesen = None
        for h in hedefler:
            if kod == normalize(h["ders"]) and (not h["crnler"] or crn in h["crnler"]):
                eslesen = h
                break

        if eslesen is None:
            continue

        sonuc.append({
            "ders": eslesen["ders"],
            "crn": crn,
            "kontenjan": sayi(hucreler[i_kont]),
            "yazilan": sayi(hucreler[i_yaz]) if i_yaz is not None else None,
            "hoca": hucreler[i_hoca] if i_hoca is not None else "",
        })

    return sonuc


def tablo_yaz(satirlar: list[dict]) -> None:
    for s in satirlar:
        bos = ""
        if s["kontenjan"] is not None and s["yazilan"] is not None:
            bos = f" | boş: {s['kontenjan'] - s['yazilan']}"
        log(
            f"   {s['ders']:<8} CRN {s['crn']:>6} | "
            f"kontenjan: {s['kontenjan']} | yazılan: {s['yazilan']}"
            f"{bos} | {s['hoca']}"
        )


# ============================================================================
# KEPLER GİRİŞİ + TOKEN YÖNETİMİ
# ============================================================================

class KeplerTokenManager(threading.Thread):
    def __init__(self, username: str, password: str, headless: bool = False):
        super().__init__(daemon=True)
        self.username = username
        self.password = password
        self.headless = headless
        self.driver = None
        self._token = ""
        self._token_lock = threading.Lock()
        self._first_token = threading.Event()
        self._stop_event = threading.Event()
        self.error: Exception | None = None

    def get_token(self) -> str:
        with self._token_lock:
            return self._token

    def wait_for_first_token(self, timeout: float = 120) -> bool:
        return self._first_token.wait(timeout)

    def stop(self) -> None:
        self._stop_event.set()

    def _create_driver(self):
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--log-level=3")
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
        if self.headless:
            options.add_argument("--headless=new")

        # Selenium 4'ün kendi Selenium Manager'ı uygun ChromeDriver'ı yönetir.
        driver = webdriver.Chrome(options=options)
        driver.execute_cdp_cmd("Network.enable", {})
        return driver

    def _login(self) -> None:
        log("Kepler açılıyor...")
        self.driver.get(KEPLER_DERS_SAYFASI)
        time.sleep(3)

        if "girisv3.itu.edu.tr" not in self.driver.current_url:
            log("Kepler oturumu zaten açık görünüyor.")
            return

        log("Kepler giriş ekranı algılandı. Giriş yapılıyor...")

        # Kullanıcı adı ve şifre alanlarını türlerine göre bulmaya çalış.
        inputs = self.driver.find_elements(By.TAG_NAME, "input")
        visible = [x for x in inputs if x.is_displayed() and x.get_attribute("type") != "hidden"]

        password_input = next(
            (x for x in visible if (x.get_attribute("type") or "").lower() == "password"),
            None,
        )
        username_input = next(
            (
                x for x in visible
                if (x.get_attribute("type") or "").lower() in ("text", "email", "")
                and x is not password_input
            ),
            None,
        )

        if username_input is None or password_input is None:
            raise RuntimeError(
                "Giriş ekranındaki kullanıcı adı/şifre alanları bulunamadı. "
                "İTÜ giriş sayfasının yapısı değişmiş olabilir."
            )

        username_input.clear()
        username_input.send_keys(self.username)
        password_input.clear()
        password_input.send_keys(self.password)

        submit = None
        for selector in ("button[type='submit']", "input[type='submit']", "button"):
            elems = self.driver.find_elements(By.CSS_SELECTOR, selector)
            submit = next((e for e in elems if e.is_displayed() and e.is_enabled()), None)
            if submit:
                break

        if submit:
            submit.click()
        else:
            password_input.send_keys(Keys.ENTER)

        # Kullanıcıya MFA/CAPTCHA gibi normal güvenlik adımlarını elle tamamlama fırsatı ver.
        son = time.time() + 120
        while time.time() < son:
            time.sleep(1)
            url = self.driver.current_url
            if "SelectIdentity" in url:
                self._select_active_identity()
                break
            if "girisv3.itu.edu.tr" not in url:
                break

        if "girisv3.itu.edu.tr" in self.driver.current_url:
            raise RuntimeError(
                "Kepler girişi 120 saniye içinde tamamlanamadı. "
                "Tarayıcıda MFA/CAPTCHA veya giriş hatası olup olmadığını kontrol edin."
            )

        if "SelectIdentity" in self.driver.current_url:
            self._select_active_identity()

        log("Kepler girişi tamamlandı.")
        self.driver.get(KEPLER_DERS_SAYFASI)
        time.sleep(3)

    def _select_active_identity(self) -> None:
        log("Birden fazla öğrenci kimliği ekranı algılandı; aktif olan seçilmeye çalışılıyor...")
        time.sleep(1)
        cards = self.driver.find_elements(By.CLASS_NAME, "card-body")
        for card in cards:
            rows = card.find_elements(By.TAG_NAME, "tr")
            if not rows:
                continue
            content = rows[-1].get_attribute("innerHTML").lower()
            if "durum" in content and "aktif" in content:
                links = card.find_elements(By.TAG_NAME, "a")
                if links:
                    links[0].click()
                    time.sleep(1)
                    log("Aktif öğrenci kimliği seçildi.")
                    return
        log("Aktif kimlik otomatik bulunamadı; tarayıcıdan manuel seçim gerekebilir.")

    def _performance_logdan_token_bul(self) -> str:
        # get_log çağrısı mevcut performance kayıtlarını tüketir.
        for entry in self.driver.get_log("performance"):
            try:
                msg = json.loads(entry["message"])["message"]
                if msg.get("method") != "Network.requestWillBeSent":
                    continue
                request = msg.get("params", {}).get("request", {})
                url = request.get("url", "")
                if KEPLER_TOKEN_ISTEGI not in url:
                    continue
                headers = request.get("headers", {})
                token = headers.get("Authorization") or headers.get("authorization")
                if token:
                    return token
            except Exception:
                continue
        return ""

    def _fetch_token_once(self) -> str:
        # Eski logları boşalt.
        try:
            self.driver.get_log("performance")
        except Exception:
            pass

        # Oturum düşmüşse yeniden giriş yap.
        if "girisv3.itu.edu.tr" in self.driver.current_url:
            self._login()

        if KEPLER_DERS_SAYFASI not in self.driver.current_url:
            self.driver.get(KEPLER_DERS_SAYFASI)
            time.sleep(2)

        self.driver.refresh()

        # Sayfanın token taşıyan zaman kontrol isteğini atmasını bekle.
        son = time.time() + 15
        while time.time() < son:
            time.sleep(0.5)

            if "girisv3.itu.edu.tr" in self.driver.current_url:
                log("Kepler oturumu düşmüş; yeniden giriş yapılıyor...")
                self._login()
                self.driver.refresh()
                time.sleep(1)

            token = self._performance_logdan_token_bul()
            if token:
                return token

        return ""

    def run(self) -> None:
        try:
            self.driver = self._create_driver()
            self._login()

            while not self._stop_event.is_set():
                try:
                    token = self._fetch_token_once()
                    if token:
                        with self._token_lock:
                            degisti = token != self._token
                            self._token = token
                        if not self._first_token.is_set():
                            self._first_token.set()
                            log("İlk Kepler API tokenı alındı.")
                        elif degisti:
                            log("Kepler API tokenı yenilendi.")
                    else:
                        log("Token bu turda bulunamadı; yeniden denenecek.")
                except Exception as e:
                    log(f"Token yenileme hatası: {e}")

                self._stop_event.wait(TOKEN_YENILEME_ARALIGI)

        except Exception as e:
            self.error = e
            log(f"Kepler/token başlatma hatası: {e}")
        finally:
            if self.driver is not None:
                try:
                    self.driver.quit()
                except Exception:
                    pass


# ============================================================================
# DERS ALMA İSTEĞİ
# ============================================================================

def ders_al(token_manager: KeplerTokenManager, crn: str) -> tuple[str, str]:
    """
    Dönüş:
        ("success", kod)   -> ders alındı
        ("retry", kod)     -> geçici hata; sonra tekrar denenebilir
        ("rate_limit",kod) -> VAL21; 1 saat beklemek mantıklı
        ("permanent", kod) -> kalıcı/iş kuralı hatası
        ("unknown", metin) -> cevap beklenmedik
    """
    token = token_manager.get_token()
    if not token:
        return "retry", "TOKEN_YOK"

    headers = {
        "Authorization": token,
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
    }

    try:
        r = requests.post(
            KEPLER_DERS_KAYIT_API,
            headers=headers,
            json={"ECRN": [str(crn)], "SCRN": []},
            timeout=20,
        )
    except requests.RequestException as e:
        log(f"CRN {crn} kayıt isteği ağ hatası: {e}")
        return "retry", "NETWORK"

    try:
        data = r.json()
    except Exception:
        log(f"CRN {crn} için JSON olmayan cevap (HTTP {r.status_code}): {r.text[:300]!r}")
        return "unknown", r.text[:100]

    results = data.get("ecrnResultList") or []
    if not results:
        log(f"CRN {crn} için beklenmedik cevap: {data}")
        return "unknown", str(data)[:100]

    result = results[0]
    result_crn = str(result.get("crn", crn))
    code = str(result.get("resultCode"))
    aciklama = RESULT_MESSAGES.get(code, "bilinmeyen sonuç kodu")
    log(f"Kepler sonucu | CRN {result_crn} | {code}: {aciklama}")

    if code in SUCCESS_CODES:
        return "success", code
    if code in TIMEOUT_CODES:
        return "rate_limit", code
    if code in RETRY_CODES:
        return "retry", code
    return "permanent", code


def dersi_deneyerek_al(token_manager: KeplerTokenManager, crn: str) -> tuple[bool, bool]:
    """
    Dönüş: (başarılı_mı, rate_limit_var_mı)
    """
    for deneme in range(1, KAYIT_DENEME_SAYISI + 1):
        log(f">>> CRN {crn} ALINMAYA ÇALIŞILIYOR ({deneme}/{KAYIT_DENEME_SAYISI})")
        durum, kod = ders_al(token_manager, crn)

        if durum == "success":
            log(f"✅ CRN {crn} BAŞARIYLA ALINDI.")
            uyari_sesi()
            return True, False

        if durum == "rate_limit":
            log("⛔ İstek limiti/VAL21 algılandı. 1 saat yeni ders kayıt isteği gönderilmeyecek.")
            return False, True

        if durum == "permanent":
            log(f"⚠️ CRN {crn} kalıcı bir nedenle alınamadı ({kod}). Otomatik tekrar durduruldu.")
            return False, False

        if deneme < KAYIT_DENEME_SAYISI:
            time.sleep(KAYIT_DENEME_ARALIGI)

    log(f"CRN {crn} bu turda alınamadı; takip devam edecek.")
    return False, False


def uyari_sesi() -> None:
    if os.name == "nt":
        try:
            import winsound
            for _ in range(3):
                winsound.Beep(1500, 250)
        except Exception:
            pass


# ============================================================================
# ANA PROGRAM
# ============================================================================

def main() -> None:
    username = ITU_USERNAME
    password = ITU_PASSWORD

    if not ENV_DOSYASI.exists():
        log(f".env dosyası bulunamadı: {ENV_DOSYASI}")
        log("Python dosyasıyla aynı klasörde .env oluşturup ITU_USERNAME ve ITU_PASSWORD yazın.")
        return

    if not username or not password:
        log(".env içinde ITU_USERNAME veya ITU_PASSWORD boş/eksik. Program durduruldu.")
        return

    aralik = max(15, int(KONTROL_ARALIGI))
    dersler = ", ".join(
        h["ders"] + (f" ({','.join(h['crnler'])})" if h["crnler"] else "")
        for h in HEDEFLER
    )

    log(f"Takip edilecek dersler: {dersler}")
    log(f"Kontrol aralığı: {aralik} sn")
    log("Kepler oturumu hazırlanıyor. Chrome penceresini program çalışırken kapatmayın.")

    token_manager = KeplerTokenManager(username, password, headless=HEADLESS)
    token_manager.start()

    if not token_manager.wait_for_first_token(timeout=150):
        token_manager.stop()
        if token_manager.error:
            log(f"İlk token alınamadı: {token_manager.error}")
        else:
            log("İlk token 150 saniye içinde alınamadı. Chrome penceresini/giriş ekranını kontrol edin.")
        return

    log("Kepler hazır. Kontenjan takibi başlıyor.")

    branslar: dict[str, list[dict]] = {}
    for h in HEDEFLER:
        branslar.setdefault(h["brans"].upper(), []).append(h)

    brans_idleri: dict[str, str | int] = {}
    baslangic: dict[tuple[str, str], int | None] = {}
    tamamlanan_crnler: set[str] = set()
    kalici_hata_crnler: set[str] = set()
    hata_sayisi = 0
    rate_limit_until = 0.0

    try:
        while True:
            try:
                satirlar: list[dict] = []

                for brans, hedefler in branslar.items():
                    aktif_hedefler = []
                    for h in hedefler:
                        # Dersin takip edilen tüm CRN'leri tamamlandı/kalıcı hata ise public sorguda
                        # yine ders satırı gelebilir; satır seviyesinde aşağıda filtrelenir.
                        aktif_hedefler.append(h)

                    if brans not in brans_idleri:
                        brans_idleri[brans] = brans_id_bul(brans)
                        log(f"{brans} branş ID = {brans_idleri[brans]}")

                    satirlar.extend(ders_satirlarini_getir(brans_idleri[brans], aktif_hedefler))

                # Zaten işi bitmiş CRN'leri değerlendirmeye sokma.
                satirlar = [
                    s for s in satirlar
                    if s["crn"] not in tamamlanan_crnler
                    and s["crn"] not in kalici_hata_crnler
                ]

                hata_sayisi = 0

                if not baslangic:
                    for s in satirlar:
                        baslangic[(s["ders"], s["crn"])] = s["kontenjan"]
                    log("Başlangıç kontenjanları kaydedildi:")
                    tablo_yaz(satirlar)
                else:
                    tetikler: list[tuple[dict, str]] = []

                    for s in satirlar:
                        anahtar = (s["ders"], s["crn"])
                        ilk = baslangic.get(anahtar)

                        # Sonradan görünür hale gelen yeni şube.
                        if anahtar not in baslangic:
                            baslangic[anahtar] = s["kontenjan"]
                            log(
                                f"Yeni hedef şube görüldü: {s['ders']} CRN {s['crn']} "
                                f"(kontenjan {s['kontenjan']}). Başlangıç değeri kaydedildi."
                            )
                            continue

                        if (
                            ilk is not None
                            and s["kontenjan"] is not None
                            and s["kontenjan"] > ilk
                        ):
                            tetikler.append(
                                (s, f"kontenjan arttı: {ilk} -> {s['kontenjan']}")
                            )
                            continue

                        if (
                            BOS_YERDE_DE_TETIKLE
                            and s["kontenjan"] is not None
                            and s["yazilan"] is not None
                            and s["kontenjan"] > s["yazilan"]
                        ):
                            tetikler.append(
                                (s, f"boş yer var: {s['yazilan']}/{s['kontenjan']}")
                            )

                    if tetikler:
                        # Aynı CRN aynı turda iki sebeple gelirse bir kez dene.
                        unique: dict[str, tuple[dict, str]] = {}
                        for s, sebep in tetikler:
                            unique.setdefault(s["crn"], (s, sebep))

                        for crn, (s, sebep) in unique.items():
                            log(f"🚨 {s['ders']} CRN {crn}: {sebep}")

                            if time.time() < rate_limit_until:
                                kalan = int(rate_limit_until - time.time())
                                log(f"Rate-limit beklemesi aktif; yaklaşık {kalan} sn sonra tekrar denenebilir.")
                                continue

                            basarili, rate_limited = dersi_deneyerek_al(token_manager, crn)

                            if basarili:
                                tamamlanan_crnler.add(crn)
                            elif rate_limited:
                                rate_limit_until = time.time() + 60 * 60
                            else:
                                # Kalıcı hata olup olmadığını son çağrıdan doğrudan bilmiyoruz;
                                # dersi_deneyerek_al kalıcı hatada anında döner ama takipte kalması
                                # kullanıcı açısından daha güvenli. Bir sonraki kontenjan turunda tekrar
                                # deneyebilir. Kalıcı hatayı listeden çıkarmak isterseniz burada eklenebilir.
                                pass
                    else:
                        ozet = ", ".join(
                            f"{s['ders']}/{s['crn']}:{s['yazilan']}/{s['kontenjan']}"
                            for s in satirlar
                        )
                        log(f"Değişiklik yok [{ozet}]")

                # Tüm açıkça hedeflenmiş CRN'ler başarıyla alındıysa bitir.
                tanimli_crnler = {
                    str(crn)
                    for h in HEDEFLER
                    for crn in h.get("crnler", [])
                }
                if tanimli_crnler and tanimli_crnler.issubset(tamamlanan_crnler):
                    log("✅ Tanımlı bütün CRN'ler başarıyla alındı. Program sonlandırılıyor.")
                    break

            except KeyboardInterrupt:
                raise
            except Exception as e:
                hata_sayisi += 1
                log(f"Kontenjan takip hatası ({hata_sayisi}): {e}")
                if hata_sayisi >= 3:
                    brans_idleri.clear()

            bekle = min(aralik * (2 ** min(hata_sayisi, 3)), 300) if hata_sayisi else aralik
            time.sleep(bekle)

    except KeyboardInterrupt:
        log("Kullanıcı tarafından durduruldu (Ctrl+C).")
    finally:
        token_manager.stop()
        token_manager.join(timeout=10)
        log("Program kapatıldı.")


if __name__ == "__main__":
    main()
