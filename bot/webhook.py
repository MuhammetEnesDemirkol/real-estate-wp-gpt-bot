import sys
import os
import requests
from requests.auth import HTTPBasicAuth
from twilio.twiml.messaging_response import MessagingResponse
from datetime import datetime
import json
from twilio.rest import Client
from sqlalchemy.orm import Session
from googleapiclient.errors import HttpError
import re
import shutil

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from bot.gpt_parser import parse_message_to_json
from drive_service.uploader import upload_multiple_photos, upload_file_to_drive, get_or_create_folder, get_drive_service, delete_folder, get_folder_info, delete_folder_by_id
from backend.database import SessionLocal
from backend.crud import create_emlak_ilan, get_ilanlar, delete_emlak_ilan
from backend.schemas.ilan import IlanCreate

load_dotenv()

app = FastAPI()

# CORS ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # React uygulamasının çalıştığı adres
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Backend API adresi
BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:8000/ilan")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")

# Twilio client oluştur
twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

# Kullanıcı durumlarını takip etmek için sözlük
user_states = {}

def generate_ilan_baslik(mahalle, sokak, oda_sayisi):
    mahalle = ''.join(c for c in mahalle if c.isalnum() or c.isspace())
    sokak = ''.join(c for c in sokak if c.isalnum() or c.isspace())
    return f"{mahalle}-{sokak}-{oda_sayisi}"

def create_ilan_folder(service, ilan_details):
    """İlan için Drive'da klasör oluştur"""
    try:
        # Ana klasör ID'sini .env'den al
        main_folder_id = os.getenv("GOOGLE_DRIVE_MAIN_FOLDER_ID")
        if not main_folder_id:
            raise ValueError("GOOGLE_DRIVE_MAIN_FOLDER_ID bulunamadı")

        # İlan detaylarını al
        mahalle = ilan_details.get("mahalle", "Belirsiz")
        sokak = ilan_details.get("sokak", "Belirsiz")
        oda_sayisi = ilan_details.get("oda_sayisi", "Belirsiz")
        
        # İlan klasör adını oluştur
        ilan_folder_name = generate_ilan_baslik(mahalle, sokak, oda_sayisi) + " #SADEEVIM"
        
        # 3+1 kontrolü
        if oda_sayisi.strip().lower() == "3 + 1":
            # Doğrudan ana klasöre ekle
            parent_id = main_folder_id
        else:
            # Önce oda türü klasörünü oluştur veya bul
            oda_folder_name = oda_sayisi.strip()
            oda_folder = get_or_create_folder(service, oda_folder_name, main_folder_id)
            parent_id = oda_folder.get('id')
        
        # İlan klasörünü oluştur
        folder_metadata = {
            'name': ilan_folder_name,
            'mimeType': 'application/vnd.google-apps.folder',
            'parents': [parent_id]
        }
        
        folder = service.files().create(body=folder_metadata, fields='id').execute()
        folder_id = folder.get('id')
        
        # Klasörü herkese açık yap
        permission = {
            'type': 'anyone',
            'role': 'reader'
        }
        service.permissions().create(fileId=folder_id, body=permission).execute()
        
        print(f"Drive klasörü oluşturuldu. ID: {folder_id}")
        return folder_id
    except Exception as e:
        print(f"Drive klasörü oluşturma hatası: {str(e)}")
        print(f"Hata detayı: {type(e).__name__}")
        raise

def send_whatsapp_message(to_number: str, message: str):
    """WhatsApp mesajı gönder"""
    try:
        message = twilio_client.messages.create(
            from_=f"whatsapp:{TWILIO_PHONE_NUMBER}",
            body=message,
            to=to_number
        )
        print(f"WhatsApp mesajı gönderildi: {message.sid}")
        return True
    except Exception as e:
        print(f"WhatsApp mesajı gönderme hatası: {str(e)}")
        return False

