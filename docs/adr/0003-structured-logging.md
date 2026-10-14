# ADR 0003: structlog ile JSON formatında, stdout'a yazan structured logging

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-15
- **Karar verenler:** Serdar Kafalı

## Bağlam

Sistem birbirinden bağımsız çalışan dört servisten oluşacak (`collector`, `ingestor`,
`face-worker`, `api`) ve bunların hepsi Docker container'ları içinde koşacak. Bir kaydın
Interpol API'sinden çekilip veritabanına yazılmasına kadar geçen yol birden fazla servise ve
Kafka'ya yayılıyor. Bir sorun çıktığında "hangi servis, ne zaman, hangi kayıt için, hangi
seviyede" sorularını tüm servislerin logları üzerinde tek bir yerden cevaplayabilmemiz
gerekiyor. Bu yüzden loglar merkezi olarak ELK yığınında (Logstash, Elasticsearch, Kibana)
toplanacak, aranacak ve uyarılar buradan üretilecek.

Logların bu şekilde kullanılabilmesi için üç gereksinim var. Birincisi, logların makine
tarafından ayrıştırılabilir olması: `"Collector started with page size 20"` gibi düz bir
metinde `page_size` değerine göre filtrelemek için metni regex ile parçalamak gerekir;
değerlerin baştan ayrı alanlar olarak üretilmesi bu işi ortadan kaldırır. İkincisi, dört
servisin birebir aynı alan adlarını ve aynı zaman biçimini kullanması; aksi halde Kibana'da
tek bir sorgu tüm servislerde çalışmaz. Üçüncüsü, yalnızca bizim kodumuzun değil, standart
`logging` modülüyle log yazan üçüncü parti kütüphanelerin (Kafka istemcisi, SQLAlchemy, HTTP
istemcisi) loglarının da aynı biçimde çıkması.

Projenin bir de gizlilik kısıtı var: Red Notice kayıtları gerçek kişilere ait kişisel veriler
içeriyor. Bu verilerin, erişimi daha az denetlenen log sistemine sızmaması gerekiyor.

## Karar

Structured logging için structlog kullanacağız. Her log kaydı bir olay adı (`event`) ve
anahtar-değer alanlarından oluşan bir sözlük olarak üretilecek. Olay adları sabit
`snake_case` isimler olacak (`notice_fetched`); değişken bilgiler olay adına gömülmeyecek,
ayrı alanlar olarak verilecek.

Kurulum kodu tek bir yerde, `libs/observability` kütüphanesinde duracak. Her servis
`main()` fonksiyonunun en başında `configure_logging()` fonksiyonunu bir kez çağıracak ve
logger'ları `get_logger(__name__)` ile alacak. Kütüphane ortam değişkeni okumayacak;
servis adı, seviye ve format ona parametre olarak verilecek. Bu değerleri her servis kendi
ayar sınıfından (pydantic-settings) okuyacak.

Her log satırı en az şu alanları taşıyacak: `event`, `level`, `logger`, `service` ve
UTC saatiyle ISO 8601 biçiminde `timestamp`. Production'da çıktı tek satırlık JSON olacak.
Geliştirme sırasında okunabilirlik için renkli bir konsol formatı da seçilebilecek; iki mod
arasında yalnızca son adım (renderer) farklı olacak.

Standart `logging` modülüyle entegrasyonu structlog'un `ProcessorFormatter` yaklaşımıyla
yapacağız. Bizim loglarımız ve üçüncü parti kütüphanelerin logları aynı processor zincirinden
geçecek ve aynı formatter tarafından aynı JSON'a çevrilecek. Tek bir handler root logger'a
bağlanacak ve çıktıyı **stdout**'a yazacak.

Uygulamalar logları hiçbir yere göndermeyecek. Logların toplanması ve taşınması altyapının
işi olacak: Docker container'ların stdout'unu yakalayacak, bir log toplayıcı bu çıktıyı
Logstash'e iletecek. Bu hattın ayrıntıları ELK fazında ayrı bir ADR ile belirlenecek.

Loglara kişisel veri yazılmayacak. Bir kayda referans gerektiğinde yalnızca kimlikler
(`interpol_id` gibi) kullanılacak; isim, doğum tarihi, uyruk veya fotoğraf bilgisi
loglanmayacak.

## Değerlendirilen alternatifler

