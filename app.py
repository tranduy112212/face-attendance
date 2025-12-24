from flask import Flask, render_template, request, jsonify
import cv2, os, base64, pickle, datetime, threading, io
import numpy as np
import dlib
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload
import gdown

app = Flask(__name__)

# ================= CẤU HÌNH =================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")

# Đọc từ biến môi trường (cho web) hoặc file (cho local)
if os.environ.get('ENVIRONMENT') == 'production':
    # Trên web server
    import json

    SERVICE_ACCOUNT_JSON = json.loads(os.environ.get('SERVICE_ACCOUNT_JSON', '{}'))
    SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')

    creds = service_account.Credentials.from_service_account_info(
        SERVICE_ACCOUNT_JSON,
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
    )
else:
    # Trên máy local
    SERVICE_ACCOUNT_FILE = r"C:\Users\FUKUHAKU DANANG\Downloads\service_account.json"
    SPREADSHEET_ID = '1kgjBSHGeRUBrTe8312apbE-ZovS5IeuL_uqHAO37p1o'

    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=[
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
    )

# Khởi tạo Google services
sheets_service = build('sheets', 'v4', credentials=creds)
drive_service = build('drive', 'v3', credentials=creds)
sheet = sheets_service.spreadsheets()
sheet_lock = threading.Lock()

print("✅ Google Sheets & Drive kết nối thành công!")


def download_models():
    """Download models từ Google Drive lần đầu"""
    files = {
        "shape_predictor": ("FILE_ID_1", "models/shape_predictor_68_face_landmarks.dat"),
        "face_recognition": ("FILE_ID_2", "models/dlib_face_recognition_resnet_model_v1.dat"),
        "encodings": ("FILE_ID_3", "known_encodings.npy"),
        "names": ("FILE_ID_4", "known_names.pkl")
    }

    for name, (file_id, path) in files.items():
        if not os.path.exists(path):
            print(f"⬇️ Downloading {name}...")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            gdown.download(id=file_id, output=path, quiet=False)


# Gọi trước khi load models
download_models()
# ================= LOAD MODELS =================
try:
    detector = dlib.get_frontal_face_detector()
    sp = dlib.shape_predictor(
        os.path.join(MODEL_DIR, "shape_predictor_68_face_landmarks.dat")
    )
    facerec = dlib.face_recognition_model_v1(
        os.path.join(MODEL_DIR, "dlib_face_recognition_resnet_model_v1.dat")
    )

    known_face_encodings = np.load(os.path.join(BASE_DIR, "known_encodings.npy"))
    with open(os.path.join(BASE_DIR, "known_names.pkl"), "rb") as f:
        known_face_names = pickle.load(f)

    print("=== NHÂN VIÊN TRONG HỆ THỐNG ===")
    print(sorted(set(known_face_names)))
    print("=================================")

    MODEL_READY = True
except Exception as e:
    print(f"❌ Lỗi load model: {e}")
    MODEL_READY = False

# ================= ATTENDANCE CACHE =================
last_attendance = {}
ATTEND_INTERVAL = 1800  # 30 phút
current_day = datetime.datetime.now().strftime('%Y-%m-%d')

# ================= GOOGLE DRIVE FUNCTIONS =================
DRIVE_ROOT_FOLDER = None  # Sẽ tạo tự động


def get_or_create_folder(folder_name, parent_id=None):
    """Tạo hoặc lấy folder trên Google Drive"""
    try:
        # Tìm folder
        query = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_id:
            query += f" and '{parent_id}' in parents"

        results = drive_service.files().list(
            q=query,
            fields="files(id, name)",
            spaces='drive'
        ).execute()

        folders = results.get('files', [])
        if folders:
            return folders[0]['id']

        # Tạo folder mới
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_id:
            file_metadata['parents'] = [parent_id]

        folder = drive_service.files().create(
            body=file_metadata,
            fields='id'
        ).execute()

        print(f"📁 Tạo folder: {folder_name}")
        return folder.get('id')

    except Exception as e:
        print(f"❌ Lỗi tạo folder: {e}")
        return None


def upload_image_to_drive(img, name, time_str):
    """Upload ảnh lên Google Drive"""
    global DRIVE_ROOT_FOLDER

    try:
        # Tạo folder gốc AttendanceImages
        if not DRIVE_ROOT_FOLDER:
            DRIVE_ROOT_FOLDER = get_or_create_folder("AttendanceImages")

        # Tạo folder ngày hôm nay
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        today_folder = get_or_create_folder(today, DRIVE_ROOT_FOLDER)

        # Encode ảnh
        _, buffer = cv2.imencode('.jpg', img)
        img_bytes = io.BytesIO(buffer.tobytes())

        # Tên file
        filename = f"{time_str.replace(':', '-')}_{name}.jpg"

        # Upload
        file_metadata = {
            'name': filename,
            'parents': [today_folder]
        }

        media = MediaInMemoryUpload(
            img_bytes.getvalue(),
            mimetype='image/jpeg',
            resumable=True
        )

        file = drive_service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        print(f"📸 Uploaded: {filename}")
        return file.get('id'), file.get('webViewLink')

    except Exception as e:
        print(f"❌ Lỗi upload: {e}")
        return None, None


