# ADR 0004: PostgreSQL veritabanı şeması ve ortak `libs/db` paketi

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-17
- **Karar verenler:** Serdar Kafalı

## Bağlam

Interpol Red Notice kayıtları PostgreSQL'de saklanacak. Bu veritabanına birden fazla
servis dokunacak: `ingestor` Kafka'dan gelen kayıtları yazacak, `face-worker` fotoğrafları
indirip fotoğraf kayıtlarını güncelleyecek ve ileride yüz embedding'lerini yazacak, `api`
arayüz için okuyacak, saklama süresini uygulayan bir iş de süresi dolan kayıtları
silecek. ADR 0002 gereği consumer'lar at-least-once teslimle çalışıyor; aynı kayıt birden
fazla kez gelebileceği için yazma işlemleri idempotent olmak zorunda.

Şemayı tahminle değil gerçek veriyle tasarlamak için Interpol'ün herkese açık API'sinden
5 uyruk için liste, 30 kişi için detay ve fotoğraf listesi cevabı toplandı
(`scripts/sample_interpol_api.py`) ve sayısal olarak analiz edildi
(`scripts/analyze_samples.py`). Örnekler kişisel veri içerdiği için repoya girmedi.
Analizin şemayı doğrudan etkileyen sonuçları şunlar:

- Detay cevabında hiçbir alan eksik değil; değeri olmayan alanlar `null` olarak geliyor.
- `name`, `forename`, `sex_id` ve `date_of_birth` 30 kayıtta da dolu. `date_of_birth`
  her kayıtta `YYYY/MM/DD` biçiminde. Diğer tanımlayıcı alanların çoğu bazı kayıtlarda
  `null` (örneğin `distinguishing_marks` 23, `weight` 20, `place_of_birth` 11 kayıtta).
- `height` metre cinsinden (en fazla 1,88), `weight` kilogram cinsinden tamsayı. İkisinde
  de "bilinmiyor" anlamında `0` değeri geçiyor.
- 30 kişiden 4'ünün iki uyruğu var. Bir kişinin üç tutuklama emri var; 32 emrin yalnızca
  19'u kişinin kendi uyruklarından birindeki bir ülke tarafından çıkarılmış.
- Ülke kodları ISO 3166-1 alpha-2 biçiminde ve aralarında bağımsız ülke olmayan topraklar
  da var (`VG`, `MS`).
- Göz rengi, saç rengi ve konuşulan dil, Interpol'e özgü kodlardan oluşan listeler
  (`BRO`, `OTHL`, `ENG`). Göz ve saç rengi dolu olduğunda her zaman tek elemanlı.
- Her kişinin 1 ile 6 arasında fotoğrafı var. Detay cevabındaki thumbnail'in kimliği,
  30 kişinin hiçbirinde fotoğraf listesinde yer almıyor; thumbnail ayrı bir görsel.

Projenin ilk sürümündeki şemada tutuklama emirleri kişiyle 1:1 ilişkilendirilmişti; birden
fazla emri olan kişilerin ilk emri dışındakiler kayboluyordu. Fotoğraflar, URL'leri tutan
gereksiz bir `link` tablosu üzerinden bağlanmıştı ve doğum tarihi metin olarak saklanıyordu.

## Karar

### Şemanın yeri

Tablo tanımları, isimlendirme kuralı ve Alembic migration'ları `libs/db` altında
`interpol-db` adlı ortak bir pakette duracak (import adı `interpol_db`). Veritabanı şeması,
`libs/contracts` içindeki Kafka mesajları gibi servisler arasında bir sözleşme olduğu için
ADR 0001'in kütüphane kuralına uyuyor. Pakete sorgu veya iş mantığı girmeyecek; upsert
mantığı `ingestor`'ın, arama sorguları `api`'nin kendi kodunda kalacak. Migration'lar
servislerin açılışında değil, servislerden önce bir kez çalışan ayrı bir adımda
uygulanacak.

### Tablolar ve ilişkiler

- `countries`: ISO 3166-1'in topraklar dahil tam listesiyle doldurulan referans tablosu
  (`code char(2)` PK, `name`). Listede olmayan bir kod gelirse `ingestor` onu adı boş
  olarak ekleyip bir uyarı loglayacak; böylece FK bütünlüğü korunacak ve tek bir bilinmeyen
  kod yüzünden kayıt kaybolmayacak.
- `notices`: Her Red Notice için bir satır. Doğum ülkesi `countries` tablosuna 1:N FK.
- `notice_nationalities`: Uyruk için `notices` ile `countries` arasında M:N ara tablo,
  birleşik PK `(notice_id, country_code)`.