**Standart `logging` ve bir JSON formatter (örneğin python-json-logger).** Yeni bir
logging API'si öğrenmeyi gerektirmediği için cazip bir seçenek. Ancak her alanın
`extra={...}` ile elle verilmesi gerekiyor; context ekleme, processor zinciri ve test
yardımcıları gibi araçlar yok. structlog dokümanı da bu yöntemde context ve exception
bilgisinin formatter doğru kurulmazsa kaybolabileceği konusunda uyarıyor. Aynı sonucu daha
fazla elle iş ve daha az güvenceyle verdiği için reddettik.

**structlog'u standart `logging` ile entegre etmeden kullanmak, yani render'ı tamamen
structlog içinde yapmak.** Kurulumu en basit seçenek bu. Ancak bu durumda yalnızca bizim
yazdığımız loglar JSON olur; üçüncü parti kütüphanelerin `logging` ile yazdığı satırlar düz
metin olarak kalır ve Logstash bunları ayrıştıramaz. Üçüncü gereksinimi karşılamadığı için
reddettik.

**Loguru.** Kullanımı kolay ve popüler bir kütüphane. Ancak standart `logging`'in yerine
geçecek şekilde tasarlanmış; üçüncü parti kütüphanelerin loglarını yakalamak için ayrıca bir
yönlendirme handler'ı yazmak gerekiyor. structlog ise standart `logging`'in üzerine oturmak
için tasarlanmış ve bu entegrasyonu resmi olarak belgeliyor. Bu yüzden structlog'u tercih
ettik.

**Uygulamanın logları doğrudan Logstash'e göndermesi (TCP/UDP handler).** Ek bir log
toplayıcıya ihtiyaç bırakmıyor. Ancak Logstash yavaşladığında veya kapandığında uygulama ya
bekler, ya logları bellekte biriktirir ya da kaybeder; böylece log altyapısındaki bir sorun
veri toplayan servisi doğrudan etkiler. Ayrıca her servis Logstash'in adresini ve bağlantı
ayarlarını bilmek zorunda kalır ve testlerde ya da geliştirme ortamında bir Logstash'in
ayakta olması gerekir. Postgres ve Kafka gibi bizim yazmadığımız container'ların logları da
bu yolla toplanamaz. Bu yüzden reddettik.

**Logları dosyaya yazmak.** Container'lar geçici olduğu için dosyaların bir volume'e
bağlanması, döndürülmesi (rotation) ve ayrıca okunması gerekir. Docker bu işleri stdout
için zaten yapıyor; aynı işi uygulama içinde tekrar yapmanın bir faydası yok.

## Sonuçlar

Dört servisin ve kullandıkları kütüphanelerin logları aynı alanlara sahip tek tip JSON
satırları olarak çıkacak. Kibana'da `service`, `level` veya `event` alanlarıyla tek bir sorgu
tüm sistemde çalışacak; Logstash'in metni regex ile ayrıştırması gerekmeyecek. Uygulamalar
log altyapısından habersiz olduğu için Logstash'in kapanması servisleri etkilemeyecek, log
hedefini değiştirmek de kod değişikliği gerektirmeyecek. `configure_logging` parametre
aldığı için testlerde ortam değişkenlerine dokunmadan kurulabilecek.

Buna karşılık bazı bedelleri kabul ediyoruz. structlog'un processor zinciri ve
`ProcessorFormatter` köprüsü, düz `logging` kurulumuna göre öğrenilmesi daha zor bir
yapı. `cache_logger_on_first_use` performans için açık olduğundan yapılandırma ilk
kullanımdan sonra donuyor; bu yüzden `configure_logging` uygulamanın en başında ve bir kez
çağrılmak zorunda. Ayarlar okunurken oluşan bir hata, logging henüz kurulmadığı için JSON
olarak değil düz traceback olarak çıkacak. Seviyenin altında kalan kayıtlar processor
zincirinden geçtikten sonra eleniyor; yoğun debug logu yazılan bir yerde bu gereksiz iş
yaratırsa zincirin başına `filter_by_level` eklenecek.

Logların toplanması için ayrıca bir log toplayıcı kurmamız ve Docker'ın her satırı kendi
JSON zarfına koyduğunu hesaba katarak onu ayarlamamız gerekecek. Python çıktıyı bir dosyaya
veya pipe'a yazarken tamponladığı için container'larda `PYTHONUNBUFFERED=1` ayarlanmalı;
aksi halde loglar gecikebilir ve çökme anındaki son satırlar kaybolabilir. Docker'ın log
dosyaları için boyut sınırı da konmalı. Son olarak kişisel veri loglanmaması kuralı şu an
yalnızca code review ile korunuyor; inceleyenlerin log çağrılarındaki alanlara özellikle
dikkat etmesi gerekecek.
