# Emlak İlan Botu (WhatsApp + GPT)

Emlak danışmanının WhatsApp'tan yazdığı serbest metni yapay zeka ile yapılandırılmış ilana çeviren, fotoğrafları Google Drive'a yükleyen ve ilanları bir web arayüzünde listeleyen sistem.

## Çözdüğü problem

Emlak ofislerinde ilan girişi çoğunlukla elle yapılır: fiyatı, mahalleyi, oda sayısını forma tek tek yazmak ve fotoğrafları klasörlemek zaman alır. Burada danışman ilanı WhatsApp'ta doğal bir mesajla yazar ve fotoğrafları gönderir; geri kalanı otomatik olur.

## Nasıl çalışır

1. Danışman WhatsApp numarasına ilanı serbest metinle yazar. Örnek: `Bahçelievler, Atatürk Cd., 3+1, 120 m2, 2.500.000 TL`
2. GPT (`gpt-3.5-turbo`) mesajı başlık, açıklama, fiyat, mahalle, sokak, oda sayısı ve metrekare alanlarına ayırır.
3. Bot fotoğrafları bekler. Gelen her fotoğraf Google Drive'da ilana özel bir klasöre yüklenir (dosyalar "bağlantıya sahip herkes okuyabilir" olarak paylaşılır).
4. Danışman `/tamamla` yazınca ilan PostgreSQL'e kaydedilir ve onay mesajı döner.
5. İlan web arayüzünde kart olarak görünür.

Komutlar:

| Komut | Ne yapar |
| --- | --- |
| `/tamamla` | Fotoğraf yüklemeyi bitirir ve ilanı kaydeder. |
| `/sil` | Anahtar kelimeyle ilan klasörü arar; numara seçilince Drive klasörü ve veritabanı kaydı silinir. |

Başka bir komut gerekmez: durumu olmayan bir kullanıcının gönderdiği ilk mesaj ilan metni olarak işlenir.

## Özellikler

- Serbest metinden ilan alanı çıkarma (GPT).
- Twilio WhatsApp webhook'u ile çok adımlı konuşma akışı (ilan metni, fotoğraflar, tamamlama).
- Google Drive'da ilan başına klasör ve fotoğraf yükleme.
- REST API: ilan listeleme, ilan oluşturma, ID ile ilan getirme.
- React arayüzü: kart görünümü, arama (başlık, açıklama, mahalle), fiyat, oda sayısı ve metrekare filtreleri.

## Teknoloji yığını

| Katman | Teknoloji |
| --- | --- |
| Bot ve API | Python, FastAPI, SQLAlchemy |
| Veritabanı | PostgreSQL |
| Mesajlaşma | Twilio (WhatsApp) |
| Yapay zeka | OpenAI API |
| Dosya depolama | Google Drive API |
| Arayüz | React, Tailwind CSS, Axios |

## Proje yapısı

```
bot/            Twilio webhook'u (webhook.py) ve GPT ayrıştırıcı (gpt_parser.py)
backend/        FastAPI ilan API'si, SQLAlchemy modelleri, testler
drive_service/  Google Drive yükleme yardımcıları
frontend/       React arayüzü
migrate.py      Tablo oluşturma yardımcısı
```

## Kurulum

Gereksinimler: Python 3.8+, Node.js, PostgreSQL, Twilio hesabı, OpenAI API anahtarı, Google Drive için servis hesabı bilgileri.

```bash
pip install -r requirements.txt
cp .env.example .env     # değerleri doldurun
```

`.env.example` dosyasındaki değişkenleri doldurun. Anahtarları depoya eklemeyin.

Çalıştırma:

```bash
# WhatsApp webhook'u (Twilio bu adrese yönlendirilir: /webhook)
uvicorn bot.webhook:app --reload

# İlan API'si (tablolar açılışta otomatik oluşturulur)
uvicorn backend.main:app --reload

# Arayüz
cd frontend
npm install
npm start
```

Arayüz API adresini `REACT_APP_API_URL` ortam değişkeninden okur (varsayılan `http://localhost:8000`).

## Bilinen sınırlar

- Konuşma durumu bellekte tutulur; bot yeniden başlarsa yarım kalan ilanlar kaybolur.
- Gönderen numaralar için yetkilendirme (izinli numara listesi) ve API için kimlik doğrulama yoktur.
- GPT istemi ofis kimliğini sabit `1` olarak üretir; tek ofis için tasarlanmıştır.

## Lisans

MIT. Ayrıntılar için [LICENSE](LICENSE).
