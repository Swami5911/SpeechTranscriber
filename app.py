from flask import Flask, render_template, request, send_file, abort
from pathlib import Path
import speech_recognition as sr
from deep_translator import GoogleTranslator
from docx import Document
import PyPDF2
from pydub import AudioSegment
from fpdf import FPDF
import uuid
from werkzeug.utils import secure_filename
from typing import Dict, List, Optional
from transformers.pipelines import pipeline
import spacy

app = Flask(__name__)

UPLOAD_FOLDER = Path("uploads")
OUTPUT_FOLDER = Path("outputs")
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)

# Load NLP models
summarizer = pipeline("summarization", model="sshleifer/distilbart-cnn-12-6")
nlp = spacy.load("en_core_web_sm")

# Set FFmpeg converter path only
ffmpeg_path = Path(r"D:/ffmpeg/bin/ffmpeg.exe")
if ffmpeg_path.exists():
    AudioSegment.converter = str(ffmpeg_path)

languages: Dict[str, str] = {
    "English (US)": "en-US", "English (UK)": "en-GB", "Hindi": "hi-IN",
    "Tamil": "ta-IN", "Telugu": "te-IN", "Bengali": "bn-IN", "Marathi": "mr-IN",
    "Gujarati": "gu-IN", "Punjabi": "pa-IN", "Malayalam": "ml-IN",
    "Kannada": "kn-IN", "Urdu": "ur-IN"
}

translation_languages: Dict[str, str] = {
    "Hindi": "hi", "Tamil": "ta", "Telugu": "te", "Bengali": "bn", "Marathi": "mr",
    "Gujarati": "gu", "Punjabi": "pa", "Malayalam": "ml", "Kannada": "kn",
    "Urdu": "ur", "English": "en"
}

def split_audio(audio_path: Path, chunk_length_ms: int = 60000) -> List[AudioSegment]:
    audio = AudioSegment.from_file(audio_path)
    return [audio[i:i + chunk_length_ms] for i in range(0, len(audio), chunk_length_ms)]

def audio_to_text(audio_path: Path, language_code: str) -> str:
    recognizer = sr.Recognizer()
    chunks = split_audio(audio_path)
    full_transcript = ""
    for i, chunk in enumerate(chunks):
        chunk_path = UPLOAD_FOLDER / f"chunk_{uuid.uuid4()}.wav"
        chunk.export(chunk_path, format="wav")
        try:
            with sr.AudioFile(str(chunk_path)) as source:
                audio = recognizer.record(source)
            chunk_text = recognizer.recognize_google(audio, language=language_code)  # type: ignore[attr-defined]
            full_transcript += chunk_text + " "
        except Exception as e:
            print(f"[!] Failed on chunk {i}: {e}")
        finally:
            chunk_path.unlink(missing_ok=True)
    return full_transcript.strip()

def translate_text(text: str, target_language: str) -> str:
    try:
        return GoogleTranslator(source='auto', target=target_language).translate(text)
    except Exception as e:
        print(f"[!] Translation error: {e}")
        return "Translation failed."

def read_docx(file_path: Path) -> str:
    return "\n".join([para.text for para in Document(str(file_path)).paragraphs])

def read_pdf(file_path: Path) -> str:
    text = ""
    with open(str(file_path), 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            content = page.extract_text()
            if content:
                text += content + "\n"
    return text

def extract_audio_from_video(video_path: Path) -> Path:
    audio_path = UPLOAD_FOLDER / f"audio_{uuid.uuid4()}.wav"
    AudioSegment.from_file(video_path).export(audio_path, format="wav")
    return audio_path

def summarize_text(text: str) -> str:
    if len(text) < 50:
        return "Text too short for summarization."
    try:
        result = summarizer(text, max_length=150, min_length=40, do_sample=False)
        if isinstance(result, list) and isinstance(result[0], dict) and 'summary_text' in result[0]:
            return result[0]['summary_text']
        return "Summary unavailable."
    except Exception as e:
        return f"Summarization failed: {e}"

def extract_entities(text: str) -> List[tuple]:
    doc = nlp(text)
    return [(ent.text, ent.label_) for ent in doc.ents]

def save_as_pdf(text: str) -> str:
    filename = f"output_{uuid.uuid4()}.pdf"
    pdf_path = OUTPUT_FOLDER / filename
    pdf = FPDF()
    pdf.add_page()
    font_path = Path(__file__).parent / "DejaVuSans.ttf"
    if not font_path.exists():
        raise FileNotFoundError("DejaVuSans.ttf not found.")
    pdf.add_font("DejaVu", "", str(font_path), uni=True)
    pdf.set_font("DejaVu", "", 12)
    pdf.multi_cell(0, 10, text)
    pdf.output(str(pdf_path))
    return filename

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        input_type = request.form.get("input_type")
        lang = request.form.get("lang")
        translate_to = request.form.get("translate_to")
        file = request.files.get("file")
        if not file or not file.filename:
            return "No file uploaded"

        language_code = languages.get(lang or "English (US)", "en-US")
        translation_code = translation_languages.get(translate_to or "English", "en")

        filename = secure_filename(file.filename)
        file_path = UPLOAD_FOLDER / filename
        file.save(str(file_path))

        transcript = ""

        match input_type:
            case "audio":
                transcript = audio_to_text(file_path, language_code)
            case "video":
                audio_path = extract_audio_from_video(file_path)
                transcript = audio_to_text(audio_path, language_code)
                audio_path.unlink(missing_ok=True)
            case "document":
                ext = file_path.suffix.lower()
                if ext == ".docx":
                    transcript = read_docx(file_path)
                elif ext == ".pdf":
                    transcript = read_pdf(file_path)
                else:
                    return "Unsupported document format"
            case _:
                return "Unsupported input type"

        translated = translate_text(transcript, translation_code)
        summary = summarize_text(transcript)
        entities = extract_entities(transcript)
        pdf_filename = save_as_pdf(translated)

        return render_template("result.html",
                               transcript=transcript,
                               translated=translated,
                               summary=summary,
                               entities=entities,
                               pdf_filename=pdf_filename)

    return render_template("index.html",
                           languages=languages.keys(),
                           translation_languages=translation_languages.keys())

@app.route("/download")
def download():
    filename: Optional[str] = request.args.get("file")
    if not filename:
        abort(400, description="Missing file parameter")
    safe_path = OUTPUT_FOLDER / secure_filename(filename)
    if safe_path.exists():
        return send_file(safe_path, as_attachment=True)
    abort(404, description="File not found")

if __name__ == "__main__":
    app.run(debug=True)
