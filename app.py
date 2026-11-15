import json
import os
import urllib.request

from flask import Flask, Response, jsonify, render_template, request, send_from_directory
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI

# Muatkan pemboleh ubah persekitaran daripada fail .env (jika wujud)
load_dotenv()

app = Flask(__name__)
CORS(app)

# Had saiz permintaan (untuk imej base64) - 25MB
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024

# --- Konfigurasi (daripada Environment Variables) ---
API_KEY = os.environ.get("AI_API_KEY", "")
BASE_URL = os.environ.get("AI_BASE_URL", "")
MODEL_NAME = os.environ.get("AI_MODEL", "deepseek-v4.1-flash")

# --- V3: Supabase (Google Auth & Cloud Database) ---
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")

# System prompt: mesra, jelas, menyokong Bahasa Melayu dan memahami imej
SYSTEM_PROMPT = (
    "Anda ialah pembantu AI yang mesra, cekap dan membantu. "
    "Jawab soalan pengguna dengan tepat, jelas dan mudah difahami. "
    "Sokong Bahasa Melayu dengan sangat baik dan sentiasa balas dalam bahasa "
    "yang sama digunakan oleh pengguna. Gunakan format Markdown apabila sesuai, "
    "termasuk blok kod dengan penunjuk bahasa (contoh: ```python ... ```). "
    "Jika pengguna menghantar imej, huraikan, analisis atau bantu berdasarkan "
    "kandungan imej tersebut."
)


# ---------------------------------------------------------------
# V2: Senarai model yang disokong (dropdown UI)
# ---------------------------------------------------------------
SUPPORTED_MODELS = [
    {"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"},
    {"id": "hunyuan-free", "name": "Hunyuan Free"},
]


# ---------------------------------------------------------------
# V2: Persona / watak (system prompt selector)
# ---------------------------------------------------------------
PERSONAS = [
    {
        "id": "general",
        "name": "General Assistant",
        "prompt": (
            "Anda ialah pembantu AI yang mesra, cekap dan membantu. "
            "Jawab soalan pengguna dengan tepat, jelas dan mudah difahami. "
            "Sokong Bahasa Melayu dengan sangat baik dan sentiasa balas dalam bahasa "
            "yang sama digunakan oleh pengguna. Gunakan format Markdown apabila sesuai, "
            "termasuk blok kod dengan penunjuk bahasa. "
            "Jika pengguna menghantar imej, huraikan atau analisis kandungan imej tersebut."
        ),
    },
    {
        "id": "developer",
        "name": "Senior Developer",
        "prompt": (
            "Anda ialah seorang senior software engineer berpengalaman 15+ tahun. "
            "Berikan jawapan teknikal yang mendalam, tepat dan boleh dilaksanakan. "
            "Gunakan amalan terbaik, nyatakan trade-off, dan berikan blok kod yang "
            "bersih dengan penunjuk bahasa. Jelaskan konsep kompleks secara berperingkat. "
            "Balas dalam bahasa yang sama digunakan pengguna."
        ),
    },
    {
        "id": "writer",
        "name": "Creative Writer",
        "prompt": (
            "Anda ialah seorang penulis kreatif yang berbakat. "
            "Hasilkan penulisan yang menarik, beremosi dan penuh imaginasi. "
            "Gunakan gaya bahasa yang indah dan sesuai dengan konteks. "
            "Balas dalam bahasa yang sama digunakan pengguna."
        ),
    },
    {
        "id": "custom",
        "name": "Custom Prompt",
        "prompt": "",
    },
]


# ---------------------------------------------------------------
# Route: Halaman utama
# ---------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------
# Route: PWA (manifest & service worker di root)
# ---------------------------------------------------------------
@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    return send_from_directory("static", "sw.js", mimetype="application/javascript")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory("static", filename)


@app.route("/api/config")
def api_config():
    return jsonify({
        "model": MODEL_NAME,
        "has_api_key": bool(API_KEY),
        "vision_enabled": True,
        "models": SUPPORTED_MODELS,
        "personas": [
            {"id": p["id"], "name": p["name"], "prompt": p["prompt"]}
            for p in PERSONAS
        ],
        # V3: Butiran Supabase untuk Google Auth & penyelarasan awan di frontend.
        #     anon_key adalah selamat untuk pelayar (dilindungi oleh RLS).
        "supabase": {
            "enabled": bool(SUPABASE_URL and SUPABASE_ANON_KEY),
            "url": SUPABASE_URL,
            "anon_key": SUPABASE_ANON_KEY,
        },
    })


