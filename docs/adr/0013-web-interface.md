# ADR 0013: Kayıtları, fotoğrafları ve olası eşleşmeleri gösteren web arayüzü

- **Durum:** Kabul edildi
- **Tarih:** 2026-11-25
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0004 (şema), ADR 0011 (fotoğraf deposu), ADR 0012 (yüzler ve
  `face_matches`)

## Bağlam

Faz 2–5 sonunda kayıtlar, fotoğraflar, yüzler ve otomatik karşılaştırma sonuçları
PostgreSQL'de ve SeaweedFS'te duruyor. Bunları görmenin tek yolu şu an `psql` ve loglar.
Faz 6'da bunları bir tarayıcıdan gezilebilir hale getiriyoruz.

İstenenler:

- sahibinden.com'daki gibi **solda bir filtre barı**, sağda sonuç listesi; seçeneklerin
  yanında o filtreyle kaç kayıt olduğu;
- bir kaydın detayı: bilgiler, yakalama kararları, fotoğraflar ve bulunan yüzler;
- kaydın kendi fotoğraflarının birbirine benzeyip benzemediği ve başka kayıtlarla olası
  eşleşmeleri;
- son olası eşleşmelerin listesi.

Koşullar: arayüz yalnızca okur; gerçek veri aktığında sayfalarda gerçek isimler görüneceği
için kişisel veri kurallarımız burada da geçerli; Faz 6 bilerek sade tutuluyor
(yaklaşık 14–18 saat).

## Karar

### Tek servis: FastAPI + sunucuda üretilen sayfalar

Yeni bir `services/web` paketi. FastAPI, sayfaları **sunucuda** Jinja2 şablonlarıyla
üretir; tarayıcıya hazır HTML gider. JavaScript framework'ü yoktur. Ayrı bir JSON API ve
ayrı bir ön yüz projesi kurulmaz.

Neden: istenen ekranlar (liste, detay, eşleşmeler) form ve bağlantılarla çalışan klasik
sayfalar. Ayrı bir ön yüz (React gibi) ikinci bir dil, derleme adımı ve iki kod tabanı
getirirdi; bu fazın amacına göre fazla. FastAPI, URL parametrelerini tiplerle doğrular,
testler için hazır bir `TestClient` sunar ve ileride bir JSON API gerekirse aynı serviste
eklenebilir.

### Filtre barı: düz bir HTML formu, filtreler URL'de

Sol bar bir `GET` formudur; seçimler URL parametrelerine yazılır
(`/?sex=M&nationality=TR&age_min=30`). Böylece bir arama bağlantı olarak paylaşılabilir,
tarayıcının geri tuşu çalışır ve JavaScript gerekmez.

| Filtre | Çalışma şekli |
|---|---|
| İsim / soyisim | `name` ve `forename` içinde, büyük-küçük harf duyarsız arama |
| Cinsiyet | Çoklu seçim |
| Uyruk | Çoklu seçim (`notice_nationalities`) |
| Kararı veren ülke | Çoklu seçim (`arrest_warrants.issuing_country_code`) |
| Yaş aralığı | `date_of_birth`'ten hesaplanır; doğum tarihi olmayan kayıtlar yaş filtresi verilince listede çıkmaz |
| Fotoğrafı var | En az bir fotoğrafı indirilmiş |
| Olası eşleşmesi var | `face_matches`'te başka bir kayıtla eşleşmesi var |
| Durum | Aktif (varsayılan) veya kaldırılmış |

**Seçenek sayıları** sahibinden'deki gibi hesaplanır: bir filtrenin seçeneklerindeki
sayılar, **o filtre hariç** diğer bütün seçimler uygulanarak sayılır. Örneğin "Türkiye"
uyruğu seçiliyken uyruk listesindeki diğer ülkelerin sayıları da görünmeye devam eder.
Kayıt sayısı küçük (yaklaşık 6.400) olduğu için sayılar her istekte `GROUP BY` ile
hesaplanır; önbellek yok.

Sıralama: en yeni (varsayılan), isme göre. Sayfalama: sayfa başına 24 kayıt, sayfa numarası
ile (`OFFSET`).

### Veritabanı erişimi: salt okunur kullanıcı

Web servisi veritabanına yalnızca `SELECT` yetkisi olan ayrı bir kullanıcıyla bağlanır.
Bir migration, giriş yapamayan bir okuma rolü oluşturur ve ona bütün tablolarda (ileride
eklenecekler dahil) `SELECT` verir. Giriş yapabilen asıl kullanıcı ve şifresi `migrate`
adımında ortam değişkeninden oluşturulur; şifre repoya ve migration'a girmez.

Neden: arayüzde bir hata ya da SQL enjeksiyonu açığı olsa bile veri değiştirilemez veya
silinemez (en az yetki ilkesi). Sorgular SQLAlchemy Core ile yazılır; ingestor ve
face-worker'daki düzenin aynısı.

### Fotoğraflar web servisi üzerinden

Fotoğraflar tarayıcıya `/photos/{id}` adresinden, web servisinin S3'ten okumasıyla
verilir. Tarayıcı depoya doğrudan bağlanmaz.

Web servisi fotoğrafın yolunu (`object_key`) veritabanından okur; yalnızca indirilmiş
fotoğraflar verilir, bilinmeyen bir id 404 döner ve depoya hiç sorulmaz. Fotoğraf
`image/jpeg` ve `X-Content-Type-Options: nosniff` ile, sayfalar gibi giriş korumasının
arkasından verilir.

