# ADR 0002: Servisler arası mesajlaşma için Kafka

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-15
- **Karar verenler:** Serdar Kafalı

## Bağlam

Sistemdeki veri akışı birden fazla servise yayılıyor. `collector` Interpol API'sinden Red
Notice kayıtlarını çekiyor, `ingestor` bu kayıtları PostgreSQL'e yazıyor, `face-worker` ise
aynı kayıtların fotoğraflarını işleyip yüz benzerliği için gerekli verileri üretiyor. Bu
servislerin birbirini doğrudan çağırmasını istemiyoruz. `collector`, `ingestor` kapalıyken
veya yavaş çalışırken de veri toplamaya devam edebilmeli; hiçbir servis bir diğerinin hızına
veya ayakta olmasına bağlı kalmamalı. Bu yüzden servisler arasında asenkron çalışan bir mesaj
altyapısına ihtiyacımız var.

Projenin ilk sürümünde bu iş için RabbitMQ kullanılıyordu ve tek bir producer ile tek bir
consumer vardı. Yeni sistemde ihtiyaçlar değişti. Birincisi, aynı olayın birden fazla servis
tarafından birbirinden bağımsız okunması gerekiyor: `collector`'ın ürettiği bir kayıt hem
`ingestor` hem `face-worker` tarafından, kendi hızlarında ve birbirlerinden habersiz
işlenmeli. İkincisi, bir hata sonrasında olayların yeniden işlenebilmesi gerekiyor:
`ingestor`'daki bir hata yüzünden veritabanına yanlış veri yazılırsa, hata düzeltildikten
sonra aynı olayları Interpol API'sine yeniden istek atmadan baştan işleyebilmeliyiz. Interpol
API'si istek sıklığına duyarlı olduğu için veriyi tekrar çekmek pahalı ve riskli. Üçüncüsü,
aynı kişiye ait olayların geldikleri sırayla işlenmesi gerekiyor; bir kaydın eski bir
sürümünün yenisinin üzerine yazılmaması lazım.

Bunlara ek olarak, merkezi loglama için kurulacak hatta (ADR 0003) Logstash'in önünde bir
tampon kullanma ihtimalimiz var. Mesajlaşma ve log tamponu için aynı altyapının
kullanılabilmesi, işletilmesi gereken sistem sayısını azaltır.

## Karar

Servisler arası asenkron mesajlaşma için Apache Kafka kullanacağız. Kafka, ZooKeeper
gerektirmeyen KRaft modunda çalıştırılacak. Geliştirme ortamında Docker Compose ile tek bir
broker yeterli olacak.

Teslim garantisi olarak **at-least-once** kullanacağız: hiçbir mesaj kaybolmayacak, ancak
bir mesaj nadiren birden fazla kez teslim edilebilecek. Consumer'lar offset'lerini ancak
mesajı başarıyla işledikten sonra kaydedecek. Bu yüzden tüm consumer'lar **idempotent**
yazılmak zorunda: aynı mesajı iki kez işlemek, bir kez işlemekle aynı sonucu vermeli.
Veritabanına yazan consumer'lar bunu kaydın `entity_id` alanı üzerinden upsert yaparak
sağlayacak.

Bir Red Notice kaydıyla ilgili mesajlarda key olarak `entity_id` kullanılacak. Kafka aynı
key'e sahip mesajları aynı partition'a yazdığı ve bir partition içindeki sırayı koruduğu
için, aynı kişiye ait olaylar geldikleri sırayla işlenecek.

Mesajları tüketen her servis kendi consumer group'unu kullanacak. Böylece `ingestor` ve
`face-worker` aynı topic'i birbirlerini etkilemeden okuyabilecek. Mesajların şemaları
ADR 0001'de belirlendiği gibi `libs/contracts` içinde tanımlanacak; producer ve consumer aynı
tanımı kullanacak.

Topic isimleri, partition sayıları, retention süreleri ve mesaj şemalarının ayrıntıları bu
ADR'nin kapsamında değil; ilgili servisler yazılırken ayrıca belirlenecek. Kafka'nın log
hattında tampon olarak kullanılıp kullanılmayacağı da ELK kurulumuyla ilgili ADR'de
kararlaştırılacak.

## Değerlendirilen alternatifler

