# VERICI - Bood B9-A / X-Sport Pro kamerasını web üzerinden yayınlar
#
# Okuldaki (kameranın yanındaki) bilgisayarda çalışır:
#   1) Bilgisayar kameranın Wi-Fi'sine bağlı olsun (telefon uygulaması KAPALI).
#   2) Bilgisayarın internet için ayrı bir bağlantısı olsun (kablo / ikinci Wi-Fi adaptörü / 4G).
#   3) pip install av opencv-python numpy
#   4) python verici.py
#
# Sonra evdeki bilgisayarın tarayıcısından  http://<verici-adresi>:8080  açılır.
# Yönetim sistemindeki 'Atölye Kamerası' sekmesine adres + şifre girilir.
# Kullanıcı adı: admin   Şifre: verici.py'nin yanındaki sifre.txt dosyasında (ilk çalışmada oluşur).
#
# Seçenekler:  python verici.py --port 8080 --kalite 60 --genislik 640 --fps 10 --kamera-ip 192.168.100.1

import argparse
import base64
import hmac
import json
import os
import secrets
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

try:
    import cv2
    import numpy as np  # noqa: F401  (cv2 için gerekli)
    import av
except ImportError as e:
    print("EKSIK PAKET:", e)
    print("Kurmak icin:  pip install av opencv-python numpy")
    input("Kapatmak icin Enter'a basin...")
    sys.exit(1)

TCP_PORT = 6666
UDP_PORT = 6669
KULLANICI = "admin"

LOGIN = (bytes.fromhex("abcd008100000110")
         + b"admin".ljust(64, b"\x00") + b"12345".ljust(65, b"\x00"))
PING_ISARETI = bytes.fromhex("abcd000000000112")
PONG = bytes.fromhex("abcd000000000113")
ONIZLEME_AC = bytes.fromhex("abcd0008000001ff0000000000000000")
ONIZLEME_KAPAT = bytes.fromhex("abcd0008000002ff0000000000000000")
UDP_CANLI_TUT = bytes.fromhex("bcde000000000005")


