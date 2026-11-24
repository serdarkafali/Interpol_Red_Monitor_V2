# ADR 0011: Face-worker'ın fotoğrafları indirip saklaması

- **Durum:** Kabul edildi
- **Tarih:** 2026-11-22
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0005'in "Fotoğraflar" bölümünü ve `face-worker` için consumer group ile
  DLQ maddelerini değiştirir; ADR 0004'teki "MinIO" yerine S3 uyumlu bir nesne deposu
  (SeaweedFS) kullanılır

## Bağlam

ADR 0004 ve ADR 0005'e göre fotoğrafları `face-worker` indirecek. `notice.observed` olayı
yalnızca `picture_id`'leri taşıyor; `ingestor` bunları `notice_photos` tablosuna yazıyor ve
`object_key`, `sha256`, `downloaded_at` sütunlarını boş bırakıyor (ADR 0008). Faz 4'te bu
sütunları dolduran servis yazılacak. Yüz benzerliği (pgvector) Faz 5'in konusu.

Kararları etkileyen koşullar şunlar:

- ADR 0005, `face-worker`'ın da `interpol.notices.v1` topic'ini kendi consumer group'uyla
  okumasını öngörüyordu. Ancak aynı olayı iki consumer birbirinden habersiz işliyor:
  `face-worker` bir olayı `ingestor`'dan önce işlerse `notice_photos` satırı henüz yoktur.
- Bir fotoğraf indirmesi nezaket gecikmesiyle birlikte saniyeler sürüyor; Kafka bir
  consumer'ı 5 dakika `poll` etmezse group'tan çıkarır (`max.poll.interval.ms`).
- Interpol'e artık iki servis istek atacak (ADR 0005'te kabul edilmiş bir bedel).
- Fotoğraflar gerçek kişilerin yüzleri; test verisi uydurma olmalı ve denemelerde gerçek
  Interpol verisi kullanılmamalı.
- Planlanan nesne deposu MinIO'nun açık kaynak sürümü artık bakımı yapılmayan bir proje:
  Ekim 2025'te community sürümü için Docker imajı ve hazır binary yayınlanması durdu,
  Şubat 2026'da depo arşivlendi ve son sürümü etkileyen güvenlik açıklarına yama çıkmıyor.
- Fotoğraf indirme isteğinin cevabı bir deneme scriptiyle ölçüldü (aşağıda "Ölçüm").

## Karar

### İşin kaynağı Kafka değil veritabanı

`face-worker` Faz 4'te Kafka okumaz. İndirilecek fotoğrafları `notice_photos` tablosundan
seçer: `downloaded_at` boş olan ve kaydı kaldırılmamış (`notices.deleted_at IS NULL`)
satırlar. Hangi fotoğrafların var olduğunun kaynağı zaten `ingestor`'ın yazdığı tablo;
böylece:

- Yarış durumu ortadan kalkar: satır yoksa iş de yoktur.
- Başarısız bir indirme kendiliğinden tekrar denenir; servis yeniden başlayınca kaldığı
  yerden devam eder.
- Listeden çıkan bir fotoğrafın satırı silindiği için onu indirmeye çalışılmaz.
- Uzun süren indirmeler bir consumer group'un zaman sınırına takılmaz.

Bu yüzden ADR 0005'teki `face-worker` consumer group'u ve `face-worker.dlq` topic'i
kullanılmayacak; `kafka-init` bu topic'i artık oluşturmayacak.

### Seçim ve yeniden deneme

`notice_photos` tablosuna bir migration ile iki sütun eklenir:

- `download_attempts smallint NOT NULL DEFAULT 0`
- `last_attempt_at timestamptz` (boş olabilir)

Sıradaki fotoğraf şöyle seçilir: hiç denenmemiş olanlar önce, sonra en eski deneme. Bir
deneme başarısız olduysa bir sonraki deneme için beklenecek süre denemeyle ikiye katlanır
(1, 2, 4, 8 saat). 5 başarısız denemeden sonra fotoğraf bir daha denenmez ve bu durum
loglanır; Faz 7'de buna bir alarm bağlanacak. İndirilmeyi bekleyen satırları hızlı bulmak
için `downloaded_at IS NULL` koşullu bir partial index eklenir.

Sütunların sahipliği net: satırları `ingestor` ekler ve siler; `face-worker` yalnızca
`object_key`, `sha256`, `downloaded_at`, `download_attempts` ve `last_attempt_at`
sütunlarını yazar.

