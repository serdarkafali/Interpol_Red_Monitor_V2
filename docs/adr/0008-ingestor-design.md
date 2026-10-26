# ADR 0008: Ingestor'ın olayları veritabanına yazması

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-26
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0005'in "Producer ve consumer davranışı" bölümündeki DLQ kuralını değiştirir

## Bağlam

`collector`, `interpol.notices.v1` topic'ine üç tür olay yazıyor (ADR 0005, ADR 0006):
`notice.observed` kaydın detayını ve fotoğraf listesini taşıyor, `notice.seen` kaydın liste
taramasında görüldüğünü, `notice.removed` ise art arda iki tam taramada görülmediğini
bildiriyor. `ingestor` bu olayları okuyup ADR 0004'teki şemaya yazacak.

Kararları etkileyen koşullar şunlar:

- Teslim at-least-once: aynı olay birden fazla kez gelebilir ve bir hata sonrasında topic
  baştan okunabilir. Her yazma idempotent olmalı.
- Aynı kaydın olayları aynı partition'da ve sırayla geliyor (key `entity_id`).
- Olaylar Interpol'ün cevabını geldiği gibi taşıyor; temizleme ve dönüştürme consumer'ın
  işi (ADR 0005). Kaynak kontrolümüzde değil: bir alan bir gün beklenmedik bir değerle
  gelebilir.
- Yeni bir kaydın `notice.seen` olayı, detayı çekilmeden önce gelir. Detay ancak sonraki
  adımlarda `notice.observed` olarak gelir.
- ADR 0005, bir mesaj 3 denemede işlenemezse DLQ'ya yazılmasını öngörüyordu. Bu kural
  veritabanının kısa bir süre erişilemediği durumda topic'teki her mesajı DLQ'ya
  boşaltırdı.

## Karar

### Okuma ve offset

`ingestor` consumer group'uyla okunacak. ADR 0005'teki kalıp korunuyor:
`enable.auto.offset.store=false`, mesajın veritabanı transaction'ı commit edildikten sonra
`store_offsets()`, commit'i arka plandaki otomatik commit yapıyor. Ek olarak
`auto.offset.reset=earliest`: group'un kayıtlı bir offset'i yoksa (ilk açılış) topic baştan
okunur. Varsayılan `latest`, `ingestor`'dan önce yazılmış olayları sessizce atlardı.

Her mesaj tek bir transaction'da işlenir. Commit ile offset kaydı arasında çökülürse mesaj
tekrar gelir; yazmalar idempotent olduğu için sonuç değişmez.

### Olayların karşılığı

**`notice.observed`:**

- Veritabanındaki `content_hash` olaydakiyle aynıysa hiçbir şey yazılmaz.
- Farklıysa ya da kayıt yoksa `INSERT ... ON CONFLICT (entity_id) DO UPDATE` ile yazılır.
  Yeni kayıtta `last_seen_at` olayın zamanı olur.
- Uyruklar ve tutuklama emirleri silinip yeniden yazılır. Emirlerin doğal bir anahtarı yok;
  bir emrin değiştiği ile yenisinin geldiği güvenilir şekilde ayırt edilemiyor.
- Fotoğraflar `picture_id` üzerinden farkla güncellenir: yeni gelenler eklenir, listeden
  çıkanlar silinir, kalanlara dokunulmaz. Böylece `face-worker`'ın yazacağı indirme
  bilgisi (`object_key`, `sha256`, `downloaded_at`) bir güncellemede kaybolmaz.
- `countries` listesinde olmayan ama biçimi doğru (iki büyük harf) bir kod, adı boş olarak
  eklenir ve uyarı loglanır (ADR 0004).
- `deleted_at`'e dokunulmaz. Kaydın listede olup olmadığının kaynağı liste taramasıdır;
  bunu yalnızca `notice.seen` ve `notice.removed` değiştirir.

**`notice.seen`:** `last_seen_at = GREATEST(last_seen_at, occurred_at)` ve, silinme
zamanı olaydan önceyse, `deleted_at = NULL`; kaldırılmış bir kayıt listeye döndüyse
böylece geri gelir (ADR 0004).