# ================= GOOGLE SHEETS FUNCTIONS =================
def check_and_create_sheet(title):
    try:
        meta = sheet.get(spreadsheetId=SPREADSHEET_ID).execute()
        titles = [s['properties']['title'] for s in meta['sheets']]

        if title not in titles:
            sheet.batchUpdate(
                spreadsheetId=SPREADSHEET_ID,
                body={"requests": [{
                    "addSheet": {"properties": {"title": title}}
                }]}
            ).execute()

            headers = ["Tên"] + [f"Lần {i}" for i in range(1, 21)]
            sheet.values().update(
                spreadsheetId=SPREADSHEET_ID,
                range=f"{title}!A1:U1",
                valueInputOption="USER_ENTERED",
                body={"values": [headers]}
            ).execute()
    except Exception as e:
        print(f"❌ Lỗi sheet: {e}")


def get_sheet_data(title):
    try:
        res = sheet.values().get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{title}!A1:Z1000"
        ).execute()
        return res.get("values", [])
    except:
        return []


def update_sheet(title, data):
    try:
        sheet.values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{title}!A1:Z1000"
        ).execute()

        sheet.values().update(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{title}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": data}
        ).execute()
    except Exception as e:
        print(f"❌ Lỗi update: {e}")


def append_attendance(name, time_str):
    today = datetime.datetime.now().strftime('%Y-%m-%d')

    with sheet_lock:
        check_and_create_sheet(today)
        data = get_sheet_data(today)

        # Tìm hàng của người này
        for row in data:
            if row and row[0] == name:
                # Thêm vào cột mới
                for i in range(1, 50):
                    if i >= len(row) or row[i] == "":
                        if i >= len(row):
                            row.append(time_str)
                        else:
                            row[i] = time_str
                        update_sheet(today, data)
                        return

        # Nếu chưa có hàng, tạo mới
        data.append([name, time_str])
        update_sheet(today, data)


# ================= FACE RECOGNITION =================
def can_mark_attendance(name):
    """Kiểm tra 30 phút"""
    now = datetime.datetime.now()
    if name not in last_attendance:
        return True
    time_diff = (now - last_attendance[name]).total_seconds()
    return time_diff >= ATTEND_INTERVAL


def recognize_face(base64_img, threshold):
    if not MODEL_READY:
        return {"success": False, "error": "Model chưa sẵn sàng"}

    try:
        # Decode ảnh
        img_data = base64.b64decode(base64_img.split(",")[1])
        img = cv2.imdecode(np.frombuffer(img_data, np.uint8), cv2.IMREAD_COLOR)

        if img is None:
            return {"success": False}

        # Resize và flip
        img = cv2.resize(img, (640, 480))
        img = cv2.flip(img, 1)
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        # Detect faces
        dets = detector(rgb, 1)
        if not dets:
            return {"success": False}

        # Get face encoding
        shape = sp(rgb, dets[0])
        face_enc = np.array(facerec.compute_face_descriptor(rgb, shape))

        # So sánh
        dists = np.linalg.norm(known_face_encodings - face_enc, axis=1)
        idx = np.argmin(dists)

        if dists[idx] > threshold:
            return {"success": False}

        name = known_face_names[idx]

        # Kiểm tra 30 phút
        if not can_mark_attendance(name):
            return {"success": False, "silent": True}

        # Chấm công
        now = datetime.datetime.now()
        last_attendance[name] = now
        time_str = now.strftime("%H:%M:%S")

        # Lưu vào Sheets
        append_attendance(name, time_str)

        # Upload ảnh lên Drive (chạy background)
        threading.Thread(
            target=upload_image_to_drive,
            args=(img, name, time_str),
            daemon=True
        ).start()

        return {
            "success": True,
            "name": name,
            "time": time_str
        }

    except Exception as e:
        print(f"❌ RECOGNIZE ERROR: {e}")
        return {"success": False}


# ================= ROUTES =================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/recognize", methods=["POST"])
def recognize():
    global current_day, last_attendance

    # Reset cache nếu qua ngày mới
    today = datetime.datetime.now().strftime('%Y-%m-%d')
    if today != current_day:
        last_attendance = {}
        current_day = today

    data = request.json
    return jsonify(
        recognize_face(
            data["image"],
            float(data.get("threshold", 0.5))
        )
    )


@app.route("/delete", methods=["POST"])
def delete_attendance():
    data = request.json
    name = data["name"]
    time_str = data["time"]
    today = datetime.datetime.now().strftime('%Y-%m-%d')

    with sheet_lock:
        check_and_create_sheet(today)
        sheet_data = get_sheet_data(today)

        for row in sheet_data:
            if row and row[0] == name:
                for i in range(1, len(row)):
                    if row[i] == time_str:
                        row[i] = ""
                        break
                break

        update_sheet(today, sheet_data)

    # Xóa cache
    if name in last_attendance:
        del last_attendance[name]

    return jsonify({"success": True})


@app.route("/status")
def status():
    return jsonify({
        "model_ready": MODEL_READY,
        "known_faces": len(set(known_face_names)) if MODEL_READY else 0
    })


# ================= START =================
if __name__ == "__main__":
    # Tạo sheet ngày hôm nay
    with sheet_lock:
        check_and_create_sheet(
            datetime.datetime.now().strftime('%Y-%m-%d')
        )

    print("\n" + "=" * 50)
    print("🚀 Face Attendance Server")
    print("=" * 50)
    print("📍 Local: http://localhost:5000")
    print("📸 Ảnh sẽ lưu lên Google Drive")
    print("=" * 50 + "\n")

    PORT = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=PORT, debug=False)