Şimdilik tek bir `face-worker` çalışacak. Birden fazla örnek gerekirse seçim
`SELECT ... FOR UPDATE SKIP LOCKED` ile yapılır; bu karar ölçüm gerektiğinde verilecek.

### Bir fotoğrafın işlenmesi

1. Fotoğraf seçilir (kısa bir okuma).
2. Interpol'den indirilir. Bu sırada veritabanında açık bir transaction tutulmaz.
3. Cevap kontrol edilir: durum kodu 200 olmalı, dosya JPEG imzasıyla (`ff d8 ff`)
   başlamalı ve en fazla 5 MB olmalı. Uymayan cevap başarısız bir deneme sayılır.
   Interpol'ün gönderdiği `Content-Type` (`image/JPG`) standart bir değer olmadığı için
   kontrol başlığa değil dosyanın kendisine bakar; depoya standart `image/jpeg` yazılır.
4. Dosyanın SHA-256 özeti hesaplanır ve dosya nesne deposuna yazılır.
5. Satır güncellenir:
   `UPDATE ... SET object_key, sha256, downloaded_at WHERE id = ... AND downloaded_at IS NULL`.
   Satır bu arada silindiyse güncelleme hiçbir satıra dokunmaz; yazılmış dosya sahipsiz
   kalır ve aşağıdaki temizlik onu siler.

Başarısız bir denemede yalnızca `download_attempts` ve `last_attempt_at` güncellenir.
Önce dosya, sonra veritabanı: tersi sırada çökmek, veritabanında olmayan bir dosyayı
"indirildi" gösterirdi. Bu sırada çökmek en fazla bir sahipsiz dosya bırakır.

Loglarda yalnızca `entity_id` ve `picture_id` yer alır; dosya içeriği ve hata metinleri
loglanmaz.

### Interpol istemcisi

`face-worker` kendi küçük istemcisini kullanır. `collector`'ın istemcisi JSON okuyor,
burada ise resim baytları indiriliyor; ortak bir pakete taşımak iki farklı ihtiyacı aynı
koda bağlardı. Taşıma katmanı (curl_cffi ile tarayıcı taklidi), yeniden deneme kuralları
(429 ve 5xx, 20 → 40 sn backoff) ve Protocol ile test edilebilirlik `collector`'dan
uyarlanır. Üçüncü bir kullanıcı çıkarsa ortak paket yeniden değerlendirilecek.

Fotoğraf `{BASE_URL}/{url_id}/images/{picture_id}` adresinden indirilir. Olmayan bir
fotoğraf 404 döner; bu da başarısız bir deneme sayılır (fotoğraf listeden çıktıysa satırı
bir sonraki `notice.observed` ile zaten silinir).

### İstek hızı ve çalıştırma

Tek bir `face-worker`, her istekten önce `collector` ile aynı nezaket gecikmesini (5–8 sn)
uygular. Bekleyen iş yoksa 60 sn bekler. İlk dolumda tüm fotoğrafların indirilmesi
tahminen bir iki gün sürer; sonrasında günde az sayıda fotoğraf gelir. İki servisin toplam
istek hızını tek bir yerden sınırlayan ortak bir mekanizma (örneğin Redis) şimdilik
kurulmayacak; 429 cevapları loglarda izlenecek.

`face-worker` gerçek Interpol API'sine istek attığı için Docker Compose'da `collector` gibi
bir profile arkasında olacak; düz bir `docker compose up` onu başlatmaz. Interpol adresi bir
ayar olacak, böylece uçtan uca denemede sahte bir sunucuya yönlendirilebilir.

### Nesne deposu: SeaweedFS ve S3 API

Fotoğraflar S3 uyumlu bir nesne deposunda tutulur. Sunucu olarak SeaweedFS seçildi:
Apache 2.0 lisanslı, uzun süredir aktif geliştiriliyor, resmi Docker imajı var ve tek node
ile çalışıyor. Kod depoyla yalnızca standart S3 API üzerinden konuşur (Python tarafında
`boto3`); depo sunucusu değişirse yalnızca Compose ayarı değişir.

- Tek bir private bucket: `notice-photos`. Bucket, `kafka-init` gibi bir kez çalışan bir
  init adımıyla oluşturulur.
- Erişim bilgileri `.env`'de durur, repoya girmez. `face-worker` şimdilik tek bir erişim
  anahtarı kullanır; yalnızca bu bucket'a yetkili ayrı bir kullanıcı Faz 8'e kalıyor.
