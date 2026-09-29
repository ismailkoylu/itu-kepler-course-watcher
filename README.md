# İTÜ Course Watcher

İTÜ OBS üzerindeki ders kontenjanlarını takip eden ve hedeflenen bir CRN'de yer açıldığında Kepler üzerinden otomatik ders ekleme isteği gönderen Python uygulaması.

Program:

- Belirlenen dersleri ve CRN'leri takip eder.
- Kontenjan artışını algılar.
- Bir öğrenci dersi bıraktığında oluşan boş yeri algılayabilir.
- İTÜ Kepler hesabına otomatik giriş yapar.
- Authorization tokenını arka planda güncel tutar.
- Yer açıldığında ilgili CRN için ders ekleme isteği gönderir.
- Başarılı şekilde alınan CRN'yi takipten çıkarır.

> Bu proje kişisel kullanım ve eğitim amacıyla geliştirilmiştir. Üniversitenin sistemlerini gereksiz isteklerle zorlamayın ve ilgili kullanım kurallarına uyun.

## Features

- Automatic course capacity monitoring
- Multiple course / CRN support
- Detects capacity increases
- Detects dropped seats (`enrolled < capacity`)
- Automatic Kepler login
- Automatic token refresh
- Automatic course registration request
- Retry mechanism for temporary failures
- `.env` based credential management
- Chrome / Selenium integration

## Requirements

- Python 3.10+
- Google Chrome
- Internet connection

Python packages:

```bash
pip install -r requirements.txt
```

## Installation

Clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/itu-kepler-course-watcher.git
cd itu-course-watcher
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create your `.env` file.

Windows:

```bash
copy .env.example .env
```

Linux / macOS:

```bash
cp .env.example .env
```

Then edit `.env`:

```env
ITU_USERNAME=your_itu_username
ITU_PASSWORD=your_itu_password
```

Do not commit your `.env` file.

## Course Configuration

Courses to monitor are configured inside:

```python
HEDEFLER = [
    {
        "ders": "ATA121",
        "brans": "ATA",
        "crnler": ["10134"]
    },
    {
        "ders": "SNT211E",
        "brans": "SNT",
        "crnler": ["14638"]
    }
]
```

Each target contains:

| Field | Description |
|---|---|
| `ders` | Course code |
| `brans` | Department / branch code |
| `crnler` | CRN numbers to monitor |

Multiple CRNs can also be specified:

```python
{
    "ders": "SNT211E",
    "brans": "SNT",
    "crnler": ["14638", "14639"]
}
```

## Detecting Available Seats

The application supports two different triggers.

### Capacity increase

Example:

```text
Capacity: 18 → 20
Enrolled: 18
```

The course registration attempt is triggered.

### Student drops the course

When:

```python
BOS_YERDE_DE_TETIKLE = True
```

the application also detects situations such as:

```text
Before:
18 / 18

After:
17 / 18
```

Even though the total capacity remains unchanged, an available seat exists, so registration is attempted.

## Configuration

Default settings:

```python
KONTROL_ARALIGI = 30
BOS_YERDE_DE_TETIKLE = True

KAYIT_DENEME_SAYISI = 3
KAYIT_DENEME_ARALIGI = 3

TOKEN_YENILEME_ARALIGI = 60

HEADLESS = False
```

### `KONTROL_ARALIGI`

How frequently the public OBS course table is checked.

```python
KONTROL_ARALIGI = 30
```

means every 30 seconds.

### `BOS_YERDE_DE_TETIKLE`

When enabled, the application reacts not only to capacity increases but also when:

```text
enrolled < capacity
```

### `KAYIT_DENEME_SAYISI`

Maximum number of registration attempts after a seat becomes available.

### `KAYIT_DENEME_ARALIGI`

Delay between retry attempts.

The first registration attempt is sent immediately. This delay only applies if a previous attempt fails.

### `TOKEN_YENILEME_ARALIGI`

How often the application tries to refresh the Kepler API token.

### `HEADLESS`

```python
HEADLESS = False
```

Chrome is visible.

```python
HEADLESS = True
```

Chrome runs in headless mode.

Using `False` is recommended during initial setup because additional login verification may require manual interaction.

## Usage

Run:

```bash
python itu_kontenjan_otomatik_al.py
```

After successful initialization, output similar to the following should appear:

```text
Takip edilecek dersler: ATA121 (10134), SNT211E (14638)
Kontrol aralığı: 30 sn
Kepler oturumu hazırlanıyor.
Kepler girişi tamamlandı.
İlk Kepler API tokenı alındı.
Kepler hazır. Kontenjan takibi başlıyor.
```

The application will then continue monitoring the configured courses.

When a seat is detected:

```text
SNT211E CRN 14638: boş yer var: 17/18
>>> CRN 14638 ALINMAYA ÇALIŞILIYOR (1/3)
```

After successful registration:

```text
CRN 14638 BAŞARIYLA ALINDI.
```

## How It Works

```text
                Start
                  │
                  ▼
          Login to Kepler
                  │
                  ▼
       Obtain Authorization Token
                  │
                  ▼
        Monitor Public OBS Data
                  │
                  ▼
       Is a seat available?
             │          │
            No         Yes
             │          │
             ▼          ▼
           Wait     Send CRN
                        │
                        ▼
                Registration API
                        │
                 ┌──────┴──────┐
                 │             │
              Success         Failed
                 │             │
                 ▼             ▼
             Stop CRN       Retry
             monitoring
```

## Security

Credentials are stored in the local `.env` file:

```env
ITU_USERNAME=...
ITU_PASSWORD=...
```

The `.env` file is ignored by Git and must never be uploaded to the repository.

Never publish:

- your password
- your authentication token
- browser session information

If credentials are accidentally committed to GitHub, change the password immediately.

## Limitations

The capacity watcher relies on the public İTÜ OBS course schedule data.

Therefore, the detection speed also depends on how frequently the public OBS data itself is updated.

A short polling interval does not guarantee that a capacity change will appear immediately in the public data source.

Login flows may also change over time. Additional authentication steps such as CAPTCHA or MFA may require manual interaction.

## Disclaimer

This is an unofficial project and is not affiliated with İstanbul Technical University.

Use it responsibly and at your own risk.

The project does not attempt to bypass CAPTCHA, MFA, authorization controls, or other security mechanisms.

## License

MIT License