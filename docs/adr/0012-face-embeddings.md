# ADR 0012: Yüzleri bulmak, vektöre çevirmek ve benzerlerini aramak

- **Durum:** Kabul edildi
- **Tarih:** 2026-11-24
- **Karar verenler:** Serdar Kafalı
- **İlişkili:** ADR 0004 (pgvector), ADR 0011 (face-worker ve fotoğraf deposu)

## Bağlam

Projenin başından beri istenen iki şey var: farklı kayıtlarda aynı kişinin bulunup
bulunmadığını görmek ve bir kaydın fotoğraflarının birbiriyle tutarlı olup olmadığını
anlamak. Faz 4'te fotoğraflar depoya indirildi; Faz 5'te bunlardaki yüzler bulunacak,
sabit uzunlukta vektörlere (embedding) çevrilecek ve PostgreSQL'de pgvector ile
karşılaştırılacak.

Bunun için model eğitilmiyor. Yüz tanıma bir sınıflandırma problemi değil: kişiler sabit
değil, çoğunun bir iki fotoğrafı var ve hangi fotoğrafların aynı kişiye ait olduğunu zaten
bilmiyoruz. Bunun yerine çok sayıda yüzle bir kez eğitilmiş, "aynı kişinin yüzleri yakın,
farklı kişilerinki uzak" vektörler üreten hazır modeller kullanılıyor. Bizim işimiz modeli
seçmek, eşiği belirlemek ve ölçmek.

Kararı etkileyen koşullar:

- En yaygın ve en doğru seçenek olan InsightFace modellerinin (buffalo_l: SCRFD + ArcFace)
  lisansı yalnızca ticari olmayan araştırmaya izin veriyor. Proje ticari olmayacak ama
  herkese açık bir portfolyoda duracak; "araştırma" sayılıp sayılmadığı belirsiz ve repoyu
  fork'layan biri için kısıt sürer.
- Test ve ölçümde gerçek kişilerin yüzleri kullanılmayacak.
- Hatalı bir eşleşme bir kişiyi haksız yere başka biriyle ilişkilendirebilir.

## Ölçüm