**Fotoğraf deposu anahtarı.** SeaweedFS şu an tek bir S3 anahtarıyla çalışıyor; web
servisi de face-worker'ın kullandığı `STORAGE_*` anahtarını kullanır (kendi
`WEB_STORAGE_*` değişkenleri verilirse onlar önceliklidir). Kod depoya yalnızca okuma
çağrısı yapar (`get_object`), ama anahtarın kendisi yazabilir: veritabanındaki salt
okunur kullanıcının dengi henüz yok. SeaweedFS'in kimlik dosyasıyla (`-s3.config`)
yalnızca `Read` yetkili ikinci bir anahtar tanımlamak bilinen bir sonraki adım.

Yüz kutuları fotoğrafın üzerine tarayıcıda çizilir: fotoğrafın asıl boyutu yüklendikten
sonra okunur ve `faces` tablosundaki kutular oranlanarak yerleştirilir. Bunun için birkaç
satırlık düz JavaScript yeterli. Fotoğrafın kendisi değiştirilmez.

### Koruma: kullanıcı adı ve şifre

Bütün sayfalar HTTP Basic ile korunur: tek bir kullanıcı adı ve şifre, ortam
değişkenlerinden (`SecretStr`). Karşılaştırma `secrets.compare_digest` ile yapılır.
Compose'da port yalnızca `127.0.0.1`'e açılır, yani yerel ağdan bile erişilemez. Gerçek
bir kullanıcı sistemi (oturumlar, roller) Faz 8'e bırakıldı.

### Kişisel veri ve loglar

- Uygulama logları yalnızca sayfa adını, süreyi ve kayıt id'lerini içerir.
- Uvicorn'un erişim logu kapatılır: URL'deki sorgu (`?q=...`) aranan ismi içerebilir.
- Sayfalara `Cache-Control: no-store` eklenir; tarayıcı kişisel veri içeren sayfaları
  diske yazmaz.

### Olası eşleşmelerin sunumu

ADR 0012'deki etik ilke arayüzde de geçerli. Bir eşleşme hiçbir zaman "aynı kişi" diye
gösterilmez; "olası eşleşme, insan incelemesi gerekir" etiketiyle, uzaklık ve benzerlik
yüzdesiyle birlikte gösterilir. Eşleşen iki yüz yan yana konur. Kaydın kendi
fotoğraflarından birbirine benzemeyenler ayrıca işaretlenir.

### Katmanlar ve test

```
routes        → sayfa adresleri, şablonlar
queries | photos → veritabanı ve depo okuma
filters       → URL parametrelerinin doğrulanması (saf)
```

`routes`, `queries` ve `photos`'u Protocol'ler üzerinden kullanır; sayfa testleri sahte
nesnelerle `TestClient` üzerinden yazılır. Sorgular gerçek PostgreSQL'e karşı test edilir
(testcontainers). Katman kuralı import-linter'a eklenir.

### Çalıştırma

Compose'a profilsiz bir `web` servisi eklenir: yalnızca veritabanını ve depoyu okuduğu için
gerçek dünyaya istek atmaz. `migrate` adımının bitmesini bekler, port
`127.0.0.1:${WEB_PORT:-8000}`.

## Değerlendirilen alternatifler

**Ayrı JSON API + React (ya da başka bir SPA).** Modern ve esnek, ama ikinci bir dil, npm,
derleme adımı ve iki kod tabanı getirir. Bu fazın ekranları için gereksiz karmaşıklık.
Reddedildi; ileride gerekirse aynı serviste JSON adresleri eklenebilir.

**Flask.** V1'de kullanılmıştı ve yeterli olurdu. FastAPI'nin URL parametrelerini tiplerle
doğrulaması (filtre barı için önemli), `TestClient`'ı ve pydantic ile uyumu tercih
sebebi. Reddedildi.

**Django.** Hazır yönetim paneli cazip, ama kendi ORM'i ve migration sistemi var; bizim
SQLAlchemy modellerimiz ve Alembic migration'larımızla çakışırdı. Reddedildi.

**Uygulamanın ana veritabanı kullanıcısıyla bağlanmak.** En kolayı, ama arayüzdeki bir
açık bütün veriyi yazılabilir kılardı. Reddedildi.

**Fotoğraflar için S3'ten geçici imzalı bağlantılar (presigned URL).** Web servisini
yormaz, ama depo adresinin tarayıcıdan erişilebilir olmasını gerektirir (SeaweedFS şu an
yalnızca Docker ağında) ve depoyu dışarı açar. Reddedildi.

**Yüz kutularını sunucuda fotoğrafın üzerine çizmek.** JavaScript gerektirmez, ama her
istekte görüntü işler ve kanıt niteliğindeki fotoğrafın değiştirilmiş bir kopyasını
üretir. Reddedildi.

**Seçenek sayılarını filtrelerden bağımsız göstermek ya da hiç göstermemek.** Daha basit,
ama "bu seçimle kaç kayıt kalır?" sorusunu cevaplamaz; istenen sahibinden davranışı bu.
Reddedildi.

**Korumasız bırakmak.** Yerel bir proje için yeterli görünür, ama gerçek veri aktığında
port yanlışlıkla açılırsa isimler herkese açık olurdu. Reddedildi.

## Sonuçlar

Kayıtlar, fotoğraflar ve olası eşleşmeler tek bir hafif servisle, sol filtre barlı bir
arayüzde gezilebilecek. Servis veriyi değiştiremez, kişisel veri loglara ve tarayıcı
önbelleğine düşmez.

Kabul edilen bedeller: sunucuda üretilen sayfalar her filtre değişikliğinde sayfanın
tamamını yeniler (anlık filtreleme yok); seçenek sayıları her istekte hesaplanır (veri
büyürse önbellek gerekir); tek kullanıcılı Basic koruma gerçek bir yetki sistemi değildir
(Faz 8); `OFFSET` sayfalaması çok büyük veride yavaşlar (bu veri boyutunda sorun değil).