- `arrest_warrants`: `notices` ile 1:N. Emri veren ülke `countries` tablosuna ayrı bir FK;
  uyruktan türetilmiyor.
- `notice_photos`: `notices` ile 1:N. Interpol'ün `picture_id` değeri UNIQUE. MinIO'daki
  yer (`object_key`), dosya özeti (`sha256`) ve `downloaded_at`, fotoğraf indirilene kadar
  `null` kalacak.

Göz rengi, saç rengi ve konuşulan diller ayrı tablo yerine `notices` üzerinde `text[]`
dizileri olarak tutulacak. Bu alanlar filtreleme veya JOIN için değil yalnızca gösterim
için kullanılacak; altı ek tablo ve her kayıtta altı ek INSERT, bu kullanım için
gerekçelendirilemiyor. İleride filtreleme gerekirse GIN index eklenecek. "Değer yok"
durumunun tek bir gösterimi olması için bu diziler `NOT NULL DEFAULT '{}'` olacak; API'den
gelen `null` boş diziye çevrilecek.

URL'ler `entity_id`'den türetilebildiği için saklanmayacak. Thumbnail fotoğraflardan biri
olmadığı için `notices` üzerinde ayrı bir `thumbnail_picture_id` kolonunda tutulacak;
yüz analizi tam boyutlu fotoğraflarla yapılacak.

### Kimlik, tipler ve kısıtlar

- Her tablonun PK'sı `bigint` identity `id` kolonu. Interpol'ün `entity_id` değeri
  `notices` üzerinde `NOT NULL UNIQUE`; upsert (`INSERT ... ON CONFLICT (entity_id) DO
  UPDATE`) bu kısıta dayanıyor.
- `NOT NULL` yalnızca sistemin onsuz çalışamayacağı alanlara konacak: kimlikler, FK'lar,
  `name`, tutuklama emrindeki `charge` ve `issuing_country_code`. Dış kaynaktan gelen
  tanımlayıcı alanlar, örneklemde dolu görünseler bile nullable olacak; kaynağı kontrol
  etmediğimiz için bir alanın bir gün boş gelmesi tüm ingest'i durdurmamalı.
- `date_of_birth` `date` tipinde. Ayrıştırılamayan bir değer `null` olarak yazılıp
  uyarı olarak loglanacak.
- `sex_id` metin ve `CHECK (sex_id IN ('M', 'F', 'U'))`. PostgreSQL `ENUM` tipi yerine
  CHECK seçildi, çünkü değer kümesini bir migration ile değiştirmek daha kolay.
- `height` `numeric(3,2)`, `CHECK (height > 0 AND height < 3)`; `weight` `smallint`,
  `CHECK (weight > 0 AND weight < 500)`. API'den gelen `0` değeri `null`'a çevrilecek.
- Metin kolonları `text`; PostgreSQL'de `varchar(n)` ile performans farkı yok ve uzunluk
  sınırı yalnızca veri kesilmesi riski getiriyor.
- Zaman kolonları `timestamptz` ve UTC.

### Yaşam döngüsü

- `notices` üzerinde `created_at`, `updated_at`, `last_seen_at` (collector'ın kaydı
  listede en son gördüğü an), `deleted_at` ve gelen verinin SHA-256 özeti `content_hash`
  bulunacak. Özet aynıysa kayıt yeniden yazılmayacak.
- `updated_at`, uygulama kodu yerine bir PostgreSQL trigger'ı ile güncellenecek; böylece
  hangi servis veya kişi yazarsa yazsın doğru kalacak.
- Interpol listesinden kaldırılan bir kayıt hemen soft delete edilecek (`deleted_at`
  doldurulacak). 50 gün içinde listeye geri dönmezse kayıt, tutuklama emirleri ve
  fotoğraflarıyla birlikte kalıcı olarak silinecek (hard delete); fotoğraf dosyaları
  MinIO'dan da silinecek. Kayıt listeye geri dönerse `deleted_at` yeniden `null` olacak.
- Alt tablolar (`notice_nationalities`, `arrest_warrants`, `notice_photos`) `notices`'a
  `ON DELETE CASCADE` ile bağlı.
- Bir kaydın ve alt tablolarındaki satırların yazılması tek bir transaction içinde
  yapılacak; yarım kalmış bir kayıt oluşamayacak.

### İsimlendirme ve index'ler

- Tablo adları çoğul ve `snake_case`, PK her zaman `id`, FK'lar `<tekil_tablo>_id`, zaman
  kolonları `_at` ile biter, boolean'lar `is_` ile başlar.
