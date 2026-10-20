# ADR 0006: Collector tarama stratejisi

- **Durum:** Kabul edildi
- **Tarih:** 2026-10-21
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0005'in iki maddesini değiştirir (aşağıda "ADR 0005'e etkisi")

## Bağlam

`collector`, Interpol listesindeki kayıtların tamamını görmek, yeni kayıtların detayını
çekmek ve listeden kalkan kayıtları tespit etmek zorunda. ADR 0005 bunun nasıl yapılacağını
API'yi ölçmeden varsaymıştı. Tasarımdan önce API `scripts/probe_interpol_filters.py` ve
`scripts/probe_interpol_paging.py` ile ölçüldü (2026-10-20). Sonuçlar:

| Ölçüm | Sonuç |
| --- | --- |
| Toplam kayıt | 6382 |
| Bir sorgunun döndürebildiği farklı kayıt | En fazla **160**. 160'ı aşan sorgularda sonraki sayfalar aynı 160 kaydı tekrar döndürüyor; hata ya da boş sayfa gelmiyor |
| Sayfa boyutu | En fazla 160; daha büyüğü istenirse 160'a indiriliyor |
| Bilinmeyen parametre | Hata vermeden görmezden geliniyor; sorgu filtresiz çalışıyor |
| Cinsiyet (`sexId`) | M 5479 + F 899 + U 4 = 6382; bütün veriyi eksiksiz bölüyor |
| Yaş (`ageMin`, `ageMax`) | 0–120 aralığı 6378 kayıt; 4 erkek kayıt hiçbir yaş aralığına girmiyor (121–250 aralığı 0). Doğum tarihi boş olan kayıtlar olduğu düşünülüyor |
| En dar grup | 40 yaşındaki erkekler 199 kişi; cinsiyet ve tek yaş birlikte bile 160'ı aşabiliyor |
| Uyruk (`nationality`) | Çalışıyor (RU 3007). Çok uyruklu kişiler birden fazla uyruk sorgusunda görünüyor |
| Tarama sırasında liste | Değişebiliyor; 6 dakikalık bir ölçümde sınırın ötesinde yeni kayıtlar belirdi |

İstekler arasında 5–8 saniye beklendiğinde saatte yaklaşık 550 istek atılabiliyor. Her
kaydın detayını ve fotoğraf listesini çekmek kayıt başına 2 istek, yani 6382 kayıt için
yaklaşık 23 saat demek.

## Karar

### İki ayrı ritim

`collector` iki işi ayrı ritimlerle yapacak:

1. **Liste taraması** (varsayılan olarak 6 saatte bir): listedeki bütün `entity_id`'leri
   toplar. Detay istemez.
2. **Detay çekme** (liste taramaları arasında sürekli): önce listede yeni görülen kayıtların,
   sonra en uzun süredir yenilenmemiş kayıtların detayını ve fotoğraf listesini çeker. Saatlik
   istek bütçesiyle her kayıt yaklaşık günde bir yenilenir. İlk dolum bir kez, yaklaşık bir
   gün sürer.

### Sorguların bölünmesi

Her sorgunun `total`'ı 160 veya daha azsa sorgu tek istekle (`resultPerPage=160`, `page=1`)
okunur; ikinci sayfa hiçbir zaman istenmez. 160'ı aşan sorgu şu sırayla bölünür:

1. **Cinsiyet:** `M`, `F`, `U`.
2. **Uyruk:** ISO 3166-1 listesindeki her ülke kodu.
3. **Yaş:** 0–120 aralığı, alt grupların her biri 160 veya daha az olana kadar ikiye bölünür.

Uyruk, yaştan önce geliyor: doğum tarihi olmayan bir kayıt, 160'ı aşmayan bir cinsiyet ve
uyruk grubundaysa yaş filtresi kullanılmadan okunur ve kaybolmaz. Tek yaşa inildiği halde
160'ı aşan bir grup kalırsa tarama **eksik** sayılır ve hata loglanır.

Her bölmeden sonra alt grupların toplamı üst grubunkiyle karşılaştırılır. Cinsiyet ve yaş
bölmelerinde toplamlar eşit olmalı; eşit değilse fark uyarı olarak loglanır (yaşta doğum
tarihi olmayan kayıtlar). Bu kontrol, yanlış yazılmış bir parametrenin API tarafından
sessizce görmezden gelinmesini de yakalar. Uyruk bölmesinde çok uyruklu kişiler yüzünden
toplam büyük olabilir; orada her kimlik bir kez sayılır.

### Taramanın tamlığı