# ---------------------------------------------------------------- kamera
class Kamera:
    def __init__(self, ip):
        self.ip = ip
        self.dur = threading.Event()
        self.kilit = threading.Lock()
        self.son_kare = None
        self.kare_no = 0
        self.durum = "Kameraya bağlanılıyor..."
        self.son_veri = 0.0
        self.tcp_koptu = threading.Event()

    def baslat(self):
        threading.Thread(target=self._dongu, daemon=True).start()

    def kapat(self):
        self.dur.set()

    def kare_al(self):
        with self.kilit:
            return self.kare_no, self.son_kare

    def _tcp_dinle(self, s):
        s.settimeout(1)
        while not self.dur.is_set() and not self.tcp_koptu.is_set():
            try:
                d = s.recv(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            if not d:
                break
            if d.startswith(PING_ISARETI):
                try:
                    s.sendall(PONG)
                except OSError:
                    break
        self.tcp_koptu.set()

    def _dongu(self):
        while not self.dur.is_set():
            try:
                self._oturum()
            except Exception as e:
                self.durum = "Bağlantı sorunu: %s — yeniden deneniyor..." % str(e)[:60]
            with self.kilit:
                self.son_kare = None
            self.dur.wait(2)

    def _yeni_cozucu(self):
        return av.CodecContext.create("h264", "r")

    def _oturum(self):
        self.tcp_koptu.clear()
        self.durum = "Kameraya bağlanılıyor..."
        s = socket.create_connection((self.ip, TCP_PORT), timeout=5)
        u = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        u.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        u.bind(("0.0.0.0", UDP_PORT))
        u.settimeout(0.5)
        try:
            threading.Thread(target=self._tcp_dinle, args=(s,), daemon=True).start()
            s.sendall(LOGIN)
            time.sleep(0.6)
            s.sendall(ONIZLEME_AC)
            self.durum = "Görüntü bekleniyor..."

            cozucu = self._yeni_cozucu()
            beklenen = None
            idr_bekle = True
            son_canli = 0.0
            self.son_veri = time.time()

            while not self.dur.is_set():
                simdi = time.time()
                if self.tcp_koptu.is_set():
                    raise ConnectionError("kamera bağlantıyı kesti")
                if simdi - son_canli > 3:
                    u.sendto(UDP_CANLI_TUT, (self.ip, UDP_PORT))
                    son_canli = simdi
                try:
                    d, _ = u.recvfrom(65535)
                except socket.timeout:
                    if simdi - self.son_veri > 6:
                        raise TimeoutError("görüntü gelmiyor")
                    continue
                if len(d) < 8 or d[:2] != b"\xbc\xde":
                    continue
                uzunluk, seq, tip = struct.unpack(">HHH", d[2:8])
                if beklenen is not None and seq != beklenen:
                    cozucu = self._yeni_cozucu()
                    idr_bekle = True
                beklenen = (seq + 1) & 0xFFFF
                if tip != 1:
                    continue
                veri = d[8:8 + uzunluk]
                self.son_veri = simdi
                if idr_bekle:
                    if veri[:4] == b"\x00\x00\x00\x01" and len(veri) > 4 and (veri[4] & 0x1F) == 7:
                        idr_bekle = False
                    else:
                        continue
                try:
                    for paket in cozucu.parse(veri):
                        for kare in cozucu.decode(paket):
                            img = kare.to_ndarray(format="bgr24")
                            with self.kilit:
                                self.son_kare = img
                                self.kare_no += 1
                            self.durum = "Canlı"
                except Exception:
                    cozucu = self._yeni_cozucu()
                    idr_bekle = True
        finally:
            try:
                s.sendall(ONIZLEME_KAPAT)
            except OSError:
                pass
            self.tcp_koptu.set()
            s.close()
            u.close()


# ---------------------------------------------------------------- yayın
class Yayin:
    """Son kareyi JPEG'e çevirir; her kare bir kez kodlanır, tüm izleyiciler paylaşır."""

    def __init__(self, kamera, kalite, genislik):
        self.kamera = kamera
        self.kalite = kalite
        self.genislik = genislik
        self.kilit = threading.Lock()
        self.no = -1
        self.jpeg = None

    def al(self):
        no, img = self.kamera.kare_al()
        if img is None:
            return self.no, None
        with self.kilit:
            if no != self.no:
                im = img
                if self.genislik and im.shape[1] > self.genislik:
                    oran = self.genislik / im.shape[1]
                    im = cv2.resize(im, (self.genislik, int(im.shape[0] * oran)))
                ok, buf = cv2.imencode(".jpg", im, [cv2.IMWRITE_JPEG_QUALITY, self.kalite])
                if ok:
                    self.jpeg = buf.tobytes()
                    self.no = no
            return self.no, self.jpeg


SAYFA = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Atölye Kamerası</title>
<style>
  body { margin:0; background:#111; color:#eee; font:15px system-ui, sans-serif; }
  header { padding:10px 16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; background:#1b1b1b; }
  h1 { font-size:17px; margin:0 auto 0 0; font-weight:600; }
  #durum { padding:3px 10px; border-radius:12px; background:#555; font-size:13px; }
  #durum.canli { background:#1c7c3a; }
  #durum.sorun { background:#9a2a2a; }
  button, a.d { background:#333; color:#eee; border:1px solid #555; border-radius:6px;
                padding:6px 12px; cursor:pointer; text-decoration:none; font:inherit; }
  button:hover, a.d:hover { background:#444; }
  main { display:flex; justify-content:center; padding:12px; }
  #video { max-width:100%; max-height:calc(100vh - 90px); background:#000; }
</style>
</head>
<body>
<header>
  <h1>Atölye Kamerası</h1>
  <span id="durum">...</span>
  <a class="d" href="/foto" download>Fotoğraf indir</a>
  <button onclick="yenile()">Yenile</button>
  <button onclick="document.getElementById('video').requestFullscreen()">Tam ekran</button>
</header>
<main><img id="video" alt="Görüntü bekleniyor..."></main>
<script>
  const video = document.getElementById('video');
  const durum = document.getElementById('durum');
  function yenile() { video.src = '/akis?t=' + Date.now(); }
  video.onerror = () => setTimeout(yenile, 3000);
  async function durumGuncelle() {
    try {
      const r = await fetch('/durum', {cache: 'no-store'});
      const d = await r.json();
      durum.textContent = d.durum;
      durum.className = d.canli ? 'canli' : 'sorun';
    } catch (e) {
      durum.textContent = 'Verici ile bağlantı yok';
      durum.className = 'sorun';
    }
  }
  yenile();
  durumGuncelle();
  setInterval(durumGuncelle, 3000);
</script>
</body>
</html>
"""


def sifreyi_al():
    """sifre.txt yoksa rastgele bir şifre üretip yazar."""
    yol = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sifre.txt")
    env = os.environ.get("VERICI_SIFRE")
    if env:
        return env, "(VERICI_SIFRE ortam değişkeni)"
    try:
        with open(yol, encoding="utf-8") as f:
            s = f.read().strip()
        if s:
            return s, yol
    except OSError:
        pass
    s = secrets.token_urlsafe(9)
    with open(yol, "w", encoding="utf-8") as f:
        f.write(s + "\n")
    return s, yol


def yerel_adresler():
    adresler = []
    try:
        for bilgi in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = bilgi[4][0]
            if not a.startswith("127.") and a not in adresler:
                adresler.append(a)
    except OSError:
        pass
    return adresler


def sunucu_kur(yayin, kamera, sifre, aralik):
    kullanici_b = KULLANICI.encode()
    sifre_b = sifre.encode()

    class Isleyici(BaseHTTPRequestHandler):
        server_version = "Verici"

        def log_message(self, *a):
            pass

        def yetkili(self):
            # 1) yönetim sistemi sekmesi: ?k=<şifre>
            q = parse_qs(urlsplit(self.path).query).get("k", [""])[0]
            if q and hmac.compare_digest(q.encode(), sifre_b):
                return True
            # 2) tarayıcıdan doğrudan açılış: Basic kimlik doğrulama
            h = self.headers.get("Authorization", "")
            if h.startswith("Basic "):
                try:
                    kul, _, sif = base64.b64decode(h[6:]).partition(b":")
                except Exception:
                    return False
                ok1 = hmac.compare_digest(kul, kullanici_b)
                ok2 = hmac.compare_digest(sif, sifre_b)
                return ok1 and ok2
            return False

        def yanit(self, kod, tur, veri, ekstra=()):
            self.send_response(kod)
            self.send_header("Content-Type", tur)
            self.send_header("Content-Length", str(len(veri)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in ekstra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(veri)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "Authorization")
            self.end_headers()

        def do_GET(self):
            if not self.yetkili():
                time.sleep(1)  # şifre denemelerini yavaşlat
                self.yanit(401, "text/plain; charset=utf-8", b"Giris gerekli",
                           [("WWW-Authenticate", 'Basic realm="Atolye Kamerasi"')])
                return
            yol = self.path.split("?", 1)[0]
            try:
                if yol == "/":
                    self.yanit(200, "text/html; charset=utf-8", SAYFA.encode("utf-8"))
                elif yol == "/durum":
                    d = {"durum": kamera.durum, "canli": kamera.durum == "Canlı"}
                    self.yanit(200, "application/json; charset=utf-8",
                               json.dumps(d, ensure_ascii=False).encode("utf-8"))
                elif yol == "/foto":
                    _, jpeg = yayin.al()
                    if jpeg is None:
                        self.yanit(503, "text/plain; charset=utf-8", "Görüntü yok".encode("utf-8"))
                    else:
                        ad = time.strftime("foto_%Y%m%d_%H%M%S.jpg")
                        self.yanit(200, "image/jpeg", jpeg,
                                   [("Content-Disposition", 'attachment; filename="%s"' % ad)])
                elif yol == "/akis":
                    self.akis()
                else:
                    self.yanit(404, "text/plain; charset=utf-8", b"Yok")
            except (BrokenPipeError, ConnectionError, OSError):
                pass

        def akis(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=kare")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            son = -1
            while True:
                t0 = time.time()
                no, jpeg = yayin.al()
                if jpeg is None or no == son:
                    time.sleep(0.03)
                    continue
                son = no
                self.wfile.write(b"--kare\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                 + str(len(jpeg)).encode() + b"\r\n\r\n")
                self.wfile.write(jpeg)
                self.wfile.write(b"\r\n")
                kalan = aralik - (time.time() - t0)
                if kalan > 0:
                    time.sleep(kalan)

    return Isleyici


def main():
    ap = argparse.ArgumentParser(description="Kamera yayın programı (verici)")
    ap.add_argument("--kamera-ip", default="192.168.100.1")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--kalite", type=int, default=60, help="JPEG kalitesi 1-100 (düşük = az internet)")
    ap.add_argument("--genislik", type=int, default=640, help="yayın genişliği px (0 = orijinal)")
    ap.add_argument("--fps", type=float, default=10, help="izleyici başına en çok kare/saniye")
    a = ap.parse_args()

    sifre, sifre_yeri = sifreyi_al()
    kamera = Kamera(a.kamera_ip)
    kamera.baslat()
    yayin = Yayin(kamera, max(1, min(100, a.kalite)), a.genislik)

    ThreadingHTTPServer.daemon_threads = True
    sunucu = ThreadingHTTPServer(("0.0.0.0", a.port),
                                 sunucu_kur(yayin, kamera, sifre, 1.0 / max(0.5, a.fps)))
    print("VERICI calisiyor.")
    print("  Kullanici: %s" % KULLANICI)
    print("  Sifre    : %s   (kayitli: %s)" % (sifre, sifre_yeri))
    print("  Adresler :")
    print("    http://localhost:%d" % a.port)
    for ad in yerel_adresler():
        print("    http://%s:%d" % (ad, a.port))
    print("Durdurmak icin Ctrl+C")
    last = ""
    try:
        threading.Thread(target=sunucu.serve_forever, daemon=True).start()
        while True:
            if kamera.durum != last:
                last = kamera.durum
                print("[%s] Kamera: %s" % (time.strftime("%H:%M:%S"), last))
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        kamera.kapat()
        sunucu.shutdown()
        time.sleep(0.5)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print("HATA:", e)
        input("Kapatmak için Enter'a basın...")
