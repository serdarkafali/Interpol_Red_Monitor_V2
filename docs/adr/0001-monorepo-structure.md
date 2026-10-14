# ADR 0001: uv workspace ile monorepo yapısı

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-10
- **Karar verenler:** Serdar Kafalı

## Bağlam

Interpol Red Notice Monitor projesini sıfırdan yeniden yazıyoruz. Yeni sistem, birbirinden
bağımsız olarak deploy edilebilen dört servisten oluşuyor: Interpol API'sinden kayıtları çeken
`collector`, bu kayıtları PostgreSQL'e yazan `ingestor`, fotoğraf ve yüz analizi yapan
`face-worker` ve web arayüzüne veri sunan `api`. Servisler birbiriyle Kafka üzerinden
haberleşiyor.

Projenin ilk sürümünde tüm kod `common/`, `producer/` ve `consumer/` klasörlerinden oluşan tek
bir Python projesinde duruyordu. Bu yapı somut sorunlara yol açtı. Modüller ancak `pytest.ini`
dosyasına `pythonpath = . producer consumer` satırı eklendikten sonra import edilebiliyordu ve
kodda iki farklı import biçimi bir arada kullanılıyordu (`from queue_consumer import ...` ve
`from consumer.processor import ...`). `common/` klasörü zamanla her şeyin atıldığı ortak bir
alana dönüştü; aynı anda veritabanı modellerini, MinIO erişimini ve RabbitMQ bağlantı kodunu
barındırdığı için her servis her şeye bağımlı hale geldi. Bir servisin başka bir servisin iç
koduna erişmesini engelleyen hiçbir sınır yoktu.

Yeni servislerin bağımlılık ihtiyaçları da birbirinden çok farklı. `face-worker`, bir ila iki
gigabayt tutan makine öğrenmesi kütüphanelerine ihtiyaç duyacak; `collector` ise yalnızca bir
HTTP istemcisi ve bir Kafka producer'ı kullanacak. Öte yandan `collector` ve `ingestor`
aralarında gidip gelen mesajların biçimi konusunda birebir anlaşmak zorunda ve bu biçimdeki bir
değişikliğin iki tarafa da aynı anda ulaşması gerekiyor.

Proje tek kişi ya da küçük bir ekip tarafından geliştiriliyor. Bu yüzden repo yapısının günlük
çalışmaya getirdiği yük, teorik olarak ne kadar "doğru" olduğu kadar önemli.

## Karar

Tüm servisleri tek bir Git reposunda, uv workspace olarak düzenlenmiş şekilde tutacağız. Her
servis `services/` altında kendi paketinde yaşayacak; kendi `pyproject.toml` dosyası, testleri
ve Dockerfile'ı olacak ve yalnızca gerçekten kullandığı bağımlılıkları tanımlayacak. Birden
fazla servise gerçekten ait olan kod `libs/` altındaki küçük kütüphanelerde duracak:
servisler arasındaki anlaşmayı oluşturan Kafka event şemaları için `libs/contracts`, ortak log
ve metrik ayarları için `libs/observability`. Workspace'in tamamı repoya commit edilen tek bir
`uv.lock` dosyasını paylaşacak.

`libs/` altına iş mantığı koymayacağız. Bir kütüphane oraya ancak en az iki servis ona
ihtiyaç duyuyorsa ve bir sözleşmeyi ya da servisler arası ortak bir konuyu (log, metrik gibi)
tanımlıyorsa eklenecek; geri kalan her şey sahibi olan serviste kalacak.

## Değerlendirilen alternatifler

**Polyrepo (her servis için ayrı repo).** Bu yaklaşım her servise tamamen bağımsız sürümleme
ve yayın döngüsü sağlar; farklı servislerin farklı ekiplere ait olduğu organizasyonlara
uygundur. Bizim için ise `contracts` paketini ayrı bir paket olarak yayınlayıp sürümlemek, tek
bir şema değişikliği için birden fazla koordineli pull request açmak ve CI ile araç
yapılandırmasını her repoda tekrar etmek anlamına gelirdi. Bu yük, bizim ekip büyüklüğümüzde
ihtiyacımız olmayan bir bağımsızlık için ödenmiş olurdu; bu yüzden reddettik.

**Tek paket (ilk sürümdeki yapı).** Başlangıç için en basit seçenek bu, ancak yukarıda
anlatılan sorunları üreten yapı da tam olarak bu: tüm servisler için tek bir bağımlılık
listesi, ancak yapılandırma hileleriyle çalışan import yolları ve servisler arasında hiçbir
sınırın olmaması. Ayrıca `face-worker`'ın makine öğrenmesi bağımlılıklarını her Docker
imajına girmeye zorlardı. Bu seçeneği de reddettik.

## Sonuçlar

Birden fazla servisi etkileyen değişiklikler, örneğin bir event şeması değişikliği, tek bir
pull request içinde yapılıp incelenecek; producer ile consumer arasındaki uyumsuzlukları test
paketi birleştirme (merge) öncesinde yakalayacak. Her servis gerçek, kurulabilir bir paket
olacağından import'lar testlerde, geliştirici bilgisayarında ve container'ların içinde aynı
şekilde çalışacak; `pythonpath` hilelerine gerek kalmayacak. Her imaj yalnızca kendi
servisinin bağımlılıklarını kuracağı için Docker imajları küçük kalacak, tek kilit dosyası
sayesinde de her ortamda birebir aynı paket sürümleri kurulacak.

Buna karşılık bazı bedelleri kabul ediyoruz. Tüm servisler her üçüncü parti kütüphanenin aynı
sürümünü kullanmak zorunda; bir gün iki servis aynı paketin birbiriyle uyumsuz sürümlerine
ihtiyaç duyarsa workspace modeli buna izin vermeyecek ve bu kararı yeniden gözden geçirmemiz
gerekecek. CI'ın yalnızca değişiklikten etkilenen servisleri derleyip test edecek şekilde
yapılandırılması gerekecek; aksi halde her pull request tüm pipeline'ı çalıştırır. Son olarak
`libs/` altına iş mantığı konmaması kuralı yalnızca code review ile korunuyor; bu yüzden
`libs/` klasörünün yeni bir `common/` klasörüne dönüşmemesi için inceleyenlerin bu kuralı
aktif olarak gözetmesi gerekecek.