def process_ilan(from_number: str, ilan_details: dict, drive_folder_id: str):
    """İlanı işle ve veritabanına kaydet"""
    try:
        # Drive klasör linkini oluştur
        drive_link = f"https://drive.google.com/drive/folders/{drive_folder_id}"
        
        # Veritabanına kaydet
        db = SessionLocal()
        try:
            # Metrekare değerini float'a çevir
            metrekare = ilan_details.get("metrekare", "")
            try:
                metrekare = float(metrekare) if metrekare else None
            except ValueError:
                metrekare = None

            # Fiyat değerini float'a çevir
            fiyat = ilan_details.get("fiyat", "")
            try:
                fiyat = float(fiyat) if fiyat else None
            except ValueError:
                fiyat = None

            # IlanCreate nesnesi oluştur
            mahalle = ilan_details.get("mahalle", "")
            sokak = ilan_details.get("sokak", "")
            oda_sayisi = ilan_details.get("oda_sayisi", "")
            baslik = generate_ilan_baslik(mahalle, sokak, oda_sayisi)
            ilan_data = IlanCreate(
                baslik=baslik,
                aciklama=ilan_details.get("aciklama", ""),
                fiyat=fiyat,
                mahalle=mahalle,
                sokak=sokak,
                oda_sayisi=oda_sayisi,
                metrekare=metrekare,
                drive_link=drive_link
            )
            
            db_ilan = create_emlak_ilan(db, ilan_data)
            
            # Kullanıcıya bildirim gönder
            success_message = f"İlanınız başarıyla kaydedildi!\n\nDrive klasör linki: {drive_link}"
            send_whatsapp_message(from_number, success_message)
            
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"İlan işleme hatası: {str(e)}")
        error_message = "İlan kaydedilirken bir hata oluştu. Lütfen daha sonra tekrar deneyiniz."
        send_whatsapp_message(from_number, error_message)
        return False