- Kısıt adları SQLAlchemy `naming_convention` ile sabit bir şablondan üretilecek
  (`pk_`, `fk_`, `uq_`, `ck_`, `ix_` önekleri); Alembic migration'larında kısıtlar adıyla
  bulunabilsin.
- PostgreSQL FK kolonlarını otomatik index'lemediği için her FK kolonuna elle index
  eklenecek. Sorguların çoğu `deleted_at IS NULL` koşulunu içereceği için bu koşulla
  partial index'ler kullanılacak.
- `countries` tablosunun anahtarı doğal bir kod (`code`) olduğu için, ona bağlanan FK'lar
  `_id` yerine `_code` ile biter (`country_of_birth_code`, `issuing_country_code`).

## Değerlendirilen alternatifler

**Şemayı `ingestor` servisinde tutmak.** Tabloların sahibinin yazan servis olması net bir
sahiplik sağlar. Ancak `api` ve `face-worker` aynı tabloları kullanmak için ya model
tanımlarını kopyalamak zorunda kalır, ki kopyalar zamanla birbirinden ayrışır, ya da
`ingestor`'ın iç kodunu import eder, ki bu ADR 0001'in servis sınırlarını çiğner. Bu
yüzden reddettik.

**Her servise ayrı veritabanı.** Servisleri birbirinden en iyi ayıran yaklaşım bu: `api`
kendi veritabanını Kafka olaylarından doldurur. Bizim ölçeğimizde ise iki ayrı şema,
aralarında senkronizasyon ve tutarsızlık riski anlamına geliyor. Şimdilik reddettik;
sistem büyürse bu karar yeniden değerlendirilecek.

**Tüm kod listelerini normalize etmek.** Göz rengi, saç rengi ve dil için ayrı referans ve
ara tablolar kurmak tam normalizasyon sağlar ve FK ile kod bütünlüğünü garanti eder. Bu
alanlarla filtreleme veya JOIN yapmayacağımız için ek tabloların ve INSERT'lerin maliyeti
karşılığını vermiyor. Yalnızca filtrelemenin temel olduğu uyruk için ara tablo kullandık.

**`entity_id`'yi birincil anahtar yapmak.** Ek bir kolon gerektirmez. Ancak metin bir
anahtar, FK olarak kullanıldığı her tabloda daha fazla yer kaplar ve kaynağın kimlik
biçimindeki bir değişiklik tüm FK'ları etkiler. Kendi `bigint` anahtarımızı kullanıp
`entity_id`'yi UNIQUE tutmayı seçtik.

**Yalnızca soft delete veya yalnızca hard delete.** Yalnızca hard delete, listeden kısa
süreliğine düşüp geri dönen kayıtların geçmişini ve fotoğraflarını kaybettirir. Yalnızca
soft delete ise artık aranmayan kişilerin kişisel verilerini süresiz tutmak demek. İkisini
50 günlük bir saklama süresiyle birleştirdik.

## Sonuçlar

Birden fazla tutuklama emri ve birden fazla uyruk veri kaybı olmadan saklanacak. Aynı
kaydın tekrar gelmesi tek satırda kalacak ve `content_hash` sayesinde gereksiz yazma
yapılmayacak. Tüm servisler tek bir şema tanımını kullanacağı için tanım kopyaları
oluşmayacak; şema değişiklikleri tek bir PR'da yapılıp CI'da tüm servislerin testleriyle
doğrulanacak. CHECK ve FK kısıtları, kaynaktan gelen saçma değerleri veritabanına girmeden
reddedecek.

Buna karşılık bazı bedelleri kabul ediyoruz. Şemadaki her değişiklik birden fazla servisi
etkileyebilir; `libs/db` paketine sorgu ve iş mantığı konmaması kuralı yalnızca code review
ile korunuyor. Örneklem 30 kayıtla sınırlı; daha büyük veride beklenmeyen biçimler
(örneğin yalnızca yıl içeren doğum tarihleri) çıkabilir ve bu durumda şemanın bir
migration ile genişletilmesi gerekecek. Thumbnail'in hangi fotoğraftan üretildiği API'de
belirtilmiyor; bu bilgi şemada yer almıyor. Migration'ları ayrı bir adımda çalıştırmak,
Docker Compose'da ve dağıtımda servislerden önce çalışan bir adım tanımlamayı
gerektirecek. 50 günlük saklama süresini uygulayacak zamanlanmış iş henüz yok; collector ve
ingestor çalışmaya başladığında yazılacak.