Bir liste taraması, **bütün istekleri başarılı olduysa ve 160'ı aşan bir grup kalmadıysa**
tamdır. Toplanan farklı kimlik sayısı ile Interpol'ün toplam sayısı arasındaki fark ayrıca
bir metrik olarak loglanır; bu fark doğum tarihi veya uyruğu olmayan kayıtlardan ve tarama
sırasında değişen listeden kaynaklanabilir ve taramanın tamlığını tek başına belirlemez.

### Listeden kalkan kayıtlar

Bir kayıt, **art arda iki tam taramada** görülmezse `notice.removed` gönderilir. Tek bir
taramada görülmemesi, tarama sırasında değişen sıralama veya geçici bir API davranışı
yüzünden olabilir.

### Görülme olayı

Liste taramasında görülen her kayıt için detay taşımayan `notice.seen` olayı gönderilir;
`ingestor` bununla yalnızca `last_seen_at` alanını günceller. Olay `interpol.notices.v1`
topic'ine, key olarak `entity_id` ile yazılır; böylece aynı kaydın `observed`, `seen` ve
`removed` olayları sırasıyla işlenir.

## ADR 0005'e etkisi

- `notice.observed` artık her taramada değil, yalnızca detay çekildiğinde gönderilir.
- Olay türlerine `notice.seen` eklenir.
- `notice.removed` için tek yerine iki tam tarama aranır.

ADR 0005'in geri kalanı (topic, mesaj biçimi, ham veri, partition, retention, DLQ) geçerli.

## Değerlendirilen alternatifler

**Her taramada her kaydın detayını çekmek.** ADR 0005'in varsaydığı yol buydu; her olay
güncel veriyi taşırdı. Ancak bir tarama yaklaşık 23 saat sürerdi; liste değişikliklerini
saatler içinde görmek mümkün olmazdı. Reddettik.

**Sayfalama ile 160 sınırını aşmak.** En basit yol olurdu, ancak ölçümde 160'tan sonraki
sayfalar aynı kayıtları tekrar döndürdü. Mümkün değil.

**Önce yaşa, sonra uyruğa göre bölmek.** Daha az istekle biterdi, ancak doğum tarihi olmayan
erkek kayıtlar hiçbir yaş sorgusuna girmediği için her taramada kaybolurdu. Reddettik.

**Yalnızca uyruğa göre bölmek.** Uyruğu olmayan kayıtlar hiçbir uyruk sorgusunda görünmez ve
Rusya gibi büyük gruplar yine yaşa göre bölünmek zorunda. Cinsiyet, veriyi eksiksiz bölen tek
filtre olduğu için ilk adım oldu.

**Tamlığı farklı kimlik sayısının toplamla eşitliğine bağlamak.** Doğum tarihi olmayan
kayıtlar yüzünden ve tarama sırasında liste değiştiği için eşitlik çoğu taramada
sağlanmazdı; `removed` olayları hiç gönderilmezdi. Fark bir metrik olarak izlenecek.

**Görülme bilgisini tarama sonunda tek bir olayla göndermek.** Bütün kimlikleri taşıyan tek
bir mesaj daha az mesaj demek. Ancak farklı bir key ve partition'a düşeceği için aynı kaydın
`removed` olayıyla sırası garanti edilemezdi. Reddettik.

## Sonuçlar

Liste değişiklikleri birkaç saat içinde görülecek; yeni kayıtların detayı hemen, mevcut
kayıtlarınki yaklaşık günde bir yenilenecek. Yanlış yazılmış bir filtre veya bölünemeyen bir
grup sessizce veri kaybettirmeyecek, loglanacak. Bir kaydın yanlışlıkla silinmesi için iki
tam taramada birden görülmemesi gerekecek.

Buna karşılık bazı bedelleri kabul ediyoruz. Bir liste taraması uyruk sorguları yüzünden
yaklaşık 500 istek ve bir saat sürecek. Veritabanındaki detay en fazla yaklaşık bir gün
eskiyebilecek. Bir kaydın kalkması en erken iki tarama sonra, yani yaklaşık 12 saat içinde
yansıyacak. Doğum tarihi olmayan bir kayıt, cinsiyet ve uyruk grubu 160'ı aşıyorsa
görülemeyecek; bu durum farkı izleyen metrikte görünecek. ISO listesinde olmayan uyruk
kodlarına sahip kayıtlar da yalnızca başka bir uyrukları varsa görülecek. Ölçümler belirli
bir güne ait; Interpol API'sinin davranışı belgelenmediği için değişebilir ve `collector`
bu varsayımları çalışırken kontrol etmeye devam edecek.
