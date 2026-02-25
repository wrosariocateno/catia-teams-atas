import subprocess
import glob
from google.cloud import speech

# ----------------------------
# Áudio / Speech
# ----------------------------
def extract_audio_wav(mp4_path: str) -> str:
    """Extrai WAV 16kHz mono PCM do MP4 usando ffmpeg."""
    wav_path = mp4_path.rsplit(".", 1)[0] + ".wav"
    cmd = ["ffmpeg", "-y", "-i", mp4_path, "-ac", "1", "-ar", "16000", "-f", "wav", wav_path]
    subprocess.check_call(cmd)
    return wav_path

def split_wav_ffmpeg(wav_path: str, segment_seconds: int = 55) -> list[str]:
    """Divide um WAV em segmentos menores (<60s)."""
    base = wav_path.rsplit(".", 1)[0]
    out_pattern = f"{base}_part_%03d.wav"

    cmd = [
        "ffmpeg", "-y", "-i", wav_path, "-f", "segment",
        "-segment_time", str(segment_seconds), "-c", "copy", out_pattern
    ]
    subprocess.check_call(cmd)

    parts = sorted(glob.glob(f"{base}_part_*.wav"))
    if not parts:
        raise RuntimeError("Falha ao dividir o WAV (nenhuma parte gerada).")
    return parts

def transcribe_wav_chunked(wav_path: str, language_code: str = "pt-BR", segment_seconds: int = 55) -> str:
    """Transcreve WAV dividindo em chunks."""
    client = speech.SpeechClient()
    parts = split_wav_ffmpeg(wav_path, segment_seconds=segment_seconds)

    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=16000,
        language_code=language_code,
        enable_automatic_punctuation=True,
        model="latest_long",
    )

    transcripts = []
    for part in parts:
        with open(part, "rb") as f:
            audio_content = f.read()

        audio = speech.RecognitionAudio(content=audio_content)
        resp = client.recognize(config=config, audio=audio)

        chunk_text = " ".join([r.alternatives[0].transcript for r in resp.results]).strip()
        if chunk_text:
            transcripts.append(chunk_text)

    return "\n".join(transcripts).strip()