- Veriler bir Docker volume'da kalıcı tutulur; imaj sürümü sabitlenir.
- SeaweedFS her bucket için ayrı volume'lar açıyor; varsayılan en fazla 8 volume ilk
  bucket'la doluyor ve ikinci bir bucket'a yazılamıyor (testlerde görüldü). Bu yüzden
  volume sayısı diskin elverdiği kadar (`-volume.max=0`), volume boyutu 1 GB.

### Object key

`notices/{entity_id'nin URL biçimi}/{picture_id}`, örneğin `notices/2024-12345/63112345`.
Uzantı eklenmez; dosya türü nesnenin `Content-Type` bilgisinde saklanır. `picture_id` tek
başına da benzersiz, ancak `entity_id` öneki bir kaydın bütün fotoğraflarının tek bir önekle
bulunup silinmesini sağlıyor.

### Sahipsiz dosyaların temizliği

`face-worker` günde bir kez bucket'taki dosyaları listeler ve veritabanındaki
`object_key`'lerde karşılığı olmayanları siler. Yalnızca 1 saatten eski dosyalar silinir;
yeni yüklenmiş ama satırı henüz güncellenmemiş bir dosya da sahipsiz görünür. Bu temizlik
üç durumu birden kapsar: listeden çıkan fotoğrafın satırının `ingestor` tarafından
silinmesi, Faz 8'deki 50 günlük kalıcı silmenin satırları silmesi ve yukarıdaki çökme
durumu.

### Test

- Birim testleri: sahte bir taşıma katmanı (kodda üretilmiş küçük bir resim döndürür) ve
  bellekte çalışan sahte bir depo; Protocol'ler sayesinde servis Docker olmadan test edilir.
- Entegrasyon testleri: gerçek PostgreSQL ve gerçek SeaweedFS, testcontainers ile.
- Uçtan uca deneme: Compose'da uydurma resimler sunan küçük bir sahte Interpol sunucusu;
  `face-worker`'ın Interpol adresi ona yönlendirilir.

Hiçbir test ve deneme gerçek Interpol'den fotoğraf indirmez. Gerçek indirmeye geçmek,
`COLLECTOR_DRY_RUN=false` kararıyla birlikte ayrıca verilecek bir karar.

## Ölçüm

`scripts/probe_interpol_photos.py` (2026-11-23) listenin ilk sayfasındaki 5 kaydın ilk
fotoğrafını indirdi. Script fotoğrafları kaydetmez ve içeriklerini yazdırmaz; yalnızca
durum kodu, içerik türü, boyut ve ilk üç bayt yazdırılır.

| Sonuç | Değer |
| --- | --- |
| Durum kodu | 5 fotoğrafın hepsi 200 |
| `Content-Type` | Hepsi `image/JPG` (standart değer `image/jpeg`) |
| Dosya imzası | Hepsi `ff d8 ff` (JPEG) |
| Boyut | 6 897 – 76 297 bayt |
| Olmayan fotoğraf (`picture_id=0`) | 404, `application/json` gövde (103 bayt) |

5 MB sınırı görülen en büyük dosyanın yaklaşık 65 katı; gerçek fotoğrafları reddetmeden
yanlışlıkla gelen büyük bir cevabı durduruyor. Örneklem küçük; ilk dolumda reddedilen
fotoğraflar loglardan izlenecek.

## Doğrulama