**`notice.removed`:** `deleted_at = occurred_at`, yalnızca `deleted_at` boşsa ve kayıt
olaydan sonra görülmemişse (`last_seen_at <= occurred_at`). Zaten silinmiş bir kaydın
silinme zamanı değişmez; 50 günlük süre ilk silinmeden sayılır.

Zaman karşılaştırmaları, olaylar sırasız işlense bile (örneğin DLQ'dan geri yüklenen eski
bir mesaj) sonucun olayların zamanına göre doğru kalmasını sağlıyor: eski bir `seen`
zamanı geri götüremez ve daha yeni bir silmeyi geri alamaz; eski bir `removed` daha sonra
görülmüş bir kaydı silemez.

Veritabanında olmayan bir kayıt için gelen `seen` ve `removed` hiçbir şey yapmaz. `seen`
için bu normal: detay henüz çekilmedi ve kayıt `observed` ile gelecek.

### Normalizasyon

Interpol'ün değerleri saf fonksiyonlarla şemanın tiplerine çevrilir:

| Alan | Kural |
| --- | --- |
| `height` | `0` → `NULL`; 2 basamağa yuvarlanır; CHECK aralığı dışı → `NULL` ve uyarı |
| `weight` | `0` → `NULL`; tamsayıya yuvarlanır; CHECK aralığı dışı → `NULL` ve uyarı |
| `date_of_birth` | `YYYY/MM/DD` ayrıştırılır; olmazsa `NULL` ve uyarı |
| `sex_id` | `M`, `F`, `U` dışı → `NULL` ve uyarı |
| İsteğe bağlı metinler | Boş ya da yalnızca boşluk → `NULL` |
| Dil, göz ve saç kodları | `null` → boş dizi |
| Uyruklar ve fotoğraflar | Tekrar eden değerler bir kez yazılır |
| Uyruk ve doğum ülkesi | Biçimi bozuk kod (iki büyük harf değil) atılır ve uyarı loglanır |
| Tutuklama emri | Ülke kodu bozuksa ya da `charge` boşsa **kayıt reddedilir** |

Tek bir bozuk tanımlayıcı alan yüzünden kaydın tamamı kaybolmasın diye çoğu alan `NULL`'a
düşüyor. Tutuklama emri bir red notice'in dayanağı olduğu için emrin bozuk olması kaydın
reddedilmesi için yeterli sayılıyor; böyle bir kayıt DLQ'ya gider.

Normalizasyon kuralları değiştirildiğinde olayların yeniden işlenebilmesi için
`INGESTOR_IGNORE_CONTENT_HASH=true` ayarı özet karşılaştırmasını kapatır. Varsayılan
`false`.

### Hatalar

ADR 0005'teki "3 denemeden sonra DLQ" kuralı yerine hatalar sınıflandırılır:

| Hata | Ne yapılır |
| --- | --- |
| Mesaj bir notice olayı değil (`ValidationError`) | Hemen DLQ; tekrar denemek aynı sonucu verir |
| Normalizasyon kaydı reddetti | Hemen DLQ |
| Veritabanı veriyi reddetti (`IntegrityError`, `DataError`) | Rollback, hemen DLQ |
| Veritabanına ya da DLQ'ya erişilemiyor | DLQ'ya yazılmaz; beklenip aynı mesaj yeniden denenir |
| Beklenmeyen başka bir hata | Servis durur; offset kaydedilmediği için mesaj kaybolmaz |