# ---------------------------------------------------------------
# V3: Pengesahan Token Supabase (JWT / Bearer Token)
# ---------------------------------------------------------------
def _extract_bearer_token(auth_header):
    """Ekstrak token daripada header 'Authorization: Bearer <token>'."""
    if not auth_header:
        return None
    parts = auth_header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def verify_supabase_token(token):
    """Sahkan token akses Supabase (JWT) melalui Auth API.

    Memanggil GET {SUPABASE_URL}/auth/v1/user dengan token Bearer.
    Kaedah ini rasmi & serasi dengan kedua-dua JWT HS256 dan RS256.
    Mengembalikan dict pengguna (termasuk 'id') jika token sah, atau None.
    """
    if not token or not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None
    url = f"{SUPABASE_URL}/auth/v1/user"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "apikey": SUPABASE_ANON_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("id"):
            return data
    except Exception:
        pass
    return None


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    """Pengesahan token backend: kembalikan profil pengguna yang log masuk.

    Digunakan oleh frontend untuk memastikan token akses masih sah
    sebelum operasi penyimpanan awan diteruskan (defense-in-depth).
    """
    token = _extract_bearer_token(request.headers.get("Authorization", ""))
    user = verify_supabase_token(token)
    if not user:
        return jsonify({"error": "Token tidak sah atau tamat tempoh."}), 401
    return jsonify({
        "id": user.get("id"),
        "email": user.get("email"),
        "user_metadata": user.get("user_metadata") or {},
    })


# ---------------------------------------------------------------
# Route: Chat (dengan streaming & sokongan imej/vision)
# ---------------------------------------------------------------
def build_client():
    """Bina OpenAI-compatible client berdasarkan konfigurasi env."""
    if not API_KEY:
        return None
    kwargs = {"api_key": API_KEY}
    if BASE_URL:
        kwargs["base_url"] = BASE_URL
    return OpenAI(**kwargs)


def _clean_messages(messages):
    """Sahkan & bersihkan array mesej daripada frontend.

    Kekalkan hanya role (user/assistant/system) + content yang sah.
    Content boleh berupa string (teks) atau array multimodal (teks + imej).
    """
    cleaned = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant", "system"):
            continue
        if not content:
            continue
        cleaned.append({"role": role, "content": content})
    return cleaned


def _prepare_messages(cleaned):
    """Pastikan sekurang-kurangnya satu system prompt wujud.

    Jika frontend menghantar mesej ber-role system (persona V2),
    hormati ia. Jika tiada, prepend SYSTEM_PROMPT lalai.
    """
    if any(m["role"] == "system" for m in cleaned):
        return cleaned
    return [{"role": "system", "content": SYSTEM_PROMPT}] + cleaned


@app.route("/api/chat", methods=["POST"])
def chat():
    if not API_KEY:
        return jsonify({"error": "AI_API_KEY tidak dikonfigurasikan pada pelayan."}), 500

    data = request.get_json(silent=True)
    if not data or "messages" not in data:
        return jsonify({"error": "Permintaan tidak sah. Sila hantar JSON dengan 'messages'."}), 400

    messages = data.get("messages", [])
    if not isinstance(messages, list) or not messages:
        return jsonify({"error": "Field 'messages' mestilah array yang tidak kosong."}), 400

    stream = bool(data.get("stream", False))

    # Model dinamik (V2): guna model dari payload, fallback ke AI_MODEL
    model = data.get("model") or MODEL_NAME
    valid_models = {m["id"] for m in SUPPORTED_MODELS}
    valid_models.add(MODEL_NAME)
    if model not in valid_models:
        model = MODEL_NAME

    # Bersihkan mesej agar hanya role/content yang sah dihantar
    cleaned = _clean_messages(messages)
    if not cleaned:
        return jsonify({"error": "Tiada mesej sah ditemui."}), 400

    # Hormati system prompt yang dihantar frontend (persona V2);
    # jika tiada, guna SYSTEM_PROMPT lalai.
    prepared = _prepare_messages(cleaned)

    client = build_client()

    if stream:
        return _chat_stream(client, prepared, model)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=prepared,
            temperature=0.7,
        )
        reply = response.choices[0].message.content or ""
        return jsonify({"reply": reply})
    except Exception as exc:
        return jsonify({"error": f"Ralat semasa menghubungi API: {str(exc)}"}), 500


def _chat_stream(client, messages, model):
    """Balas sebagai Server-Sent Events (SSE) untuk streaming masa nyata."""

    def generate():
        full_reply = ""
        try:
            stream_resp = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.7,
                stream=True,
            )
            for chunk in stream_resp:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        full_reply += delta
                        yield f"data: {json.dumps({'delta': delta}, ensure_ascii=False)}\n\n"

            yield f"data: {json.dumps({'done': True, 'full': full_reply}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"

    return Response(generate(), mimetype="text/event-stream")


@app.errorhandler(413)
def too_large(e):
    return jsonify({"error": "Imej terlalu besar (had 25MB)."}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