Uçtan uca deneme (2026-11-23, Serdar'ın makinesinde, Docker). `scripts/produce_fake_events.py`
uydurma olayları yazdı, ingestor `notice_photos` satırlarını oluşturdu ve face-worker
`docker-compose.e2e.yml` ile sahte Interpol sunucusuna yönlendirildi. Gerçek Interpol'e
istek atılmadı.

| Kayıt | Beklenen | Sonuç |
| --- | --- | --- |
| Açılış | Sahipsiz dosya temizliği | `orphan_photos_deleted`, `count: 0` |
| `1900/1` | Depoya yazılır | `photo_stored`; depoda `notices/1900-1/1900-1-photo` (630 bayt) |
| `1900/2` | Sahte sunucu HTML döndürür | `photo_download_failed`, `reason: not_jpeg`, `attempts: 1` |
| `1900/6` | Sahte sunucu 404 döndürür | `photo_download_failed`, `reason: not_found`, `attempts: 1` |
| `1900/3` | Kaldırılmış kayıt, denenmez | Log yok |
| `1900/5` | DLQ'ya gitmişti, satırı yok | Log yok |

İstekler arasında 6–7 sn geçti (5–8 sn nezaket gecikmesi). Daha önce Claude'un ortamında
yerel PostgreSQL ve SeaweedFS ile yapılan denemede ayrıca şunlar görüldü: depo kapalıyken
servis çökmeden her bekleme süresinde tek satırlık `dependency_unavailable` yazdı; 10
dakikalık sahipsiz bir dosya 1 saat kuralı nedeniyle silinmedi; SIGTERM'de servis o anki
adımı bitirip `face_worker_stopped` ile kapandı.

Bu fazda ayrıca `console` log formatında hata çıktılarının yerel değişkenleri gösterdiği
fark edildi (Faz 3'teki düzeltme yalnızca `json` formatını kapsıyordu); `libs/observability`
iki formatta da yerel değişkenleri gizleyecek şekilde düzeltildi.

## Değerlendirilen alternatifler

**ADR 0005'teki gibi Kafka'yı okumak.** Olay geldiği anda indirme başlardı. Ancak satır
henüz yoksa ne yapılacağı sorunu çıkar: beklemek tüketimi durdurur, satırı `face-worker`'ın
eklemesi `ingestor`'ın sahipliğini bozar, atlamak fotoğrafı kaybettirir. Başarısız
indirmeler için ayrı bir tekrar mekanizması ve uzun indirmeler için `max.poll.interval.ms`
ayarı da gerekirdi. Reddettik.

**`ingestor`'ın commit'ten sonra ayrı bir topic'e "fotoğraf bekliyor" yazması.** Yarış
durumunu çözer, ancak `ingestor`'a bir producer ve "veritabanına yazıldı ama mesaj
gönderilemedi" durumu (outbox problemi) ekler. Veritabanı zaten aynı bilgiyi tutuyor.
Reddettik.

**`collector`'ın istemcisini ortak bir pakete taşımak.** Tek bir yer olurdu, ancak iki
istemcinin ortak noktası yalnızca taşıma katmanı; biri JSON, diğeri bayt okuyor. Erken bir
soyutlama iki servisi birbirine bağlardı. Reddettik.

**MinIO kullanmaya devam etmek.** Son yayımlanan imaj hâlâ çekilebiliyor, ancak bilinen
güvenlik açıkları yamanmıyor ve yeni sürüm çıkmayacak. Reddettik. Diğer adaylar: Garage
(AGPL lisanslı, küçük dağıtık kümeler için tasarlanmış), RustFS (MinIO'ya en yakın
alternatif, ancak henüz olgunlaşma aşamasında), Ceph RGW (bu ölçek için fazla karmaşık).

**Dosyaları sunucunun diskinde bir klasörde tutmak.** En basit yol, ancak ileride `api`
fotoğrafları sunarken ve servisler farklı makinelere dağıtılırken paylaşılan bir disk
gerekirdi; S3 API bu sorunu baştan çözüyor. Reddettik.

**Dosya silmeyi `ingestor`'a yaptırmak.** Satırı silerken dosyayı da silerdi, ancak
`ingestor`'a nesne deposu bağımlılığı ve transaction'ın dışında kalan bir yan etki eklerdi:
transaction geri alınırsa dosya çoktan silinmiş olurdu. Reddettik.

## Sonuçlar

Fotoğraflar `ingestor`'dan bağımsız, kendi hızında ve hata durumunda kaybolmadan
indirilecek. Yarış durumu ve consumer group zaman sınırı sorunları hiç oluşmayacak.
Fotoğraf dosyaları sahipsiz kalmayacak; kaldırılan bir kaydın fotoğrafları kalıcı
silmeden en geç bir gün sonra depodan da silinecek.

Buna karşılık bazı bedelleri kabul ediyoruz. `face-worker` veritabanını periyodik olarak
sorguluyor; yeni bir fotoğraf en fazla bekleme süresi kadar (60 sn) geç fark edilecek.
`face-worker` artık veritabanına bağımlı ve `notice_photos` şeması değişiyor (migration).
İki servisin Interpol'e toplam istek hızı tek bir yerden kontrol edilmiyor. Depo için
yeni bir altyapı bileşeni (SeaweedFS) ve onu oluşturan bir init adımı ekleniyor; erişim
bilgisi henüz tek bir anahtar. 5 denemede indirilemeyen fotoğraflar yalnızca loglarda
görünecek; takibi Faz 7'deki alarmlara kalıyor.

## ADR 0005'e etkisi

- "Fotoğraflar" bölümü: `face-worker` fotoğrafları olaylardan değil `notice_photos`
  tablosundan seçer.
- "Producer ve consumer davranışı": `face-worker` consumer group'u ve `face-worker.dlq`
  kullanılmaz; Kafka'yı okuyan tek consumer `ingestor`.