**RabbitMQ'da kalmak.** RabbitMQ'yu ilk sürümden tanıyoruz; kurulumu ve işletilmesi
Kafka'dan daha hafif, esnek yönlendirme (routing), mesaj önceliği ve gecikmeli mesaj gibi
özellikleri var. Ancak klasik bir kuyruk olarak çalışıyor: bir mesaj onaylandıktan sonra
kuyruktan siliniyor, bu yüzden geçmiş olayları yeniden işlemek mümkün değil. Aynı mesajın
birden fazla servise gitmesi için exchange ve servis başına ayrı kuyruklar kurmak gerekiyor
ve sonradan eklenen bir servis geçmiş mesajları göremiyor. RabbitMQ'nun stream özelliği bu
eksiklerin bir kısmını kapatıyor, ancak bu durumda Kafka'nın modelini daha küçük bir ekosistem
ve daha az kaynakla kullanmış oluruz. Yeniden işleme ve bağımsız tüketici ihtiyaçlarımız
temel olduğu için bu seçeneği reddettik.

**Redis Streams.** Hafif ve kurulumu kolay; offset ve consumer group kavramları Kafka'ya
benziyor. Ancak veriyi esas olarak bellekte tutuyor; uzun süreli saklama ve dayanıklılık
Kafka kadar güçlü değil ve Python tarafındaki araç ekosistemi daha dar. Olayları günlerce
saklayıp yeniden işleyebilmek istediğimiz için reddettik.

**Mesaj altyapısı kullanmamak.** Servisler birbirini doğrudan HTTP ile çağırabilir ya da
veritabanındaki bir tablo kuyruk olarak kullanılabilir. Ek bir altyapı gerektirmediği için en
basit seçenek bu. Ancak HTTP ile servisler birbirine sıkıca bağlanır: `ingestor` kapalıyken
`collector` ya veri kaybeder ya da kendi tamponunu yazmak zorunda kalır. Veritabanını kuyruk
olarak kullanmak ise birden fazla bağımsız okuyucuyu, sıralamayı ve yeniden işlemeyi elle
yazmayı gerektirir; bu da Kafka'nın hazır sunduğu şeyleri kötü bir şekilde yeniden yazmak
olur. Bu seçeneği de reddettik.

**Exactly-once teslim garantisi.** Kafka'nın transaction özelliği, Kafka'dan okuyup yine
Kafka'ya yazan işlemlerde bir mesajın etkisinin tam olarak bir kez oluşmasını sağlıyor.
Ancak bizim consumer'larımız sonucu PostgreSQL'e ve MinIO'ya yazıyor; bu sistemler Kafka
transaction'ının parçası olamadığı için orada yine idempotency gerekiyor. Ek karmaşıklığa
karşılık bize bir güvence sağlamadığı için at-least-once ve idempotent consumer yaklaşımını
seçtik.

## Sonuçlar

`collector`'ın ürettiği olaylar retention süresi boyunca Kafka'da saklanacak. Bir hata
sonrasında bir consumer group'un offset'i geri alınarak olaylar Interpol API'sine yeniden
istek atılmadan baştan işlenebilecek. Yeni bir servis eklendiğinde mevcut servislere
dokunmadan kendi consumer group'uyla aynı olayları okuyabilecek. `collector` ile diğer
servisler birbirinin ayakta olmasından bağımsız çalışacak ve key olarak `entity_id`
kullanıldığı için aynı kişiye ait olayların sırası korunacak.

Buna karşılık bazı bedelleri kabul ediyoruz. Kafka'yı çalıştırmak RabbitMQ'ya göre daha
karmaşık ve daha fazla bellek istiyor; geliştirme ortamında bile ayrı bir container olarak
yönetilmesi gerekecek. At-least-once garantisi nedeniyle her consumer idempotent yazılmak
zorunda. Bu bir kodlama disiplini ve testlerde aynı mesajın iki kez işlenmesi senaryosu
özellikle doğrulanmalı. Bu karar veritabanı tasarımını da doğrudan etkiliyor: Red Notice
kayıtları `entity_id` üzerinde tekil (unique) bir kısıta sahip olmalı. Kafka'da mesaj
önceliği ve gecikmeli mesaj gibi özellikler yok; ileride ihtiyaç olursa uygulama tarafında
çözülmesi gerekecek. Retention süresi dolan mesajlar silindiği için Kafka kalıcı bir veri
kaynağı değil; sistemin asıl kaynağı PostgreSQL olmaya devam edecek. Son olarak Kafka'ya
bağlı kodun testlerinde gerçek bir broker gerekeceği için testcontainers kullanılacak; bu da
test sürelerini uzatacak.
