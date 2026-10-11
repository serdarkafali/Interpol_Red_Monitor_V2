# interpol-contracts

Servisler arasında Kafka üzerinden gönderilen mesajların şemalarını tanımlayan kütüphane.
Mesajı üreten servis (collector) ve tüketen servisler (ingestor, face-worker) aynı tanımı
kullanır; böylece iki taraf arasında format uyumsuzluğu oluşamaz.

Bu paket iş mantığı içermez; sadece veri sözleşmelerini tanımlar.
