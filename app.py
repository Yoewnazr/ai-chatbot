import json
import os

from flask import Flask, Response, jsonify, render_template, request
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
# Route: Halaman utama
# ---------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/config")
def api_config():
    return jsonify({
        "model": MODEL_NAME,
        "has_api_key": bool(API_KEY),
        "vision_enabled": True,
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

    # Bersihkan mesej agar hanya role/content yang sah dihantar
    cleaned = _clean_messages(messages)
    if not cleaned:
        return jsonify({"error": "Tiada mesej sah ditemui."}), 400

    client = build_client()

    if stream:
        return _chat_stream(client, cleaned)

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + cleaned,
            temperature=0.7,
        )
        reply = response.choices[0].message.content or ""
        return jsonify({"reply": reply})
    except Exception as exc:
        return jsonify({"error": f"Ralat semasa menghubungi API: {str(exc)}"}), 500


def _chat_stream(client, messages):
    """Balas sebagai Server-Sent Events (SSE) untuk streaming masa nyata."""

    def generate():
        full_reply = ""
        try:
            stream_resp = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}] + messages,
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
