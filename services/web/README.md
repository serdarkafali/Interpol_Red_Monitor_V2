# interpol-web

Kayıtları, fotoğrafları ve olası yüz eşleşmelerini gösteren web arayüzü (ADR 0013).
Solda sahibinden.com'daki gibi bir filtre barı, sağda sonuçlar. Veritabanına yalnızca
okuyabilen `web` kullanıcısıyla bağlanır; sayfalar kullanıcı adı ve şifreyle korunur.

## Sayfalar

| Adres | Ne gösterir |
| --- | --- |
| `/` | Kayıt listesi: sol barda isim, cinsiyet, yaş, uyruk, kararı veren ülke, fotoğraf, olası eşleşme, durum ve sıralama; her seçeneğin yanında kaç kayıt kaldığı |
| `/notices/{id}` | Bir kaydın bilgileri, yakalama kararları, fotoğrafları (yüz kutularıyla), kendi fotoğraflarının birbirine benzeyip benzemediği ve başka kayıtlarla olası eşleşmeleri |
| `/matches` | Farklı kayıtlardaki birbirine benzeyen yüz çiftleri, yan yana, en benzer çift en üstte |
| `/photos/{id}` | İndirilmiş bir fotoğraf; web servisi depodan okuyup verir, tarayıcı depoya bağlanmaz |
| `/healthz` | Docker'ın sağlık kontrolü; giriş istemez, veri göstermez |

Olası eşleşme bir kimlik tespiti değildir; arayüz her yerde "olası eşleşme, insan
incelemesi gerekir" der (ADR 0012).

## Çalıştırma

`docker compose up -d --build` web servisini de başlatır. Tarayıcıda
<http://127.0.0.1:8000> açılır; kullanıcı adı ve şifre `.env`'deki `WEB_USERNAME` ve
`WEB_PASSWORD`'dür. Port yalnızca `127.0.0.1`'e açılır, yerel ağdan erişilemez.

Docker dışında (geliştirirken):

```powershell
docker compose up -d postgres seaweedfs migrate storage-init
uv run --env-file .env interpol-web
```

## Ayarlar

| Değişken | Varsayılan | Açıklama |
| --- | --- | --- |
| `WEB_DATABASE_URL` | zorunlu | Salt okunur `web` kullanıcısıyla veritabanı adresi; compose `WEB_DATABASE_USER` ve `WEB_DATABASE_PASSWORD`'den kendisi kurar |
| `WEB_USERNAME`, `WEB_PASSWORD` | zorunlu | Tarayıcının soracağı giriş bilgileri; şifre yalnızca ASCII karakterlerden oluşmalı |
| `WEB_STORAGE_ACCESS_KEY`, `WEB_STORAGE_SECRET_KEY` | `STORAGE_*` | Fotoğraf deposunun anahtarı |
| `WEB_STORAGE_ENDPOINT_URL` | `http://localhost:8333` | Depo adresi; compose'da `http://seaweedfs:8333` |
| `WEB_STORAGE_BUCKET` | `notice-photos` | Fotoğrafların bucket'ı |
| `WEB_HOST`, `WEB_PORT` | `127.0.0.1`, `8000` | Dinlenen adres; container içinde `0.0.0.0` |
| `WEB_LOG_LEVEL`, `WEB_LOG_FORMAT` | `INFO`, `json` | Loglama |

## Güvenlik ve kişisel veri

- Veritabanına salt okunur kullanıcıyla bağlanır; bir açık veriyi değiştiremez.
- Uvicorn'un erişim logu kapalı: arama adresi (`?q=...`) bir isim içerebilir.
- Her cevapta `Cache-Control: no-store`: tarayıcı kişisel veri içeren sayfaları saklamaz.
- API dokümantasyonu (`/docs`, `/openapi.json`) kapalı.
- Veritabanı bağlantısı 5 saniyede, depo isteği 15 saniyede zaman aşımına uğrar; bir
  sayfa sonsuza kadar beklemez.

## Kod yapısı

```text
app.py      uygulamayı kurar: giriş kontrolü, statik dosyalar, no-store, /healthz
routes.py   sayfa adresleri; veritabanını NoticeReader, depoyu PhotoSource
            Protocol'leri üzerinden kullanır
queries.py  salt okunur SQL sorguları (liste, seçenek sayıları, detay, eşleşmeler)
photos.py   depodan fotoğraf okuma
filters.py  URL'deki filtrelerin doğrulanması
auth.py     HTTP Basic giriş kontrolü
config.py   ortam değişkenlerinden ayarlar
```

Katman kuralı (`pyproject.toml`, import-linter): `app` → `routes | auth` →
`queries | photos` → `filters`.
