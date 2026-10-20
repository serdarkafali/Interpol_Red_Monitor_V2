# ADR 0005: Kafka topic'leri ve mesaj sözleşmesi

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-19
- **Karar verenler:** Serdar Kafalı

## Bağlam

ADR 0002 servisler arası mesajlaşma için Kafka'yı, at-least-once teslimi ve key olarak
`entity_id` kullanılmasını belirledi; topic adlarını, partition sayısını, retention süresini
ve mesaj şemasını bilerek sonraya bıraktı. `collector` yazılmaya başlanmadan önce bu
kararların verilmesi gerekiyor, çünkü mesajın ne taşıdığı hem `collector`'ın hem de onu
okuyan `ingestor` ve `face-worker`'ın tasarımını belirliyor.

Kararları etkileyen koşullar şunlar:

- Interpol API'si istek sıklığına duyarlı. ADR 0002'de Kafka'nın seçilme sebeplerinden biri,
  bir hata sonrasında olayların API'ye yeniden gidilmeden işlenebilmesiydi.
- Toplam kayıt sayısı birkaç bin; tarama günde birkaç kez yapılıyor. Trafik düşük.
- Bir kaydın Interpol listesinden kalktığını anlamak için o anki listenin tamamını görmek
  gerekiyor. Bir sorgu en fazla 160 sonuç döndüğü için tek bir tarama çok sayıda sorgudan
  oluşuyor ve bunlardan biri başarısız olabilir.
- Mesajlar kişisel veri içeriyor. ADR 0004, listeden kalkan kayıtların 50 gün sonra kalıcı
  olarak silinmesini öngörüyor.

## Karar

### Olaylar ve topic

Notice olayları tek bir topic'e yazılacak: `interpol.notices.v1`. İki olay türü var:

- `notice.observed`: `collector` bir kaydı listede gördü. Mesaj kaydın detayını ve
  fotoğraf listesini (`picture_id`'ler; fotoğrafların kendisi değil) **tam olarak** taşır.
- `notice.removed`: kayıt, tamamlanmış iki tarama arasında listeden kalktı. Mesaj yalnızca
  `entity_id` taşır.

Her iki olayın key'i `entity_id`. Aynı kaydın olayları aynı partition'a düştüğü için
`observed` ve `removed` olaylarının sırası korunur. Topic adındaki `v1`, mesaj şemasının
geriye uyumsuz bir değişikliği için yeni bir topic açılabilmesini sağlar.

### Mesaj biçimi

Mesajlar UTF-8 JSON olarak yazılacak ve `libs/contracts` içindeki pydantic modelleriyle
tanımlanacak; producer ve consumer aynı modeli kullanacak. Her mesaj aynı zarfı taşır:

| Alan | Açıklama |
| --- | --- |
| `event_id` | UUID. Aynı olayın tekrar geldiğini anlamak ve loglarda izlemek için |
| `event_type` | `notice.observed` veya `notice.removed` |
| `schema_version` | Şu an `1` |
| `occurred_at` | Olayın UTC zamanı |
| `scan_id` | Olayı üreten taramanın kimliği |
| `entity_id` | Kaydın Interpol kimliği; key ile aynı |
| `payload` | `observed` için detay, fotoğraf listesi ve `content_hash`; `removed` için boş |

`content_hash`, gelen verinin SHA-256 özeti olarak `collector` tarafından hesaplanacak;
`ingestor` veritabanındaki özetle aynıysa yazmayı atlayacak (ADR 0004).
`payload`, Interpol'ün cevabını geldiği gibi taşır; değerler `collector`'da temizlenmez,
dönüştürülmez veya yeniden adlandırılmaz (örneğin `height` için gelen `0` değeri `0` olarak
kalır, doğum tarihi metin olarak kalır). Bu dönüşümler consumer'ların işidir. Böylece
dönüşüm kodundaki bir hata düzeltildiğinde olaylar Interpol'e yeniden gidilmeden işlenebilir.
Interpol'ün eklediği ve modelde tanımlı olmayan alanlar da atılmadan taşınır.

Şema değişikliklerinde yeni alan yalnızca varsayılan değerle eklenebilir; alan silmek veya
anlamını değiştirmek yeni bir `schema_version` ve gerekirse yeni bir topic gerektirir.

### Listeden kalkan kayıtlar

Kaldırma kararını `collector` verecek. `collector`, son **tamamlanmış** taramada gördüğü
`entity_id` listesini yerel bir dosyada tutacak. Yeni tarama da eksiksiz biterse iki listeyi
karşılaştıracak ve yalnızca eskisinde olup yenisinde olmayan kayıtlar için `notice.removed`
gönderecek. Sorgulardan biri başarısız olan ya da 160 sınırı yüzünden eksik kalan bir
taramadan sonra hiçbir kaldırma olayı gönderilmeyecek. Dosya kaybolursa ilk tarama yalnızca
yeni bir referans liste oluşturacak. Bu kurgu hata durumunda kayıt silmek yerine silmemeyi
seçiyor.

### Topic ayarları

- Partition sayısı **3**. Paralellik üst sınırı iki veya üç `ingestor` örneği için yeterli.
  Partition sayısını sonradan artırmak key'lerin partition'lara dağılımını değiştirip geçiş
  anında sırayı bozabileceği için bu sayı baştan belirlendi.
- Retention **7 gün** (`retention.ms`), `cleanup.policy=delete`.
- Geliştirme ortamında tek broker olduğu için replication factor 1; production'da 3.
- Broker'da `auto.create.topics.enable=false`. Topic'ler, migration'lar gibi servislerden
  önce bir kez çalışan ayrı bir adımda, bu ayarlarla oluşturulacak.

### Producer ve consumer davranışı

- İstemci kütüphanesi `confluent-kafka`; broker image'ı `apache/kafka`, sürümü sabitlenmiş
  olarak. Kafka 4.0'dan beri yalnızca KRaft modunda çalışıyor.
- Producer `acks=all` ve `enable.idempotence=true` ile çalışacak; kapanmadan önce `flush()`
  çağrılacak.
- Her consumer kendi consumer group'unu kullanacak (`ingestor`, `face-worker`).
- At-least-once için consumer'lar `enable.auto.offset.store=false` ile çalışacak; bir mesaj
  başarıyla işlendikten sonra `store_offsets()` çağrılacak, commit'i ise arka plandaki
  otomatik commit yapacak. Böylece yalnızca işlenmiş mesajların offset'i commit edilir.
- Bir mesaj 3 denemede işlenemezse consumer'ın kendi dead letter topic'ine yazılacak
  (`ingestor.dlq`, `face-worker.dlq`), ardından offset'i ilerletilecek. Aynı mesaj bir
  consumer için bozuk, diğeri için sorunsuz olabileceğinden DLQ consumer başına tutuluyor.

### Fotoğraflar

ADR 0004'te belirlendiği gibi fotoğrafları `face-worker` indirecek. `notice.observed`
mesajı yalnızca `picture_id`'leri taşıyacak; `face-worker` kendi istek gecikmesiyle
Interpol'den indirip MinIO'ya yazacak.

## Değerlendirilen alternatifler

**Yalnızca bildirim taşıyan mesajlar.** Mesaj "şu kayıt değişti" deyip detayı taşımazdı;
mesajlar küçük olur ve Kafka'da kişisel veri durmazdı. Ancak her consumer detayı almak için
Interpol'e gitmek zorunda kalırdı: yeniden işleme API'ye bağımlı hale gelir ve ADR 0002'deki
gerekçe ortadan kalkardı. Reddettik.

**Her olay türü için ayrı topic.** Consumer'lar yalnızca ilgilendikleri olayı okurdu. Ancak
Kafka sırayı yalnızca bir partition içinde garanti ettiği için, aynı kaydın `observed` ve
`removed` olayları farklı sırayla işlenebilir ve silinmiş bir kayıt yeniden canlanabilirdi.
Reddettik.

**Kaldırma kararını `ingestor`'ın vermesi.** `ingestor`, `last_seen_at` belirli bir süreden
eski olan kayıtları silebilirdi; `collector`'ın durum tutması gerekmezdi. Ancak `collector`
bir süre çalışmazsa bütün kayıtlar silinirdi. Hata durumunda veri kaybettiren bir tasarımı
reddettik.

**Log compaction.** Topic her key'in son mesajını süresiz saklar; mevcut durumun tamamı her
zaman yeniden işlenebilirdi. Ancak artık aranmayan kişilerin verisi Kafka'da süresiz kalırdı;
bu ADR 0004'teki 50 günlük saklama politikasıyla çelişiyor. Reddettik.

**Avro veya Protobuf ve Schema Registry.** Şema uyumluluğunu broker tarafında zorlar ve
mesajları küçültür. Ancak ayrı bir servis çalıştırmayı ve ek bir araç zincirini gerektiriyor;
iki servisin aynı Python modelini kullandığı bu ölçekte getirisi yok. Sistem büyürse yeniden
değerlendirilecek.

**Otomatik commit'i tamamen kapatıp her mesajda elle commit etmek.** Aynı garantiyi verir,
ancak her mesaj için ayrı bir commit isteği yapar. `store_offsets()` ile arka plandaki
otomatik commit'in birlikte kullanılması Confluent'ın önerdiği kalıp ve daha az istek yapıyor.

**`aiokafka` veya `kafka-python`.** `aiokafka` asyncio ile çalışan servisler için uygun, ancak
servislerimiz senkron. `kafka-python` uzun süre bakımsız kaldı. `confluent-kafka` resmi
`librdkafka` istemcisini kullanıyor ve 2.13 sürümünden beri tip ipuçları içeriyor.

**Fotoğrafları `collector`'ın indirmesi.** Interpol'e yalnızca bir servis gitmiş olurdu ve
mesaj fotoğrafın MinIO'daki yerini taşırdı. Ancak yavaş ve hata yapmaya açık bir iş
`collector`'ın tarama döngüsüne bağlanırdı ve ADR 0004 değişirdi. `face-worker`'ın indirmesini
koruduk.

## Sonuçlar

Olaylar Interpol'e gidilmeden 7 gün boyunca yeniden işlenebilecek. Aynı kaydın olayları
sırasıyla işlenecek ve eksik bir tarama hiçbir kaydı sildirmeyecek. `ingestor` ve
`face-worker` aynı topic'i birbirinden bağımsız okuyacak, bozuk bir mesaj bir consumer'ı
durdurmayacak.

Buna karşılık bazı bedelleri kabul ediyoruz. Mesajlar kişisel veri taşıdığı için bu veri
Kafka'da 7 gün kalacak; Kafka'ya erişim kısıtlı tutulmalı ve mesaj içerikleri loglanmamalı.
`collector` artık durum tutan bir servis: son taramanın listesi için kalıcı bir alan
(Docker volume) gerekiyor. Interpol'e iki servis (`collector` ve `face-worker`) istek atacak;
toplam istek sıklığı tek bir yerden kontrol edilmiyor. Partition sayısının sonradan
değiştirilmesi pahalı. Topic'leri oluşturan ayrı bir adımın Docker Compose'da tanımlanması
gerekecek. Şema değişikliklerinde uyumluluk kuralları yalnızca code review ve testlerle
korunuyor.