Geçici hatada consumer bekler (5 s'den başlayıp her seferinde ikiye katlanan, en fazla
60 s'lik backoff), sonra mesajın offset'ine `seek` edip `poll`'a döner; aynı mesaj yeniden
gelir. Bekleme `max.poll.interval.ms`'in (varsayılan 5 dakika) altında kaldığı için
consumer group'tan atılmaz. Veritabanı kapalı kaldıkça tüketim durur, sıra bozulmaz.

Beklenmeyen bir hata bir programlama hatası demek. Onu DLQ'ya yazmak her mesajı sessizce
DLQ'ya gönderebilirdi; servisin durması hatayı görünür kılar. Compose servisi yeniden
başlatır ve hata loglarda kalır.

DLQ mesajı (`ingestor.dlq`), orijinal mesajın key'i ve byte'larıyla birlikte header'larda
hata türünü, kaynak topic, partition ve offset'i taşır. `ValidationError`'ın ve veritabanı
hatalarının metni değer içerebildiği için header'lara ve loglara yalnızca hata sınıfı,
alan yolları ve SQLSTATE kodu yazılır. DLQ'ya yazma `flush` ile onaylanmadan offset
kaydedilmez.

### Yazma ve şema

Yazmalar SQLAlchemy Core ile, `interpol_db` modellerinin tablo tanımları kullanılarak
yapılır. ORM'un `merge()` önce okuyup sonra yazdığı için iki sorgu yapar ve eşzamanlı
yazmalarda çakışmaya açıktır; PostgreSQL'in `ON CONFLICT` desteği bunu tek sorguda yapıyor.

Migration'lar `ingestor` tarafından değil, Compose'daki ayrı bir `migrate` adımında
(`alembic upgrade head`) uygulanır. `ingestor` bu adımın başarıyla bitmesini bekler ve
şemayı değiştirme yetkisine ihtiyaç duymaz.

## Değerlendirilen alternatifler

**Her mesajı 3 kez deneyip DLQ'ya yazmak (ADR 0005).** Basit, ama hatanın türüne
bakmıyor: veritabanı kesintisinde sağlam mesajlar DLQ'ya gider ve sonradan elle geri
yüklenmesi gerekir. Bozuk bir mesajı ise boşuna üç kez dener. Reddettik.

**Mesajları toplu (batch) işlemek.** Her transaction'da yüzlerce mesaj yazmak daha hızlı.
Ancak trafik 6 saatte birkaç bin mesaj; tek bir bozuk mesaj tüm batch'i geri alır ve
hata işleme karmaşıklaşır. Mesaj başına transaction yeterli; gerekirse sonra
değiştirilebilir.

**Uyrukları, emirleri ve fotoğrafları hep farkla güncellemek.** Satırlara dokunmayarak daha
az yazma yapar. Emirlerin doğal anahtarı olmadığı için emirlerde güvenilir değil;
uyruklarda ise kazanç küçük. Farkla güncellemeyi yalnızca başka bir servisin veri yazdığı
fotoğraflarda kullandık.

**`notice.observed`'ün `deleted_at`'i temizlemesi.** Detayı çekilebilen bir kayıt hâlâ
duruyor gibi görünür. Ancak kaldırma kararı liste taramasına dayanıyor (ADR 0006); iki
kaynağın aynı alanı değiştirmesi, kaldırılmış bir kaydın gecikmiş bir `observed` ile
canlanmasına yol açabilirdi. Reddettik.

**Bilinmeyen bir kayıt için `seen` geldiğinde boş bir satır oluşturmak.** `name` zorunlu
olduğu için yer tutucu bir değer gerekirdi; API'de yarım kayıtlar görünürdü. Reddettik.

**Migration'ları `ingestor`'ın açılışında uygulamak.** Ek bir adım gerektirmez. Ancak
servise şema değiştirme yetkisi verir ve birden fazla örnek aynı anda migration
çalıştırmaya çalışabilir. Reddettik.

## Sonuçlar

Aynı olayın tekrar işlenmesi veritabanını değiştirmeyecek; değişmeyen bir kayıt için
yalnızca bir okuma yapılacak. Veritabanı kesintisi mesaj kaybettirmeyecek ve DLQ'yu
doldurmayacak; DLQ'da yalnızca gerçekten işlenemeyen mesajlar olacak. Bozuk bir boy veya
kilo değeri kaydı kaybettirmeyecek.

Buna karşılık bazı bedelleri kabul ediyoruz. Bir veritabanı kesintisi tüm tüketimi
durduracak; gecikme, kesinti süresi kadar olacak. Programlama hataları servisi durduracak
ve hata giderilene kadar tüketim duracak. Değeri `NULL`'a düşen alanlar yalnızca uyarı
loglarında görünecek; bunların takibi Faz 7'deki alarmlara kalıyor. Listeden çıkan bir
fotoğrafın satırı silindiğinde MinIO'daki dosyasının da silinmesi Faz 4'te `face-worker`
ile birlikte ele alınacak. Kafka ile gerçek bir entegrasyon testi henüz yok; consumer
döngüsü sahte bir consumer ile test ediliyor.