@app.post("/webhook")
async def receive_message(request: Request):
    try:
        form_data = await request.form()
        from_number = form_data.get("From")
        message_body = form_data.get("Body")
        num_media = int(form_data.get("NumMedia", 0))

        print(f"\nYeni mesaj geldi: {from_number} - {message_body} (Foto sayısı: {num_media})")
        print(f"Form verileri: {dict(form_data)}")
        print(f"Mevcut kullanıcı durumu: {json.dumps(user_states.get(from_number, {}), indent=2)}")

        # TwiML yanıtı oluştur
        resp = MessagingResponse()

        # Kullanıcının mevcut durumunu kontrol et
        current_state = user_states.get(from_number, {})

        if message_body and message_body.strip().lower() == "/ekle":
            # Yeni ilan ekleme başlat
            user_states[from_number] = {
                "state": "waiting_for_details",
                "expected_photos": 0,
                "received_photos": 0,
                "details": "",
                "photos": [],
                "temp_photos": []
            }
            print(f"Yeni ilan başlatıldı: {from_number}")
            resp.message("Lütfen ilan detaylarını giriniz.")
            response = Response(content=str(resp), media_type="application/xml")
            print(f"Gönderilen yanıt: {str(resp)}")
            return response

        elif message_body and message_body.strip().lower() == "/sil":
            # Silme işlemi için kullanıcıdan anahtar kelime iste
            user_states[from_number] = {
                "state": "waiting_for_search_keyword",
                "action": "delete"
            }
            resp.message("Lütfen silmek istediğiniz klasör için bir anahtar kelime (ör: mahalle, oda tipi, vs.) giriniz.")
            response = Response(content=str(resp), media_type="application/xml")
            return response

        elif current_state.get("state") == "waiting_for_details":
            # İlan detaylarını analiz et
            try:
                print(f"İlan detayları analiz ediliyor: {message_body}")
                parsed_details = parse_message_to_json(message_body)
                print(f"Analiz sonucu: {json.dumps(parsed_details, indent=2)}")
                
                if not parsed_details:
                    print("İlan detayları analiz edilemedi")
                    resp.message("İlan detayları analiz edilemedi. Lütfen daha açıklayıcı bir şekilde tekrar giriniz.")
                    response = Response(content=str(resp), media_type="application/xml")
                    print(f"Gönderilen yanıt: {str(resp)}")
                    return response
                
                # İlan detaylarını kaydet
                user_states[from_number] = {
                    "state": "waiting_for_photo_count",
                    "details": parsed_details,
                    "expected_photos": 0,
                    "received_photos": 0,
                    "photos": [],
                    "temp_photos": []
                }
                print(f"İlan detayları kaydedildi: {json.dumps(parsed_details, indent=2)}")
                resp.message("Kaç görsel ekleyeceksiniz?")
                response = Response(content=str(resp), media_type="application/xml")
                print(f"Gönderilen yanıt: {str(resp)}")
                return response
            except Exception as e:
                print(f"İlan detayları analiz hatası: {str(e)}")
                print(f"Hata detayı: {type(e).__name__}")
                resp.message("İlan detayları analiz edilirken bir hata oluştu. Lütfen tekrar deneyiniz.")
                response = Response(content=str(resp), media_type="application/xml")
                print(f"Gönderilen yanıt: {str(resp)}")
                return response

        elif current_state.get("state") == "waiting_for_photo_count":
            try:
                photo_count = int(message_body)
                user_states[from_number] = {
                    "state": "waiting_for_photos",
                    "expected_photos": photo_count,
                    "received_photos": 0,
                    "details": current_state["details"],
                    "photos": [],
                    "temp_photos": []
                }
                print(f"Görsel sayısı kaydedildi: {photo_count}")
                resp.message(f"Lütfen {photo_count} adet görseli tek seferde seçip gönderiniz.")
                response = Response(content=str(resp), media_type="application/xml")
                print(f"Gönderilen yanıt: {str(resp)}")
                return response
            except ValueError:
                print("Geçersiz görsel sayısı")
                resp.message("Lütfen geçerli bir sayı giriniz.")
                response = Response(content=str(resp), media_type="application/xml")
                print(f"Gönderilen yanıt: {str(resp)}")
                return response

        elif current_state.get("state") == "waiting_for_photos":
            if num_media > 0:
                print(f"\nYeni görseller alındı: {num_media} adet")
                # Her ilana özel benzersiz klasör oluştur
                import time
                photo_folder = f"temp_whatsapp/{from_number.replace(':', '_')}_{int(time.time())}"
                os.makedirs(photo_folder, exist_ok=True)
                for i in range(num_media):
                    media_url = form_data.get(f"MediaUrl{i}")
                    media_type = form_data.get(f"MediaContentType{i}")
                    ext = ".jpg" if "jpeg" in media_type else ".png"
                    try:
                        response = requests.get(media_url, auth=HTTPBasicAuth(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
                        if response.status_code == 200:
                            temp_filename = os.path.join(photo_folder, f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{i}{ext}")
                            with open(temp_filename, "wb") as f:
                                f.write(response.content)
                            current_state["temp_photos"].append(temp_filename)
                            print(f"Fotoğraf indirildi: {temp_filename}")
                        else:
                            print(f"Fotoğraf indirme hatası: {response.status_code}")
                            print(f"Hata detayı: {response.text}")
                    except Exception as e:
                        print(f"Fotoğraf indirme hatası: {str(e)}")
                        print(f"Hata detayı: {type(e).__name__}")
                current_state["received_photos"] += num_media
                remaining = current_state["expected_photos"] - current_state["received_photos"]
                print(f"Toplam alınan: {current_state['received_photos']} / Beklenen: {current_state['expected_photos']}")
                if current_state["received_photos"] >= current_state["expected_photos"]:
                    try:
                        service = get_drive_service()
                        drive_folder_id = create_ilan_folder(service, current_state["details"])
                        # Sadece bu ilana özel klasördeki fotoğrafları yükle
                        photo_links = upload_multiple_photos(photo_folder, drive_folder_id)
                        if not photo_links:
                            raise Exception("Fotoğraflar Drive'a yüklenemedi")
                        if process_ilan(from_number, current_state["details"], drive_folder_id):
                            # Geçici fotoğraf klasörünü sil
                                try:
                                shutil.rmtree(photo_folder)
                                except Exception as e:
                                print(f"Geçici klasör silme hatası: {str(e)}")
                                    print(f"Hata detayı: {type(e).__name__}")
                            user_states[from_number] = {}
                            resp.message("İlanınız başarıyla kaydedildi!")
                        else:
                            resp.message("İlan kaydedilirken bir hata oluştu. Lütfen tekrar deneyiniz.")
                    except Exception as e:
                        print(f"İlan işleme hatası: {str(e)}")
                        print(f"Hata detayı: {type(e).__name__}")
                        resp.message("İlan işlenirken bir hata oluştu. Lütfen tekrar deneyiniz.")
                        response = Response(content=str(resp), media_type="application/xml")
                        return response
                else:
                    send_whatsapp_message(from_number, f"{remaining} görsel daha bekleniyor.")
                    return Response(content="", media_type="application/xml")
            else:
                print("Görsel beklenirken medya yok")
                send_whatsapp_message(from_number, "Lütfen görselleri gönderin.")
                return Response(content="", media_type="application/xml")

        elif current_state.get("state") == "waiting_for_search_keyword" and current_state.get("action") == "delete":
            search_keyword = message_body.strip()
            drive_service = get_drive_service()
            success, folder_info = get_folder_info(drive_service, search_keyword)
            if not success:
                resp.message(f"Klasör bulunamadı. Lütfen anahtar kelimeyi kontrol ediniz.")
                response = Response(content=str(resp), media_type="application/xml")
                return response

            # Klasörlerin tam yolunu bulmak için yardımcı fonksiyon
            def get_folder_path(service, folder_id, name_cache=None):
                if name_cache is None:
                    name_cache = {}
                try:
                    folder = service.files().get(fileId=folder_id, fields="id, name, parents").execute()
                    name = folder['name']
                    parents = folder.get('parents', [])
                    if not parents:
                        return name
                    parent_id = parents[0]
                    if parent_id in name_cache:
                        parent_name = name_cache[parent_id]
                    else:
                        parent = service.files().get(fileId=parent_id, fields="id, name, parents").execute()
                        parent_name = parent['name']
                        name_cache[parent_id] = parent_name
                    return f"{get_folder_path(service, parent_id, name_cache)}/{name}"
                except HttpError:
                    return name

            folder_list = "\n".join([
                f"- {get_folder_path(drive_service, item['id'])} (id: {item['id']})" for item in folder_info
            ])
            resp.message(f"Bulunan klasörler:\n{folder_list}\n\nLütfen silmek istediğiniz klasörün tam adını ve id'sini giriniz.")
            # Sonraki adımda id bekle
            user_states[from_number] = {
                "state": "waiting_for_folder_name",
                "action": "delete"
            }
            response = Response(content=str(resp), media_type="application/xml")
            return response

        elif current_state.get("state") == "waiting_for_folder_name" and current_state.get("action") == "delete":
            folder_info_text = message_body.strip()
            # Klasör id'sini ayıkla
            match = re.search(r'id: ([a-zA-Z0-9_-]+)', folder_info_text)
            if not match:
                resp.message("Klasör id'si bulunamadı. Lütfen mesajı aşağıdaki formatta girin:\n(id: xxxxxxxx)")
                response = Response(content=str(resp), media_type="application/xml")
                return response
            folder_id = match.group(1)

            drive_service = get_drive_service()
            # Klasörü id ile sil
            drive_success, drive_message = delete_folder_by_id(drive_service, folder_id)

            # Veritabanından ilanı sil (isimle devam edelim)
            db = SessionLocal()
            try:
                # Klasör adını veritabanındaki başlık formatına çevir
                # (Kullanıcıdan ad+id birlikte gelirse, ad kısmını ayıklayalım)
                db_folder_name = folder_info_text.split(' (id:')[0].split('/')[-1].replace(" #SADEEVIM", "").strip()
                db_success, db_message = delete_emlak_ilan(db, db_folder_name)
            finally:
                db.close()

            # Sonucu kullanıcıya bildir
            if drive_success and db_success:
                resp.message("İlan ve ilgili klasör başarıyla silindi.")
            else:
                error_message = "İlan silinirken hatalar oluştu:\n"
                if not drive_success:
                    error_message += f"Drive: {drive_message}\n"
                if not db_success:
                    error_message += f"Veritabanı: {db_message}"
                resp.message(error_message)

            # Kullanıcı durumunu sıfırla
            user_states[from_number] = {}

            response = Response(content=str(resp), media_type="application/xml")
            return response

        # Varsayılan yanıt
        resp.message("Geçersiz komut. İlan eklemek için /ekle komutunu kullanın.")
        return Response(content=str(resp), media_type="application/xml")

    except Exception as e:
        print(f"Genel hata: {str(e)}")
        print(f"Hata detayı: {type(e).__name__}")
        resp = MessagingResponse()
        resp.message("Bir hata oluştu. Lütfen tekrar deneyiniz.")
        return Response(content=str(resp), media_type="application/xml")

@app.get("/ilan")
async def get_ilanlar_endpoint():
    try:
        db = SessionLocal()
        try:
            ilanlar = get_ilanlar(db)
            return ilanlar
        finally:
            db.close()
    except Exception as e:
        print(f"İlanları getirme hatası: {str(e)}")
        print(f"Hata detayı: {type(e).__name__}")
        return {"error": "İlanlar getirilirken bir hata oluştu"}