`scripts/compare_face_models.py` (2026-11-24, Serdar'ın makinesinde, CPU). Veri:
DigiFace-1M'in `subjects_133333-166665_5_imgs` parçasından rastgele seçilen 300 sentetik
kişi, kişi başına 5 yüz (1500 yüz); 3000 aynı kişi ve 3000 farklı kişi çifti. Veri seti
yalnızca okundu; repoya ve imaja girmedi.

| Model | Boyut | Yüz başına | Aynı kişi (ortanca) | Farklı kişi (ortanca) | EER | Eşik @ FAR %1 | FRR @ FAR %1 | Lisans |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| **SFace** | 128 | 5,2 ms | 0,651 | 0,211 | **%3,93** | **0,443** | **%7,33** | Apache-2.0 |
| AuraFace (glintr100) | 512 | 46,8 ms | 0,524 | 0,118 | %4,58 | 0,316 | %8,67 | Apache-2.0 |
| InsightFace w600k_r50 (yalnızca referans) | 512 | 85,4 ms | 0,593 | 0,105 | %1,23 | 0,298 | %1,50 | Ticari olmayan araştırma |

- **EER:** yanlış kabul ve yanlış ret oranlarının eşit olduğu noktadaki hata.
- **FAR %1:** farklı iki kişiyi "olası eşleşme" diye gösterme oranı en fazla %1.
- **FRR @ FAR %1:** o eşikte aynı kişinin kaçırılma oranı.

AuraFace'in beklenenden zayıf çıkması üzerine girdi hazırlığı ayrıca denendi: RGB yerine
BGR verildiğinde sonuç kötüleşti (EER %5,38, FRR %11,60), yani hazırlık doğruydu.
YuNet, 112x112 kırpılmış yüzlerin kenarlarına boşluk eklendiğinde yüzlerin %98,8'ini
buldu.

## Karar

### Model: YuNet + SFace

Yüz bulma için YuNet (MIT), vektör için SFace (Apache-2.0, 128 boyut). İkisi de OpenCV'nin
içinde hazır (`FaceDetectorYN`, `FaceRecognizerSF`); ek bir çalışma zamanı gerekmiyor.
SFace ölçümde AuraFace'ten hem daha doğru hem dokuz kat hızlı çıktı; model dosyası 39 MB
(AuraFace 261 MB).

Lisans özgürlüğünün bedeli ölçüldü: aynı yanlış kabul oranında SFace, InsightFace'in
kaçırdığının yaklaşık beş katı kadar gerçek eşleşmeyi kaçırıyor. Bu bedel, projenin
kısıtsız kalması için kabul edildi.

Model dosyaları repoya girmez; Docker imajı oluşturulurken sabit bir adresten indirilir ve
SHA-256 özetiyle doğrulanır.

### Hangi servis

Yüz işleme face-worker'a ikinci bir iş olarak eklenir. Fotoğraflar zaten onun deposunda;
iş, Faz 4'teki gibi veritabanından seçilir (ADR 0011). Döngünün sırası: günlük temizlik
zamanı geldiyse temizlik; yoksa önce yüz işleme (yerel ve hızlı), o yoksa indirme, o da
yoksa bekleme. İndirmeler Interpol için 5–8 sn beklediğinden yüz işleme onları
yavaşlatmaz.

### Bir fotoğrafın işlenmesi

1. İndirilmiş (`downloaded_at` dolu), kaydı kaldırılmamış ve yüz işleme yapılmamış
   (`faces_processed_at` boş) sıradaki fotoğraf seçilir.
2. Dosya depodan okunur ve çözülür.
3. YuNet yüzleri bulur. Tespit skoru 0,6'nın altındaki ve kısa kenarı 40 pikselden küçük
   yüzler atlanır: kötü yüzlerin vektörleri yanlış eşleşme üretir.
4. Kalan her yüz, YuNet'in beş noktasıyla `alignCrop` ile 112x112 hizalanır ve SFace ile
   vektöre çevrilir; vektörün uzunluğu 1'e ölçeklenir.
5. Tek transaction'da: fotoğrafın eski yüz satırları silinir, yenileri eklenir ve
   `faces_processed_at` doldurulur. Hiç yüz bulunmasa da doldurulur; fotoğraf tekrar
   işlenmez.

Çözülemeyen bir dosya da işlenmiş sayılır ve loglanır. Depoya ya da veritabanına
ulaşılamıyorsa fotoğraf işlenmiş sayılmaz, ADR 0011'deki gibi beklenir.

### Veri modeli

Bir migration ile:

- pgvector eklentisi (`CREATE EXTENSION IF NOT EXISTS vector`). İmaj zaten pgvector'lü
  (ADR 0004).
- `notice_photos.faces_processed_at timestamptz` (boş olabilir).
- Yeni `faces` tablosu: `id`, `photo_id` (FK, `ON DELETE CASCADE`), yüz kutusu (`box_x`,
  `box_y`, `box_width`, `box_height`), `detection_score`, `embedding vector(128)`,
  `model` (ör. `yunet-2023mar+sface-2021dec`), `created_at`.
- `embedding` üzerinde kosinüs uzaklığına göre HNSW index (`vector_cosine_ops`).

`CASCADE` sayesinde ingestor bir fotoğraf satırını sildiğinde ya da Faz 8'deki kalıcı
silme bir kaydı sildiğinde yüzleri de silinir. `model` sütunu, model değişirse eski ve yeni
vektörlerin karıştırılmasını önler: farklı modellerin vektörleri karşılaştırılamaz. Model
değişirse `faces_processed_at` sıfırlanarak fotoğraflar yeniden işlenir.

### Benzerlik araması

Eşleşmeler önceden hesaplanıp bir tabloya yazılmaz; sorgu anında pgvector ile aranır:
"bu yüze en yakın N yüz". Veri küçük (on binlerce yüz) ve eşik değiştiğinde önceden
hesaplanmış sonuçların hepsi geçersiz kalırdı. İki sorgu yazılır:

- **Kayıtlar arası:** bir yüze benzeyen, **başka kayıtlara** ait yüzler.
- **Kayıt içi:** bir kaydın yüzlerinin birbirine benzerliği; eşiğin altında kalan çiftler
  "tutarsız olabilir" diye işaretlenir.

Başlangıç eşiği **0,443** (benzerlik; pgvector'ün `<=>` uzaklığı için 0,557). Bir ayardır
ve gerçek fotoğraflarda yeniden değerlendirilecek.

### Etik ilke

Sistem hiçbir zaman "aynı kişi" demez. Eşiği geçen her sonuç **insan incelemesi gereken
olası eşleşme** olarak, benzerlik skoruyla birlikte sunulur. Eşik bilerek yanlış kabulü
düşük tutacak şekilde seçildi; bunun bedeli bazı gerçek eşleşmelerin kaçırılması.

### Test

- Birim testleri: sahte detektör ve sahte embedder; servis ve sorgular gerçek görüntü
  olmadan test edilir.
- Veritabanı testleri: gerçek PostgreSQL + pgvector (testcontainers), elle hazırlanmış
  küçük vektörlerle en yakın komşu ve eşik davranışı.
- Repoya hiçbir yüz fotoğrafı girmez.

## Değerlendirilen alternatifler

**InsightFace (buffalo_l).** Ölçümde açık ara en doğrusu, ancak modellerin lisansı ticari
olmayan araştırmayla sınırlı. Yalnızca referans olarak ölçüldü. Reddedildi.

**AuraFace.** Apache-2.0 ve ArcFace uyumlu, ama bu veride SFace'ten daha az doğru, dokuz
kat yavaş ve yedi kat büyük çıktı; ayrıca `onnxruntime` gerektiriyor. Reddedildi;
gerçek fotoğraflarda SFace yetersiz kalırsa ilk aday.

**face_recognition (dlib).** Kolay kullanılıyor ama eski, Windows'ta kurulumu zahmetli ve
128 boyutlu modeli SFace'ten zayıf. Ölçülmedi, reddedildi.

**Kendi modelimizi eğitmek.** Binlerce kişinin etiketli yüzlerini, GPU'yu ve gerçek
yüzlerle çalışmayı gerektirir; gizlilik ilkemizle çelişir. Reddedildi.

**Ayrı bir yüz servisi.** Fotoğrafları depodan ikinci kez okuyacak, aynı seçme kalıbını
tekrar edecek ve yeni bir container getirecekti. İşlem süresi küçük olduğu için gerek
yok. Reddedildi; yük artarsa face-worker'dan ayrılabilir.

**Eşleşmeleri önceden hesaplayıp tablolamak.** Okuma hızlı olurdu ama her yeni yüzde ve her
eşik değişikliğinde yeniden hesaplama gerekir. Bu veri boyutunda sorgu anında arama yeterli.
Reddedildi.

## Sonuçlar

Fotoğraflardaki yüzler açık lisanslı, hafif bir modelle vektöre çevrilecek ve benzerler
pgvector ile aranabilecek. Face-worker'a ek bir servis eklenmeden yeni bir iş eklenecek.

Kabul edilen bedeller: SFace, en iyi modele göre daha çok gerçek eşleşmeyi kaçırıyor; eşik
sentetik verilerle seçildi ve gerçek fotoğraflarda farklı davranabilir; face-worker'ın
imajı OpenCV ve model dosyalarıyla büyüyecek; yüz işleme indirmelerle aynı döngüde
çalıştığı için biri yavaşlarsa diğeri bekler.
